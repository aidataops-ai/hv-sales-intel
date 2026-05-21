"use client"

import { Suspense, useState, useMemo, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import type { Practice } from "@/lib/types"
import { mockPractices } from "@/lib/mock-data"
import TopBar from "@/components/top-bar"
import PracticeCard from "@/components/practice-card"
import FilterBar from "@/components/filter-bar"
import { searchPractices, analyzePractice, listPractices } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useUrlState, EMPTY_FILTERS } from "@/lib/use-url-state"
import {
  readSnapshot,
  clearSnapshot,
  useSessionSnapshot,
} from "@/lib/use-session-snapshot"

const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

export default function Page() {
  return (
    <Suspense fallback={<div className="h-screen w-screen" />}>
      <PageContent />
    </Suspense>
  )
}

function PageContent() {
  const { user: currentUser } = useAuth()
  const [filters, setFilters] = useUrlState()
  const sidebarRef = useRef<HTMLDivElement>(null)

  // Hydrate practices: server uses mockPractices (so SSR + first client paint
  // match), then a client-only effect swaps in sessionStorage snapshot if present.
  const [practices, setPractices] = useState<Practice[]>(mockPractices)
  const [hydratedFromDb, setHydratedFromDb] = useState<boolean>(false)

  const [isLoading, setIsLoading] = useState(false)
  const [isRescanning, setIsRescanning] = useState(false)
  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set())
  const [scoreProgress, setScoreProgress] = useState<string | null>(null)

  // Snapshot of lead_scores frozen at "sort triggers" — first hydrate,
  // a fresh search, a Refresh click, or a filter change. Per-analyze
  // updates do NOT refresh the snapshot, so a card the user just
  // analyzed keeps its position in the sidebar instead of jumping to
  // the top. Sort falls back to live lead_score for practices not in
  // the snapshot yet.
  //
  // NOTE: declare captureSortScores BEFORE the effects that depend on it.
  // TypeScript / Next.js builds reject "used before declaration" even
  // though the closure works at runtime.
  const [sortScores, setSortScores] = useState<Record<string, number>>({})
  const captureSortScores = useCallback((list: Practice[]) => {
    const next: Record<string, number> = {}
    for (const p of list) next[p.place_id] = p.lead_score ?? -1
    setSortScores(next)
  }, [])

  useEffect(() => {
    const snap = readSnapshot()
    if (snap?.practices && snap.practices.length > 0) {
      setPractices(snap.practices)
      captureSortScores(snap.practices)
      setHydratedFromDb(true)
    }
  }, [captureSortScores])

  // DB hydrate when no snapshot. Always fetches the full DB on first
  // login — no owner / search filters applied at fetch time so the user
  // sees everything they have access to.
  useEffect(() => {
    if (hydratedFromDb) return
    let cancelled = false
    async function hydrate() {
      try {
        const dbRows = await listPractices({ limit: 20000 })
        if (cancelled) return
        // Always reflect what the API returned — including the empty list.
        // Without this, wiping the DB + clicking Refresh leaves stale
        // React state from the previous session in the sidebar.
        // (listPractices already falls back to mock data when the backend
        // is unreachable, so there's no risk of blanking the UI on a
        // transient network error.)
        setPractices(dbRows)
        captureSortScores(dbRows)
      } catch {
        /* keep current */
      } finally {
        if (!cancelled) setHydratedFromDb(true)
      }
    }
    hydrate()
    return () => {
      cancelled = true
    }
  }, [hydratedFromDb, captureSortScores])

  // Restore scroll once on mount if snapshot present.
  useEffect(() => {
    const snap = readSnapshot()
    if (snap?.scrollTop && sidebarRef.current) {
      sidebarRef.current.scrollTop = snap.scrollTop
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useSessionSnapshot(practices, filters, sidebarRef)

  // Re-fetch from DB whenever a filter changes (debounced 300ms).
  // The actual filtering still happens in-memory; this just guarantees
  // the source list is fresh on every filter interaction.
  const filterKey = JSON.stringify({
    cat: filters.cat,
    rating: filters.rating,
    minIcp: filters.minIcp,
    tags: filters.tags,
    enriched: filters.enriched,
    owner: filters.owner,
    search: filters.search,
  })
  useEffect(() => {
    if (!hydratedFromDb) return  // skip initial mount; main hydrate already runs
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const dbRows = await listPractices({ limit: 20000 })
        if (cancelled) return
        // Mirror the main hydrate: always reflect the API response,
        // including the empty list, so wiping the DB takes effect on
        // the next filter change without needing a hard reload.
        setPractices(dbRows)
        captureSortScores(dbRows)
      } catch {
        /* keep current */
      }
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  const handleSearch = useCallback(
    async (query: string) => {
      setIsLoading(true)
      try {
        const results = await searchPractices(query)
        setPractices(results)
        captureSortScores(results)
        setFilters({ q: query, sel: "" })
      } finally {
        setIsLoading(false)
      }
    },
    [setFilters, captureSortScores],
  )

  const handleRescan = useCallback(async () => {
    if (!filters.q.trim()) return
    setIsRescanning(true)
    try {
      const results = await searchPractices(filters.q, true)  // force fresh
      setPractices(results)
      captureSortScores(results)
      setFilters({ sel: "" })
    } finally {
      setIsRescanning(false)
    }
  }, [filters.q, setFilters, captureSortScores])

  const handleAnalyze = useCallback(async (placeId: string, refresh = false) => {
    setAnalyzingIds((prev) => new Set(prev).add(placeId))
    try {
      const updated = await analyzePractice(placeId, {
        force: refresh,
        rescan: refresh,
      })
      setPractices((prev) =>
        prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p)),
      )
      // Per-analyze updates intentionally don't refresh sortScores — the
      // card stays in its current sidebar position until the user hits
      // Refresh, changes a filter, or runs a new search.
      requestAnimationFrame(() => {
        const el = document.getElementById(`practice-card-${placeId}`)
        if (el) {
          el.classList.add("flash-just-analyzed")
          setTimeout(() => el.classList.remove("flash-just-analyzed"), 1500)
        }
      })
    } finally {
      setAnalyzingIds((prev) => {
        const next = new Set(prev)
        next.delete(placeId)
        return next
      })
    }
  }, [])

  const handleScoreAll = useCallback(async () => {
    const unscored = practices.filter((p) => p.lead_score == null)
    if (unscored.length === 0) return
    for (let i = 0; i < unscored.length; i++) {
      setScoreProgress(`Scoring ${i + 1}/${unscored.length}...`)
      const placeId = unscored[i].place_id
      setAnalyzingIds((prev) => new Set(prev).add(placeId))
      try {
        const updated = await analyzePractice(placeId, {
          force: false,
          rescan: false,
        })
        setPractices((prev) =>
          prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p)),
        )
      } finally {
        setAnalyzingIds((prev) => {
          const next = new Set(prev)
          next.delete(placeId)
          return next
        })
      }
    }
    setScoreProgress(null)
    // After the bulk Score All finishes, re-snapshot so the just-scored
    // leads float to their proper sorted positions. (Single Analyze does
    // not re-snapshot — see handleAnalyze for the rationale.)
    setPractices((prev) => {
      captureSortScores(prev)
      return prev
    })
  }, [practices, captureSortScores])

  const filtered = useMemo(() => {
    const needle = filters.search.toLowerCase()
    const list = practices.filter((p) => {
      if (needle) {
        const hay = [
          p.name,
          p.address,
          p.city,
          p.owner_name,
          p.website_doctor_name,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
        if (!hay.includes(needle)) return false
      }
      if (filters.cat && p.category !== filters.cat) return false
      if (filters.rating && (p.rating ?? 0) < filters.rating) return false
      if (filters.minIcp && (p.lead_score ?? -1) < filters.minIcp) return false
      if (filters.tags.length > 0) {
        const tags = p.tags ?? []
        if (!filters.tags.some((t) => tags.includes(t))) return false
      }
      if (filters.enriched === "yes" && p.enrichment_status !== "enriched") return false
      if (filters.enriched === "no" && p.enrichment_status === "enriched") return false
      if (filters.owner) {
        if (p.assigned_to !== filters.owner && p.last_touched_by !== filters.owner) {
          return false
        }
      }
      return true
    })
    return list.sort((a, b) => {
      // Sort by the frozen sort-snapshot, falling back to live lead_score
      // for practices that joined the list after the last snapshot. Stable
      // tiebreak on place_id so equal-score cards don't shuffle on every
      // render.
      const aScore = sortScores[a.place_id] ?? a.lead_score ?? -1
      const bScore = sortScores[b.place_id] ?? b.lead_score ?? -1
      if (bScore !== aScore) return bScore - aScore
      return a.place_id.localeCompare(b.place_id)
    })
  }, [practices, filters, sortScores])

  return (
    <div className="h-screen w-screen overflow-hidden">
      <TopBar
        onSearch={handleSearch}
        isLoading={isLoading}
        onScoreAll={handleScoreAll}
        scoreProgress={scoreProgress}
        onRescan={handleRescan}
        canRescan={!!filters.q.trim()}
        isRescanning={isRescanning}
        currentQuery={filters.q}
        onBulkScanComplete={() => {
          // Force a fresh DB pull so the newly-upserted practices appear.
          clearSnapshot()
          setHydratedFromDb(false)
        }}
      />

      <main className="relative w-full h-full pt-14">
        <div className="absolute top-16 left-4 bottom-4 w-[420px] z-10 glass-panel rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 pt-5 pb-3 border-b border-gray-200/50 flex items-start justify-between gap-2">
            <div>
              <h2 className="font-serif text-lg font-semibold text-gray-900">
                {filters.q || "All practices"}
              </h2>
              <p className="text-sm text-gray-500 mt-0.5">
                {filtered.length} practice{filtered.length !== 1 ? "s" : ""}
              </p>
            </div>
            <button
              onClick={() => {
                clearSnapshot()
                setFilters(EMPTY_FILTERS)
                setHydratedFromDb(false)
              }}
              className="text-xs text-gray-500 hover:text-teal-700 underline"
              title="Refresh from database"
            >
              Refresh
            </button>
          </div>
          <FilterBar
            search={filters.search}
            onSearchChange={(s) => setFilters({ search: s })}
            category={filters.cat}
            onCategoryChange={(c) => setFilters({ cat: c })}
            minRating={filters.rating}
            onMinRatingChange={(r) => setFilters({ rating: r })}
            minIcp={filters.minIcp}
            onMinIcpChange={(v) => setFilters({ minIcp: v })}
            tags={filters.tags}
            onTagsChange={(t) => setFilters({ tags: t })}
            enriched={filters.enriched}
            onEnrichedChange={(v) => setFilters({ enriched: v })}
            owner={filters.owner}
            onOwnerChange={(o) => setFilters({ owner: o })}
            currentUser={currentUser}
          />
          <div
            ref={sidebarRef}
            className="flex-1 overflow-y-auto sidebar-scroll p-3 space-y-2"
          >
            {filtered.length === 0 ? (
              <p className="text-center text-gray-400 py-10 text-sm">
                No practices match these filters.
              </p>
            ) : (
              filtered.map((p) => (
                <PracticeCard
                  key={p.place_id}
                  practice={p}
                  isSelected={filters.sel === p.place_id}
                  onSelect={(id) => setFilters({ sel: id ?? "" })}
                  onAnalyze={handleAnalyze}
                  isAnalyzing={analyzingIds.has(p.place_id)}
                  onCallLogged={(response) => {
                    setPractices((prev) =>
                      prev.map((x) =>
                        x.place_id === response.practice.place_id
                          ? { ...x, ...response.practice }
                          : x,
                      ),
                    )
                    if (response.sf_warning) {
                      console.warn("[SF]", response.sf_warning)
                    }
                  }}
                  onEnrichmentUpdate={(next) => {
                    setPractices((prev) =>
                      prev.map((x) =>
                        x.place_id === next.place_id ? { ...x, ...next } : x,
                      ),
                    )
                  }}
                />
              ))
            )}
          </div>
        </div>

        <MapView
          practices={filtered}
          selectedId={filters.sel || null}
          onSelect={(id) => setFilters({ sel: id ?? "" })}
        />
      </main>
    </div>
  )
}
