"use client"

import { Search } from "lucide-react"
import TagsFilter from "./tags-filter"
import OwnerFilter from "./owner-filter"
import type { User } from "@/lib/types"

interface FilterBarProps {
  search: string
  onSearchChange: (s: string) => void
  category: string
  onCategoryChange: (cat: string) => void
  minRating: number
  onMinRatingChange: (r: number) => void
  minIcp: number
  onMinIcpChange: (v: number) => void
  tags: string[]
  onTagsChange: (tags: string[]) => void
  enriched: "" | "yes" | "no"
  onEnrichedChange: (v: "" | "yes" | "no") => void
  owner: string
  onOwnerChange: (uid: string) => void
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
  { value: "specialty", label: "Specialty" },
]

export default function FilterBar(p: FilterBarProps) {
  return (
    <div className="flex flex-col gap-2 px-5 py-3 border-b border-gray-200/50">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="search"
          placeholder="Search name, address, doctor…"
          value={p.search}
          onChange={(e) => p.onSearchChange(e.target.value)}
          className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-white/80 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={p.category}
          onChange={(e) => p.onCategoryChange(e.target.value)}
          className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <TagsFilter selected={p.tags} onChange={p.onTagsChange} />
        <select
          value={p.enriched}
          onChange={(e) => p.onEnrichedChange(e.target.value as "" | "yes" | "no")}
          className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
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
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          Min rating
          <input
            type="range"
            min={0}
            max={5}
            step={0.5}
            value={p.minRating}
            onChange={(e) => p.onMinRatingChange(Number(e.target.value))}
            className="w-20 accent-teal-600"
          />
          <span className="text-xs font-medium w-6">{p.minRating || "Any"}</span>
        </label>
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          Min ICP
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={p.minIcp}
            onChange={(e) => p.onMinIcpChange(Number(e.target.value))}
            className="w-24 accent-teal-600"
          />
          <span className="text-xs font-medium w-8">{p.minIcp || "Any"}</span>
        </label>
      </div>
    </div>
  )
}
