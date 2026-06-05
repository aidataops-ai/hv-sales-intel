"use client"

import { Suspense, useState, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import type { Practice } from "@/lib/types"
import TopBar from "@/components/top-bar"
import PracticeCard from "@/components/practice-card"
import FilterBar from "@/components/filter-bar"
import Pagination from "@/components/pagination"
import {
  searchPractices,
  analyzePractice,
  listPractices,
  type ListParams,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useUrlState, EMPTY_FILTERS } from "@/lib/use-url-state"

const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

const PAGE_SIZE = 10
// The map plots every matching lead (clustered); the sidebar paginates. This
// caps the map fetch so a huge result set can't re-introduce a heavy payload.
const MAP_POINT_CAP = 500

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

  // Visible page of rows for the sidebar. Starts empty — real data only.
  const [practices, setPractices] = useState<Practice[]>([])
  // All Google-Places results when in "places" mode (paginated client-side).
  const [allResults, setAllResults] = useState<Practice[]>([])
  // Every matching lead's point, for the clustered map.
  const [mapPoints, setMapPoints] = useState<Practice[]>([])
  const [total, setTotal] = useState<number>(0)
  const [page, setPage] = useState(1)
  // "db" = server-paginated list; "places" = Google Places scan results.
  const [mode, setMode] = useState<"db" | "places">("db")

  const [isLoading, setIsLoading] = useState(false)
  const [isRescanning, setIsRescanning] = useState(false)
  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set())
  const [scoreProgress, setScoreProgress] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)

  // Monotonic request id: any reload/search bumps it so a stale in-flight
  // response resolves into a no-op instead of corrupting state.
  const reqIdRef = useRef(0)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const buildParams = useCallback(
    (offset: number, limit: number): ListParams => ({
      search: filters.search || undefined,
      category: filters.cat || undefined,
      vertical: filters.vertical || undefined,
      geo: filters.geo || undefined,
      tier: filters.tier || undefined,
      status: filters.status || undefined,
      min_rating: filters.rating || undefined,
      min_score: filters.minIcp || undefined,
      max_score: filters.maxIcp,
      enriched: filters.enriched || undefined,
      owner: filters.owner || undefined,
      tags: filters.tags,
      sort: filters.sort,
      dir: filters.dir,
      offset,
      limit,
    }),
    [filters],
  )

  // Full reload (page 1 + all map points) for the server-backed list view.
  const reload = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setMode("db")
    setIsLoading(true)
    try {
      const [list, points] = await Promise.all([
        listPractices(buildParams(0, PAGE_SIZE)),
        listPractices(buildParams(0, MAP_POINT_CAP)),
      ])
      if (reqId !== reqIdRef.current) return
      setAllResults([])
      setPractices(list.practices)
      setTotal(list.total)
      setPage(1)
      setMapPoints(points.practices)
    } finally {
      if (reqId === reqIdRef.current) setIsLoading(false)
    }
  }, [buildParams])

  // Reload page 1 + map whenever a server-affecting filter/sort changes
  // (debounced so dragging a slider doesn't fire a request per tick).
  const filterKey = JSON.stringify({
    search: filters.search,
    cat: filters.cat,
    vertical: filters.vertical,
    geo: filters.geo,
    tier: filters.tier,
    status: filters.status,
    rating: filters.rating,
    minIcp: filters.minIcp,
    maxIcp: filters.maxIcp,
    tags: filters.tags,
    enriched: filters.enriched,
    owner: filters.owner,
    sort: filters.sort,
    dir: filters.dir,
    reloadNonce,
  })
  const firstRunRef = useRef(true)
  useEffect(() => {
    const delay = firstRunRef.current ? 0 : 300
    firstRunRef.current = false
    const timer = setTimeout(() => {
      reload()
    }, delay)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  const goToPage = useCallback(
    async (target: number) => {
      const clamped = Math.max(1, Math.min(target, totalPages))
      sidebarRef.current?.scrollTo({ top: 0 })
      if (mode === "places") {
        setPage(clamped)
        setPractices(
          allResults.slice((clamped - 1) * PAGE_SIZE, clamped * PAGE_SIZE),
        )
        return
      }
      const reqId = ++reqIdRef.current
      setIsLoading(true)
      try {
        const res = await listPractices(
          buildParams((clamped - 1) * PAGE_SIZE, PAGE_SIZE),
        )
        if (reqId !== reqIdRef.current) return
        setPractices(res.practices)
        setTotal(res.total)
        setPage(clamped)
      } finally {
        if (reqId === reqIdRef.current) setIsLoading(false)
      }
    },
    [mode, allResults, totalPages, buildParams],
  )

  // Patch a single lead everywhere it appears (visible page, places set, map).
  const patchPractice = useCallback((placeId: string, patch: Partial<Practice>) => {
    const merge = (list: Practice[]) =>
      list.map((p) => (p.place_id === placeId ? { ...p, ...patch } : p))
    setPractices(merge)
    setAllResults(merge)
    setMapPoints(merge)
  }, [])

  const runPlacesSearch = useCallback(
    async (query: string, refresh: boolean) => {
      const reqId = ++reqIdRef.current
      setMode("places")
      if (refresh) setIsRescanning(true)
      else setIsLoading(true)
      try {
        const results = await searchPractices(query, refresh)
        if (reqId !== reqIdRef.current) return
        setAllResults(results)
        setMapPoints(results)
        setTotal(results.length)
        setPage(1)
        setPractices(results.slice(0, PAGE_SIZE))
        setFilters(refresh ? { sel: "" } : { q: query, sel: "" })
      } finally {
        if (reqId === reqIdRef.current) {
          setIsLoading(false)
          setIsRescanning(false)
        }
      }
    },
    [setFilters],
  )

  const handleSearch = useCallback(
    (query: string) => runPlacesSearch(query, false),
    [runPlacesSearch],
  )
  const handleRescan = useCallback(() => {
    if (filters.q.trim()) runPlacesSearch(filters.q, true)
  }, [filters.q, runPlacesSearch])

  // "Search this area": re-run the active query (re-scan if a city query is
  // active, otherwise refresh the DB view).
  const handleSearchArea = useCallback(() => {
    if (mode === "places" && filters.q.trim()) runPlacesSearch(filters.q, true)
    else setReloadNonce((n) => n + 1)
  }, [mode, filters.q, runPlacesSearch])

  const handleAnalyze = useCallback(
    async (placeId: string, refresh = false) => {
      setAnalyzingIds((prev) => new Set(prev).add(placeId))
      try {
        const updated = await analyzePractice(placeId, {
          force: refresh,
          rescan: refresh,
        })
        // In place — the card keeps its position until the next reload.
        patchPractice(placeId, updated)
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
    },
    [patchPractice],
  )

  // Scores the unscored leads on the current page (labelled "Score loaded").
  const handleScoreAll = useCallback(async () => {
    const unscored = practices.filter((p) => p.lead_score == null)
    if (unscored.length === 0) return
    for (let i = 0; i < unscored.length; i++) {
      setScoreProgress(`Scoring ${i + 1}/${unscored.length}...`)
      const placeId = unscored[i].place_id
      setAnalyzingIds((prev) => new Set(prev).add(placeId))
      try {
        const updated = await analyzePractice(placeId, { force: false, rescan: false })
        patchPractice(placeId, updated)
      } finally {
        setAnalyzingIds((prev) => {
          const next = new Set(prev)
          next.delete(placeId)
          return next
        })
      }
    }
    setScoreProgress(null)
    // Re-sort by the new scores (DB view only — re-scanning would be wasteful).
    if (mode === "db") setReloadNonce((n) => n + 1)
  }, [practices, mode, patchPractice])

  const refreshFromDb = useCallback(() => {
    setFilters(EMPTY_FILTERS)
    setReloadNonce((n) => n + 1)
  }, [setFilters])

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
        onBulkScanComplete={() => setReloadNonce((n) => n + 1)}
      />

      <main className="relative w-full h-full pt-14">
        <div className="absolute top-16 left-4 bottom-4 w-[420px] z-10 glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 pt-5 pb-3 border-b border-gray-200/50 dark:border-white/10 flex items-start justify-between gap-2">
            <div>
              <h2 className="font-serif text-lg font-semibold text-gray-900 dark:text-white">
                {filters.q || "All practices"}
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                {total} practice{total !== 1 ? "s" : ""}
              </p>
            </div>
            <button
              onClick={refreshFromDb}
              className="inline-flex items-center gap-1 text-sm text-teal-700 dark:text-teal-400 hover:text-teal-800 transition"
              title="Reset filters and refresh from database"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                <path d="M21 3v6h-6" />
              </svg>
              Refresh
            </button>
          </div>

          <FilterBar
            search={filters.search}
            onSearchChange={(s) => setFilters({ search: s })}
            category={filters.cat}
            onCategoryChange={(c) => setFilters({ cat: c })}
            vertical={filters.vertical}
            onVerticalChange={(v) => setFilters({ vertical: v })}
            geo={filters.geo}
            onGeoChange={(v) => setFilters({ geo: v })}
            tier={filters.tier}
            onTierChange={(v) => setFilters({ tier: v })}
            status={filters.status}
            onStatusChange={(v) => setFilters({ status: v })}
            minRating={filters.rating}
            onMinRatingChange={(r) => setFilters({ rating: r })}
            minIcp={filters.minIcp}
            onMinIcpChange={(v) => setFilters({ minIcp: v })}
            maxIcp={filters.maxIcp}
            onMaxIcpChange={(v) => setFilters({ maxIcp: v })}
            tags={filters.tags}
            onTagsChange={(t) => setFilters({ tags: t })}
            enriched={filters.enriched}
            onEnrichedChange={(v) => setFilters({ enriched: v })}
            owner={filters.owner}
            onOwnerChange={(o) => setFilters({ owner: o })}
            sort={filters.sort}
            onSortChange={(v) => setFilters({ sort: v })}
            dir={filters.dir}
            onDirChange={(v) => setFilters({ dir: v })}
            currentUser={currentUser}
          />

          <div
            ref={sidebarRef}
            className="flex-1 overflow-y-auto sidebar-scroll p-3 space-y-2"
          >
            {practices.length === 0 ? (
              <p className="text-center text-gray-400 dark:text-gray-500 py-10 text-sm">
                {isLoading ? "Loading…" : "No practices match these filters."}
              </p>
            ) : (
              practices.map((p) => (
                <PracticeCard
                  key={p.place_id}
                  practice={p}
                  isSelected={filters.sel === p.place_id}
                  onSelect={(id) => setFilters({ sel: id ?? "" })}
                  onAnalyze={handleAnalyze}
                  isAnalyzing={analyzingIds.has(p.place_id)}
                  onCallLogged={(response) => {
                    patchPractice(response.practice.place_id, response.practice)
                    if (response.sf_warning) console.warn("[SF]", response.sf_warning)
                  }}
                  onEnrichmentUpdate={(next) => patchPractice(next.place_id, next)}
                />
              ))
            )}
          </div>

          <div className="border-t border-gray-200/50 dark:border-white/10">
            <Pagination page={page} totalPages={totalPages} onChange={goToPage} />
          </div>
        </div>

        <MapView
          practices={mapPoints}
          selectedId={filters.sel || null}
          onSelect={(id) => setFilters({ sel: id ?? "" })}
          onSearchArea={handleSearchArea}
        />
      </main>
    </div>
  )
}
