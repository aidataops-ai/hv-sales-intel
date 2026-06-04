import { cn } from "@/lib/utils"

interface ScoreBarProps {
  label: string
  value: number
  max?: number
}

export default function ScoreBar({ label, value, max = 100 }: ScoreBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  const color =
    value >= 75 ? "bg-rose-500" : value >= 50 ? "bg-amber-400" : "bg-teal-500"

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 dark:text-gray-400 w-14 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-gray-200 dark:bg-white/10 rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-medium text-gray-700 dark:text-[#d9d9d9] w-7 text-right">{value}</span>
    </div>
  )
}
