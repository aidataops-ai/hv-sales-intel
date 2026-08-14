"use client"

import { useCallback, useMemo } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { EMPTY_LEAD_FILTERS, type LeadFilters } from "@/lib/leads"

export interface SignalsState extends LeadFilters {
  sort: string                 // band | posted | employer | role | city | track | confidence | disposition
  dir: "asc" | "desc"
  page: number
}

export const EMPTY_SIGNALS_STATE: SignalsState = {
  ...EMPTY_LEAD_FILTERS,
  sort: "band",
  dir: "asc",
  page: 1,
}

/**
 * Signals filter state, held in the query string.
 *
 * Three things depend on it living in the URL rather than in component state:
 * a filtered view is shareable, it survives a refresh, and the CSV export can
 * reuse it verbatim so the download matches what is on screen.
 *
 * Defaults are omitted from the URL so a clean view has a clean address bar.
 */
export function useSignalsUrlState(): [
  SignalsState,
  (next: Partial<SignalsState>) => void,
] {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const state = useMemo<SignalsState>(
    () => ({
      cities: (params.get("cities") ?? "").split(",").filter(Boolean),
      tracks: (params.get("tracks") ?? "").split(",").filter(Boolean),
      states: (params.get("states") ?? "").split(",").filter(Boolean),
      band: params.get("band") ?? "",
      decision: params.get("decision") ?? "",
      work_mode: params.get("work_mode") ?? "",
      source: params.get("source") ?? "",
      salary: params.get("salary") ?? "",
      practice: params.get("practice") ?? "",
      search: params.get("search") ?? "",
      sort: params.get("sort") ?? "band",
      dir: (params.get("dir") as "asc" | "desc") ?? "asc",
      page: Math.max(1, Number(params.get("page") ?? 1)),
    }),
    [params],
  )

  const update = useCallback(
    (next: Partial<SignalsState>) => {
      const merged = { ...state, ...next }
      // Any filter change invalidates the page number — page 7 of a
      // now-3-page result set renders an empty table that looks like a bug.
      const changedFilter = Object.keys(next).some(
        (key) => key !== "page" && key !== "sort" && key !== "dir",
      )
      if (changedFilter && next.page === undefined) merged.page = 1

      const sp = new URLSearchParams()
      if (merged.cities.length) sp.set("cities", merged.cities.join(","))
      if (merged.tracks.length) sp.set("tracks", merged.tracks.join(","))
      if (merged.states.length) sp.set("states", merged.states.join(","))
      if (merged.band) sp.set("band", merged.band)
      if (merged.decision) sp.set("decision", merged.decision)
      if (merged.work_mode) sp.set("work_mode", merged.work_mode)
      if (merged.source) sp.set("source", merged.source)
      if (merged.salary) sp.set("salary", merged.salary)
      if (merged.practice) sp.set("practice", merged.practice)
      if (merged.search) sp.set("search", merged.search)
      if (merged.sort !== "band") sp.set("sort", merged.sort)
      if (merged.dir !== "asc") sp.set("dir", merged.dir)
      if (merged.page > 1) sp.set("page", String(merged.page))

      const qs = sp.toString()
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
    },
    [state, pathname, router],
  )

  return [state, update]
}
