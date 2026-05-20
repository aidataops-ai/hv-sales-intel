-- Reset script: wipe all practice + email + search data. Keep auth users
-- and their profiles intact.
--
-- WHAT GETS DELETED
--   practices         (every lead row)
--   email_messages    (every sent/received email — cascades from practices
--                      but truncated explicitly for clarity)
--   searches          (Google Places query cache)
--   _dupe_plan        (any stale temp tables from cleanup_dupes.sql)
--   _dupe_pairs
--
-- WHAT IS KEPT
--   auth.users        (Supabase Auth credentials)
--   profiles          (name, role, bootstrap_admin flag — everything user-related)
--
-- This is destructive. Read the verification counts at the bottom before
-- you COMMIT.

begin;

-- Drop temp tables from a previous cleanup_dupes.sql run, if any.
drop table if exists _dupe_plan;
drop table if exists _dupe_pairs;

-- The actual wipe. RESTART IDENTITY resets the bigserial counters so the
-- next inserted row gets id=1. CASCADE handles the email_messages →
-- practices FK relationship.
truncate table email_messages restart identity cascade;
truncate table practices      restart identity cascade;
truncate table searches       restart identity cascade;

-- Verification — every row count below should be 0 EXCEPT profiles + auth.users.
select 'practices'            as table_name, count(*) as rows from practices
union all
select 'email_messages',       count(*) from email_messages
union all
select 'searches',             count(*) from searches
union all
select 'profiles (kept)',      count(*) from profiles
union all
select 'auth.users (kept)',    count(*) from auth.users
order by 1;

-- If the verification looks right:
--   COMMIT;
-- Otherwise:
--   ROLLBACK;
