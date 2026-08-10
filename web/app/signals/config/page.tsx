"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, Plus, MapPin, Tags } from "lucide-react"

import SignalsTopBar from "@/components/signals-top-bar"
import { useAuth } from "@/lib/auth"
import {
  addTargets, getSignalsConfig, setTargetEnabled,
  type NewTargetRow, type SearchTarget, type SignalsConfig,
} from "@/lib/leads"

/**
 * Instant Signals — collector config.
 *
 * Edits the live `company_search_targets` table (what the collector actually
 * searches), not the checked-in `config/leads/*.json`. The JSON files are the
 * catalog surfaced in the "Add …" forms; the table is the source of truth the
 * collect stage reads (ADR-03). Admin only — the whole page is behind writes
 * that require admin, so a non-admin sees an explicit notice, not a blank.
 *
 * See `docs/specs/2026-08-10-instant-signals-config-page-design.md`.
 */
export default function SignalsConfigPage() {
  const { user, loading: authLoading } = useAuth()
  const [config, setConfig] = useState<SignalsConfig | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setConfig(await getSignalsConfig())
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!authLoading && user?.role === "admin") refresh()
    else if (!authLoading) setLoading(false)
  }, [authLoading, user?.role, refresh])

  return (
    <div className="min-h-screen bg-cream dark:bg-night-900">
      <SignalsTopBar />
      <main className="pt-14">
        <div className="max-w-[1200px] mx-auto p-4 space-y-4">
          <header className="px-1">
            <h1 className="font-serif text-lg font-semibold text-gray-900 dark:text-white">
              Collector configuration
            </h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
              What the pipeline searches for — states, cities, tracks &amp; keywords.
              Changes take effect on the next{" "}
              <span className="text-gray-400 dark:text-gray-500">Run pipeline</span> sweep.
            </p>
          </header>

          {authLoading || loading ? (
            <Loading />
          ) : user?.role !== "admin" ? (
            <Notice>Configuration is admin only.</Notice>
          ) : !config ? (
            <Notice>Could not load configuration. Try reloading.</Notice>
          ) : (
            <ConfigBody config={config} onChanged={refresh} />
          )}
        </div>
      </main>
    </div>
  )
}

function ConfigBody({
  config,
  onChanged,
}: {
  config: SignalsConfig
  onChanged: () => Promise<void>
}) {
  const rows = config.targets.rows

  // The tenant's current terms and locations drive expansion: adding a state
  // crosses its locations with every term already in play, adding a track
  // crosses its keywords with every location. On a fresh tenant with no rows
  // yet, fall back to the config catalog so the first add still expands.
  const terms = useMemo(() => {
    const seen = new Map<string, { term: string; service_line: string }>()
    for (const r of rows) if (!seen.has(r.term)) {
      seen.set(r.term, { term: r.term, service_line: r.service_line })
    }
    if (seen.size) return Array.from(seen.values())
    return config.catalog.tracks.flatMap((t) =>
      t.terms.map((term) => ({ term, service_line: t.service_line })),
    )
  }, [rows, config.catalog.tracks])

  const locations = useMemo(() => {
    const seen = new Map<string, { location: string; state: string; granularity: "state" | "city" }>()
    for (const r of rows) if (!seen.has(r.location)) {
      seen.set(r.location, { location: r.location, state: r.state, granularity: r.granularity })
    }
    if (seen.size) return Array.from(seen.values())
    return config.catalog.states.flatMap((s) => [
      ...(s.statewide_query
        ? [{ location: s.statewide_query, state: s.code, granularity: "state" as const }]
        : []),
      ...s.cities.map((c) => ({ location: c, state: s.code, granularity: "city" as const })),
    ])
  }, [rows, config.catalog.states])

  return (
    <>
      <GeographyPanel rows={rows} terms={terms} onChanged={onChanged} catalog={config.catalog} />
      <TracksPanel rows={rows} locations={locations} onChanged={onChanged} catalog={config.catalog} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Geography
// ---------------------------------------------------------------------------

function GeographyPanel({
  rows,
  terms,
  onChanged,
  catalog,
}: {
  rows: SearchTarget[]
  terms: { term: string; service_line: string }[]
  onChanged: () => Promise<void>
  catalog: SignalsConfig["catalog"]
}) {
  const [adding, setAdding] = useState(false)

  const byState = useMemo(() => groupBy(rows, (r) => r.state), [rows])

  async function addState(code: string, statewide: string, cities: string[]) {
    const st = code.trim().toUpperCase()
    const locs = [
      ...(statewide.trim()
        ? [{ location: statewide.trim(), granularity: "state" as const }]
        : []),
      ...cities.map((c) => ({ location: c, granularity: "city" as const })),
    ]
    const newRows: NewTargetRow[] = locs.flatMap((l) =>
      terms.map((t) => ({
        term: t.term,
        service_line: t.service_line,
        location: l.location,
        state: st,
        granularity: l.granularity,
      })),
    )
    return newRows
  }

  return (
    <Panel
      icon={<MapPin className="w-4 h-4" />}
      title="Geography"
      subtitle="States and cities the collector searches"
      action={
        <PanelAddButton open={adding} onClick={() => setAdding((v) => !v)} label="Add state" />
      }
    >
      {adding && (
        <AddStateForm
          catalog={catalog}
          disabled={terms.length === 0}
          onSubmit={async (code, statewide, cities) => {
            const newRows = await addState(code, statewide, cities)
            const res = await addTargets(newRows)
            if (res.ok) {
              await onChanged()
              setAdding(false)
            }
            return res
          }}
        />
      )}

      <div className="space-y-3">
        {Object.entries(byState).map(([state, stateRows]) => (
          <StateRow
            key={state}
            state={state}
            rows={stateRows}
            terms={terms}
            catalogCities={
              catalog.states.find((s) => s.code === state)?.cities ?? []
            }
            onChanged={onChanged}
          />
        ))}
        {Object.keys(byState).length === 0 && (
          <EmptyRow>No states yet — add one to start collecting.</EmptyRow>
        )}
      </div>
    </Panel>
  )
}

/** One state as a bulk-scan-style box: a scrollable grid of city chips you
 *  toggle on/off, mirroring the Bulk Scan modal's city picker. A chip is a
 *  whole `(term x city)` group — teal when any keyword is on, grey when the
 *  city is fully off. Cities in the config catalog that aren't targets yet
 *  appear as dashed "+ city" chips you can add in one click; anything not in
 *  the catalog goes in via the free-text field. */
function StateRow({
  state,
  rows,
  terms,
  catalogCities,
  onChanged,
}: {
  state: string
  rows: SearchTarget[]
  terms: { term: string; service_line: string }[]
  catalogCities: string[]
  onChanged: () => Promise<void>
}) {
  const [addingCity, setAddingCity] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)

  const byLocation = useMemo(() => groupBy(rows, (r) => r.location), [rows])
  // Statewide first, then cities alphabetically.
  const locations = useMemo(
    () =>
      Object.entries(byLocation).sort(([, a], [, b]) => {
        const ga = a[0].granularity === "state" ? 0 : 1
        const gb = b[0].granularity === "state" ? 0 : 1
        return ga - gb || a[0].location.localeCompare(b[0].location)
      }),
    [byLocation],
  )

  const cityLocations = locations.filter(([, r]) => r[0].granularity === "city")
  const cityNames = new Set(cityLocations.map(([loc]) => loc))
  const addable = catalogCities.filter((c) => !cityNames.has(c))
  const enabledCities = cityLocations.filter(([, r]) => r.some((x) => x.enabled)).length

  async function setAll(next: boolean) {
    setBulkBusy(true)
    await Promise.all(
      rows.filter((r) => r.enabled !== next).map((r) => setTargetEnabled(r.id, next)),
    )
    await onChanged()
    setBulkBusy(false)
  }

  async function addCity(city: string) {
    const res = await addTargets(
      terms.map((t) => ({
        term: t.term,
        service_line: t.service_line,
        location: city,
        state,
        granularity: "city" as const,
      })),
    )
    if (res.ok) await onChanged()
    return res
  }

  return (
    <div className="rounded-md border border-gray-200 dark:border-white/10 bg-gray-50/40 dark:bg-white/5 p-2.5">
      <div className="flex items-baseline justify-between gap-2 mb-1.5">
        <span className="text-xs font-semibold text-gray-800 dark:text-[#d9d9d9]">
          {state} — {enabledCities} of {cityLocations.length}{" "}
          {cityLocations.length === 1 ? "city" : "cities"} on
        </span>
        <div className="flex items-center gap-2 text-[11px]">
          {bulkBusy && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
          <button
            disabled={bulkBusy}
            onClick={() => setAll(true)}
            className="text-teal-700 dark:text-teal-400 hover:underline disabled:opacity-50"
          >
            Enable all
          </button>
          <button
            disabled={bulkBusy}
            onClick={() => setAll(false)}
            className="text-gray-500 dark:text-gray-400 hover:underline disabled:opacity-50"
          >
            Disable all
          </button>
        </div>
      </div>

      <div className="max-h-44 overflow-y-auto">
        <div className="flex flex-wrap gap-1">
          {locations.map(([location, locRows]) => (
            <CityChip
              key={location}
              label={locRows[0].granularity === "state" ? "Statewide" : location}
              rows={locRows}
              onChanged={onChanged}
            />
          ))}
          {addable.map((city) => (
            <AddCityChip key={city} label={city} onAdd={() => addCity(city)} />
          ))}
        </div>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={() => setAddingCity((v) => !v)}
          className="text-[11px] text-teal-700 dark:text-teal-400 hover:underline"
        >
          {addingCity ? "Cancel" : "+ custom city"}
        </button>
      </div>

      {addingCity && (
        <AddCityForm
          onSubmit={async (city) => {
            const res = await addCity(city)
            if (res.ok) setAddingCity(false)
            return res
          }}
        />
      )}
    </div>
  )
}

/** A city (or the statewide query) as a toggleable pill — teal when any of its
 *  keywords is enabled, grey when the city is fully off. Clicking flips the
 *  whole group, matching the Bulk Scan modal's chip interaction. */
function CityChip({
  label,
  rows,
  onChanged,
}: {
  label: string
  rows: SearchTarget[]
  onChanged: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const enabled = rows.some((r) => r.enabled)
  const onCount = rows.filter((r) => r.enabled).length

  async function toggle() {
    setBusy(true)
    const next = !enabled
    await Promise.all(
      rows.filter((r) => r.enabled !== next).map((r) => setTargetEnabled(r.id, next)),
    )
    await onChanged()
    setBusy(false)
  }

  return (
    <button
      onClick={toggle}
      disabled={busy}
      title={`${label} — ${onCount}/${rows.length} keywords on · click to ${
        enabled ? "disable" : "enable"
      }`}
      className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border transition disabled:opacity-50 ${
        enabled
          ? "bg-teal-50 dark:bg-[#284b63]/40 border-teal-500 text-teal-700 dark:text-teal-400"
          : "bg-white dark:bg-night-800 border-gray-200 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:border-gray-400 line-through decoration-gray-300"
      }`}
    >
      {busy && <Loader2 className="w-3 h-3 animate-spin" />}
      {label}
    </button>
  )
}

/** A catalog city not yet a target — a dashed "+ city" pill that adds it. */
function AddCityChip({ label, onAdd }: { label: string; onAdd: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false)
  async function add() {
    setBusy(true)
    await onAdd()
    setBusy(false)
  }
  return (
    <button
      onClick={add}
      disabled={busy}
      title={`Add ${label} from the config catalog`}
      className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-dashed border-gray-300 dark:border-white/15 text-gray-400 dark:text-gray-500 hover:border-teal-400 hover:text-teal-600 transition disabled:opacity-50"
    >
      {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
      {label}
    </button>
  )
}

function AddCityForm({
  onSubmit,
}: {
  onSubmit: (city: string) => Promise<{ ok: boolean; error?: string; inserted?: number }>
}) {
  const [city, setCity] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const res = await onSubmit(city.trim())
    setBusy(false)
    if (res.ok) setMsg({ ok: true, text: `Added ${res.inserted ?? 0} targets.` })
    else setMsg({ ok: false, text: res.error ?? "Failed." })
  }

  return (
    <div className="mt-2 pl-1 flex items-end gap-2">
      <div className="flex-1 max-w-sm">
        <TextInput
          label="New city"
          placeholder="Houston, TX"
          value={city}
          onChange={setCity}
        />
      </div>
      <FormActions busy={busy} disabled={!city.trim()} onSubmit={submit} msg={msg} compact />
    </div>
  )
}

function AddStateForm({
  catalog,
  disabled,
  onSubmit,
}: {
  catalog: SignalsConfig["catalog"]
  disabled: boolean
  onSubmit: (
    code: string,
    statewide: string,
    cities: string[],
  ) => Promise<{ ok: boolean; error?: string; inserted?: number }>
}) {
  const [code, setCode] = useState("")
  const [statewide, setStatewide] = useState("")
  const [citiesText, setCitiesText] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const cities = citiesText.split("\n").map((c) => c.trim()).filter(Boolean)
    const res = await onSubmit(code, statewide, cities)
    setBusy(false)
    if (res.ok) setMsg({ ok: true, text: `Added ${res.inserted ?? 0} targets.` })
    else setMsg({ ok: false, text: res.error ?? "Failed." })
  }

  return (
    <FormShell>
      {disabled && (
        <p className="text-[11px] text-amber-600 dark:text-amber-400">
          Add a track first — a state is searched against your existing keywords.
        </p>
      )}
      <div className="grid sm:grid-cols-3 gap-2">
        <TextInput label="State code" placeholder="TX" value={code} onChange={setCode} maxLength={2} />
        <div className="sm:col-span-2">
          <TextInput
            label="Statewide query"
            placeholder="Texas, USA"
            value={statewide}
            onChange={setStatewide}
          />
        </div>
      </div>
      <TextArea
        label="Cities (one per line, e.g. “Houston, TX”)"
        placeholder={"Houston, TX\nDallas, TX\nAustin, TX"}
        value={citiesText}
        onChange={setCitiesText}
      />
      <p className="text-[11px] text-gray-400 dark:text-gray-500">
        Suggestions from config: {catalog.states.map((s) => s.code).join(", ")}.
      </p>
      <FormActions busy={busy} disabled={disabled || !code.trim()} onSubmit={submit} msg={msg} />
    </FormShell>
  )
}

// ---------------------------------------------------------------------------
// Tracks & keywords
// ---------------------------------------------------------------------------

function TracksPanel({
  rows,
  locations,
  onChanged,
  catalog,
}: {
  rows: SearchTarget[]
  locations: { location: string; state: string; granularity: "state" | "city" }[]
  onChanged: () => Promise<void>
  catalog: SignalsConfig["catalog"]
}) {
  const [adding, setAdding] = useState(false)
  const byTrack = useMemo(() => groupBy(rows, (r) => r.service_line), [rows])

  function expand(serviceLine: string, keywords: string[]): NewTargetRow[] {
    return keywords.flatMap((term) =>
      locations.map((l) => ({
        term,
        service_line: serviceLine,
        location: l.location,
        state: l.state,
        granularity: l.granularity,
      })),
    )
  }

  return (
    <Panel
      icon={<Tags className="w-4 h-4" />}
      title="Tracks & keywords"
      subtitle="Service lines and the job titles that surface them"
      action={
        <PanelAddButton open={adding} onClick={() => setAdding((v) => !v)} label="Add track" />
      }
    >
      {adding && (
        <AddTrackForm
          catalog={catalog}
          disabled={locations.length === 0}
          onSubmit={async (sl, keywords) => {
            const res = await addTargets(expand(sl, keywords))
            if (res.ok) {
              await onChanged()
              setAdding(false)
            }
            return res
          }}
        />
      )}

      <div className="divide-y divide-gray-100 dark:divide-white/5">
        {Object.entries(byTrack).map(([track, trackRows]) => (
          <TrackRow
            key={track}
            track={track}
            rows={trackRows}
            locations={locations}
            onChanged={onChanged}
          />
        ))}
        {Object.keys(byTrack).length === 0 && (
          <EmptyRow>No tracks yet.</EmptyRow>
        )}
      </div>
    </Panel>
  )
}

function TrackRow({
  track,
  rows,
  locations,
  onChanged,
}: {
  track: string
  rows: SearchTarget[]
  locations: { location: string; state: string; granularity: "state" | "city" }[]
  onChanged: () => Promise<void>
}) {
  const [addingKw, setAddingKw] = useState(false)
  const keywords = useMemo(() => Array.from(new Set(rows.map((r) => r.term))), [rows])
  const enabled = rows.some((r) => r.enabled)

  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 dark:text-white">{track}</div>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {keywords.map((k) => (
              <span
                key={k}
                className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-white/5 text-gray-600 dark:text-gray-300"
              >
                {k}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setAddingKw((v) => !v)}
            className="text-[11px] text-teal-700 dark:text-teal-400 hover:underline"
          >
            + keyword
          </button>
          <GroupToggle rows={rows} enabled={enabled} onChanged={onChanged} />
        </div>
      </div>

      {addingKw && (
        <AddKeywordForm
          onSubmit={async (term) => {
            const res = await addTargets(
              locations.map((l) => ({
                term,
                service_line: track,
                location: l.location,
                state: l.state,
                granularity: l.granularity,
              })),
            )
            if (res.ok) {
              await onChanged()
              setAddingKw(false)
            }
            return res
          }}
        />
      )}
    </div>
  )
}

function AddTrackForm({
  catalog,
  disabled,
  onSubmit,
}: {
  catalog: SignalsConfig["catalog"]
  disabled: boolean
  onSubmit: (
    serviceLine: string,
    keywords: string[],
  ) => Promise<{ ok: boolean; error?: string; inserted?: number }>
}) {
  const [name, setName] = useState("")
  const [kwText, setKwText] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const keywords = kwText.split("\n").map((k) => k.trim()).filter(Boolean)
    const res = await onSubmit(name.trim(), keywords)
    setBusy(false)
    if (res.ok) setMsg({ ok: true, text: `Added ${res.inserted ?? 0} targets.` })
    else setMsg({ ok: false, text: res.error ?? "Failed." })
  }

  return (
    <FormShell>
      {disabled && (
        <p className="text-[11px] text-amber-600 dark:text-amber-400">
          Add a state first — a track is searched across your existing locations.
        </p>
      )}
      <TextInput
        label="Track (service line)"
        placeholder="Virtual Medical Assistant"
        value={name}
        onChange={setName}
      />
      <TextArea
        label="Keywords (job titles, one per line)"
        placeholder={"medical assistant\nfront office assistant"}
        value={kwText}
        onChange={setKwText}
      />
      <p className="text-[11px] text-amber-600 dark:text-amber-400 leading-snug">
        A brand-new track is <strong>searched</strong> right away, but the qualifier only
        <strong> labels</strong> leads with a track that also exists in{" "}
        <code className="font-mono">roles.json</code>. To label leads under a new track,
        also add it there and deploy. Adding keywords to a track already listed above has no
        such caveat.
      </p>
      <p className="text-[11px] text-gray-400 dark:text-gray-500">
        Configured tracks: {catalog.tracks.map((t) => t.service_line).join(" · ")}.
      </p>
      <FormActions
        busy={busy}
        disabled={disabled || !name.trim() || !kwText.trim()}
        onSubmit={submit}
        msg={msg}
      />
    </FormShell>
  )
}

function AddKeywordForm({
  onSubmit,
}: {
  onSubmit: (term: string) => Promise<{ ok: boolean; error?: string; inserted?: number }>
}) {
  const [term, setTerm] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const res = await onSubmit(term.trim())
    setBusy(false)
    if (res.ok) setMsg({ ok: true, text: `Added ${res.inserted ?? 0} targets.` })
    else setMsg({ ok: false, text: res.error ?? "Failed." })
  }

  return (
    <div className="mt-2 pl-1 flex items-end gap-2">
      <div className="flex-1 max-w-sm">
        <TextInput label="New keyword" placeholder="patient coordinator" value={term} onChange={setTerm} />
      </div>
      <FormActions busy={busy} disabled={!term.trim()} onSubmit={submit} msg={msg} compact />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

/** Enable/disable every row in a group. Fires one PATCH per row — chatty for a
 *  big state, and design doc §6.1 flags a bulk endpoint as the follow-up. */
function GroupToggle({
  rows,
  enabled,
  onChanged,
}: {
  rows: SearchTarget[]
  enabled: boolean
  onChanged: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  async function toggle() {
    setBusy(true)
    const next = !enabled
    // Only flip rows that actually differ, to keep the request count down.
    await Promise.all(
      rows.filter((r) => r.enabled !== next).map((r) => setTargetEnabled(r.id, next)),
    )
    await onChanged()
    setBusy(false)
  }
  return <Toggle on={enabled} busy={busy} onClick={toggle} />
}

function Toggle({ on, busy, onClick }: { on: boolean; busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition disabled:opacity-50 ${
        on ? "bg-teal-600" : "bg-gray-300 dark:bg-white/15"
      }`}
      title={on ? "Enabled — click to disable" : "Disabled — click to enable"}
    >
      {busy ? (
        <Loader2 className="w-3 h-3 animate-spin text-white mx-auto" />
      ) : (
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
            on ? "translate-x-4" : "translate-x-0.5"
          }`}
        />
      )}
    </button>
  )
}

function Panel({
  icon,
  title,
  subtitle,
  action,
  children,
}: {
  icon?: React.ReactNode
  title: string
  subtitle?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-start gap-2">
          {icon && <span className="text-teal-700 dark:text-teal-400 mt-0.5">{icon}</span>}
          <div>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h2>
            {subtitle && (
              <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">{subtitle}</p>
            )}
          </div>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function PanelAddButton({
  open,
  onClick,
  label,
}: {
  open: boolean
  onClick: () => void
  label: string
}) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 text-[12px] px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition"
    >
      <Plus className={`w-3.5 h-3.5 transition ${open ? "rotate-45" : ""}`} />
      {label}
    </button>
  )
}

function FormShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-xl border border-gray-200 dark:border-white/10 bg-gray-50/50 dark:bg-white/5 p-4 space-y-3">
      {children}
    </div>
  )
}

function FormActions({
  busy,
  disabled,
  onSubmit,
  msg,
  compact,
}: {
  busy: boolean
  disabled: boolean
  onSubmit: () => void
  msg: { ok: boolean; text: string } | null
  compact?: boolean
}) {
  return (
    <div className={compact ? "flex items-center gap-3" : "flex items-center gap-3 pt-1"}>
      <button
        onClick={onSubmit}
        disabled={busy || disabled}
        className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
        {busy ? "Adding…" : "Add"}
      </button>
      {msg && (
        <span
          className={`text-[11px] ${
            msg.ok ? "text-teal-700 dark:text-teal-400" : "text-red-600 dark:text-red-400"
          }`}
        >
          {msg.text}
        </span>
      )}
    </div>
  )
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  maxLength,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  maxLength?: number
}) {
  return (
    <label className="block">
      <span className="text-[11px] text-gray-500 dark:text-gray-400">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        className="mt-1 w-full text-sm px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-white/10 bg-white dark:bg-night-900 text-gray-900 dark:text-white placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-teal-500"
      />
    </label>
  )
}

function TextArea({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="text-[11px] text-gray-500 dark:text-gray-400">{label}</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={4}
        className="mt-1 w-full text-sm px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-white/10 bg-white dark:bg-night-900 text-gray-900 dark:text-white placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-teal-500 font-mono"
      />
    </label>
  )
}

function Loading() {
  return (
    <div className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-10 flex items-center justify-center text-gray-400">
      <Loader2 className="w-5 h-5 animate-spin" />
    </div>
  )
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-8 text-center text-sm text-gray-500 dark:text-gray-400">
      {children}
    </div>
  )
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return <div className="py-6 text-center text-[12px] text-gray-400 dark:text-gray-500">{children}</div>
}

function groupBy<T>(items: T[], key: (t: T) => string): Record<string, T[]> {
  const out: Record<string, T[]> = {}
  for (const item of items) {
    const k = key(item)
    ;(out[k] ??= []).push(item)
  }
  return out
}
