"use client"

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react"
import { useRouter } from "next/navigation"
import { getSupabaseBrowserClient } from "./supabase-client"
import {
  clearCredits, seedCredits, settleCreditsUnfetched, type CreditsState,
} from "./credits"
import type { User } from "./types"

export interface Company {
  id: string
  slug: string
  name: string
  branding?: {
    display_name?: string
    short_name?: string
    accent_color?: string
    logo_url?: string
  } | null
  role: "admin" | "sdr"
  is_current: boolean
  /** True iff icp_parsed.verticals_in_scope is non-empty.
   *  Drives whether the Analyze button on practice cards is shown. */
  has_icp: boolean
}

interface AuthContextValue {
  user: User | null
  loading: boolean
  signOut: () => Promise<void>
  // Multi-tenant additions
  companies: Company[]
  currentCompany: Company | null
  switchCompany: (companyId: string) => Promise<void>
  refreshCompanies: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  signOut: async () => {},
  companies: [],
  currentCompany: null,
  switchCompany: async () => {},
  refreshCompanies: async () => {},
})

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
const IS_PROD = process.env.NODE_ENV === "production"

/** Re-read just the company list. Boot gets these from `/api/session`; this
 *  narrow route is for the later re-reads (a membership edit), where the user
 *  and the credit balance are not in question and a whole session would be
 *  the wasteful call. Never rejects — an unreachable API is an empty list. */
async function fetchCompanies(): Promise<Company[]> {
  if (!API_URL && !IS_PROD) return []
  try {
    const res = await fetch(`${API_URL}/api/me/companies`, { credentials: "include" })
    if (!res.ok) return []
    const data = await res.json()
    return data.companies ?? []
  } catch {
    return []
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [companies, setCompanies] = useState<Company[]>([])
  const router = useRouter()

  const refreshCompanies = useCallback(async () => {
    setCompanies(await fetchCompanies())
  }, [])

  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      try {
        // No backend configured in local dev: settle the store so the
        // topbar pill resolves to "hidden" instead of pulsing forever.
        // The old hook got there by way of a request that 404'd.
        if (!API_URL && !IS_PROD) return settleCreditsUnfetched()
        // One request for the whole shell. This used to be /api/me and
        // /api/me/companies in parallel plus a third /api/me/credits fired
        // independently by `useCredits` in the topbar — three requests
        // asking three questions about the same user, each separately
        // paying the backend's auth round trips. /api/session resolves the
        // user once and answers all three; the keys carry those routes'
        // bodies verbatim.
        const res = await fetch(`${API_URL}/api/session`, { credentials: "include" })
        if (cancelled) return
        if (res.ok) {
          const body = (await res.json()) as {
            user: User
            companies?: { companies?: Company[] }
            credits?: CreditsState | null
          }
          setUser(body.user ?? null)
          setCompanies(body.companies?.companies ?? [])
          seedCredits(body.credits ?? null)
        } else {
          setUser(null)
          setCompanies([])
          settleCreditsUnfetched()
        }
      } catch {
        /* leave state as-is */
        if (!cancelled) settleCreditsUnfetched()
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    hydrate()

    const supabase = getSupabaseBrowserClient()
    const { data: sub } = supabase.auth.onAuthStateChange((event: string) => {
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") hydrate()
      else if (event === "SIGNED_OUT") {
        setUser(null)
        setCompanies([])
        clearCredits()
      }
    })

    return () => {
      cancelled = true
      sub.subscription.unsubscribe()
    }
  }, [])

  async function signOut() {
    const supabase = getSupabaseBrowserClient()
    await supabase.auth.signOut()
    setUser(null)
    setCompanies([])
    clearCredits()
    router.push("/login")
    router.refresh()
  }

  const switchCompany = useCallback(async (companyId: string) => {
    if (!API_URL && !IS_PROD) return
    const res = await fetch(`${API_URL}/api/me/companies/${companyId}/switch`, {
      method: "POST",
      credentials: "include",
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || "Switch failed")
    }
    // Hard reload so every cached fetch refetches against the new tenant.
    window.location.href = "/"
  }, [])

  const currentCompany = companies.find((c) => c.is_current) ?? null

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        signOut,
        companies,
        currentCompany,
        switchCompany,
        refreshCompanies,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
