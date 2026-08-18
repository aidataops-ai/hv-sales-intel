"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, Plus, MapPin, Tags, Pin, X } from "lucide-react"

import PipelineToggleButton from "@/components/pipeline-toggle-button"
import RetriggerButton from "@/components/retrigger-button"
import SignalsTopBar from "@/components/signals-top-bar"
import { useAuth } from "@/lib/auth"
import {
  addLocations, addTerms, deleteLocation, deleteTerm, getSignalsConfig,
  setLocationEnabled, setLocationsEnabledBulk, setOverride, setTermEnabled,
  setTermsEnabledBulk,
  type AddDimensionResult, type CatalogState, type NewLocationRow,
  type SearchLocation, type SearchTerm, type SignalsConfig, type SweepSourceStatus,
  type TargetOverride,
} from "@/lib/leads"

/**
 * How a control tells the page its edit landed.
 *
 * `refresh` re-reads the whole config — 4 backend queries, each paying its own
 * server-side auth round trips — so it is reserved for edits that change the
 * *shape* of the config (add / delete, where rows appear or vanish and catalog
 * membership shifts) and for the genuinely ambiguous outcome: a bulk toggle
 * that came back with fewer rows than it asked for, which only a re-read can
 * reconcile.
 *
 * The `splice*` callbacks cover everything else: a PATCH/PUT whose response
 * already *is* the new row (or rows). Flipping one boolean used to cost ~11
 * round trips; splicing costs the one PATCH. The bulk toggles pass the whole
 * batch — "Enable all" on a state is one request answering with every row it
 * flipped. See docs/refactor/supabase-data-layer.md §4.
 */
interface ConfigEdits {
  refresh: () => Promise<void>
  spliceTerms: (rows: SearchTerm[]) => void
  spliceLocations: (rows: SearchLocation[]) => void
  spliceOverride: (
    termId: number,
    locationId: number,
    row: TargetOverride | null,
  ) => void
}

/**
 * Instant Signals — collector config.
 *
 * Edits the live `search_terms` / `search_locations` dimension tables (what
 * the collector actually crosses at claim time), not the checked-in
 * `config/leads/*.json`. The JSON files are the catalog surfaced in the
 * "Add …" forms; the tables are the source of truth the collect stage reads
 * (ADR-03). Admin only — the whole page is behind writes that require admin,
 * so a non-admin sees an explicit notice, not a blank.
 *
 * Two dimensions, not a stored product (instant-signals refactor, Phase 3):
 * a term and a location are edited independently, and the collector computes
 * the cross at claim time. That means "Add state" and "Add track" no longer
 * expand into a batch of `(term x location)` rows here — they just insert
 * dimension rows, and enabling/disabling one term or one location is a
 * single request instead of one per cell.
 *
 * See docs/refactor/instant-signals-targets.md §4-5.
 */
export default function SignalsConfigPage() {
  const { user, loading: authLoading } = useAuth()
  const [config, setConfig] = useState<SignalsConfig | null>(null)
  const [loading, setLoading] = useState(true)

  // Silent after the first load: `loading` gates the whole body behind a
  // spinner, so setting it on every chip toggle reads as a full page reload.
  // Each control already shows its own busy state while its PATCH is in
  // flight — the ~176-row refetch just lands in place when it arrives.
  const refresh = useCallback(async () => {
    setConfig(await getSignalsConfig())
    setLoading(false)
  }, [])

  // `prev.catalog` is passed through untouched, so the catalog-membership
  // memos in ConfigBody keep their identity and don't recompute on a toggle.
  // Both take a batch: a single toggle is the one-element case, so the bulk
  // toggles need no second code path.
  const spliceTerms = useCallback((rows: SearchTerm[]) => {
    if (rows.length === 0) return
    const byId = new Map(rows.map((r) => [r.id, r]))
    setConfig((prev) =>
      prev
        ? { ...prev, terms: prev.terms.map((t) => byId.get(t.id) ?? t) }
        : prev,
    )
  }, [])

  const spliceLocations = useCallback((rows: SearchLocation[]) => {
    if (rows.length === 0) return
    const byId = new Map(rows.map((r) => [r.id, r]))
    setConfig((prev) =>
      prev
        ? { ...prev, locations: prev.locations.map((l) => byId.get(l.id) ?? l) }
        : prev,
    )
  }, [])

  const spliceOverride = useCallback(
    (termId: number, locationId: number, row: TargetOverride | null) => {
      setConfig((prev) => {
        if (!prev) return prev
        const rest = prev.overrides.filter(
          (o) => !(o.term_id === termId && o.location_id === locationId),
        )
        return { ...prev, overrides: row ? [...rest, row] : rest }
      })
    },
    [],
  )

  const edits: ConfigEdits = useMemo(
    () => ({ refresh, spliceTerms, spliceLocations, spliceOverride }),
    [refresh, spliceTerms, spliceLocations, spliceOverride],
  )

  useEffect(() => {
    if (!authLoading && user?.role === "admin") refresh()
    else if (!authLoading) setLoading(false)
  }, [authLoading, user?.role, refresh])

  return (
    <div className="min-h-screen bg-cream dark:bg-night-900">
      <SignalsTopBar />
      <main className="pt-14">
        <div className="max-w-[1200px] mx-auto p-4 space-y-4">
          <header className="px-1 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="font-serif text-lg font-semibold text-gray-900 dark:text-white">
                Collector configuration
              </h1>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                What the pipeline searches for — states, cities, tracks &amp; keywords.
                Changes take effect on the next{" "}
                <span className="text-gray-400 dark:text-gray-500">Run pipeline</span> sweep.
              </p>
            </div>
            {user?.role === "admin" && (
              <div className="flex items-center gap-2">
                <PipelineToggleButton />
                <RetriggerButton />
              </div>
            )}
          </header>

          {authLoading || loading ? (
            <Loading />
          ) : user?.role !== "admin" ? (
            <Notice>Configuration is admin only.</Notice>
          ) : !config ? (
            <Notice>Could not load configuration. Try reloading.</Notice>
          ) : (
            <ConfigBody config={config} edits={edits} />
          )}
        </div>
      </main>
    </div>
  )
}

function ConfigBody({
  config,
  edits,
}: {
  config: SignalsConfig
  edits: ConfigEdits
}) {
  // Catalog membership, computed once here rather than per-chip: a row is
  // "in the catalog" if its term/location string still appears in the
  // checked-in config, which is exactly the condition delete refuses on
  // server-side (CatalogProtectedError) — the client-side check exists so
  // the × affordance never shows on a row delete would 409 anyway.
  const catalogTermSet = useMemo(
    () => new Set(config.catalog.tracks.flatMap((t) => t.terms)),
    [config.catalog.tracks],
  )
  const catalogLocationSet = useMemo(
    () =>
      new Set(
        config.catalog.states.flatMap((s) => [
          ...(s.statewide_query ? [s.statewide_query] : []),
          ...s.cities,
        ]),
      ),
    [config.catalog.states],
  )

  return (
    <>
      {/* SweepStatusStrip is intentionally unmounted for now: until the new
          collector has run for a while, coverage/never-swept read as alarming
          ("10% coverage · 60 never swept") when they only mean the 60 new
          cities haven't had their first sweep yet. Re-mount once cursors are
          warm: <SweepStatusStrip sweep={config.sweep} /> */}
      <GeographyPanel
        locations={config.locations}
        catalog={config.catalog}
        catalogLocationSet={catalogLocationSet}
        edits={edits}
      />
      <TracksPanel
        terms={config.terms}
        catalog={config.catalog}
        catalogTermSet={catalogTermSet}
        edits={edits}
      />
      <OverridesPanel
        overrides={config.overrides}
        terms={config.terms}
        locations={config.locations}
        edits={edits}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// Sweep status — read-only freshness strip (plan §5)
// ---------------------------------------------------------------------------

/** A compact one-line-per-source strip, not the big analytics StatTile cards
 *  — this is a glance while editing, not a dashboard. Makes the freshness
 *  cost of adding a state visible before an operator does it. */
// Intentionally unmounted until the new collector warms the cursors — see the
// ConfigBody note. The disable below keeps the component compiled and ready.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function SweepStatusStrip({ sweep }: { sweep: Record<string, SweepSourceStatus> }) {
  const sources = Object.entries(sweep)
  if (sources.length === 0) return null
  return (
    <div className="rounded-xl border border-gray-200 dark:border-white/10 bg-gray-50/40 dark:bg-white/5 px-4 py-2 flex flex-wrap items-center gap-x-6 gap-y-1">
      {sources.map(([source, s]) => (
        <span
          key={source}
          className="inline-flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400"
        >
          <span className="font-semibold text-gray-700 dark:text-[#d9d9d9]">
            {source === "linkedin" ? "LinkedIn" : "Indeed"}
          </span>
          <span>{s.coverage_pct.toFixed(0)}% coverage</span>
          <span aria-hidden>·</span>
          <span>{s.never_swept} never swept</span>
          <span aria-hidden>·</span>
          <span>
            oldest{" "}
            {s.oldest_cursor_age_hours != null
              ? `${Math.round(s.oldest_cursor_age_hours)}h`
              : "—"}
          </span>
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Geography
// ---------------------------------------------------------------------------

function GeographyPanel({
  locations,
  edits,
  catalog,
  catalogLocationSet,
}: {
  locations: SearchLocation[]
  edits: ConfigEdits
  catalog: SignalsConfig["catalog"]
  catalogLocationSet: Set<string>
}) {
  const [adding, setAdding] = useState(false)
  const byState = useMemo(() => groupBy(locations, (l) => l.state), [locations])

  // Catalog states with zero live rows — otherwise invisible except as a
  // text hint in AddStateForm's suggestion line. GA/NC/SC/TN land here for
  // any tenant that hasn't adopted them yet.
  const unadoptedStates = useMemo(
    () => catalog.states.filter((s) => !byState[s.code]),
    [catalog.states, byState],
  )

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
          onSubmit={async (code, statewide, cities) => {
            const rows: NewLocationRow[] = [
              ...(statewide.trim()
                ? [{ location: statewide.trim(), state: code, granularity: "state" as const }]
                : []),
              ...cities.map((c) => ({ location: c, state: code, granularity: "city" as const })),
            ]
            const res = await addLocations(rows)
            if (res.ok) {
              // Rows appear — a shape change, so re-read the config.
              await edits.refresh()
              setAdding(false)
            }
            return res
          }}
        />
      )}

      <div className="space-y-3">
        {Object.entries(byState).map(([state, stateLocations]) => (
          <StateRow
            key={state}
            state={state}
            locations={stateLocations}
            catalogCities={
              catalog.states.find((s) => s.code === state)?.cities ?? []
            }
            catalogLocationSet={catalogLocationSet}
            edits={edits}
          />
        ))}
        {Object.keys(byState).length === 0 && (
          <EmptyRow>No states yet — add one to start collecting.</EmptyRow>
        )}
      </div>

      {unadoptedStates.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-100 dark:border-white/5">
          <div className="text-[11px] text-gray-400 dark:text-gray-500 mb-1.5">
            In the config catalog, not yet added:
          </div>
          <div className="flex flex-wrap gap-1.5">
            {unadoptedStates.map((s) => (
              <AdoptStateChip key={s.code} state={s} edits={edits} />
            ))}
          </div>
        </div>
      )}
    </Panel>
  )
}

/** A catalog state with no live `search_locations` rows yet — a scaled-up
 *  `AddCityChip`: one click adds the statewide row plus every catalog city
 *  for that state in a single request, no form. Keeps a checked-in
 *  geography.json expansion (a new state added for every tenant) from being
 *  invisible until someone notices the text hint in `AddStateForm`. */
function AdoptStateChip({
  state,
  edits,
}: {
  state: CatalogState
  edits: ConfigEdits
}) {
  const [busy, setBusy] = useState(false)
  const cityCount = state.cities.length

  async function adopt() {
    setBusy(true)
    const rows: NewLocationRow[] = [
      ...(state.statewide_query
        ? [{ location: state.statewide_query, state: state.code, granularity: "state" as const }]
        : []),
      ...state.cities.map((c) => ({ location: c, state: state.code, granularity: "city" as const })),
    ]
    await addLocations(rows)
    await edits.refresh()
    setBusy(false)
  }

  return (
    <button
      onClick={adopt}
      disabled={busy}
      title={`Add ${state.code} — ${cityCount} ${cityCount === 1 ? "city" : "cities"} from the config catalog`}
      className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-dashed border-gray-300 dark:border-white/15 text-gray-400 dark:text-gray-500 hover:border-teal-400 hover:text-teal-600 transition disabled:opacity-50"
    >
      {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
      {state.code} · {cityCount} {cityCount === 1 ? "city" : "cities"}
    </button>
  )
}

/** One state as a bulk-scan-style box: a scrollable grid of location chips
 *  you toggle on/off, mirroring the Bulk Scan modal's city picker. A chip is
 *  now exactly ONE `search_locations` row — no term crossing — so a click is
 *  one request, not a Promise.all over a cell group. Cities in the config
 *  catalog that aren't locations yet appear as dashed "+ city" chips you can
 *  add in one click; anything not in the catalog goes in via the free-text
 *  field. */
function StateRow({
  state,
  locations,
  catalogCities,
  catalogLocationSet,
  edits,
}: {
  state: string
  locations: SearchLocation[]
  catalogCities: string[]
  catalogLocationSet: Set<string>
  edits: ConfigEdits
}) {
  const [addingCity, setAddingCity] = useState(false)
  const group = useGroupEdits()

  // Statewide first, then cities alphabetically.
  const sorted = useMemo(
    () =>
      [...locations].sort((a, b) => {
        const ga = a.granularity === "state" ? 0 : 1
        const gb = b.granularity === "state" ? 0 : 1
        return ga - gb || a.location.localeCompare(b.location)
      }),
    [locations],
  )

  const cityLocations = sorted.filter((l) => l.granularity === "city")
  const cityNames = new Set(cityLocations.map((l) => l.location))
  const addable = catalogCities.filter((c) => !cityNames.has(c))
  const enabledCities = cityLocations.filter((l) => l.enabled).length

  // One PATCH for the whole state. This used to be a PATCH per row inside a
  // `Promise.all` — ~64 requests for a big state, each paying its own auth
  // round trips — followed by a full config re-read, because a partial
  // failure across that batch left the panel unable to say which rows had
  // actually flipped. A single tenant-scoped UPDATE answers with the rows it
  // changed, so the common case splices; the re-read is now only for the one
  // case that is still ambiguous (see below).
  //
  // Runs through `group.runBulk`, which parks every chip in this state for the
  // duration — a chip clicked mid-flight would PATCH a row this batch is
  // already rewriting.
  async function setAll(next: boolean) {
    const ids = locations.filter((l) => l.enabled !== next).map((l) => l.id)
    if (ids.length === 0) return
    await group.runBulk(async () => {
      const rows = await setLocationsEnabledBulk(ids, next)
      // A short answer means some ids didn't update (stray or another
      // tenant's); the server is the only one who knows the true state.
      if (rows && rows.length === ids.length) edits.spliceLocations(rows)
      else await edits.refresh()
    })
  }

  async function addCity(city: string) {
    const res = await addLocations([{ location: city, state, granularity: "city" }])
    if (res.ok) await edits.refresh()
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
          {group.bulkBusy && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
          <button
            disabled={group.bulkDisabled}
            onClick={() => setAll(true)}
            className="text-teal-700 dark:text-teal-400 hover:underline disabled:opacity-50"
          >
            Enable all
          </button>
          <button
            disabled={group.bulkDisabled}
            onClick={() => setAll(false)}
            className="text-gray-500 dark:text-gray-400 hover:underline disabled:opacity-50"
          >
            Disable all
          </button>
        </div>
      </div>

      <div className="max-h-44 overflow-y-auto">
        <div className="flex flex-wrap gap-1">
          {sorted.map((loc) => (
            <LocationChip
              key={loc.id}
              location={loc}
              isCatalog={catalogLocationSet.has(loc.location)}
              disabled={group.rowsDisabled}
              runEdit={group.runRow}
              edits={edits}
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

/** One `search_locations` row (a city or the statewide query) as a toggleable
 *  pill — teal when enabled, grey when off. Clicking the label flips exactly
 *  this row. A hand-added (non-catalog) row also gets a small × — visible on
 *  hover, kept subtle — that hard-deletes it. A catalog row shows no ×: the
 *  server would 409 it anyway (`CatalogProtectedError` — deleting it would
 *  just be undone by the next collect run's re-seed), so the affordance
 *  would only be a promise the page can't keep. */
function LocationChip({
  location,
  isCatalog,
  disabled,
  runEdit,
  edits,
}: {
  location: SearchLocation
  isCatalog: boolean
  /** True while this state's bulk toggle is in flight — see `useGroupEdits`. */
  disabled: boolean
  runEdit: GroupEdits["runRow"]
  edits: ConfigEdits
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = location.granularity === "state" ? "Statewide" : location.location

  // The PATCH response is the updated row — splice it in rather than re-reading
  // the whole config to observe one boolean. A null row means the request
  // failed, and only then do we fall back to a re-read so the chip can't be
  // left showing a state the server never accepted.
  async function toggle() {
    setBusy(true)
    await runEdit(async () => {
      const row = await setLocationEnabled(location.id, !location.enabled)
      if (row) edits.spliceLocations([row])
      else await edits.refresh()
    })
    setBusy(false)
  }

  async function remove() {
    setBusy(true)
    setError(null)
    await runEdit(async () => {
      const res = await deleteLocation(location.id)
      if (res.ok) {
        // The row is gone — a shape change, so re-read.
        await edits.refresh()
      } else {
        setError(res.error)
        setBusy(false)
      }
    })
  }

  return (
    <span
      className={`group relative inline-flex items-center gap-0.5 text-[11px] pl-2 pr-1 py-0.5 rounded-full border transition ${
        location.enabled
          ? "bg-teal-50 dark:bg-[#284b63]/40 border-teal-500 text-teal-700 dark:text-teal-400"
          : "bg-white dark:bg-night-800 border-gray-200 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:border-gray-400"
      }`}
    >
      <button
        onClick={toggle}
        disabled={busy || disabled}
        title={
          disabled
            ? `${label} — waiting on this state's bulk change`
            : isCatalog
              ? `${label} — from the checked-in catalog; disable instead of removing`
              : `${label} — click to ${location.enabled ? "disable" : "enable"}`
        }
        className={`disabled:opacity-50 ${!location.enabled ? "line-through decoration-gray-300" : ""}`}
      >
        {busy && <Loader2 className="w-3 h-3 animate-spin inline-block mr-1 align-[-1px]" />}
        {label}
      </button>
      {!isCatalog && (
        <button
          onClick={remove}
          disabled={busy || disabled}
          title="Remove — not in the config catalog"
          className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition disabled:opacity-50"
        >
          <X className="w-3 h-3" />
        </button>
      )}
      {error && (
        <span className="absolute top-full left-0 mt-1 z-10 text-[10px] text-red-600 dark:text-red-400 bg-white dark:bg-night-900 border border-red-200 dark:border-red-500/30 rounded px-1.5 py-0.5 whitespace-nowrap shadow-sm">
          {error}
        </span>
      )}
    </span>
  )
}

/** A catalog city not yet a location — a dashed "+ city" pill that adds it. */
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
  onSubmit: (city: string) => Promise<AddDimensionResult>
}) {
  const [city, setCity] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const res = await onSubmit(city.trim())
    setBusy(false)
    setMsg(
      res.ok
        ? { ok: true, text: `Added ${res.inserted} location${res.inserted === 1 ? "" : "s"}.` }
        : { ok: false, text: res.error },
    )
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
  onSubmit,
}: {
  catalog: SignalsConfig["catalog"]
  onSubmit: (
    code: string,
    statewide: string,
    cities: string[],
  ) => Promise<AddDimensionResult>
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
    const res = await onSubmit(code.trim().toUpperCase(), statewide, cities)
    setBusy(false)
    setMsg(
      res.ok
        ? { ok: true, text: `Added ${res.inserted} location${res.inserted === 1 ? "" : "s"}.` }
        : { ok: false, text: res.error },
    )
  }

  return (
    <FormShell>
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
      <FormActions busy={busy} disabled={!code.trim()} onSubmit={submit} msg={msg} />
    </FormShell>
  )
}

// ---------------------------------------------------------------------------
// Tracks & keywords
// ---------------------------------------------------------------------------

function TracksPanel({
  terms,
  edits,
  catalog,
  catalogTermSet,
}: {
  terms: SearchTerm[]
  edits: ConfigEdits
  catalog: SignalsConfig["catalog"]
  catalogTermSet: Set<string>
}) {
  const [adding, setAdding] = useState(false)
  const byTrack = useMemo(() => groupBy(terms, (t) => t.service_line), [terms])

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
          onSubmit={async (sl, keywords) => {
            const res = await addTerms(keywords.map((term) => ({ term, service_line: sl })))
            if (res.ok) {
              // Rows appear — a shape change, so re-read the config.
              await edits.refresh()
              setAdding(false)
            }
            return res
          }}
        />
      )}

      <div className="divide-y divide-gray-100 dark:divide-white/5">
        {Object.entries(byTrack).map(([track, trackTerms]) => (
          <TrackRow
            key={track}
            track={track}
            terms={trackTerms}
            catalogTermSet={catalogTermSet}
            edits={edits}
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
  terms,
  catalogTermSet,
  edits,
}: {
  track: string
  terms: SearchTerm[]
  catalogTermSet: Set<string>
  edits: ConfigEdits
}) {
  const [addingKw, setAddingKw] = useState(false)
  const group = useGroupEdits()

  // Three states, not two: a track with some keywords on and some off used to
  // render identically to a fully enabled one, so disabling a single keyword
  // left the track toggle claiming everything was still on.
  const enabledCount = terms.filter((t) => t.enabled).length
  const toggleState: ToggleState =
    enabledCount === 0 ? "off" : enabledCount === terms.length ? "on" : "mixed"

  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 dark:text-white">{track}</div>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {terms.map((t) => (
              <TermPill
                key={t.id}
                term={t}
                isCatalog={catalogTermSet.has(t.term)}
                disabled={group.rowsDisabled}
                runEdit={group.runRow}
                edits={edits}
              />
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
          <TrackToggle terms={terms} state={toggleState} group={group} edits={edits} />
        </div>
      </div>

      {addingKw && (
        <AddKeywordForm
          onSubmit={async (term) => {
            const res = await addTerms([{ term, service_line: track }])
            if (res.ok) {
              // A row appears — a shape change, so re-read the config.
              await edits.refresh()
              setAddingKw(false)
            }
            return res
          }}
        />
      )}
    </div>
  )
}

/** One `search_terms` row as a clickable pill — teal/on, grey-strikethrough
 *  off, styled to match the geography panel's location chips. Clicking the
 *  label flips `enabled`; a hand-added (non-catalog) term also gets a
 *  hover-visible × that hard-deletes it — see `LocationChip` for why
 *  catalog rows show no ×. */
function TermPill({
  term,
  isCatalog,
  disabled,
  runEdit,
  edits,
}: {
  term: SearchTerm
  isCatalog: boolean
  /** True while this track's toggle is in flight — see `useGroupEdits`. */
  disabled: boolean
  runEdit: GroupEdits["runRow"]
  edits: ConfigEdits
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Splices the PATCH response instead of re-reading the config — see
  // `LocationChip.toggle` for the null/failure fallback and for why the edit
  // runs through the track's `runEdit`.
  async function toggle() {
    setBusy(true)
    await runEdit(async () => {
      const row = await setTermEnabled(term.id, !term.enabled)
      if (row) edits.spliceTerms([row])
      else await edits.refresh()
    })
    setBusy(false)
  }

  async function remove() {
    setBusy(true)
    setError(null)
    await runEdit(async () => {
      const res = await deleteTerm(term.id)
      if (res.ok) {
        // The row is gone — a shape change, so re-read.
        await edits.refresh()
      } else {
        setError(res.error)
        setBusy(false)
      }
    })
  }

  return (
    <span
      className={`group relative inline-flex items-center gap-0.5 text-[11px] pl-2 pr-1 py-0.5 rounded-full border transition ${
        term.enabled
          ? "bg-teal-50 dark:bg-[#284b63]/40 border-teal-500 text-teal-700 dark:text-teal-400"
          : "bg-white dark:bg-night-800 border-gray-200 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:border-gray-400"
      }`}
    >
      <button
        onClick={toggle}
        disabled={busy || disabled}
        title={
          disabled
            ? `${term.term} — waiting on this track's toggle`
            : isCatalog
              ? `${term.term} — from the checked-in catalog; disable instead of removing`
              : `${term.term} — click to ${term.enabled ? "disable" : "enable"}`
        }
        className={`disabled:opacity-50 ${!term.enabled ? "line-through decoration-gray-300" : ""}`}
      >
        {busy && <Loader2 className="w-3 h-3 animate-spin inline-block mr-1 align-[-1px]" />}
        {term.term}
      </button>
      {!isCatalog && (
        <button
          onClick={remove}
          disabled={busy || disabled}
          title="Remove — not in the config catalog"
          className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition disabled:opacity-50"
        >
          <X className="w-3 h-3" />
        </button>
      )}
      {error && (
        <span className="absolute top-full left-0 mt-1 z-10 text-[10px] text-red-600 dark:text-red-400 bg-white dark:bg-night-900 border border-red-200 dark:border-red-500/30 rounded px-1.5 py-0.5 whitespace-nowrap shadow-sm">
          {error}
        </span>
      )}
    </span>
  )
}

function AddTrackForm({
  catalog,
  onSubmit,
}: {
  catalog: SignalsConfig["catalog"]
  onSubmit: (
    serviceLine: string,
    keywords: string[],
  ) => Promise<AddDimensionResult>
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
    setMsg(
      res.ok
        ? { ok: true, text: `Added ${res.inserted} keyword${res.inserted === 1 ? "" : "s"}.` }
        : { ok: false, text: res.error },
    )
  }

  return (
    <FormShell>
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
        disabled={!name.trim() || !kwText.trim()}
        onSubmit={submit}
        msg={msg}
      />
    </FormShell>
  )
}

function AddKeywordForm({
  onSubmit,
}: {
  onSubmit: (term: string) => Promise<AddDimensionResult>
}) {
  const [term, setTerm] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit() {
    setBusy(true)
    setMsg(null)
    const res = await onSubmit(term.trim())
    setBusy(false)
    setMsg(
      res.ok
        ? { ok: true, text: `Added ${res.inserted} keyword${res.inserted === 1 ? "" : "s"}.` }
        : { ok: false, text: res.error },
    )
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

/** Enable/disable every term in a track in one PATCH, splicing the rows it
 *  answers with. Same shape as the state-level location toggle — see `setAll`
 *  for why a short answer falls back to the full re-read. */
function TrackToggle({
  terms,
  state,
  group,
  edits,
}: {
  terms: SearchTerm[]
  state: ToggleState
  group: GroupEdits
  edits: ConfigEdits
}) {
  async function toggle() {
    // Unchanged from when this read a bare boolean: anything short of fully
    // off turns the track off, so a mixed track disables rather than fills in.
    const next = state === "off"
    const ids = terms.filter((t) => t.enabled !== next).map((t) => t.id)
    if (ids.length === 0) return
    await group.runBulk(async () => {
      const rows = await setTermsEnabledBulk(ids, next)
      if (rows && rows.length === ids.length) edits.spliceTerms(rows)
      else await edits.refresh()
    })
  }
  return (
    <Toggle
      state={state}
      busy={group.bulkBusy}
      disabled={group.bulkDisabled}
      onClick={toggle}
    />
  )
}

// ---------------------------------------------------------------------------
// Pinned cells (overrides) — display + unpin only. Pins are created elsewhere
// (per-cell pinning UI is a later increment; this page only lists and removes
// existing ones).
// ---------------------------------------------------------------------------

function OverridesPanel({
  overrides,
  terms,
  locations,
  edits,
}: {
  overrides: TargetOverride[]
  terms: SearchTerm[]
  locations: SearchLocation[]
  edits: ConfigEdits
}) {
  // Hooks run before the early return (Rules of Hooks) — cheap even when
  // there is nothing to render, since `overrides` is a handful of rows.
  const termById = useMemoMap(terms, (t) => t.id)
  const locationById = useMemoMap(locations, (l) => l.id)

  if (overrides.length === 0) return null

  return (
    <Panel
      icon={<Pin className="w-4 h-4" />}
      title="Pinned cells"
      subtitle="Hand-pinned exceptions to a term's or location's own on/off state"
    >
      <ul className="divide-y divide-gray-100 dark:divide-white/5">
        {overrides.map((o) => (
          <OverrideRow
            key={`${o.term_id}-${o.location_id}`}
            override={o}
            term={termById.get(o.term_id)}
            location={locationById.get(o.location_id)}
            edits={edits}
          />
        ))}
      </ul>
    </Panel>
  )
}

function OverrideRow({
  override,
  term,
  location,
  edits,
}: {
  override: TargetOverride
  term?: SearchTerm
  location?: SearchLocation
  edits: ConfigEdits
}) {
  const [busy, setBusy] = useState(false)

  // A successful unpin returns `override: null` — splice that (drop the row)
  // rather than re-reading the config. `setOverride` reports success
  // separately from the row precisely so this can't confuse "unpinned" with
  // "the request failed"; a failure falls back to a re-read.
  async function unpin() {
    setBusy(true)
    const res = await setOverride(override.term_id, override.location_id, null)
    if (res.ok) edits.spliceOverride(override.term_id, override.location_id, res.override)
    else await edits.refresh()
    setBusy(false)
  }

  return (
    <li className="py-2 flex items-center justify-between gap-3 text-xs">
      <span className="text-gray-700 dark:text-[#d9d9d9] truncate">
        {term?.term ?? `term #${override.term_id}`}
        <span className="text-gray-400 dark:text-gray-500 mx-1">×</span>
        {location?.location ?? `location #${override.location_id}`}
        <span
          className={`ml-2 text-[10px] uppercase tracking-wide ${
            override.enabled
              ? "text-teal-700 dark:text-teal-400"
              : "text-gray-400 dark:text-gray-500"
          }`}
        >
          forced {override.enabled ? "on" : "off"}
        </span>
      </span>
      <button
        onClick={unpin}
        disabled={busy}
        className="text-[11px] text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition disabled:opacity-50 shrink-0"
      >
        {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : "Unpin"}
      </button>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

/** What a group of rows adds up to. `mixed` is the state a plain boolean
 *  couldn't express — some rows on, some off — which rendered as fully on. */
type ToggleState = "on" | "off" | "mixed"

/** `role="checkbox"` rather than `switch`: ARIA only allows `aria-checked`
 *  to be `mixed` on a checkbox, and mixed is exactly the state this has to
 *  announce. Visually it stays a switch — the knob parks half-way with a dash
 *  through it over a faded track, so a partly-enabled group reads as partial
 *  at a glance, not as on. */
function Toggle({
  state,
  busy,
  disabled,
  onClick,
}: {
  state: ToggleState
  busy: boolean
  disabled: boolean
  onClick: () => void
}) {
  const on = state === "on"
  const mixed = state === "mixed"
  return (
    <button
      onClick={onClick}
      disabled={busy || disabled}
      role="checkbox"
      aria-checked={mixed ? "mixed" : on}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition disabled:opacity-50 ${
        on
          ? "bg-teal-600"
          : mixed
            ? "bg-teal-600/40"
            : "bg-gray-300 dark:bg-white/15"
      }`}
      title={
        mixed
          ? "Some keywords on — click to disable all"
          : on
            ? "Enabled — click to disable"
            : "Disabled — click to enable"
      }
    >
      {busy ? (
        <Loader2 className="w-3 h-3 animate-spin text-white mx-auto" />
      ) : (
        <span
          className={`inline-flex h-4 w-4 items-center justify-center transform rounded-full bg-white transition ${
            on ? "translate-x-4" : mixed ? "translate-x-2" : "translate-x-0.5"
          }`}
        >
          {mixed && <span className="block h-0.5 w-2 rounded-full bg-teal-600" />}
        </span>
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

/** A `Map` keyed by `key(item)`, recomputed only when `items` changes —
 *  avoids an O(n) `.find()` per row when rendering the overrides list. */
function useMemoMap<T, K>(items: T[], key: (t: T) => K): Map<K, T> {
  return useMemo(() => new Map(items.map((item) => [key(item), item])), [items, key])
}

/** The in-flight edits of one group — a state and its location chips, or a
 *  track and its keyword pills. */
interface GroupEdits {
  /** True while the group's own bulk PATCH is in flight; drives its spinner. */
  bulkBusy: boolean
  /** Bulk controls are dead while any single row in the group is saving. */
  bulkDisabled: boolean
  /** Row controls are dead while the group's bulk PATCH is in flight. */
  rowsDisabled: boolean
  runBulk: (fn: () => Promise<void>) => Promise<void>
  runRow: (fn: () => Promise<void>) => Promise<void>
}

/**
 * Keeps a group's bulk toggle and its individual chips from racing.
 *
 * "Enable all" and a chip in the same state (or a track toggle and one of its
 * keywords) write overlapping rows, and both splice their own response into
 * local state — so clicking a chip while the bulk PATCH was still in flight
 * left whichever answer landed last silently winning, with the panel showing a
 * state neither request had actually produced. Each side now disables while
 * the *other* is pending. Two chips still don't block each other: they touch
 * different rows, so there is nothing to race.
 *
 * The row side counts rather than flags because a chip can vanish mid-request
 * — `remove()` deletes its row and re-reads the config — and the count lives
 * up here in the group, so the `finally` still lands after the chip unmounts
 * and the bulk buttons can't stick disabled.
 */
function useGroupEdits(): GroupEdits {
  const [bulkBusy, setBulkBusy] = useState(false)
  const [rowsPending, setRowsPending] = useState(0)

  const runBulk = useCallback(async (fn: () => Promise<void>) => {
    setBulkBusy(true)
    try {
      await fn()
    } finally {
      setBulkBusy(false)
    }
  }, [])

  const runRow = useCallback(async (fn: () => Promise<void>) => {
    setRowsPending((n) => n + 1)
    try {
      await fn()
    } finally {
      setRowsPending((n) => n - 1)
    }
  }, [])

  return {
    bulkBusy,
    bulkDisabled: bulkBusy || rowsPending > 0,
    rowsDisabled: bulkBusy,
    runBulk,
    runRow,
  }
}
