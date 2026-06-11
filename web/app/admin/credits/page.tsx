"use client"

import { useState } from "react"
import Link from "next/link"
import {
  ArrowLeft, Coins, Loader2, Plus, History, TrendingUp, TrendingDown,
} from "lucide-react"
import { useAuth } from "@/lib/auth"
import {
  CREDIT_VALUE_CENTS, creditsToDollars, formatCredits,
  topupCredits, useCredits, type CreditTransaction,
} from "@/lib/credits"

const PRESETS = [50, 100, 500, 1000, 5000]

export default function AdminCreditsPage() {
  const { user } = useAuth()
  const { data, loading, refresh } = useCredits()
  const [topupAmount, setTopupAmount] = useState<number>(100)
  const [topupNotes, setTopupNotes] = useState<string>("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // Reachable by direct URL for admins only — intentionally NOT linked in the
  // UI; the customer-facing billing surfaces stay hidden via SHOW_BILLING.
  if (user?.role !== "admin") {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-600 dark:text-[#d9d9d9]">
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
    <div className="min-h-screen bg-ivory-50 dark:bg-night text-gray-900 dark:text-white">
      <header className="sticky top-0 z-10 backdrop-blur bg-white/80 dark:bg-night-800 border-b border-gray-200/50 dark:border-white/10">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-1 text-sm text-gray-600 dark:text-[#d9d9d9] hover:text-gray-900 dark:hover:text-white"
            >
              <ArrowLeft className="w-4 h-4" /> Back
            </Link>
            <h1 className="font-serif text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <Coins className="w-5 h-5 text-amber-600" /> Credits
            </h1>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            1 credit = ${(CREDIT_VALUE_CENTS / 100).toFixed(2)}
          </p>
        </div>
      </header>

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        {loading && !data && (
          <div className="flex items-center justify-center text-gray-600 dark:text-[#d9d9d9] py-12">
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
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
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
                          ? "bg-teal-50 dark:bg-[#284b63]/40 border-teal-500 text-teal-700 dark:text-teal-400"
                          : "bg-white dark:bg-night-800 border-gray-200 dark:border-white/10 text-gray-600 dark:text-[#d9d9d9] hover:border-gray-400"
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
                  <label className="text-xs font-medium text-gray-700 dark:text-[#d9d9d9] w-20">Amount</label>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={topupAmount}
                    disabled={submitting}
                    onChange={(e) => setTopupAmount(parseInt(e.target.value || "0", 10))}
                    className="w-32 text-sm rounded-md border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white px-3 py-1.5 tabular-nums"
                  />
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    = ${((topupAmount * CREDIT_VALUE_CENTS) / 100).toFixed(2)}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-xs font-medium text-gray-700 dark:text-[#d9d9d9] w-20">Notes</label>
                  <input
                    type="text"
                    value={topupNotes}
                    disabled={submitting}
                    onChange={(e) => setTopupNotes(e.target.value)}
                    placeholder="e.g. monthly retainer top-up — March 2026"
                    className="flex-1 text-sm rounded-md border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-1.5"
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
                  {error && <span className="text-xs text-rose-600 dark:text-rose-400">{error}</span>}
                  {successMsg && (
                    <span className="text-xs text-emerald-700 dark:text-emerald-300">{successMsg}</span>
                  )}
                </div>
              </form>
            </Card>

            {/* Rate card */}
            <Card title="What each action consumes">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
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
              <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-2">
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
                <p className="text-sm text-gray-500 dark:text-gray-400 italic">No transactions yet.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 border-b dark:border-white/10">
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
      ? "border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/60 dark:bg-emerald-500/10"
      : accent === "warn"
        ? "border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-500/10"
        : accent === "danger"
          ? "border-rose-200 dark:border-rose-500/30 bg-rose-50/60 dark:bg-rose-500/10"
          : "border-gray-200 dark:border-white/10 bg-white dark:bg-night-800"
  const iconTone =
    accent === "ok"
      ? "text-emerald-700 dark:text-emerald-300"
      : accent === "warn"
        ? "text-amber-700 dark:text-amber-300"
        : accent === "danger"
          ? "text-rose-700 dark:text-rose-300"
          : "text-gray-500 dark:text-gray-400"
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
        <span className={iconTone}>{icon}</span>
        {label}
      </div>
      <p className="text-2xl font-semibold mt-2 tabular-nums">{value}</p>
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{sub}</p>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-gray-200 dark:border-white/10 bg-white dark:bg-night-800 p-5">
      <h2 className="font-serif text-base font-semibold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
        <History className="w-4 h-4 text-gray-400 dark:text-gray-500" /> {title}
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
    <tr className="border-b last:border-b-0 dark:border-white/10">
      <td className="py-1 pr-2">{label}</td>
      <td className="py-1 pr-2 text-right font-mono tabular-nums">{credits}</td>
      <td className="py-1 text-right font-mono text-gray-500 dark:text-gray-400">{dollars}</td>
    </tr>
  )
}

function TxRow({ t }: { t: CreditTransaction }) {
  const isConsume = t.kind === "consume"
  const sign = t.delta >= 0 ? "+" : ""
  const when = new Date(t.created_at).toLocaleString()
  return (
    <tr className="border-b last:border-b-0 dark:border-white/10 hover:bg-gray-50/40 dark:hover:bg-white/5">
      <td className="py-1.5 pr-3 text-xs text-gray-600 dark:text-[#d9d9d9] tabular-nums">{when}</td>
      <td className="py-1.5 pr-3">
        <span
          className={`text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded-full ${
            t.kind === "topup"
              ? "bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
              : t.kind === "consume"
                ? "bg-gray-100 dark:bg-white/10 text-gray-600 dark:text-[#d9d9d9]"
                : "bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300"
          }`}
        >
          {t.kind}
        </span>
      </td>
      <td className="py-1.5 pr-3 text-xs text-gray-700 dark:text-[#d9d9d9]">{t.action ?? "—"}</td>
      <td
        className={`py-1.5 pr-3 text-right font-mono tabular-nums ${
          isConsume ? "text-rose-600 dark:text-rose-400" : "text-emerald-700 dark:text-emerald-300"
        }`}
      >
        {sign}{formatCredits(t.delta)}
      </td>
      <td className="py-1.5 pr-3 text-right font-mono tabular-nums">
        {formatCredits(t.balance_after)}
      </td>
      <td className="py-1.5 text-xs text-gray-500 dark:text-gray-400 truncate max-w-xs">
        {t.notes ?? ""}
      </td>
    </tr>
  )
}
