-- Prepaid credit system.
--
-- A credit is the customer-facing unit of pricing (1 credit = $0.33,
-- see src/credits.py:CREDIT_VALUE_CENTS). Customers buy credits in
-- advance and every billable action deducts from the per-company
-- balance.
--
-- This migration adds:
--   1. `companies.credit_balance`    — running balance (numeric)
--   2. `companies.credits_purchased` — lifetime topped-up (audit)
--   3. `companies.credits_consumed`  — lifetime spent  (audit)
--   4. `credit_transactions`         — append-only ledger of every
--                                       deduction / top-up / adjust
--   5. RPC `consume_credits()`       — atomic check + deduct + log
--   6. RPC `add_credits()`           — atomic top-up + log
--   7. RLS so a company only sees its own ledger
--   8. Seed: every existing company starts with 100 demo credits so
--      the demo flow still works after deploy.
--
-- Idempotent. Re-runnable.

-- ---------------------------------------------------------------
-- 1) Balance columns on companies
-- ---------------------------------------------------------------
alter table companies
  add column if not exists credit_balance     numeric(14, 4) not null default 0,
  add column if not exists credits_purchased  numeric(14, 4) not null default 0,
  add column if not exists credits_consumed   numeric(14, 4) not null default 0;

-- ---------------------------------------------------------------
-- 2) Ledger table — append-only
-- ---------------------------------------------------------------
create table if not exists credit_transactions (
  id              bigserial primary key,
  company_id      uuid not null references companies(id) on delete cascade,
  user_id         uuid references auth.users(id) on delete set null,
  -- 'consume' or 'topup' or 'adjustment' or 'refund'
  kind            text not null check (kind in ('consume','topup','adjustment','refund')),
  -- Signed change in credit balance: negative for consume/adjust-down,
  -- positive for topup/refund/adjust-up.
  delta           numeric(14, 4) not null,
  balance_after   numeric(14, 4) not null,
  -- For consume rows: which action drove the deduction (analyze,
  -- bulk_scan_query, call_script, email_draft, enrichment). For other
  -- kinds: a short source label ('admin_topup', 'stripe', 'manual').
  action          text,
  related_id      text,            -- place_id / run_id / payment id
  cost_cents      numeric(14, 4),  -- underlying $ cost (consume only)
  notes           text,
  created_at      timestamptz not null default now()
);

create index if not exists idx_credit_tx_company_created
  on credit_transactions (company_id, created_at desc);
create index if not exists idx_credit_tx_kind
  on credit_transactions (kind);


-- ---------------------------------------------------------------
-- 3) Atomic CONSUME — verifies balance, deducts, writes ledger
-- ---------------------------------------------------------------
--
-- Raises with SQLSTATE 'P0001' when the balance would go negative.
-- The API layer catches that and returns HTTP 402 Payment Required.
--
-- Returns the new running balance so the caller can show it in the
-- response without a second round-trip.
create or replace function consume_credits(
  p_company_id uuid,
  p_user_id    uuid,
  p_amount     numeric,    -- positive number — how many credits to burn
  p_action     text,
  p_related_id text default null,
  p_cost_cents numeric default null,
  p_notes      text default null
) returns numeric
language plpgsql
security definer
set search_path = public
as $$
declare
  v_balance numeric(14, 4);
  v_after   numeric(14, 4);
begin
  if p_amount is null or p_amount < 0 then
    raise exception 'consume_credits: amount must be >= 0 (got %)', p_amount
      using errcode = '22023';
  end if;
  if p_amount = 0 then
    -- Zero-cost no-op (e.g. cache hit). Don't write a ledger row.
    select credit_balance into v_balance from companies where id = p_company_id;
    return v_balance;
  end if;

  -- Row-lock the company so concurrent consumes serialize.
  select credit_balance into v_balance
    from companies
   where id = p_company_id
   for update;

  if v_balance is null then
    raise exception 'consume_credits: company % not found', p_company_id
      using errcode = '02000';
  end if;
  if v_balance < p_amount then
    raise exception 'INSUFFICIENT_CREDITS: balance=% needed=%', v_balance, p_amount
      using errcode = 'P0001';
  end if;

  v_after := round(v_balance - p_amount, 4);

  update companies
     set credit_balance    = v_after,
         credits_consumed  = credits_consumed + p_amount
   where id = p_company_id;

  insert into credit_transactions
    (company_id, user_id, kind, delta, balance_after,
     action, related_id, cost_cents, notes)
  values
    (p_company_id, p_user_id, 'consume', -p_amount, v_after,
     p_action, p_related_id, p_cost_cents, p_notes);

  return v_after;
end;
$$;


-- ---------------------------------------------------------------
-- 4) Atomic ADD (topup / refund / positive adjustment)
-- ---------------------------------------------------------------
create or replace function add_credits(
  p_company_id uuid,
  p_user_id    uuid,
  p_amount     numeric,    -- positive amount of credits to grant
  p_kind       text,       -- 'topup' | 'refund' | 'adjustment'
  p_source     text default null,  -- 'admin_topup' | 'stripe' | …
  p_notes      text default null
) returns numeric
language plpgsql
security definer
set search_path = public
as $$
declare
  v_balance numeric(14, 4);
  v_after   numeric(14, 4);
begin
  if p_amount is null or p_amount <= 0 then
    raise exception 'add_credits: amount must be > 0 (got %)', p_amount
      using errcode = '22023';
  end if;
  if p_kind not in ('topup','refund','adjustment') then
    raise exception 'add_credits: bad kind %', p_kind
      using errcode = '22023';
  end if;

  select credit_balance into v_balance
    from companies
   where id = p_company_id
   for update;
  if v_balance is null then
    raise exception 'add_credits: company % not found', p_company_id
      using errcode = '02000';
  end if;
  v_after := round(v_balance + p_amount, 4);

  update companies
     set credit_balance     = v_after,
         credits_purchased  = case
           when p_kind = 'topup' then credits_purchased + p_amount
           else credits_purchased
         end
   where id = p_company_id;

  insert into credit_transactions
    (company_id, user_id, kind, delta, balance_after, action, notes)
  values
    (p_company_id, p_user_id, p_kind, p_amount, v_after, p_source, p_notes);

  return v_after;
end;
$$;


-- ---------------------------------------------------------------
-- 5) Negative adjustment helper (debits without raising on
--    insufficient balance — admin-driven only). Sometimes you want
--    to claw back credits on a refund; this is the unsafe twin.
-- ---------------------------------------------------------------
create or replace function debit_credits(
  p_company_id uuid,
  p_user_id    uuid,
  p_amount     numeric,
  p_notes      text default null
) returns numeric
language plpgsql
security definer
set search_path = public
as $$
declare
  v_balance numeric(14, 4);
  v_after   numeric(14, 4);
begin
  if p_amount is null or p_amount <= 0 then
    raise exception 'debit_credits: amount must be > 0 (got %)', p_amount
      using errcode = '22023';
  end if;
  select credit_balance into v_balance
    from companies
   where id = p_company_id
   for update;
  v_after := round(v_balance - p_amount, 4);
  update companies set credit_balance = v_after where id = p_company_id;
  insert into credit_transactions
    (company_id, user_id, kind, delta, balance_after, action, notes)
  values
    (p_company_id, p_user_id, 'adjustment', -p_amount, v_after,
     'admin_debit', p_notes);
  return v_after;
end;
$$;


-- ---------------------------------------------------------------
-- 6) RLS — tenant members see their own ledger, only admins write
-- ---------------------------------------------------------------
alter table credit_transactions enable row level security;

drop policy if exists "tenant_credit_tx_read" on credit_transactions;
create policy "tenant_credit_tx_read"
  on credit_transactions for select
  using (company_id in (
    select company_id from company_members where user_id = auth.uid()
  ));

-- Writes always go through the RPCs (security definer), so we lock
-- down direct writes from authenticated roles.
drop policy if exists "tenant_credit_tx_no_direct_write" on credit_transactions;
create policy "tenant_credit_tx_no_direct_write"
  on credit_transactions for insert
  with check (false);


-- ---------------------------------------------------------------
-- 7) Seed every existing company with 100 demo credits if they
--    don't have any yet. Lifetime stats stay at 0 so the admin
--    dashboard makes sense ("haven't purchased anything yet").
-- ---------------------------------------------------------------
update companies
   set credit_balance = 100
 where credit_balance = 0
   and credits_purchased = 0
   and credits_consumed = 0;


-- ---------------------------------------------------------------
-- Sanity report
-- ---------------------------------------------------------------
select id, name, credit_balance, credits_purchased, credits_consumed
  from companies
 order by created_at;
