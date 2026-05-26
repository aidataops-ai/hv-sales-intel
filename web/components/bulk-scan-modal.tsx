"use client"

import { useMemo, useState } from "react"
import { Loader2, X, Search, Layers, Download } from "lucide-react"
import { searchPractices } from "@/lib/api"
import {
  STATE_LABELS,
  STATE_CITIES,
  VERTICAL_LABELS,
  SPECIALTIES_BY_VERTICAL,
  buildSpecialtyGridQueries,
  buildStateSweepQueries,
  buildUKQueries,
  parseCustomCities,
  templateForVertical,
  totalCitiesForStates,
  UK_ALL_CITIES,
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
  uniquePlaceIds: string[]      // accumulated across every query in the run
  errors: { query: string; message: string }[]
  currentQuery: string | null
  done: boolean
}

const EMPTY_STATS: RunStats = {
  ranQueries: 0,
  totalPractices: 0,
  uniquePlaceIds: [],
  errors: [],
  currentQuery: null,
  done: false,
}

// The state picker only lists US states + DC. The UK is its own
// country with its own city-level picker further down the modal —
// it never appears in the US chip grid (was confusing before).
const US_STATES = (Object.keys(STATE_LABELS) as StateCode[])
  .filter((s) => s !== "UK")
  .sort() as StateCode[]

export default function BulkScanModal({
  open,
  onClose,
  onComplete,
}: BulkScanModalProps) {
  const [mode, setMode] = useState<Mode>("sweep")
  const [vertical, setVertical] = useState<Vertical>("dental")
  const [states, setStates] = useState<StateCode[]>(["FL"])
  const [ukCities, setUkCities] = useState<string[]>([])  // empty == UK not selected
  const [template, setTemplate] = useState<string>(templateForVertical("dental"))
  const [specialties, setSpecialties] = useState<string[]>(
    SPECIALTIES_BY_VERTICAL.dental.slice(0, 3),
  )
  const [customCitiesText, setCustomCitiesText] = useState<string>("")
  const extraCitiesByState = useMemo(
    () => parseCustomCities(customCitiesText),
    [customCitiesText],
  )
  const [running, setRunning] = useState(false)
  const [stats, setStats] = useState<RunStats>(EMPTY_STATS)
  const [stopRequested, setStopRequested] = useState(false)

  const queries = useMemo(() => {
    const usQs =
      mode === "sweep"
        ? buildStateSweepQueries({ template, states, extraCitiesByState })
        : buildSpecialtyGridQueries({ states, specialties, extraCitiesByState })
    const ukQs =
      ukCities.length === 0
        ? []
        : mode === "sweep"
          ? buildUKQueries({ template, cities: ukCities })
          : buildUKQueries({ cities: ukCities, specialties })
    return [...usQs, ...ukQs]
  }, [mode, template, states, specialties, extraCitiesByState, ukCities])

  const totalCities = useMemo(
    () => totalCitiesForStates(states, extraCitiesByState),
    [states, extraCitiesByState],
  )

  const extraCitiesCount = useMemo(
    () => Object.values(extraCitiesByState).reduce(
      (n, list) => n + (list?.length ?? 0), 0,
    ),
    [extraCitiesByState],
  )

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

  function toggleUKCity(city: string) {
    setUkCities((prev) =>
      prev.includes(city) ? prev.filter((c) => c !== city) : [...prev, city],
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
    const seen = new Set<string>()
    for (const q of queries) {
      if (stopRequested) break
      setStats({
        ranQueries: ran,
        totalPractices: total,
        uniquePlaceIds: Array.from(seen),
        errors,
        currentQuery: q,
        done: false,
      })
      try {
        const results = await searchPractices(q, false)
        total += results.length
        for (const r of results) {
          if (r.place_id) seen.add(r.place_id)
        }
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
        uniquePlaceIds: Array.from(seen),
        errors,
        currentQuery: q,
        done: false,
      })
    }
    setStats({
      ranQueries: ran,
      totalPractices: total,
      uniquePlaceIds: Array.from(seen),
      errors,
      currentQuery: null,
      done: true,
    })
    setRunning(false)
    onComplete()
  }

  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  async function exportScanResults() {
    if (stats.uniquePlaceIds.length === 0 || exporting) return
    setExporting(true)
    setExportError(null)
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
      const res = await fetch(`${API_URL}/api/practices/export.csv`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place_ids: stats.uniquePlaceIds }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Export failed (${res.status})`)
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `apex-leads-bulkscan-${new Date()
        .toISOString()
        .replace(/[:.]/g, "-")
        .slice(0, 16)}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
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
    <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm flex items-start justify-center pt-20 px-4 pb-4">
      <div className="bg-white rounded-2xl shadow-xl w-[680px] max-w-[92vw] max-h-[calc(100vh-6rem)] overflow-hidden flex flex-col">
        <div className="flex-none flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div>
            <h2 className="font-serif text-lg font-semibold text-gray-900">
              Bulk Scan
            </h2>
            <p className="text-xs text-gray-500">
              Pull leads from the underlying directory across many cities at
              once — useful when one search would clip too early.
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

          {/* US states multi-select */}
          <div className="rounded-md border border-gray-200 bg-gray-50/40 p-2.5">
            <div className="flex items-baseline justify-between mb-1.5">
              <span className="block text-xs font-semibold text-gray-800">
                <span className="mr-1">🇺🇸</span>
                United States — {states.length} selected · {totalCities} cities
              </span>
              <div className="flex gap-2 text-[11px]">
                <button
                  disabled={running}
                  onClick={() => setStates(US_STATES)}
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
            <div className="max-h-40 overflow-y-auto">
              <div className="flex flex-wrap gap-1">
                {US_STATES.map((s) => (
                  <StateChip
                    key={s}
                    code={s}
                    active={states.includes(s)}
                    disabled={running}
                    onToggle={toggleState}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* United Kingdom — separate country, city-level picker */}
          <div className="rounded-md border border-gray-200 bg-gray-50/40 p-2.5">
            <div className="flex items-baseline justify-between mb-1.5">
              <span className="block text-xs font-semibold text-gray-800">
                <span className="mr-1">🇬🇧</span>
                United Kingdom — {ukCities.length} of {UK_ALL_CITIES.length} cities
              </span>
              <div className="flex gap-2 text-[11px]">
                <button
                  disabled={running}
                  onClick={() => setUkCities([...UK_ALL_CITIES])}
                  className="text-teal-700 hover:underline"
                >
                  Select all
                </button>
                <button
                  disabled={running}
                  onClick={() => setUkCities([])}
                  className="text-gray-500 hover:underline"
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="max-h-40 overflow-y-auto">
              <div className="flex flex-wrap gap-1">
                {UK_ALL_CITIES.map((city) => {
                  const active = ukCities.includes(city)
                  return (
                    <button
                      key={city}
                      disabled={running}
                      onClick={() => toggleUKCity(city)}
                      className={`text-[11px] px-2 py-0.5 rounded-full border transition ${
                        active
                          ? "bg-teal-50 border-teal-500 text-teal-700"
                          : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
                      }`}
                    >
                      {city}
                    </button>
                  )
                })}
              </div>
            </div>
            <p className="text-[11px] text-gray-500 mt-1.5">
              UK is its own country — queries are built as{" "}
              <code>… in &lt;city&gt;, UK</code>, not as a US state.
            </p>
          </div>

          {/* Custom cities supplement */}
          <div>
            <label className="block">
              <span className="block text-xs font-medium text-gray-700 mb-1">
                Custom cities (optional)
                {extraCitiesCount > 0 && (
                  <span className="ml-1 text-teal-700">
                    · +{extraCitiesCount} added
                  </span>
                )}
              </span>
              <textarea
                disabled={running}
                value={customCitiesText}
                onChange={(e) => setCustomCitiesText(e.target.value)}
                rows={3}
                className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5 font-mono"
                placeholder={"Vernon, CA\nMaywood, CA\nCalifornia: Bell, Cudahy, Huntington Park"}
              />
            </label>
            <p className="text-[11px] text-gray-500 mt-1">
              One per line. Formats: <code>City, ST</code> or{" "}
              <code>StateName: city, city, …</code>. Supplements the defaults
              above for any state you selected.
            </p>
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
            US state{states.length === 1 ? "" : "s"}
            {ukCities.length > 0 && (
              <>
                {" "}+{" "}
                <span className="font-semibold text-gray-900">
                  {ukCities.length}
                </span>{" "}
                UK cit{ukCities.length === 1 ? "y" : "ies"}
              </>
            )}
            . Each query returns up to 60 leads. The 24-hour cache
            short-circuits duplicate queries so re-running the same scan is
            free.
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

              {stats.done && stats.uniquePlaceIds.length > 0 && (
                <div className="mt-2 rounded-md bg-emerald-50 border border-emerald-200 px-3 py-2 flex items-center justify-between gap-3">
                  <span className="text-xs text-emerald-900">
                    Scan complete — {stats.uniquePlaceIds.length.toLocaleString()}{" "}
                    unique leads collected.
                  </span>
                  <button
                    onClick={exportScanResults}
                    disabled={exporting}
                    className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 transition"
                  >
                    {exporting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Download className="w-3.5 h-3.5" />
                    )}
                    Export these to CSV
                  </button>
                </div>
              )}
              {exportError && (
                <p className="text-xs text-rose-600">{exportError}</p>
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


function StateChip({
  code, active, disabled, onToggle, label,
}: {
  code: StateCode
  active: boolean
  disabled: boolean
  onToggle: (s: StateCode) => void
  label?: string
}) {
  const cityCount = STATE_CITIES[code]?.length ?? 0
  return (
    <button
      disabled={disabled}
      onClick={() => onToggle(code)}
      className={`text-[11px] px-2 py-0.5 rounded-full border transition ${
        active
          ? "bg-teal-50 border-teal-500 text-teal-700"
          : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
      }`}
      title={`${STATE_LABELS[code]} — ${cityCount} cities`}
    >
      {label ?? code}
    </button>
  )
}
