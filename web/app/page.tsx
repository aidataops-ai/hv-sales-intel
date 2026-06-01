"use client"

import { Suspense, useState, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import { Loader2 } from "lucide-react"
import type { Practice } from "@/lib/types"
import { mockPractices } from "@/lib/mock-data"
import TopBar from "@/components/top-bar"
import PracticeCard from "@/components/practice-card"
import FilterBar from "@/components/filter-bar"
import {
  searchPractices,
  analyzePractice,
  listPractices,
  type ListParams,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useUrlState, EMPTY_FILTERS, type FilterState } from "@/lib/use-url-state"
import {
  readSnapshot,
  clearSnapshot,
  useSessionSnapshot,
} from "@/lib/use-session-snapshot"

const MapView = dynamic(() => import("@/components/map-view"), { ssr: false })

const PAGE_SIZE = 100

// Signature of the server-affecting filters only (excludes sel/q/reloadNonce).
// Used to decide whether a saved snapshot still matches the current URL.
function serverFilterSig(f: FilterState): string {
  return JSON.stringify({
    search: f.search,
    cat: f.cat,
    vertical: f.vertical,
    geo: f.geo,
    tier: f.tier,
    status: f.status,
    rating: f.rating,
    minIcp: f.minIcp,
    maxIcp: f.maxIcp,
    tags: [...f.tags].sort(),
    enriched: f.enriched,
    owner: f.owner,
    sort: f.sort,
    dir: f.dir,
  })
}

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
  const sentinelRef = useRef<HTMLDivElement>(null)

  // First paint uses mockPractices so SSR + first client paint match; the
  // effects below immediately swap in real (server-filtered, paginated) data.
  const [practices, setPractices] = useState<Practice[]>(mockPractices)
  const [total, setTotal] = useState<number>(mockPractices.length)
  const [hasMore, setHasMore] = useState<boolean>(false)
  // "db" = server-paginated list view; "places" = Google Places search results
  // (a flat, unpaginated set that the filter controls don't drive).
  const [mode, setMode] = useState<"db" | "places">("db")

  const [isLoading, setIsLoading] = useState(false) // full reload (page 0)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [isRescanning, setIsRescanning] = useState(false)
  const [analyzingIds, setAnalyzingIds] = useState<Set<string>>(new Set())
  const [scoreProgress, setScoreProgress] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)

  // How many rows we've loaded so far (next page's offset).
  const offsetRef = useRef(0)
  // Monotonic request id: a page-0 reload bumps it so any in-flight "load more"
  // or stale Places response resolves into a no-op instead of corrupting state.
  const reqIdRef = useRef(0)
  // Synchronous re-entrancy lock for loadMore. The isLoadingMore *state* lags a
  // render behind, so two IntersectionObserver fires can both pass a state-based
  // guard and double-append the same page; a ref closes that window.
  const loadingMoreRef = useRef(false)

  const buildParams = useCallback(
    (offset: number): ListParams => ({
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
      limit: PAGE_SIZE,
    }),
    [filters],
  )

  const loadFirstPage = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setMode("db")
    setIsLoading(true)
    try {
      const res = await listPractices(buildParams(0))
      if (reqId !== reqIdRef.current) return
      setPractices(res.practices)
      setTotal(res.total)
      setHasMore(res.hasMore)
      offsetRef.current = res.practices.length
    } finally {
      if (reqId === reqIdRef.current) setIsLoading(false)
    }
  }, [buildParams])

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current) return // synchronous lock — beats the state lag
    if (mode !== "db" || isLoading || !hasMore) return
    loadingMoreRef.current = true
    const reqId = reqIdRef.current
    setIsLoadingMore(true)
    try {
      const res = await listPractices(buildParams(offsetRef.current))
      // A page-0 reload happened mid-flight → discard this stale append.
      if (reqId !== reqIdRef.current) return
      setPractices((prev) => [...prev, ...res.practices])
      setTotal(res.total)
      setHasMore(res.hasMore)
      offsetRef.current += res.practices.length
    } finally {
      // Always release — even when superseded — or infinite scroll dies.
      loadingMoreRef.current = false
      setIsLoadingMore(false)
    }
  }, [mode, isLoading, hasMore, buildParams])

  // Load page 0 on mount and whenever a server-affecting filter/sort changes.
  // Debounced so dragging a slider doesn't fire a request per tick. On the very
  // first run we restore a sessionStorage snapshot instead, if present.
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
    const timer = setTimeout(() => {
      if (firstRunRef.current) {
        firstRunRef.current = false
        const snap = readSnapshot()
        // Only trust the snapshot when its server-affecting filters still match
        // the current URL — otherwise the restored rows would contradict the
        // filter controls (which are driven by the URL). On mismatch, fall
        // through to a correct server fetch.
        if (
          snap?.practices &&
          snap.practices.length > 0 &&
          snap.filters &&
          serverFilterSig(snap.filters) === serverFilterSig(filters)
        ) {
          setPractices(snap.practices)
          const tot = snap.total ?? snap.practices.length
          setTotal(tot)
          offsetRef.current = snap.practices.length
          setHasMore(tot > snap.practices.length)
          setMode("db")
          // Restore scroll only after the (taller) restored list has painted;
          // setting it now would clamp against the short initial mock list.
          if (snap.scrollTop) {
            requestAnimationFrame(() =>
              requestAnimationFrame(() => {
                if (sidebarRef.current) sidebarRef.current.scrollTop = snap.scrollTop
              }),
            )
          }
          return
        }
      }
      loadFirstPage()
    }, delay)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  useSessionSnapshot(practices, filters, total, sidebarRef)

  // Infinite scroll: load the next page when the sentinel scrolls into view.
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore()
      },
      { root: sidebarRef.current, rootMargin: "300px" },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [loadMore])

  const handleSearch = useCallback(
    async (query: string) => {
      const reqId = ++reqIdRef.current
      setMode("places")
      setIsLoading(true)
      try {
        const results = await searchPractices(query)
        if (reqId !== reqIdRef.current) return
        setPractices(results)
        setTotal(results.length)
        setHasMore(false)
        offsetRef.current = results.length
        setFilters({ q: query, sel: "" })
      } finally {
        if (reqId === reqIdRef.current) setIsLoading(false)
      }
    },
    [setFilters],
  )

  const handleRescan = useCallback(async () => {
    if (!filters.q.trim()) return
    const reqId = ++reqIdRef.current
    setMode("places")
    setIsRescanning(true)
    try {
      const results = await searchPractices(filters.q, true) // force fresh
      if (reqId !== reqIdRef.current) return
      setPractices(results)
      setTotal(results.length)
      setHasMore(false)
      offsetRef.current = results.length
      setFilters({ sel: "" })
    } finally {
      if (reqId === reqIdRef.current) setIsRescanning(false)
    }
  }, [filters.q, setFilters])

  const handleAnalyze = useCallback(async (placeId: string, refresh = false) => {
    setAnalyzingIds((prev) => new Set(prev).add(placeId))
    try {
      const updated = await analyzePractice(placeId, {
        force: refresh,
        rescan: refresh,
      })
      // Update the card in place. With server-side sort, the card keeps its
      // position until the next reload (filter/sort change or Refresh), so a
      // freshly analyzed lead doesn't jump to the top mid-review.
      setPractices((prev) =>
        prev.map((p) => (p.place_id === placeId ? { ...p, ...updated } : p)),
      )
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

  // Scores the unscored leads currently loaded in the sidebar (the button is
  // labelled "Score loaded" to be honest about that scope under pagination).
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
    // Re-pull page 0 so the server re-sorts with the freshly-computed scores and
    // the just-scored leads move into their ranked positions (single Analyze
    // intentionally keeps its position; the bulk run re-sorts, matching the old
    // behavior).
    clearSnapshot()
    setReloadNonce((n) => n + 1)
  }, [practices])

  // Refresh: drop the snapshot, reset every filter, and force a page-0 reload
  // (the nonce guarantees a reload even if the filters were already empty).
  const refreshFromDb = useCallback(() => {
    clearSnapshot()
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
        onBulkScanComplete={() => {
          // Force a fresh DB pull so the newly-upserted practices appear.
          clearSnapshot()
          setReloadNonce((n) => n + 1)
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
                {total} practice{total !== 1 ? "s" : ""}
                {practices.length < total && (
                  <span className="text-gray-400"> · showing {practices.length}</span>
                )}
              </p>
            </div>
            <button
              onClick={refreshFromDb}
              className="text-xs text-gray-500 hover:text-teal-700 underline"
              title="Reset filters and refresh from database"
            >
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
              <p className="text-center text-gray-400 py-10 text-sm">
                {isLoading ? "Loading…" : "No practices match these filters."}
              </p>
            ) : (
              <>
                {practices.map((p) => (
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
                ))}
                {/* Infinite-scroll sentinel + spinner */}
                {mode === "db" && hasMore && (
                  <div
                    ref={sentinelRef}
                    className="flex items-center justify-center py-4 text-gray-400"
                  >
                    {isLoadingMore && <Loader2 className="w-4 h-4 animate-spin" />}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <MapView
          practices={practices}
          selectedId={filters.sel || null}
          onSelect={(id) => setFilters({ sel: id ?? "" })}
        />
      </main>
    </div>
  )
}
