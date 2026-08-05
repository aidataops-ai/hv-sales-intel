"use client"

import { useEffect, useRef, useState } from "react"
import { ChevronDown, Loader2, Play } from "lucide-react"

import { useAuth } from "@/lib/auth"
import { retriggerLeads } from "@/lib/leads"

/**
 * Manually kick off the full collect + qualify sweep.
 *
 * The backend dispatches the GitHub Actions workflow (`.github/workflows/
 * leads.yml`) — the same runner the hourly cron uses — rather than running the
 * sweep in the API process, which would hit the serverless wall-clock ceiling.
 * So a manual run and the scheduled run take the identical path.
 *
 * Admin only: the run spends model credits, and the endpoint is behind
 * `require_admin`. Non-admins get nothing rendered rather than a disabled
 * button that hints at a control they can't use. The click opens a small
 * confirm step rather than firing immediately, so an accidental click doesn't
 * spend a sweep's worth of credits.
 */
export default function RetriggerButton() {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
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

  if (user?.role !== "admin") return null

  async function run() {
    setRunning(true)
    setMessage(null)
    const result = await retriggerLeads()
    setRunning(false)
    if (result.ok) {
      setMessage({ ok: true, text: "Pipeline started — it runs on GitHub Actions." })
      setTimeout(() => setOpen(false), 1800)
    } else {
      setMessage({ ok: false, text: result.error })
    }
  }

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] text-sm font-medium hover:bg-gray-50 dark:hover:bg-white/10 transition"
        title="Run the collect + qualify pipeline now (admin)"
      >
        <Play className="w-4 h-4" />
        Run pipeline
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-72 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md z-30 overflow-hidden p-4 space-y-3">
          <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
            Runs the full collect + qualify sweep on GitHub Actions — the same job
            as the hourly cron. It spends model credits.
          </p>

          <button
            onClick={run}
            disabled={running}
            className="w-full inline-flex items-center justify-center gap-1.5 text-sm px-3 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
          >
            {running && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {running ? "Starting…" : "Run now"}
          </button>

          {message && (
            <p
              className={`text-[11px] leading-snug ${
                message.ok
                  ? "text-teal-700 dark:text-teal-400"
                  : "text-red-600 dark:text-red-400"
              }`}
            >
              {message.text}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
