"use client"

import { useState } from "react"
import Link from "next/link"
import { notFound } from "next/navigation"
import {
  ArrowLeft, Coins, Loader2, Plus, History, TrendingUp, TrendingDown,
} from "lucide-react"
import { useAuth } from "@/lib/auth"
import {
  CREDIT_VALUE_CENTS, creditsToDollars, formatCredits,
  topupCredits, useCredits, type CreditTransaction,
} from "@/lib/credits"
import { SHOW_BILLING } from "@/lib/flags"

const PRESETS = [50, 100, 500, 1000, 5000]

export default function AdminCreditsPage() {
  const { user } = useAuth()
  const { data, loading, refresh } = useCredits()
  const [topupAmount, setTopupAmount] = useState<number>(100)
  const [topupNotes, setTopupNotes] = useState<string>("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // Cost/credit surfaces are hidden for demo — see SHOW_BILLING in lib/flags.ts.
  if (!SHOW_BILLING) notFound()
  if (user?.role !== "admin") {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-600">
        Admins only.
      </div>
    )
  }

  async function handleTopup(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccessMsg(null)
    if (!topupAmount || topupAmount <= 0) {
      setError("Amount must be greater than zero")
      return
    }
    setSubmitting(true)
    const newBalance = await topupCredits(topupAmount, topupNotes || undefined)
    setSubmitting(false)
    if (newBalance == null) {
      setError("Top-up failed — check the API logs")
      return
    }
    setSuccessMsg(
      `Added ${formatCredits(topupAmount)} credits — new balance ${formatCredits(newBalance)} credits.`,
    )
    setTopupNotes("")
    refresh()
  }

  return (
    <div className="min-h-screen bg-ivory-50 text-gray-900">
      <header className="sticky top-0 z-10 backdrop-blur bg-white/80 border-b border-gray-200/50">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900"
            >
              <ArrowLeft className="w-4 h-4" /> Back
            </Link>
            <h1 className="font-serif text-lg font-semibold text-gray-900 flex items-center gap-2">
              <Coins className="w-5 h-5 text-amber-600" /> Credits
            </h1>
          </div>
          <p className="text-xs text-gray-500">
            1 credit = ${(CREDIT_VALUE_CENTS / 100).toFixed(2)}
          </p>
        </div>
      </header>

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        {loading && !data && (
          <div className="flex items-center justify-center text-gray-600 py-12">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading credits…
          </div>
        )}

        {data && (
          <>
            {/* Headline stats */}
            <div className="grid md:grid-cols-3 gap-4">
              <StatCard
                icon={<Coins className="w-5 h-5" />}
                label="Current balance"
                value={formatCredits(data.balance)}
                sub={`~${creditsToDollars(data.balance)}`}
                accent={data.balance < 10 ? "danger" : data.balance < 50 ? "warn" : "ok"}
              />
              <StatCard
                icon={<TrendingUp className="w-5 h-5" />}
                label="Lifetime purchased"
                value={formatCredits(data.purchased)}
                sub={`~${creditsToDollars(data.purchased)}`}
                accent="neutral"
              />
              <StatCard
                icon={<TrendingDown className="w-5 h-5" />}
                label="Lifetime consumed"
                value={formatCredits(data.consumed)}
                sub={`~${creditsToDollars(data.consumed)}`}
                accent="neutral"
              />
            </div>

            {/* Top-up form */}
            <Card title="Add credits">
              <p className="text-xs text-gray-500 mb-3">
                Mock top-up — wire this to Stripe for production. Each credit
                costs the customer ${(CREDIT_VALUE_CENTS / 100).toFixed(2)}.
              </p>
              <form
                onSubmit={handleTopup}
                className="flex flex-col gap-3 max-w-2xl"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  {PRESETS.map((n) => (
                    <button
                      key={n}
                      type="button"
                      disabled={submitting}
                      onClick={() => setTopupAmount(n)}
                      className={`text-xs px-3 py-1.5 rounded-full border transition ${
                        topupAmount === n
                          ? "bg-teal-50 border-teal-500 text-teal-700"
                          : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
                      }`}
                    >
                      {n} credits
                      <span className="ml-1 text-[10px] opacity-70">
                        (${((n * CREDIT_VALUE_CENTS) / 100).toFixed(0)})
                      </span>
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-xs font-medium text-gray-700 w-20">Amount</label>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={topupAmount}
                    disabled={submitting}
                    onChange={(e) => setTopupAmount(parseInt(e.target.value || "0", 10))}
                    className="w-32 text-sm rounded-md border border-gray-200 px-3 py-1.5 tabular-nums"
                  />
                  <span className="text-xs text-gray-500">
                    = ${((topupAmount * CREDIT_VALUE_CENTS) / 100).toFixed(2)}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-xs font-medium text-gray-700 w-20">Notes</label>
                  <input
                    type="text"
                    value={topupNotes}
                    disabled={submitting}
                    onChange={(e) => setTopupNotes(e.target.value)}
                    placeholder="e.g. monthly retainer top-up — March 2026"
                    className="flex-1 text-sm rounded-md border border-gray-200 px-3 py-1.5"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={submitting || topupAmount <= 0}
                    className="inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
                  >
                    {submitting ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Plus className="w-4 h-4" />
                    )}
                    {submitting ? "Adding…" : `Add ${formatCredits(topupAmount)} credits`}
                  </button>
                  {error && <span className="text-xs text-rose-600">{error}</span>}
                  {successMsg && (
                    <span className="text-xs text-emerald-700">{successMsg}</span>
                  )}
                </div>
              </form>
            </Card>

            {/* Rate card */}
            <Card title="What each action consumes">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b">
                    <th className="py-1 pr-2">Action</th>
                    <th className="py-1 pr-2 text-right">Credits</th>
                    <th className="py-1 text-right">Customer cost</th>
                  </tr>
                </thead>
                <tbody>
                  <Rate label="Analyze a lead (10× OpenAI cost)" lo={data.rates.analyze[0]} hi={data.rates.analyze[1]} />
                  <Rate label="Call playbook (10× OpenAI cost)" lo={data.rates.call_script[0]} hi={data.rates.call_script[1]} />
                  <Rate label="Email draft (10× OpenAI cost)" lo={data.rates.email_draft[0]} hi={data.rates.email_draft[1]} />
                  <Rate label="Bulk Scan — per Places search (10× Places cost, 1-3 pages)" lo={data.rates.bulk_scan_query[0]} hi={data.rates.bulk_scan_query[1]} />
                  <Rate label="Place Details refresh (10× Places cost)" fixed={data.rates.places_details} />
                  <Rate label="Enrichment — per Clay / Apollo lookup (10× provider cost)" fixed={data.rates.enrichment} />
                </tbody>
              </table>
              <p className="text-[11px] text-gray-400 mt-2">
                Every action bills at{" "}
                <span className="font-medium">{data.cost_multiplier}× </span>
                our underlying vendor cost. Dynamic rows show a range because
                the underlying cost varies with prompt size or Places pagination;
                the server deducts the precise amount post-call.
              </p>
            </Card>

            {/* Transaction history */}
            <Card title="Recent transactions">
              {data.transactions.length === 0 ? (
                <p className="text-sm text-gray-500 italic">No transactions yet.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                      <th className="py-2 pr-3">When</th>
                      <th className="py-2 pr-3">Kind</th>
                      <th className="py-2 pr-3">Action</th>
                      <th className="py-2 pr-3 text-right">Δ Credits</th>
                      <th className="py-2 pr-3 text-right">Balance after</th>
                      <th className="py-2">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.transactions.map((t) => (
                      <TxRow key={t.id} t={t} />
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </>
        )}
      </main>
    </div>
  )
}

function StatCard({
  icon, label, value, sub, accent,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub: string
  accent: "ok" | "warn" | "danger" | "neutral"
}) {
  const tone =
    accent === "ok"
      ? "border-emerald-200 bg-emerald-50/60"
      : accent === "warn"
        ? "border-amber-200 bg-amber-50/60"
        : accent === "danger"
          ? "border-rose-200 bg-rose-50/60"
          : "border-gray-200 bg-white"
  const iconTone =
    accent === "ok"
      ? "text-emerald-700"
      : accent === "warn"
        ? "text-amber-700"
        : accent === "danger"
          ? "text-rose-700"
          : "text-gray-500"
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-gray-500">
        <span className={iconTone}>{icon}</span>
        {label}
      </div>
      <p className="text-2xl font-semibold mt-2 tabular-nums">{value}</p>
      <p className="text-xs text-gray-500 mt-0.5">{sub}</p>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5">
      <h2 className="font-serif text-base font-semibold text-gray-900 mb-3 flex items-center gap-2">
        <History className="w-4 h-4 text-gray-400" /> {title}
      </h2>
      {children}
    </section>
  )
}

function Rate({
  label, fixed, lo, hi,
}: {
  label: string
  fixed?: number
  lo?: number
  hi?: number
}) {
  const credits =
    fixed != null
      ? `${formatCredits(fixed)}`
      : `${formatCredits(lo ?? 0)}–${formatCredits(hi ?? 0)}`
  const dollars =
    fixed != null
      ? creditsToDollars(fixed)
      : `${creditsToDollars(lo ?? 0)} – ${creditsToDollars(hi ?? 0)}`
  return (
    <tr className="border-b last:border-b-0">
      <td className="py-1 pr-2">{label}</td>
      <td className="py-1 pr-2 text-right font-mono tabular-nums">{credits}</td>
      <td className="py-1 text-right font-mono text-gray-500">{dollars}</td>
    </tr>
  )
}

function TxRow({ t }: { t: CreditTransaction }) {
  const isConsume = t.kind === "consume"
  const sign = t.delta >= 0 ? "+" : ""
  const when = new Date(t.created_at).toLocaleString()
  return (
    <tr className="border-b last:border-b-0 hover:bg-gray-50/40">
      <td className="py-1.5 pr-3 text-xs text-gray-600 tabular-nums">{when}</td>
      <td className="py-1.5 pr-3">
        <span
          className={`text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded-full ${
            t.kind === "topup"
              ? "bg-emerald-100 text-emerald-700"
              : t.kind === "consume"
                ? "bg-gray-100 text-gray-600"
                : "bg-amber-100 text-amber-700"
          }`}
        >
          {t.kind}
        </span>
      </td>
      <td className="py-1.5 pr-3 text-xs text-gray-700">{t.action ?? "—"}</td>
      <td
        className={`py-1.5 pr-3 text-right font-mono tabular-nums ${
          isConsume ? "text-rose-600" : "text-emerald-700"
        }`}
      >
        {sign}{formatCredits(t.delta)}
      </td>
      <td className="py-1.5 pr-3 text-right font-mono tabular-nums">
        {formatCredits(t.balance_after)}
      </td>
      <td className="py-1.5 text-xs text-gray-500 truncate max-w-xs">
        {t.notes ?? ""}
      </td>
    </tr>
  )
}
