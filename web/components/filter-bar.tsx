"use client"

import { Search, ArrowUp, ArrowDown } from "lucide-react"
import TagsFilter from "./tags-filter"
import OwnerFilter from "./owner-filter"
import { ALL_STATUSES } from "./status-badge"
import { STATE_LABELS } from "@/lib/bulk-scan"
import type { User } from "@/lib/types"

interface FilterBarProps {
  search: string
  onSearchChange: (s: string) => void
  category: string
  onCategoryChange: (cat: string) => void
  vertical: string
  onVerticalChange: (v: string) => void
  geo: string
  onGeoChange: (v: string) => void
  tier: string
  onTierChange: (v: string) => void
  status: string
  onStatusChange: (v: string) => void
  minRating: number
  onMinRatingChange: (r: number) => void
  minIcp: number
  onMinIcpChange: (v: number) => void
  maxIcp: number
  onMaxIcpChange: (v: number) => void
  tags: string[]
  onTagsChange: (tags: string[]) => void
  enriched: "" | "yes" | "no"
  onEnrichedChange: (v: "" | "yes" | "no") => void
  owner: string
  onOwnerChange: (uid: string) => void
  sort: string
  onSortChange: (v: string) => void
  dir: "asc" | "desc"
  onDirChange: (v: "asc" | "desc") => void
  currentUser: User | null
}

const CATEGORIES = [
  { value: "", label: "All categories" },
  { value: "dental", label: "Dental" },
  { value: "chiropractic", label: "Chiropractic" },
  { value: "urgent_care", label: "Urgent Care" },
  { value: "mental_health", label: "Mental Health" },
  { value: "primary_care", label: "Primary Care" },
  { value: "alf_nh", label: "Assisted Living / Nursing" },
  { value: "hotel_resort", label: "Hotels / Resorts" },
  { value: "medspa_wellness", label: "MedSpa / Wellness" },
  { value: "fast_food", label: "Fast food / QSR" },
  { value: "specialty", label: "Specialty" },
]

const VERTICALS = [
  { value: "", label: "All verticals" },
  { value: "medical", label: "Medical" },
  { value: "dental", label: "Dental" },
  { value: "mental_health", label: "Mental Health" },
  { value: "alf_nh", label: "Assisted Living / Nursing" },
  { value: "hotel_resort", label: "Hotels / Resorts" },
  { value: "medspa_wellness", label: "MedSpa / Wellness" },
  { value: "other", label: "Other" },
]

const TIERS = ["A", "B", "C", "D"]

const SORT_OPTIONS = [
  { value: "lead_score", label: "Lead score" },
  { value: "rating", label: "Rating" },
  { value: "review_count", label: "Reviews" },
  { value: "last_touched", label: "Recently touched" },
  { value: "name", label: "Name" },
  { value: "country", label: "Country" },
  { value: "vertical", label: "Vertical" },
]

// Geography options: "All", "United States (all)", then every state/region the
// bulk scanner knows about (UK is a StateCode whose value we pass through as-is).
const GEO_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All regions" },
  { value: "US", label: "United States (all)" },
  ...Object.entries(STATE_LABELS).map(([code, label]) => ({
    value: code,
    label,
  })),
]

const selectClass =
  "w-full text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-2 text-gray-700 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-teal-500/30"

export default function FilterBar(p: FilterBarProps) {
  return (
    <div className="flex flex-col gap-3 px-5 py-3 border-b border-gray-200/50 dark:border-white/10">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 dark:text-gray-500" />
        <input
          type="search"
          placeholder="Search name, address, doctor…"
          value={p.search}
          onChange={(e) => p.onSearchChange(e.target.value)}
          className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-200 bg-white/80 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>

      {/* Sort */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500 dark:text-gray-400 shrink-0">Sort by</span>
        <select
          value={p.sort}
          onChange={(e) => p.onSortChange(e.target.value)}
          className={`${selectClass} flex-1`}
        >
          {SORT_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => p.onDirChange(p.dir === "asc" ? "desc" : "asc")}
          title={p.dir === "asc" ? "Ascending" : "Descending"}
          className="shrink-0 w-9 h-9 grid place-items-center rounded-lg border border-gray-200 bg-white/80 text-gray-600 hover:bg-gray-50 dark:bg-white/5 dark:border-white/10 dark:text-[#d9d9d9] dark:hover:bg-white/10 transition"
        >
          {p.dir === "asc" ? (
            <ArrowUp className="w-4 h-4" />
          ) : (
            <ArrowDown className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Filter grid */}
      <div className="grid grid-cols-2 gap-2">
        <select
          value={p.category}
          onChange={(e) => p.onCategoryChange(e.target.value)}
          className={selectClass}
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <select
          value={p.vertical}
          onChange={(e) => p.onVerticalChange(e.target.value)}
          className={selectClass}
        >
          {VERTICALS.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
        </select>
        <select
          value={p.geo}
          onChange={(e) => p.onGeoChange(e.target.value)}
          className={selectClass}
        >
          {GEO_OPTIONS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
        <select
          value={p.tier}
          onChange={(e) => p.onTierChange(e.target.value)}
          className={selectClass}
        >
          <option value="">Any tier</option>
          {TIERS.map((t) => (
            <option key={t} value={t}>
              Tier {t}
            </option>
          ))}
        </select>
        <select
          value={p.status}
          onChange={(e) => p.onStatusChange(e.target.value)}
          className={selectClass}
        >
          <option value="">Any status</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <TagsFilter selected={p.tags} onChange={p.onTagsChange} />
        <select
          value={p.enriched}
          onChange={(e) => p.onEnrichedChange(e.target.value as "" | "yes" | "no")}
          className={selectClass}
        >
          <option value="">Any enrichment</option>
          <option value="yes">Enriched</option>
          <option value="no">Not enriched</option>
        </select>
        <OwnerFilter
          selected={p.owner}
          onChange={p.onOwnerChange}
          currentUser={p.currentUser}
        />
      </div>

      {/* Sliders */}
      <div className="flex flex-col gap-2 pt-1">
        <div className="flex items-center gap-3 text-sm text-gray-600 dark:text-[#d9d9d9]">
          <span className="w-16 shrink-0">Min rating</span>
          <input
            type="range"
            min={0}
            max={5}
            step={0.5}
            value={p.minRating}
            onChange={(e) => p.onMinRatingChange(Number(e.target.value))}
            className="flex-1 accent-teal-600"
          />
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 text-right">
            {p.minRating || "Any"}
          </span>
        </div>
        <div className="flex items-center gap-3 text-sm text-gray-600 dark:text-[#d9d9d9]">
          <span className="w-16 shrink-0">ICP score</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={p.minIcp}
            onChange={(e) =>
              p.onMinIcpChange(Math.min(Number(e.target.value), p.maxIcp))
            }
            className="flex-1 accent-teal-600"
          />
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={p.maxIcp}
            onChange={(e) =>
              p.onMaxIcpChange(Math.max(Number(e.target.value), p.minIcp))
            }
            className="flex-1 accent-teal-600"
          />
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-14 text-right">
            {p.minIcp || 0} – {p.maxIcp}
          </span>
        </div>
      </div>
    </div>
  )
}
