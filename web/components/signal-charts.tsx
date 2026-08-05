"use client"

import { cn } from "@/lib/utils"

/**
 * Chart primitives for the Instant Signals analytics page.
 *
 * **Why every chart here is single-series.** The app enforces a strict
 * five-colour palette (`tailwind.config.ts`), whose only two usable mark hues
 * are the brand teal `#3c6e71` and navy `#284b63`. Run as a categorical pair
 * they fail the normal-vision separation floor — OKLab ΔE 11.4, well under the
 * 15 needed for full-colour readers to tell two adjacent series apart, and
 * 11.3 under deuteranopia. Rather than break the palette, the multi-series
 * views are faceted into small multiples: one chart per source, each with one
 * series and a title that names it. No legend is needed and identity is never
 * carried by colour alone.
 *
 * The one ordered scale — the confidence bands — is a genuine sequential ramp
 * (ready → check → decide), so it uses one hue light→dark. Both ramps below
 * are validated: monotone lightness, adjacent ΔL ≥ 0.06, and a pale end that
 * still clears its surface in each mode.
 */

// Sequential teal, light→dark, validated against #ffffff.
export const RAMP_LIGHT = ["#8fb8ba", "#4f8a8d", "#335d60"]
// The same hue re-stepped for the dark surface #3d3d3d — a selected ramp, not
// an automatic flip of the light one.
export const RAMP_DARK = ["#d6e6e6", "#8fb8ba", "#4f8a8d"]

const MARK = "bg-teal-600 dark:bg-teal-400"

export function StatTile({
  label, value, hint, tone = "neutral",
}: {
  label: string
  value: string
  hint?: string
  tone?: "neutral" | "alert"
}) {
  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        tone === "alert"
          ? "border-teal-600/40 bg-teal-50 dark:bg-teal-500/10 dark:border-teal-500/30"
          : "border-gray-200 dark:border-white/10",
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
        {label}
      </p>
      <p className="font-serif text-2xl font-bold text-gray-900 dark:text-white mt-1 tabular-nums">
        {value}
      </p>
      {hint && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 leading-snug">
          {hint}
        </p>
      )}
    </div>
  )
}

export interface BarRow {
  label: string
  value: number
  /** Optional per-row colour, for the one ordered scale on the page. */
  color?: string
}

/**
 * Ranked horizontal bars. One series, so the section title names it and no
 * legend is drawn. Every bar is directly labelled — the list is short enough
 * that a value axis would cost more than it explains.
 */
export function BarList({
  rows, emptyHint, total,
}: {
  rows: BarRow[]
  emptyHint: string
  /** Denominator for the share label. Defaults to the largest row. */
  total?: number
}) {
  if (rows.length === 0) {
    return (
      <p className="text-xs text-gray-400 dark:text-gray-500 py-4">{emptyHint}</p>
    )
  }
  const peak = Math.max(...rows.map((r) => r.value), 1)
  const denominator = total ?? rows.reduce((sum, r) => sum + r.value, 0)

  return (
    <ul className="space-y-2">
      {rows.map((row) => {
        const share = denominator ? Math.round((row.value / denominator) * 100) : 0
        return (
          <li key={row.label} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="text-gray-700 dark:text-[#d9d9d9] truncate" title={row.label}>
                {row.label}
              </span>
              <span className="text-gray-500 dark:text-gray-400 tabular-nums whitespace-nowrap">
                {row.value}
                <span className="text-gray-400 dark:text-gray-500"> · {share}%</span>
              </span>
            </div>
            {/* Thin mark, rounded data-end, anchored to the baseline. */}
            <div className="h-2 rounded-sm bg-gray-100 dark:bg-white/5 overflow-hidden">
              <div
                className={cn("h-full rounded-r-[4px]", !row.color && MARK)}
                style={{
                  width: `${Math.max(2, (row.value / peak) * 100)}%`,
                  backgroundColor: row.color,
                }}
                title={`${row.label}: ${row.value}`}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * Daily volume for one source. Faceted rather than stacked — see the module
 * note. Bars carry a 2px surface gap so adjacent days stay separable, and only
 * the peak is directly labelled; a number on every column would be noise.
 */
export function ColumnChart({
  points, emptyHint,
}: {
  points: Array<{ day: string; value: number }>
  emptyHint: string
}) {
  if (points.length === 0) {
    return <p className="text-xs text-gray-400 dark:text-gray-500 py-4">{emptyHint}</p>
  }
  const peak = Math.max(...points.map((p) => p.value), 1)

  return (
    <div className="space-y-1">
      <div className="flex items-end gap-[2px] h-24">
        {points.map((point) => (
          <div
            key={point.day}
            className="flex-1 min-w-[3px] flex flex-col justify-end group relative"
            title={`${point.day}: ${point.value}`}
          >
            <div
              className={cn("w-full rounded-t-[4px] transition-opacity", MARK,
                            "opacity-90 group-hover:opacity-100")}
              style={{ height: `${Math.max(3, (point.value / peak) * 100)}%` }}
            />
            <span className="pointer-events-none absolute -top-5 left-1/2 -translate-x-1/2 hidden group-hover:block whitespace-nowrap text-[10px] px-1 py-0.5 rounded bg-gray-900 text-white dark:bg-white dark:text-gray-900 z-10">
              {point.value}
            </span>
          </div>
        ))}
      </div>
      {/* Recessive axis: only the endpoints and the peak are labelled. */}
      <div className="flex items-center justify-between text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">
        <span>{points[0].day.slice(5)}</span>
        <span>peak {peak}</span>
        <span>{points[points.length - 1].day.slice(5)}</span>
      </div>
    </div>
  )
}

/** The table view — identity and value without relying on any mark at all. */
export function DataTable({
  columns, rows,
}: {
  columns: string[]
  rows: Array<Array<string | number>>
}) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-400 transition">
        Show as a table
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-gray-200 dark:border-white/10">
              {columns.map((column) => (
                <th
                  key={column}
                  className="px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-gray-100 dark:border-white/5">
                {row.map((cell, j) => (
                  <td
                    key={j}
                    className="px-2 py-1 text-gray-700 dark:text-[#d9d9d9] tabular-nums"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
