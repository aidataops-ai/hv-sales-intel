import { cn } from "@/lib/utils"

const STATUS_COLORS: Record<string, string> = {
  NEW: "bg-gray-100 text-gray-600 dark:bg-white/10 dark:text-[#d9d9d9]",
  RESEARCHED: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300",
  "SCRIPT READY": "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300",
  CONTACTED: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
  "FOLLOW UP": "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
  "MEETING SET": "bg-teal-100 text-teal-700 dark:bg-teal-500/20 dark:text-teal-300",
  PROPOSAL: "bg-teal-100 text-teal-700 dark:bg-teal-500/20 dark:text-teal-300",
  "CLOSED WON": "bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300",
  "CLOSED LOST": "bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300",
}

export default function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-600 dark:bg-white/10 dark:text-[#d9d9d9]"
  return (
    <span className={cn("text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide", color)}>
      {status}
    </span>
  )
}

export const ALL_STATUSES = [
  "NEW",
  "RESEARCHED",
  "SCRIPT READY",
  "CONTACTED",
  "FOLLOW UP",
  "MEETING SET",
  "PROPOSAL",
  "CLOSED WON",
  "CLOSED LOST",
]
