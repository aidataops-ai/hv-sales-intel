"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronDown, X } from "lucide-react"
import { cn } from "@/lib/utils"

interface MultiSelectProps {
  label: string
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
  /** Shown when `options` is empty — usually "no leads collected yet". */
  emptyHint?: string
}

/**
 * Free multi-select with chips, over whatever values are actually present.
 *
 * The city and track filters are the two priority filters (design §4.3), and
 * both are driven by the tenant's own data rather than a fixed list — a tenant
 * whose targets cover three cities should not scroll past thirty empty ones.
 */
export default function MultiSelect({
  label, options, selected, onChange, emptyHint,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [open])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle
      ? options.filter((o) => o.toLowerCase().includes(needle))
      : options
  }, [options, query])

  function toggle(option: string) {
    onChange(
      selected.includes(option)
        ? selected.filter((s) => s !== option)
        : [...selected, option],
    )
  }

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full inline-flex items-center justify-between gap-1.5 px-2.5 py-1.5 rounded-md border text-xs transition",
          selected.length
            ? "border-teal-500 text-teal-800 dark:border-teal-500/60 dark:text-teal-300"
            : "border-gray-200 text-gray-600 dark:border-white/10 dark:text-[#d9d9d9]",
          "hover:bg-gray-50 dark:hover:bg-white/5",
        )}
      >
        <span className="truncate">
          {label}
          {selected.length > 0 && (
            <span className="ml-1 font-semibold">({selected.length})</span>
          )}
        </span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0 text-gray-400 dark:text-gray-500" />
      </button>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {selected.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => toggle(value)}
              className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-800 dark:bg-teal-500/20 dark:text-teal-300 hover:bg-teal-200 dark:hover:bg-teal-500/30 transition"
              title={`Remove ${value}`}
            >
              {value}
              <X className="w-2.5 h-2.5" />
            </button>
          ))}
        </div>
      )}

      {open && (
        <div className="absolute left-0 right-0 mt-1 z-30 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md overflow-hidden">
          {options.length > 8 && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Filter ${label.toLowerCase()}…`}
              autoFocus
              className="w-full text-xs px-2.5 py-2 border-b border-gray-200 dark:border-white/10 bg-transparent dark:text-white dark:placeholder:text-gray-500 focus:outline-none"
            />
          )}
          <div className="max-h-56 overflow-y-auto py-1">
            {visible.length === 0 ? (
              <p className="px-2.5 py-2 text-[11px] text-gray-400 dark:text-gray-500">
                {options.length === 0
                  ? emptyHint ?? "Nothing to filter yet."
                  : "No match."}
              </p>
            ) : (
              visible.map((option) => (
                <label
                  key={option}
                  className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(option)}
                    onChange={() => toggle(option)}
                    className="accent-teal-600"
                  />
                  <span className="truncate">{option}</span>
                </label>
              ))
            )}
          </div>
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="w-full text-[11px] px-2.5 py-1.5 border-t border-gray-200 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition"
            >
              Clear {label.toLowerCase()}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
