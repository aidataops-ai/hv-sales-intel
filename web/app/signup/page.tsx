"use client"

import { Suspense, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { getSupabaseBrowserClient } from "@/lib/supabase-client"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

function SignupForm() {
  const router = useRouter()
  const search = useSearchParams()
  const next = search.get("next") || "/"

  const [companyName, setCompanyName] = useState("")
  const [fullName, setFullName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/api/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email,
          password,
          company_name: companyName,
          full_name: fullName,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Signup failed (${res.status})`)
      }

      // Auth user was created admin-side with email_confirm=true. Sign them
      // in client-side so the session cookie lands and the AuthProvider
      // picks them + their new company up.
      const supabase = getSupabaseBrowserClient()
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      })
      if (signInError) throw new Error(signInError.message)

      router.push(next)
      router.refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-md space-y-4 bg-white/80 p-8 rounded-2xl shadow-lg backdrop-blur"
    >
      <h1 className="font-serif text-2xl font-bold text-teal-700">
        Create your company
      </h1>
      <p className="text-sm text-gray-500">
        You&apos;ll be the admin. Add teammates after onboarding.
      </p>

      <Field
        label="Company name"
        value={companyName}
        onChange={setCompanyName}
        autoComplete="organization"
        required
      />
      <Field
        label="Your name"
        value={fullName}
        onChange={setFullName}
        autoComplete="name"
      />
      <Field
        label="Work email"
        type="email"
        value={email}
        onChange={setEmail}
        autoComplete="email"
        required
      />
      <Field
        label="Password"
        type="password"
        value={password}
        onChange={setPassword}
        autoComplete="new-password"
        required
        hint="8+ chars, with upper / lower / number / symbol."
      />

      {error && <p className="text-sm text-rose-600">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="w-full text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {loading ? "Creating company…" : "Create company"}
      </button>

      <p className="text-xs text-gray-500 text-center">
        Already have an account?{" "}
        <Link href="/login" className="text-teal-700 hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  )
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
  autoComplete,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  required?: boolean
  autoComplete?: string
  hint?: string
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        autoComplete={autoComplete}
        className="w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
      />
      {hint && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
    </div>
  )
}

export default function SignupPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-cream">
      <Suspense fallback={null}>
        <SignupForm />
      </Suspense>
    </div>
  )
}
