"use client"

import { createContext, useContext, useEffect, useState, ReactNode } from "react"
import { useRouter } from "next/navigation"
import { getSupabaseBrowserClient } from "./supabase-client"
import type { User } from "./types"

interface AuthContextValue {
  user: User | null
  loading: boolean
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      try {
        const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
        const IS_PROD = process.env.NODE_ENV === "production"
        // Skip backend hydration only when there's no API URL AND we're in dev.
        // In prod, empty API_URL means same-origin (Vercel rewrite handles /api/*).
        if (!API_URL && !IS_PROD) return
        const res = await fetch(`${API_URL}/api/me`, { credentials: "include" })
        if (cancelled) return
        if (res.ok) setUser(await res.json())
        else setUser(null)
      } catch {
        // Backend unreachable — leave user as-is; UI degrades gracefully.
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    hydrate()

    // Re-hydrate on sign-in / sign-out / token refresh. Without this, a
    // client-side `router.push` right after login leaves the provider with
    // its stale pre-login state (loading=false, user=null) until a hard
    // refresh, which hides UserMenu.
    const supabase = getSupabaseBrowserClient()
    const { data: sub } = supabase.auth.onAuthStateChange((event: string) => {
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") hydrate()
      else if (event === "SIGNED_OUT") setUser(null)
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
    router.push("/login")
    router.refresh()
  }

  return (
    <AuthContext.Provider value={{ user, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
