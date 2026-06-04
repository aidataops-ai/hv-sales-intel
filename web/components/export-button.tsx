"use client"

import { useEffect, useRef, useState } from "react"
import { Download, ChevronDown } from "lucide-react"

/**
 * Bulk CSV export trigger.
 *
 * Opens a tiny popover with a single "max exports" input:
 *   - empty                   → export every row
 *   - 0                       → only never-exported rows (export_count = 0)
 *   - N                       → only rows with export_count <= N
 *
 * After the file lands, the backend has already incremented export_count
 * by 1 on every exported row, so a follow-up export with `0` will skip
 * the rows you already pulled.
 */
export default function ExportButton() {
  const [open, setOpen] = useState(false)
  const [maxExports, setMaxExports] = useState<string>("")
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

  function triggerDownload() {
    const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
    const qs = maxExports.trim() === "" ? "" : `?max_exports=${encodeURIComponent(maxExports.trim())}`
    // Use a hidden anchor so credentials: include behaviour applies the
    // session cookie. window.location.href works too but doesn't preserve
    // referrer or download attribute hints.
    const url = `${API_URL}/api/practices/export.csv${qs}`
    const a = document.createElement("a")
    a.href = url
    a.rel = "noopener"
    a.style.display = "none"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setOpen(false)
  }

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] text-sm font-medium hover:bg-gray-50 dark:hover:bg-white/10 transition"
        title="Bulk export leads to CSV"
      >
        <Download className="w-4 h-4" />
        Export CSV
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-72 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md z-30 overflow-hidden p-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-[#d9d9d9] mb-1">
              Max prior exports
            </label>
            <input
              type="number"
              min={0}
              inputMode="numeric"
              placeholder="empty = all, 0 = new only"
              value={maxExports}
              onChange={(e) => setMaxExports(e.target.value)}
              className="w-full text-sm rounded-md border border-gray-200 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
              onKeyDown={(e) => {
                if (e.key === "Enter") triggerDownload()
              }}
              autoFocus
            />
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5 leading-snug">
              Filters by <code>export_count</code>. Leave empty to grab every lead.
              Set <code>0</code> next time to skip ones you&apos;ve already downloaded.
            </p>
          </div>
          <button
            onClick={triggerDownload}
            className="w-full text-sm px-3 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 transition"
          >
            Download CSV
          </button>
        </div>
      )}
    </div>
  )
}
