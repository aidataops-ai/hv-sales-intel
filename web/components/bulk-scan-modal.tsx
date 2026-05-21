"use client"

import { useMemo, useState } from "react"
import { Loader2, X, Search, Layers } from "lucide-react"
import { searchPractices } from "@/lib/api"
import {
  STATE_LABELS,
  STATE_CITIES,
  VERTICAL_LABELS,
  SPECIALTIES_BY_VERTICAL,
  buildSpecialtyGridQueries,
  buildStateSweepQueries,
  templateForVertical,
  totalCitiesForStates,
  type StateCode,
  type Vertical,
} from "@/lib/bulk-scan"

type Mode = "sweep" | "grid"

interface BulkScanModalProps {
  open: boolean
  onClose: () => void
  onComplete: () => void
}

interface RunStats {
  ranQueries: number
  totalPractices: number
  errors: { query: string; message: string }[]
  currentQuery: string | null
  done: boolean
}

const EMPTY_STATS: RunStats = {
  ranQueries: 0,
  totalPractices: 0,
  errors: [],
  currentQuery: null,
  done: false,
}

const ALL_STATES = Object.keys(STATE_LABELS).sort() as StateCode[]

export default function BulkScanModal({
  open,
  onClose,
  onComplete,
}: BulkScanModalProps) {
  const [mode, setMode] = useState<Mode>("sweep")
  const [vertical, setVertical] = useState<Vertical>("dental")
  const [states, setStates] = useState<StateCode[]>(["FL"])
  const [template, setTemplate] = useState<string>(templateForVertical("dental"))
  const [specialties, setSpecialties] = useState<string[]>(
    SPECIALTIES_BY_VERTICAL.dental.slice(0, 3),
  )
  const [running, setRunning] = useState(false)
  const [stats, setStats] = useState<RunStats>(EMPTY_STATS)
  const [stopRequested, setStopRequested] = useState(false)

  const queries = useMemo(() => {
    if (mode === "sweep") {
      return buildStateSweepQueries({ template, states })
    }
    return buildSpecialtyGridQueries({ states, specialties })
  }, [mode, template, states, specialties])

  const totalCities = useMemo(() => totalCitiesForStates(states), [states])

  if (!open) return null

  function reset() {
    setStats(EMPTY_STATS)
    setStopRequested(false)
  }

  function toggleSpecialty(s: string) {
    setSpecialties((prev) =>
      prev.includes(s) ? prev.filter((p) => p !== s) : [...prev, s],
    )
  }

  function toggleState(s: StateCode) {
    setStates((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    )
  }

  function handleVerticalChange(v: Vertical) {
    setVertical(v)
    // Auto-fill the State sweep template + reset specialty selection so
    // the modal always reflects the chosen vertical.
    setTemplate(templateForVertical(v))
    setSpecialties(SPECIALTIES_BY_VERTICAL[v].slice(0, 3))
  }

  async function runScan() {
    if (queries.length === 0 || running) return
    setRunning(true)
    reset()
    let ran = 0
    let total = 0
    const errors: RunStats["errors"] = []
    for (const q of queries) {
      if (stopRequested) break
      setStats({
        ranQueries: ran,
        totalPractices: total,
        errors,
        currentQuery: q,
        done: false,
      })
      try {
        const results = await searchPractices(q, false)
        total += results.length
      } catch (e) {
        errors.push({
          query: q,
          message: e instanceof Error ? e.message : String(e),
        })
      }
      ran += 1
      setStats({
        ranQueries: ran,
        totalPractices: total,
        errors,
        currentQuery: q,
        done: false,
      })
    }
    setStats({
      ranQueries: ran,
      totalPractices: total,
      errors,
      currentQuery: null,
      done: true,
    })
    setRunning(false)
    onComplete()
  }

  function handleClose() {
    if (running) {
      setStopRequested(true)
      return
    }
    reset()
    onClose()
  }

  const progressPct =
    queries.length > 0
      ? Math.round((stats.ranQueries / queries.length) * 100)
      : 0

  return (
    <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm grid place-items-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-[680px] max-w-[92vw] max-h-[calc(100vh-2rem)] overflow-hidden flex flex-col">
        <div className="flex-none flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div>
            <h2 className="font-serif text-lg font-semibold text-gray-900">
              Bulk Scan
            </h2>
            <p className="text-xs text-gray-500">
              Run targeted Google Places queries across many cities to get past
              the 60-results-per-query ceiling.
            </p>
          </div>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-gray-700"
            title={running ? "Stop after current query" : "Close"}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-none px-5 pt-4 pb-2 border-b border-gray-100">
          <div className="inline-flex bg-gray-100 rounded-lg p-0.5 text-sm">
            <button
              disabled={running}
              onClick={() => setMode("sweep")}
              className={`px-3 py-1.5 rounded-md inline-flex items-center gap-1.5 transition ${
                mode === "sweep"
                  ? "bg-white shadow-sm text-gray-900"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              <Search className="w-3.5 h-3.5" /> State sweep
            </button>
            <button
              disabled={running}
              onClick={() => setMode("grid")}
              className={`px-3 py-1.5 rounded-md inline-flex items-center gap-1.5 transition ${
                mode === "grid"
                  ? "bg-white shadow-sm text-gray-900"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              <Layers className="w-3.5 h-3.5" /> Specialty grid
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 px-5 py-4 space-y-4 overflow-y-auto">
          {/* Vertical picker — drives the default template and specialty list */}
          <div>
            <span className="block text-xs font-medium text-gray-700 mb-1">
              Vertical
            </span>
            <select
              disabled={running}
              value={vertical}
              onChange={(e) => handleVerticalChange(e.target.value as Vertical)}
              className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5 bg-white"
            >
              {(Object.keys(VERTICAL_LABELS) as Vertical[]).map((v) => (
                <option key={v} value={v}>
                  {VERTICAL_LABELS[v]}
                </option>
              ))}
            </select>
          </div>

          {/* State multi-select */}
          <div>
            <div className="flex items-baseline justify-between mb-1">
              <span className="block text-xs font-medium text-gray-700">
                States ({states.length} selected · {totalCities} cities)
              </span>
              <div className="flex gap-2 text-[11px]">
                <button
                  disabled={running}
                  onClick={() => setStates(ALL_STATES)}
                  className="text-teal-700 hover:underline"
                >
                  Select all
                </button>
                <button
                  disabled={running}
                  onClick={() => setStates([])}
                  className="text-gray-500 hover:underline"
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1 max-h-32 overflow-y-auto p-1 border border-gray-200 rounded-md bg-gray-50/40">
              {ALL_STATES.map((s) => {
                const active = states.includes(s)
                const cityCount = STATE_CITIES[s]?.length ?? 0
                return (
                  <button
                    key={s}
                    disabled={running}
                    onClick={() => toggleState(s)}
                    className={`text-[11px] px-2 py-0.5 rounded-full border transition ${
                      active
                        ? "bg-teal-50 border-teal-500 text-teal-700"
                        : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
                    }`}
                    title={`${STATE_LABELS[s]} — ${cityCount} cities`}
                  >
                    {s}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Mode-specific controls */}
          {mode === "sweep" ? (
            <div>
              <label className="block">
                <span className="block text-xs font-medium text-gray-700 mb-1">
                  Query template
                </span>
                <input
                  disabled={running}
                  value={template}
                  onChange={(e) => setTemplate(e.target.value)}
                  className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5 font-mono"
                  placeholder="e.g. dental clinics in {city}, {state}"
                />
              </label>
              <p className="text-[11px] text-gray-500 mt-1">
                <code>&#123;city&#125;</code> /{" "}
                <code>&#123;state&#125;</code> /{" "}
                <code>&#123;stateLabel&#125;</code> are substituted per city.
              </p>
            </div>
          ) : (
            <div>
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Specialties ({specialties.length} selected)
              </span>
              <div className="flex flex-wrap gap-1.5">
                {SPECIALTIES_BY_VERTICAL[vertical].map((s) => {
                  const active = specialties.includes(s)
                  return (
                    <button
                      key={s}
                      disabled={running}
                      onClick={() => toggleSpecialty(s)}
                      className={`text-xs px-2 py-1 rounded-full border transition ${
                        active
                          ? "bg-teal-50 border-teal-500 text-teal-700"
                          : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
                      }`}
                    >
                      {s}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          <div className="rounded-lg bg-gray-50 border border-gray-200 px-3 py-2 text-xs text-gray-700">
            Will run{" "}
            <span className="font-semibold text-gray-900">{queries.length}</span>{" "}
            quer{queries.length === 1 ? "y" : "ies"} across{" "}
            <span className="font-semibold text-gray-900">{states.length}</span>{" "}
            state{states.length === 1 ? "" : "s"}. Each one hits Google Places
            once (≈1 billable call) and returns up to 60 results. The 24-hour
            search cache short-circuits duplicate queries.
          </div>

          {(running || stats.done) && (
            <div className="space-y-1.5">
              <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
                <div
                  className="h-full bg-teal-600 transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <div className="flex items-baseline justify-between text-xs">
                <span className="text-gray-700">
                  {stats.ranQueries}/{queries.length} queries
                  {stats.currentQuery && (
                    <span className="text-gray-400">
                      {" · "}now: {stats.currentQuery}
                    </span>
                  )}
                </span>
                <span className="font-medium text-gray-900">
                  {stats.totalPractices} practices · {stats.errors.length} errors
                </span>
              </div>
              {stopRequested && !stats.done && (
                <p className="text-xs text-amber-600">
                  Stopping after current query finishes…
                </p>
              )}
              {stats.errors.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-rose-600">
                    {stats.errors.length} failed quer
                    {stats.errors.length === 1 ? "y" : "ies"}
                  </summary>
                  <ul className="mt-1 space-y-0.5 max-h-24 overflow-y-auto text-rose-500">
                    {stats.errors.map((e, i) => (
                      <li key={i}>
                        <code>{e.query}</code> — {e.message}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>

        <div className="flex-none px-5 py-3 border-t border-gray-200 flex items-center justify-end gap-2">
          <button
            onClick={handleClose}
            className="text-sm px-3 py-1.5 rounded-md text-gray-700 hover:bg-gray-50"
          >
            {running ? "Stop" : "Close"}
          </button>
          <button
            disabled={running || queries.length === 0}
            onClick={runScan}
            className="inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
          >
            {running ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Running…
              </>
            ) : (
              <>
                Start {queries.length} quer{queries.length === 1 ? "y" : "ies"}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
