"use client"

import { Search, X } from "lucide-react"

import MultiSelect from "./multi-select"
import {
  ALL_SOURCES, ALL_WORK_MODES,
  formatWorkMode, stateLabel, type FilterOptions,
} from "@/lib/leads"
import type { SignalsState } from "@/lib/use-signals-url-state"

interface Props {
  state: SignalsState
  onChange: (next: Partial<SignalsState>) => void
  options: FilterOptions
  onReset: () => void
}

const select =
  "w-full text-xs rounded-md border border-gray-200 dark:border-white/10 " +
  "bg-transparent dark:bg-white/5 dark:text-white px-2 py-1.5 " +
  "focus:outline-none focus:ring-2 focus:ring-teal-500/40"

const labelClass =
  "block text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"

export default function SignalsFilterBar({ state, onChange, options, onReset }: Props) {
  const active =
    state.cities.length + state.tracks.length + state.states.length +
    [state.work_mode, state.source,
     state.salary, state.practice, state.search].filter(Boolean).length

  return (
    <div className="px-5 py-4 border-b border-gray-200/60 dark:border-white/10 space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 dark:text-gray-500" />
          <input
            value={state.search}
            onChange={(e) => onChange({ search: e.target.value })}
            placeholder="Search employer or role…"
            className="w-full text-xs rounded-md border border-gray-200 dark:border-white/10 bg-transparent dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 pl-8 pr-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          />
        </div>
        {active > 0 && (
          <button
            onClick={onReset}
            className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-400 transition whitespace-nowrap"
            title="Clear every filter"
          >
            <X className="w-3.5 h-3.5" />
            Clear {active}
          </button>
        )}
      </div>

      {/* Cities, tracks and states are the priority filters — free multi-selects
          over the values actually present in this tenant's leads. */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className={labelClass}>Cities</label>
          <MultiSelect
            label="Cities"
            options={options.cities}
            selected={state.cities}
            onChange={(cities) => onChange({ cities })}
            emptyHint="No leads collected yet."
          />
        </div>
        <div>
          <label className={labelClass}>Tracks</label>
          <MultiSelect
            label="Tracks"
            options={options.tracks}
            selected={state.tracks}
            onChange={(tracks) => onChange({ tracks })}
            emptyHint="No leads collected yet."
          />
        </div>
        <div>
          <label className={labelClass}>States</label>
          <MultiSelect
            label="States"
            options={options.states}
            selected={state.states}
            onChange={(states) => onChange({ states })}
            format={stateLabel}
            emptyHint="No leads collected yet."
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelClass}>Mode</label>
          <select
            value={state.work_mode}
            onChange={(e) => onChange({ work_mode: e.target.value })}
            className={select}
          >
            <option value="">Any mode</option>
            {ALL_WORK_MODES.map((mode) => (
              <option key={mode} value={mode}>{formatWorkMode(mode)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelClass}>Source</label>
          <select
            value={state.source}
            onChange={(e) => onChange({ source: e.target.value })}
            className={select}
          >
            <option value="">Any source</option>
            {ALL_SOURCES.map((source) => (
              <option key={source} value={source}>
                {source === "linkedin" ? "LinkedIn" : "Indeed"}
              </option>
            ))}
          </select>
        </div>
        <div>
          {/* On-site postings with a stated wage are the ones that support a
              direct cost comparison — ~28% of on-site supply. */}
          <label className={labelClass}>Salary</label>
          <select
            value={state.salary}
            onChange={(e) => onChange({ salary: e.target.value })}
            className={select}
          >
            <option value="">Any</option>
            <option value="yes">Salary stated</option>
            <option value="no">No salary</option>
          </select>
        </div>
        <div>
          {/* Whether the posting resolved to a practice in the bank — "Linked"
              is the set an operator can act on with provider data in hand. */}
          <label className={labelClass}>Practice</label>
          <select
            value={state.practice}
            onChange={(e) => onChange({ practice: e.target.value })}
            className={select}
          >
            <option value="">Any</option>
            <option value="yes">Linked</option>
            <option value="no">Not linked</option>
          </select>
        </div>
      </div>
    </div>
  )
}
