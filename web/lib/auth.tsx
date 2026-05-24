"use client"

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react"
import { useRouter } from "next/navigation"
import { getSupabaseBrowserClient } from "./supabase-client"
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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [companies, setCompanies] = useState<Company[]>([])
  const router = useRouter()

  const fetchCompanies = useCallback(async (): Promise<Company[]> => {
    if (!API_URL && !IS_PROD) return []
    try {
      const res = await fetch(`${API_URL}/api/me/companies`, { credentials: "include" })
      if (!res.ok) return []
      const data = await res.json()
      return data.companies ?? []
    } catch {
      return []
    }
  }, [])

  const refreshCompanies = useCallback(async () => {
    setCompanies(await fetchCompanies())
  }, [fetchCompanies])

  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      try {
        if (!API_URL && !IS_PROD) return
        const res = await fetch(`${API_URL}/api/me`, { credentials: "include" })
        if (cancelled) return
        if (res.ok) {
          setUser(await res.json())
          // Pull companies in parallel with first paint.
          const cs = await fetchCompanies()
          if (!cancelled) setCompanies(cs)
        } else {
          setUser(null)
          setCompanies([])
        }
      } catch {
        /* leave state as-is */
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
      }
    })

    return () => {
      cancelled = true
      sub.subscription.unsubscribe()
    }
  }, [fetchCompanies])

  async function signOut() {
    const supabase = getSupabaseBrowserClient()
    await supabase.auth.signOut()
    setUser(null)
    setCompanies([])
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
