"use client"

import { ChevronLeft, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"

interface PaginationProps {
  page: number // 1-based
  totalPages: number
  onChange: (page: number) => void
}

// Compact page list with ellipses: 1 … 4 5 6 … 10
function pageList(page: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | "…")[] = [1]
  const left = Math.max(2, page - 1)
  const right = Math.min(total - 1, page + 1)
  if (left > 2) out.push("…")
  for (let i = left; i <= right; i++) out.push(i)
  if (right < total - 1) out.push("…")
  out.push(total)
  return out
}

const circle =
  "w-8 h-8 grid place-items-center rounded-full text-sm transition shrink-0"

export default function Pagination({ page, totalPages, onChange }: PaginationProps) {
  if (totalPages <= 1) return null
  return (
    <div className="flex items-center justify-center gap-1.5 py-3">
      <button
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        className={cn(
          circle,
          "border border-gray-200 text-gray-500 hover:bg-gray-50 dark:border-white/10 dark:text-gray-400 dark:hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent",
        )}
        aria-label="Previous page"
      >
        <ChevronLeft className="w-4 h-4" />
      </button>

      {pageList(page, totalPages).map((p, i) =>
        p === "…" ? (
          <span key={`gap-${i}`} className="w-6 text-center text-gray-400 dark:text-gray-500 text-sm">
            …
          </span>
        ) : (
          <button
            key={p}
            onClick={() => onChange(p)}
            className={cn(
              circle,
              p === page
                ? "bg-teal-600 text-white font-semibold"
                : "text-gray-600 hover:bg-gray-100 dark:text-[#d9d9d9] dark:hover:bg-white/10",
            )}
            aria-current={p === page ? "page" : undefined}
          >
            {p}
          </button>
        ),
      )}

      <button
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
        className={cn(
          circle,
          "border border-gray-200 text-gray-500 hover:bg-gray-50 dark:border-white/10 dark:text-gray-400 dark:hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent",
        )}
        aria-label="Next page"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  )
}
