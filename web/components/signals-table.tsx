"use client"

import Link from "next/link"
import { ExternalLink } from "lucide-react"

import { DispositionBadge } from "./signal-badges"
import { cn } from "@/lib/utils"
import { employerLabel, formatWorkMode, type Lead } from "@/lib/leads"
import type { SignalsState } from "@/lib/use-signals-url-state"

interface Props {
  leads: Lead[]
  isLoading: boolean
  state: SignalsState
  onSort: (key: string) => void
  onApprove: (lead: Lead) => void
  onReject: (lead: Lead) => void
  busyIds: Set<number>
}

/**
 * Column key -> the sort key the API understands. A null key is not sortable.
 * `width` feeds a fixed table layout so a long Role or Track never bleeds into
 * the next column — it truncates inside its own share instead.
 */
const COLUMNS: Array<{ label: string; sort: string | null; width: string; className?: string }> = [
  { label: "Employer", sort: "employer", width: "24%" },
  { label: "Role", sort: "role", width: "34%" },
  { label: "Track", sort: "track", width: "24%" },
  { label: "Mode", sort: null, width: "9%" },
  { label: "Decision", sort: "disposition", width: "9%" },
]

const cell = "px-3 py-2.5 align-middle"

/**
 * A table, not the card grid the practices list uses.
 *
 * These are scanned in volume and compared row to row — employer against
 * employer, salary against salary — which cards make harder, not easier.
 */
export default function SignalsTable({
  leads, isLoading, state, onSort, busyIds,
}: Props) {
  if (!isLoading && leads.length === 0) {
    return (
      <div className="py-20 text-center">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No signals match these filters.
        </p>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
          Collection runs in the background — new postings appear as they are qualified.
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] table-fixed text-sm border-collapse">
        <colgroup>
          {COLUMNS.map((column) => (
            <col key={column.label} style={{ width: column.width }} />
          ))}
        </colgroup>
        <thead>
          <tr className="bg-gray-100/80 dark:bg-white/5 border-b border-gray-200 dark:border-white/10">
            {COLUMNS.map((column) => (
              <th
                key={column.label}
                className={cn(
                  "px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide",
                  "text-gray-600 dark:text-gray-300 whitespace-nowrap",
                  column.className,
                )}
              >
                {column.sort ? (
                  <button
                    onClick={() => onSort(column.sort!)}
                    className={cn(
                      "hover:text-teal-700 dark:hover:text-teal-400 transition",
                      state.sort === column.sort && "text-teal-700 dark:text-teal-400",
                    )}
                  >
                    {column.label}
                    {state.sort === column.sort && (
                      <span className="ml-1">{state.dir === "asc" ? "↑" : "↓"}</span>
                    )}
                  </button>
                ) : (
                  column.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const busy = busyIds.has(lead.id)
            return (
              <tr
                key={lead.id}
                className={cn(
                  "border-b border-gray-200/70 dark:border-white/5 transition",
                  "odd:bg-white even:bg-gray-50/60 dark:odd:bg-transparent dark:even:bg-white/[0.02]",
                  "hover:bg-teal-50/50 dark:hover:bg-white/5",
                  busy && "opacity-50",
                )}
              >
                <td className={cell}>
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Link
                      href={`/signals/${lead.id}`}
                      className={cn(
                        "block truncate min-w-0 font-semibold hover:text-teal-700 dark:hover:text-teal-400 transition",
                        lead.employer_name
                          ? "text-gray-900 dark:text-white"
                          : "text-gray-400 dark:text-gray-500 italic",
                      )}
                      title={employerLabel(lead)}
                    >
                      {employerLabel(lead)}
                    </Link>
                    {/* Linked-to-a-practice marker: teal = auto, amber = review.
                        The at-a-glance counterpart to the Practice filter. */}
                    {lead.practice_id != null && (
                      <span
                        title={`Linked to ${lead.practice?.name ?? "a practice"}${
                          lead.match_status ? ` (${lead.match_status})` : ""
                        }`}
                        className={cn(
                          "shrink-0 w-1.5 h-1.5 rounded-full",
                          lead.match_status === "review" ? "bg-amber-500" : "bg-teal-500",
                        )}
                      />
                    )}
                  </div>
                </td>

                <td className={cell}>
                  {lead.url ? (
                    <a
                      href={lead.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-gray-700 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 transition"
                      title={`${lead.title} — open the original posting`}
                    >
                      <span className="truncate min-w-0">{lead.title}</span>
                      <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
                    </a>
                  ) : (
                    <span className="block truncate text-gray-700 dark:text-[#d9d9d9]" title={lead.title ?? ""}>
                      {lead.title}
                    </span>
                  )}
                </td>

                <td className={cn(cell, "text-gray-600 dark:text-gray-400")}>
                  <span className="block truncate" title={lead.service_line ?? ""}>
                    {lead.service_line ?? "—"}
                  </span>
                </td>

                <td className={cn(cell, "text-gray-600 dark:text-gray-400 whitespace-nowrap")}>
                  {formatWorkMode(lead.work_mode)}
                </td>

                <td className={cell}>
                  <DispositionBadge disposition={lead.disposition} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
