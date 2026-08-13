"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { AlertTriangle, ArrowLeft } from "lucide-react"

import SignalsTopBar from "@/components/signals-top-bar"
import {
  BarList, ColumnChart, DataTable, StatTile,
  type BarRow,
} from "@/components/signal-charts"
import { ALL_DISPOSITIONS, ALL_SOURCES, getLeadAnalytics, type LeadAnalytics } from "@/lib/leads"
import { timeAgo } from "@/lib/utils"

function Panel({
  title, subtitle, children,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
      <div>
        <h2 className="font-serif font-semibold text-gray-900 dark:text-white">{title}</h2>
        {subtitle && (
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 leading-snug">
            {subtitle}
          </p>
        )}
      </div>
      {children}
    </section>
  )
}

export default function SignalsAnalyticsPage() {
  const [data, setData] = useState<LeadAnalytics | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    getLeadAnalytics(30).then((result) => {
      setData(result)
      setIsLoading(false)
    })
  }, [])

  const dispositionRows: BarRow[] = useMemo(
    () =>
      ALL_DISPOSITIONS.map((disposition) => ({
        label: disposition,
        value: data?.dispositions?.[disposition] ?? 0,
      })).filter((row) => row.value > 0),
    [data],
  )

  const trackRows: BarRow[] = useMemo(
    () =>
      Object.entries(data?.tracks ?? {})
        .map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value),
    [data],
  )

  const rejectRows: BarRow[] = useMemo(
    () => (data?.reject_reasons ?? []).map((r) => ({ label: r.reason, value: r.count })),
    [data],
  )

  const perDay = data?.per_day ?? []
  const collector = data?.collector

  return (
    <div className="min-h-screen bg-cream dark:bg-night-900">
      <SignalsTopBar />

      <main className="pt-14">
        <div className="max-w-[1400px] mx-auto p-4 space-y-4">
          <Link
            href="/signals"
            className="inline-flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-400 transition"
          >
            <ArrowLeft className="w-4 h-4" />
            All signals
          </Link>

          {isLoading ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 p-4">Loading…</p>
          ) : !data ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 p-4">
              Analytics are unavailable right now.
            </p>
          ) : (
            <>
              {collector?.alert && (
                <div className="rounded-xl border border-teal-600/40 bg-teal-50 dark:bg-teal-500/10 dark:border-teal-500/30 p-4 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-teal-700 dark:text-teal-400" />
                  <div>
                    <p className="text-sm font-semibold text-gray-900 dark:text-white">
                      Collector alert
                    </p>
                    <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5">
                      {collector.alert}
                    </p>
                  </div>
                </div>
              )}

              {/* Headline numbers. A hero figure beats a chart when there is
                  one value and no comparison to draw. */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatTile label="Signals" value={String(data.total)}
                          hint="Qualified postings for this company" />
                <StatTile label="Keep rate" value={`${Math.round(data.keep_rate * 100)}%`}
                          hint="Share the qualifier kept" />
                <StatTile
                  label="Locations swept"
                  value={`${collector?.swept ?? 0} / ${collector?.locations ?? 0}`}
                  hint="Search locations fully swept at least once"
                />
                <StatTile
                  label="Zero-row locations"
                  value={String(collector?.zero_row_locations ?? 0)}
                  tone={collector?.alert ? "alert" : "neutral"}
                  hint="Silence, not an error, is the Indeed failure mode"
                />
              </div>

              {/* Faceted rather than stacked: the palette has no second mark
                  hue that a full-colour reader could separate from the first. */}
              <Panel
                title="Signals per day"
                subtitle="One chart per board — small multiples instead of two series, so identity never rests on colour."
              >
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {ALL_SOURCES.map((source) => (
                    <div key={source} className="space-y-2">
                      <h3 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9]">
                        {source === "linkedin" ? "LinkedIn" : "Indeed"}
                      </h3>
                      <ColumnChart
                        points={perDay.map((day) => ({
                          day: String(day.day),
                          value: Number(day[source] ?? 0),
                        }))}
                        emptyHint="No signals collected yet."
                      />
                    </div>
                  ))}
                </div>
                {perDay.length > 0 && (
                  <DataTable
                    columns={["Day", "Indeed", "LinkedIn", "Total"]}
                    rows={perDay.map((day) => [
                      String(day.day),
                      Number(day.indeed ?? 0),
                      Number(day.linkedin ?? 0),
                      Number(day.total ?? 0),
                    ])}
                  />
                )}
              </Panel>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <Panel title="Decisions" subtitle="How reps have triaged the feed — approved, rejected, or not yet decided.">
                  <BarList rows={dispositionRows} total={data.total}
                           emptyHint="No leads have been worked yet." />
                </Panel>

                <Panel title="Tracks" subtitle="Which service line the signals map to.">
                  <BarList rows={trackRows} emptyHint="No tracks assigned yet." />
                </Panel>

                <Panel
                  title="Reject reasons"
                  subtitle="The tuning signal for the qualifier prompt and the search prefilter — a reason that keeps repeating is a term to narrow."
                >
                  <BarList rows={rejectRows}
                           emptyHint="Nothing rejected yet — reasons appear here as reps triage." />
                </Panel>
              </div>

              <Panel title="Collector health" subtitle="Is collection returning rows at all?">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <StatTile
                    label="Last collect run"
                    value={collector?.last_run_at ? timeAgo(collector.last_run_at) : "never"}
                    hint={collector?.last_run_at ?? "No target has been swept yet"}
                  />
                  <StatTile
                    label="Newest posting seen"
                    value={collector?.last_posting_at ? timeAgo(collector.last_posting_at) : "—"}
                    hint={collector?.last_posting_at ?? "No postings collected yet"}
                  />
                </div>
              </Panel>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
