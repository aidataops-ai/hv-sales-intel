"use client"

import Link from "next/link"
import { Check, ExternalLink, Loader2, X } from "lucide-react"

import { BandBadge, LeadStatusBadge, SourceBadge } from "./signal-badges"
import { cn, timeAgo } from "@/lib/utils"
import { employerLabel, formatSalary, formatWorkMode, type Lead } from "@/lib/leads"
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

/** Column key -> the sort key the API understands. A null key is not sortable. */
const COLUMNS: Array<{ label: string; sort: string | null; className?: string }> = [
  { label: "Employer", sort: "employer" },
  { label: "Role", sort: "role" },
  { label: "City", sort: "city" },
  { label: "Track", sort: "track" },
  { label: "Salary", sort: null, className: "text-right" },
  { label: "Mode", sort: null },
  { label: "Band", sort: "band" },
  { label: "Posted", sort: "posted" },
  { label: "Status", sort: "status" },
  { label: "Actions", sort: null, className: "text-right" },
]

const cell = "px-3 py-2.5 align-middle"

/**
 * A table, not the card grid the practices list uses.
 *
 * These are scanned in volume and compared row to row — employer against
 * employer, salary against salary — which cards make harder, not easier.
 */
export default function SignalsTable({
  leads, isLoading, state, onSort, onApprove, onReject, busyIds,
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
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-200 dark:border-white/10">
            {COLUMNS.map((column) => (
              <th
                key={column.label}
                className={cn(
                  "px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wide",
                  "text-gray-400 dark:text-gray-500 whitespace-nowrap",
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
                  "border-b border-gray-100 dark:border-white/5 transition",
                  "hover:bg-gray-50/70 dark:hover:bg-white/5",
                  busy && "opacity-50",
                )}
              >
                <td className={cn(cell, "max-w-[220px]")}>
                  <div className="flex items-center gap-1.5">
                    <Link
                      href={`/signals/${lead.id}`}
                      className={cn(
                        "font-semibold truncate hover:text-teal-700 dark:hover:text-teal-400 transition",
                        lead.employer_name
                          ? "text-gray-900 dark:text-white"
                          : "text-gray-400 dark:text-gray-500 italic",
                      )}
                      title={employerLabel(lead)}
                    >
                      {employerLabel(lead)}
                    </Link>
                    <SourceBadge source={lead.source} />
                  </div>
                </td>

                <td className={cn(cell, "max-w-[220px]")}>
                  {lead.url ? (
                    <a
                      href={lead.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-gray-700 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 transition truncate"
                      title={`${lead.title} — open the original posting`}
                    >
                      <span className="truncate">{lead.title}</span>
                      <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
                    </a>
                  ) : (
                    <span className="text-gray-700 dark:text-[#d9d9d9] truncate">
                      {lead.title}
                    </span>
                  )}
                </td>

                <td className={cn(cell, "text-gray-600 dark:text-gray-400 whitespace-nowrap")}>
                  {lead.city ? `${lead.city}${lead.state ? `, ${lead.state}` : ""}` : "—"}
                </td>

                <td className={cn(cell, "text-gray-600 dark:text-gray-400 max-w-[160px]")}>
                  <span className="truncate block" title={lead.service_line ?? ""}>
                    {lead.service_line ?? "—"}
                  </span>
                </td>

                <td className={cn(cell, "text-right tabular-nums text-gray-700 dark:text-[#d9d9d9] whitespace-nowrap")}>
                  {formatSalary(lead)}
                </td>

                <td className={cn(cell, "text-gray-600 dark:text-gray-400 whitespace-nowrap")}>
                  {formatWorkMode(lead.work_mode)}
                </td>

                <td className={cell}>
                  <BandBadge band={lead.confidence_band} />
                </td>

                {/* The posting date is how an operator judges staleness before
                    calling — v1 does not remove older leads automatically. */}
                <td
                  className={cn(cell, "text-gray-500 dark:text-gray-400 whitespace-nowrap")}
                  title={lead.posted_at ?? "The board did not report a posting date"}
                >
                  {lead.posted_at ? timeAgo(lead.posted_at) : "—"}
                </td>

                <td className={cell}>
                  <LeadStatusBadge status={lead.status} />
                </td>

                <td className={cn(cell, "text-right whitespace-nowrap")}>
                  <div className="inline-flex items-center gap-1">
                    {busy ? (
                      <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                    ) : (
                      <>
                        <button
                          onClick={() => onApprove(lead)}
                          disabled={lead.status === "approved"}
                          className="p-1.5 rounded-md text-teal-700 dark:text-teal-400 hover:bg-teal-50 dark:hover:bg-teal-500/15 disabled:opacity-30 transition"
                          title="Approve — opens the drafted message"
                        >
                          <Check className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => onReject(lead)}
                          disabled={lead.status === "rejected"}
                          className="p-1.5 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/10 disabled:opacity-30 transition"
                          title="Reject — asks for a reason"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </>
                    )}
                    <Link
                      href={`/signals/${lead.id}`}
                      className="px-2 py-1 rounded-md text-xs text-gray-600 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 transition"
                    >
                      View
                    </Link>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
