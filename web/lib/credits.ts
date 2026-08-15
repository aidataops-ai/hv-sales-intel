/**
 * Prepaid credits — frontend constants + fetchers.
 *
 * Mirrors the backend constants in src/credits.py. Keep these in sync;
 * the server is the source of truth (it returns its own copy in
 * /api/me/credits.rates) but the constants here drive the upfront UI
 * estimates so we don't need a roundtrip before showing a "1 credit"
 * badge on a button.
 */

import { useSyncExternalStore } from "react"

// 1 credit = 33¢. Customer-facing price.
export const CREDIT_VALUE_CENTS = 33

// Universal multiple of vendor cost — every billable action runs the
// customer at 10x our underlying cost. Mirrors COST_MULTIPLIER in
// src/credits.py.
export const COST_MULTIPLIER = 10

// Display ranges for dynamic actions. Every action is dynamic now —
// even Bulk Scan, since a single Places search can fan out to 1-3
// pages. The server returns the same ranges in /api/me/credits.rates
// so this object stays in sync if constants change.
export const ANALYZE_RANGE:         [number, number] = [0.3, 1.5]
export const CALL_SCRIPT_RANGE:     [number, number] = [0.1, 0.4]
export const EMAIL_DRAFT_RANGE:     [number, number] = [0.05, 0.20]
// Bulk scan: 0.97 (1 page) → 2.91 (3 pages). We display the HIGH end
// in the modal so users see the worst-case spend before pressing Start.
export const BULK_SCAN_RANGE:       [number, number] = [0.97, 2.91]
// Single Place Details call (1.7¢ × 10 / 33¢).
export const PLACES_DETAILS_CREDITS = 0.52
// Enrichment uses a representative cost (6.6¢ × 10 / 33¢ = 2).
export const ENRICHMENT_CREDITS     = 2

export type CreditAction =
  | "analyze"
  | "call_script"
  | "email_draft"
  | "bulk_scan_query"
  | "enrichment"
  | "topup"
  | "adjustment"
  | "refund"

export interface CreditTransaction {
  id: number
  kind: "consume" | "topup" | "adjustment" | "refund"
  delta: number
  balance_after: number
  action: string | null
  related_id: string | null
  cost_cents: number | null
  notes: string | null
  created_at: string
}

export interface CreditsState {
  balance: number
  purchased: number
  consumed: number
  credit_value_cents: number
  cost_multiplier: number
  rates: {
    analyze:         [number, number]
    call_script:     [number, number]
    email_draft:     [number, number]
    bulk_scan_query: [number, number]
    places_details:  number
    enrichment:      number
  }
  transactions: CreditTransaction[]
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

export async function fetchCredits(): Promise<CreditsState | null> {
  try {
    const res = await fetch(`${API_URL}/api/me/credits`, {
      credentials: "include",
    })
    if (!res.ok) return null
    return (await res.json()) as CreditsState
  } catch {
    return null
  }
}

export async function topupCredits(amount: number, notes?: string): Promise<number | null> {
  try {
    const res = await fetch(`${API_URL}/api/admin/credits/topup`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount, notes }),
    })
    if (!res.ok) return null
    const body = await res.json()
    return body.balance ?? null
  } catch {
    return null
  }
}

// ---------------------------------------------------------------
// Formatting + display helpers
// ---------------------------------------------------------------

export function formatCredits(n: number): string {
  if (!Number.isFinite(n)) return "0"
  if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n))
  return n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")
}

export function creditsToDollars(n: number): string {
  return `$${((n * CREDIT_VALUE_CENTS) / 100).toFixed(2)}`
}

export function rangeLabel(range: [number, number]): string {
  const [lo, hi] = range
  return `${formatCredits(lo)}–${formatCredits(hi)} credits`
}

// ---------------------------------------------------------------
// Shared store — one credits snapshot for the whole app
//
// The balance used to be per-hook state, so every `useCredits()` mount
// fired its own GET /api/me/credits — and the topbar pill mounts on
// every page, alongside the shell's own /api/me + /api/me/companies.
// Credits now arrive with the rest of the shell in GET /api/session,
// which `AuthProvider` seeds into this store on boot.
//
// A module-level store rather than a second React context, for two
// reasons: `lib/auth.tsx` already imports this module for the seed, so
// a context here would close the import cycle; and a plain store lets a
// non-React caller (a spend that just settled) push a fresh balance.
// `useSyncExternalStore` gives every subscriber the same snapshot, so
// the pill and a page reading the same balance can't disagree.
// ---------------------------------------------------------------

export interface CreditsSnapshot {
  data: CreditsState | null
  /** True until the first read settles — seeded or failed. Drives the
   *  pill's skeleton exactly as the old per-hook `loading` did. */
  loading: boolean
  /** `Date.now()` of the last settle; 0 while unsettled. Only used by
   *  `refreshCreditsIfStale`. */
  fetchedAt: number
}

const UNSETTLED: CreditsSnapshot = { data: null, loading: true, fetchedAt: 0 }

// Replaced wholesale, never mutated: `useSyncExternalStore` compares
// snapshots by identity and would miss an in-place edit.
let snapshot: CreditsSnapshot = UNSETTLED
const listeners = new Set<() => void>()

function publish(next: CreditsSnapshot) {
  snapshot = next
  // `forEach`, not `for…of`: the project compiles without downlevelIteration.
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const getSnapshot = () => snapshot
// Client components still render once on the server, where the store can
// only ever be unsettled. A stable constant keeps that render hydratable.
const getServerSnapshot = () => UNSETTLED

/** Seed the store from a `/api/session` payload (or any already-fetched
 *  body). Called by `AuthProvider` — the balance rides along with the
 *  shell's one boot request instead of costing a second one. */
export function seedCredits(data: CreditsState | null) {
  publish({ data, loading: false, fetchedAt: Date.now() })
}

/** Mark the store settled after a *failed* session read, keeping whatever
 *  data is already there. On a cold load that leaves `null` + settled —
 *  identical to what the old hook produced when its fetch 401'd or threw.
 *  On a re-hydrate (token refresh) it declines to blank a good balance,
 *  which the old hook — fetching only on mount — also never did. */
export function settleCreditsUnfetched() {
  if (!snapshot.loading) return
  publish({ ...snapshot, loading: false, fetchedAt: Date.now() })
}

/** Drop the balance on sign-out, so a second sign-in in the same tab can
 *  never paint the previous tenant's number before its session lands.
 *
 *  Settled-and-empty rather than back to `UNSETTLED`: a signed-out shell has
 *  no balance to wait for, and leaving `loading` true would park the pill on
 *  its skeleton for as long as the tab stayed on the page. */
export function clearCredits() {
  publish({ data: null, loading: false, fetchedAt: Date.now() })
}

/** Re-read the balance from `/api/me/credits` — the single-purpose route,
 *  still the right call for a targeted refresh after a spend or a top-up
 *  (a whole `/api/session` would be the wasteful option here). */
export async function refreshCredits(): Promise<void> {
  publish({ ...snapshot, loading: true })
  const data = await fetchCredits()
  publish({ data, loading: false, fetchedAt: Date.now() })
}

/** Refresh only if the snapshot has gone stale, and never while one is in
 *  flight. Lets a detail view (the credits ledger) guarantee fresh
 *  transactions on a client-side navigation without duplicating the boot
 *  fetch when it is the page the session just loaded for. */
export async function refreshCreditsIfStale(maxAgeMs = 5_000): Promise<void> {
  if (snapshot.loading) return
  if (Date.now() - snapshot.fetchedAt <= maxAgeMs) return
  await refreshCredits()
}

/**
 * Read the shared credit balance.
 *
 * Same `{ data, loading, refresh }` shape it always had, so callers didn't
 * change — but it now subscribes to the store instead of owning a fetch.
 */
export function useCredits() {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
  return { data: snap.data, loading: snap.loading, refresh: refreshCredits }
}
