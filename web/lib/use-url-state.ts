"use client"

import { useCallback, useMemo } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"

export interface FilterState {
  q: string
  search: string
  cat: string
  vertical: string
  geo: string                  // "" | "US" | "UK" | "<state code>"
  tier: string                 // "" | A | B | C | D
  status: string               // "" | pipeline status
  rating: number
  minIcp: number
  maxIcp: number
  tags: string[]
  enriched: "" | "yes" | "no"
  owner: string
  sort: string                 // lead_score | rating | review_count | last_touched | name | country | vertical
  dir: "asc" | "desc"
  sel: string
}

export const EMPTY_FILTERS: FilterState = {
  q: "",
  search: "",
  cat: "",
  vertical: "",
  geo: "",
  tier: "",
  status: "",
  rating: 0,
  minIcp: 0,
  maxIcp: 100,
  tags: [],
  enriched: "",
  owner: "",
  sort: "lead_score",
  dir: "desc",
  sel: "",
}

export function useUrlState(): [FilterState, (next: Partial<FilterState>) => void] {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const state = useMemo<FilterState>(
    () => ({
      q: params.get("q") ?? "",
      search: params.get("search") ?? "",
      cat: params.get("cat") ?? "",
      vertical: params.get("vertical") ?? "",
      geo: params.get("geo") ?? "",
      tier: params.get("tier") ?? "",
      status: params.get("status") ?? "",
      rating: Number(params.get("rating") ?? 0),
      minIcp: Number(params.get("minIcp") ?? 0),
      maxIcp: Number(params.get("maxIcp") ?? 100),
      tags: (params.get("tags") ?? "").split(",").filter(Boolean),
      enriched: (params.get("enriched") as "" | "yes" | "no") ?? "",
      owner: params.get("owner") ?? "",
      sort: params.get("sort") ?? "lead_score",
      dir: (params.get("dir") as "asc" | "desc") ?? "desc",
      sel: params.get("sel") ?? "",
    }),
    [params],
  )

  const update = useCallback(
    (next: Partial<FilterState>) => {
      const merged = { ...state, ...next }
      const sp = new URLSearchParams()
      if (merged.q) sp.set("q", merged.q)
      if (merged.search) sp.set("search", merged.search)
      if (merged.cat) sp.set("cat", merged.cat)
      if (merged.vertical) sp.set("vertical", merged.vertical)
      if (merged.geo) sp.set("geo", merged.geo)
      if (merged.tier) sp.set("tier", merged.tier)
      if (merged.status) sp.set("status", merged.status)
      if (merged.rating) sp.set("rating", String(merged.rating))
      if (merged.minIcp) sp.set("minIcp", String(merged.minIcp))
      if (merged.maxIcp < 100) sp.set("maxIcp", String(merged.maxIcp))
      if (merged.tags.length > 0) sp.set("tags", merged.tags.join(","))
      if (merged.enriched) sp.set("enriched", merged.enriched)
      if (merged.owner) sp.set("owner", merged.owner)
      if (merged.sort && merged.sort !== "lead_score") sp.set("sort", merged.sort)
      if (merged.dir && merged.dir !== "desc") sp.set("dir", merged.dir)
      if (merged.sel) sp.set("sel", merged.sel)
      const qs = sp.toString()
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
    },
    [state, pathname, router],
  )

  return [state, update]
}
