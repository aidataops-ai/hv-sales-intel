/**
 * Prepaid credits — frontend constants + fetchers.
 *
 * Mirrors the backend constants in src/credits.py. Keep these in sync;
 * the server is the source of truth (it returns its own copy in
 * /api/me/credits.rates) but the constants here drive the upfront UI
 * estimates so we don't need a roundtrip before showing a "1 credit"
 * badge on a button.
 */

import { useCallback, useEffect, useState } from "react"

// 1 credit = 33¢. Customer-facing price.
export const CREDIT_VALUE_CENTS = 33

// Display ranges for dynamic (OpenAI-driven) actions. The server
// returns these too so the UI can sync if pricing changes — but
// having local fallbacks lets us render instantly on first paint.
export const ANALYZE_RANGE: [number, number]      = [0.3, 1.5]
export const CALL_SCRIPT_RANGE: [number, number]  = [0.1, 0.4]
export const EMAIL_DRAFT_RANGE: [number, number]  = [0.05, 0.20]

// Fixed-cost actions.
export const BULK_SCAN_QUERY_CREDITS = 1
export const ENRICHMENT_CREDITS      = 2

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
  openai_multiplier: number
  rates: {
    analyze:         [number, number]
    call_script:     [number, number]
    email_draft:     [number, number]
    bulk_scan_query: number
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
// React hook — global credits state with manual refresh
// ---------------------------------------------------------------

export function useCredits() {
  const [data, setData] = useState<CreditsState | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setData(await fetchCredits())
    setLoading(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchCredits().then((d) => {
      if (!cancelled) {
        setData(d)
        setLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  return { data, loading, refresh }
}
