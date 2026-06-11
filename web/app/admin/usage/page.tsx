"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import {
  ArrowLeft, RefreshCw, Loader2, Activity, DollarSign,
  Cloud, Sparkles, Calculator, Check,
} from "lucide-react"
import { useAuth } from "@/lib/auth"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

const KIND_LABELS: Record<string, string> = {
  places_search:    "Places · Text Search",
  places_details:   "Places · Place Details",
  openai_analyze:   "OpenAI · ICP Analyze",
  openai_script:    "OpenAI · Call Script",
  openai_email:     "OpenAI · Email Draft",
  openai_icp_parse: "OpenAI · ICP Parser",
}

interface UsageResponse {
  window_days: number
  totals: {
    events: number
    places_calls: number
    openai_calls: number
    input_tokens: number
    output_tokens: number
    cost_cents: number
    places_cost_cents: number
    openai_cost_cents: number
  }
  by_kind: Array<{
    kind: string
    count_events: number
    total_calls: number
    input_tokens: number
    output_tokens: number
    cost_cents: number
  }>
  by_model: Array<{
    model: string
    count_events: number
    input_tokens: number
    output_tokens: number
    cost_cents: number
  }>
  recent: Array<{
    created_at: string
    kind: string
    model: string | null
    input_tokens: number | null
    output_tokens: number | null
    calls: number
    cost_cents: number
    metadata: Record<string, unknown> | null
  }>
  pricing: {
    openai_per_million_tokens: Record<string, { input: number; cached_input?: number; output: number }>
    places_per_call: Record<string, number>
  }
}

const WINDOWS = [
  { label: "24h",  days: 1 },
  { label: "7d",   days: 7 },
  { label: "30d",  days: 30 },
  { label: "90d",  days: 90 },
  { label: "1yr",  days: 365 },
]

export default function UsagePage() {
  const { user } = useAuth()
  const [data, setData] = useState<UsageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [windowDays, setWindowDays] = useState(30)
  const [error, setError] = useState<string | null>(null)
  const [recomputing, setRecomputing] = useState(false)
  const [recomputeMsg, setRecomputeMsg] = useState<string | null>(null)

  const fetchUsage = useCallback(async (days: number) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/usage?days=${days}`, {
        credentials: "include",
      })
      if (!res.ok) throw new Error(`Failed (${res.status})`)
      setData(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchUsage(windowDays)
  }, [windowDays, fetchUsage])

  async function handleRecompute() {
    setRecomputing(true)
    setRecomputeMsg(null)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/usage/recompute-costs`, {
        method: "POST",
        credentials: "include",
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Failed (${res.status})`)
      }
      const json = await res.json()
      setRecomputeMsg(
        `Recomputed ${json.updated} of ${json.scanned} events.`,
      )
      // Refresh visible numbers with the corrected costs.
      await fetchUsage(windowDays)
      setTimeout(() => setRecomputeMsg(null), 4000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRecomputing(false)
    }
  }

  // Reachable by direct URL for admins only — intentionally NOT linked in the
  // UI; the customer-facing billing surfaces stay hidden via SHOW_BILLING.
  if (!user) {
    return <Centered>Sign in to view usage.</Centered>
  }
  if (user.role !== "admin") {
    return <Centered>Admin only.</Centered>
  }

  return (
    <div className="min-h-screen bg-cream dark:bg-night py-10 px-6">
      <div className="max-w-5xl mx-auto">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to map
        </Link>

        <header className="flex items-start justify-between mb-6 gap-4">
          <div>
            <h1 className="font-serif text-3xl font-bold text-gray-900 dark:text-white">
              Usage &amp; cost
            </h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-2xl">
              Every Places API call and OpenAI completion gets logged.
              Aggregated here so you can size a pricing model that covers
              your variable costs and leaves room for margin.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex bg-gray-100 dark:bg-white/5 rounded-lg p-0.5 text-sm">
              {WINDOWS.map((w) => (
                <button
                  key={w.days}
                  onClick={() => setWindowDays(w.days)}
                  className={`px-2.5 py-1 rounded-md transition ${
                    windowDays === w.days
                      ? "bg-white dark:bg-night-800 shadow-sm text-gray-900 dark:text-white"
                      : "text-gray-600 dark:text-[#d9d9d9] hover:text-gray-900 dark:hover:text-white"
                  }`}
                >
                  {w.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => fetchUsage(windowDays)}
              className="inline-flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-md border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10"
            >
              <RefreshCw
                className={`w-3 h-3 ${loading ? "animate-spin" : ""}`}
              />
              Refresh
            </button>
            <button
              onClick={handleRecompute}
              disabled={recomputing}
              className="inline-flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-md border border-teal-600 text-teal-700 dark:text-teal-400 hover:bg-teal-50 dark:hover:bg-[#284b63]/40 disabled:opacity-50"
              title="Walk every usage_events row for this company and recalculate cost_cents from the current pricing constants in src/usage.py. Run after a pricing edit."
            >
              {recomputing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Calculator className="w-3 h-3" />
              )}
              Recompute costs
            </button>
          </div>
        </header>

        {recomputeMsg && (
          <div className="bg-emerald-50 dark:bg-emerald-500/20 border border-emerald-200 dark:border-emerald-500/30 text-emerald-800 dark:text-emerald-300 rounded-lg px-3 py-2 text-sm mb-4 inline-flex items-center gap-2">
            <Check className="w-4 h-4" /> {recomputeMsg}
          </div>
        )}

        {error && (
          <div className="bg-rose-50 dark:bg-rose-500/20 border border-rose-200 dark:border-rose-500/30 text-rose-700 dark:text-rose-300 rounded-lg px-3 py-2 text-sm mb-4">
            {error}
          </div>
        )}

        {data && (
          <>
            {/* Top-line stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <StatCard
                label="Total cost"
                value={formatDollars(data.totals.cost_cents)}
                icon={<DollarSign className="w-4 h-4" />}
                hint={`${data.totals.events} events · last ${data.window_days}d`}
              />
              <StatCard
                label="Places spend"
                value={formatDollars(data.totals.places_cost_cents)}
                icon={<Cloud className="w-4 h-4" />}
                hint={`${data.totals.places_calls.toLocaleString()} calls`}
              />
              <StatCard
                label="OpenAI spend"
                value={formatDollars(data.totals.openai_cost_cents)}
                icon={<Sparkles className="w-4 h-4" />}
                hint={`${data.totals.openai_calls.toLocaleString()} completions`}
              />
              <StatCard
                label="Tokens"
                value={(
                  data.totals.input_tokens + data.totals.output_tokens
                ).toLocaleString()}
                icon={<Activity className="w-4 h-4" />}
                hint={`${data.totals.input_tokens.toLocaleString()} in · ${data.totals.output_tokens.toLocaleString()} out`}
              />
            </div>

            {/* By kind */}
            <Card title="By kind">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
                    <th className="py-2 pr-3">Kind</th>
                    <th className="py-2 pr-3 text-right">Events</th>
                    <th className="py-2 pr-3 text-right">Calls</th>
                    <th className="py-2 pr-3 text-right">Input tokens</th>
                    <th className="py-2 pr-3 text-right">Output tokens</th>
                    <th className="py-2 pr-3 text-right">Cost</th>
                    <th className="py-2 text-right">Cost / event</th>
                  </tr>
                </thead>
                <tbody className="text-gray-700 dark:text-[#d9d9d9]">
                  {data.by_kind.length === 0 ? (
                    <tr><td colSpan={7} className="py-4 text-center text-gray-400 dark:text-gray-500">No usage in this window.</td></tr>
                  ) : data.by_kind.map((k) => (
                    <tr key={k.kind} className="border-b last:border-b-0 dark:border-white/10">
                      <td className="py-2 pr-3 font-medium text-gray-900 dark:text-white">
                        {KIND_LABELS[k.kind] ?? k.kind}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">{k.count_events.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right font-mono">{k.total_calls.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right font-mono">{k.input_tokens.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right font-mono">{k.output_tokens.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right font-mono font-medium">{formatDollars(k.cost_cents)}</td>
                      <td className="py-2 text-right font-mono text-gray-500 dark:text-gray-400">
                        {k.count_events > 0
                          ? formatDollars(k.cost_cents / k.count_events)
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            {/* By model */}
            {data.by_model.length > 0 && (
              <Card title="OpenAI by model">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
                      <th className="py-2 pr-3">Model</th>
                      <th className="py-2 pr-3 text-right">Completions</th>
                      <th className="py-2 pr-3 text-right">Input tokens</th>
                      <th className="py-2 pr-3 text-right">Output tokens</th>
                      <th className="py-2 text-right">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="text-gray-700 dark:text-[#d9d9d9]">
                    {data.by_model.map((m) => (
                      <tr key={m.model} className="border-b last:border-b-0 dark:border-white/10">
                        <td className="py-2 pr-3 font-mono">{m.model}</td>
                        <td className="py-2 pr-3 text-right font-mono">{m.count_events.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right font-mono">{m.input_tokens.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right font-mono">{m.output_tokens.toLocaleString()}</td>
                        <td className="py-2 text-right font-mono font-medium">{formatDollars(m.cost_cents)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}

            {/* Pricing reference */}
            <Card title="Pricing assumptions">
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                Edit these in <code>src/usage.py</code> when vendor pricing changes.
                All values are in <strong>cents</strong>.
              </p>
              <div className="grid md:grid-cols-2 gap-4">
                <div>
                  <h3 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-2 uppercase tracking-wide">
                    OpenAI · ¢ per 1M tokens
                  </h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
                        <th className="py-1 pr-2">Model</th>
                        <th className="py-1 pr-2 text-right">Input ¢/M</th>
                        <th className="py-1 pr-2 text-right">Cached ¢/M</th>
                        <th className="py-1 text-right">Output ¢/M</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.pricing.openai_per_million_tokens).map(([m, p]) => (
                        <tr key={m} className="border-b last:border-b-0 dark:border-white/10">
                          <td className="py-1 pr-2 font-mono">{m}</td>
                          <td className="py-1 pr-2 text-right font-mono">{p.input}</td>
                          <td className="py-1 pr-2 text-right font-mono text-gray-500 dark:text-gray-400">
                            {p.cached_input ?? "—"}
                          </td>
                          <td className="py-1 text-right font-mono">{p.output}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1.5">
                    Cached input applies when OpenAI&apos;s prompt cache hits — fresh
                    prompt tokens stay billed at the Input rate.
                  </p>
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-2 uppercase tracking-wide">
                    Places · ¢ per call
                  </h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
                        <th className="py-1 pr-2">Endpoint</th>
                        <th className="py-1 text-right">¢ / call</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.pricing.places_per_call).map(([k, c]) => (
                        <tr key={k} className="border-b last:border-b-0 dark:border-white/10">
                          <td className="py-1 pr-2 font-mono">{k}</td>
                          <td className="py-1 text-right font-mono">{c.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </Card>

            {/* Recent events */}
            <Card title="Recent events (latest 50)">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
                    <th className="py-2 pr-3">When</th>
                    <th className="py-2 pr-3">Kind</th>
                    <th className="py-2 pr-3">Model</th>
                    <th className="py-2 pr-3 text-right">In tok</th>
                    <th className="py-2 pr-3 text-right">Out tok</th>
                    <th className="py-2 pr-3 text-right">Calls</th>
                    <th className="py-2 pr-3 text-right">Cost</th>
                    <th className="py-2">Detail</th>
                  </tr>
                </thead>
                <tbody className="text-gray-700 dark:text-[#d9d9d9]">
                  {data.recent.length === 0 ? (
                    <tr><td colSpan={8} className="py-4 text-center text-gray-400 dark:text-gray-500">No events.</td></tr>
                  ) : data.recent.map((r, i) => (
                    <tr key={i} className="border-b last:border-b-0 dark:border-white/10">
                      <td className="py-2 pr-3 font-mono text-xs text-gray-500 dark:text-gray-400">
                        {new Date(r.created_at).toLocaleString()}
                      </td>
                      <td className="py-2 pr-3">{KIND_LABELS[r.kind] ?? r.kind}</td>
                      <td className="py-2 pr-3 font-mono text-xs text-gray-500 dark:text-gray-400">{r.model ?? "—"}</td>
                      <td className="py-2 pr-3 text-right font-mono">{r.input_tokens ?? "—"}</td>
                      <td className="py-2 pr-3 text-right font-mono">{r.output_tokens ?? "—"}</td>
                      <td className="py-2 pr-3 text-right font-mono">{r.calls}</td>
                      <td className="py-2 pr-3 text-right font-mono">{formatDollars(r.cost_cents)}</td>
                      <td className="py-2 text-xs text-gray-500 dark:text-gray-400 truncate max-w-[220px]">
                        {r.metadata ? Object.entries(r.metadata).slice(0, 2).map(([k, v]) => `${k}: ${formatMeta(v)}`).join(" · ") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </>
        )}

        {loading && !data && (
          <div className="text-center text-gray-500 dark:text-gray-400 py-10">
            <Loader2 className="w-5 h-5 animate-spin mx-auto" />
          </div>
        )}
      </div>
    </div>
  )
}

function Card({
  title, children,
}: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white/80 dark:bg-night-800 rounded-2xl shadow-sm border border-gray-200/50 dark:border-white/10 p-5 mb-5">
      <h2 className="font-serif text-lg font-semibold text-gray-900 dark:text-white mb-3">
        {title}
      </h2>
      {children}
    </section>
  )
}

function StatCard({
  label, value, icon, hint,
}: { label: string; value: string; icon: React.ReactNode; hint?: string }) {
  return (
    <div className="bg-white/80 dark:bg-night-800 rounded-2xl shadow-sm border border-gray-200/50 dark:border-white/10 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
          {label}
        </span>
        <span className="text-teal-700 dark:text-teal-400">{icon}</span>
      </div>
      <p className="font-serif text-2xl font-bold text-gray-900 dark:text-white">{value}</p>
      {hint && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">{hint}</p>
      )}
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen grid place-items-center text-gray-500 dark:text-gray-400">
      {children}
    </div>
  )
}

function formatDollars(cents: number | null | undefined): string {
  const c = typeof cents === "number" ? cents : 0
  if (Math.abs(c) < 1) {
    // Show sub-cent figures with up to 3 decimals.
    return `$${(c / 100).toFixed(5)}`
  }
  const dollars = c / 100
  return `$${dollars.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function formatMeta(v: unknown): string {
  if (v === null || v === undefined) return ""
  if (typeof v === "string") return v.length > 40 ? v.slice(0, 40) + "…" : v
  if (typeof v === "number" || typeof v === "boolean") return String(v)
  try {
    const s = JSON.stringify(v)
    return s.length > 40 ? s.slice(0, 40) + "…" : s
  } catch {
    return ""
  }
}
