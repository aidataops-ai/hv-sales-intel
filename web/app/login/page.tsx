"use client"

import { Suspense, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { getSupabaseBrowserClient } from "@/lib/supabase-client"

function LoginForm() {
  const router = useRouter()
  const search = useSearchParams()
  const redirect = search.get("redirect") || "/"

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const supabase = getSupabaseBrowserClient()
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) {
      setError(error.message)
      setLoading(false)
      return
    }
    router.push(redirect)
    router.refresh()
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-sm space-y-4 bg-white/80 dark:bg-night-800 p-8 rounded-2xl shadow-lg backdrop-blur"
    >
      <h1 className="font-serif text-2xl font-bold text-teal-700 dark:text-teal-400">Sign in</h1>
      <p className="text-sm text-gray-500 dark:text-gray-400">Apex Sales Intel</p>

      <div>
        <label className="block text-xs font-medium text-gray-600 dark:text-[#d9d9d9] mb-1">Email</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          className="w-full text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2
                     focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-600 dark:text-[#d9d9d9] mb-1">Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
          className="w-full text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2
                     focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="w-full text-sm px-4 py-2 rounded-lg bg-teal-600 text-white
                   hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {loading ? "Signing in..." : "Sign in"}
      </button>
    </form>
  )
}

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-cream dark:bg-night">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </div>
  )
}
