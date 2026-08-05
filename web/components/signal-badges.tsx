import { cn } from "@/lib/utils"
import type { Band, LeadDisposition } from "@/lib/leads"

const pill =
  "text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide whitespace-nowrap"

/**
 * The confidence band (ADR-07).
 *
 * A decimal is not actionable — nobody can act on 0.76 versus 0.82. The band
 * turns a calibrated score into a workflow decision, so the badge carries the
 * instruction rather than the number.
 */
const BAND_COLORS: Record<Band, string> = {
  ready: "bg-teal-100 text-teal-800 dark:bg-teal-500/20 dark:text-teal-300",
  check: "bg-navy-100 text-navy-700 dark:bg-navy-500/25 dark:text-navy-200",
  decide: "bg-gray-100 text-gray-600 dark:bg-white/10 dark:text-[#d9d9d9]",
}

const BAND_TITLES: Record<Band, string> = {
  ready: "Confidence ≥ 0.85 — work it like any new lead",
  check: "Confidence 0.70–0.85 — worth a glance before calling",
  decide: "Confidence below 0.70 — held for review",
}

export function BandBadge({ band }: { band: Band | null }) {
  if (!band) return <span className="text-gray-400 dark:text-gray-500">—</span>
  return (
    <span className={cn(pill, BAND_COLORS[band])} title={BAND_TITLES[band]}>
      {band}
    </span>
  )
}

/**
 * The operator's call on a lead: the lightweight approve/reject flag that
 * replaced the old multi-stage status pipeline. `undecided` is the resting
 * state a lead lands in before anyone has triaged it.
 */
const DISPOSITION_COLORS: Record<LeadDisposition, string> = {
  undecided: "bg-gray-100 text-gray-600 dark:bg-white/10 dark:text-[#d9d9d9]",
  approved: "bg-teal-600 text-white dark:bg-teal-600 dark:text-white",
  rejected: "bg-gray-200 text-gray-500 dark:bg-white/5 dark:text-gray-400",
}

export function DispositionBadge({ disposition }: { disposition: LeadDisposition }) {
  return (
    <span className={cn(pill, DISPOSITION_COLORS[disposition] ?? DISPOSITION_COLORS.undecided)}>
      {disposition}
    </span>
  )
}

/**
 * Which board the posting came from. Worth showing per row: the two sources
 * carry different data (Indeed has structured salary on ~44% of rows,
 * LinkedIn on none), so an operator scanning the list can tell an em-dash
 * salary from a genuinely unstated one.
 */
export function SourceBadge({ source }: { source: string }) {
  const label = source === "linkedin" ? "LinkedIn" : source === "indeed" ? "Indeed" : source
  return (
    <span
      className={cn(
        "text-[10px] font-medium px-1.5 py-0.5 rounded border whitespace-nowrap",
        "border-gray-200 text-gray-500 dark:border-white/10 dark:text-gray-400",
      )}
    >
      {label}
    </span>
  )
}
