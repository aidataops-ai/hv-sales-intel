"use client"

import { useEffect, useRef, useState } from "react"
import { ChevronDown, Loader2, Pause, Play } from "lucide-react"

import { useAuth } from "@/lib/auth"
import { getPipelineState, togglePipeline, type PipelineState } from "@/lib/leads"

/**
 * Pause or resume the scheduled lead pipeline.
 *
 * The backend flips every scheduled GitHub Actions workflow (the per-board
 * sweep pair plus the enrich→push job, `github_leads_scheduled_workflows`)
 * between enabled and disabled — pausing also cancels any queued or in-flight
 * runs, so "stop" means nothing is spending credits after the click. The
 * manual dispatch workflow (leads.yml) is NOT in that set — pause stops the
 * automatic spend, not the operator's escape hatch.
 *
 * Admin only: non-admins get nothing rendered. The
 * click opens a confirm step rather than acting immediately — pausing silently
 * would starve the board of fresh signals, and an accidental resume restarts
 * the scheduled credit spend.
 */
export default function PipelineToggleButton() {
  const { user } = useAuth()
  const isAdmin = user?.role === "admin"
  const [state, setState] = useState<PipelineState | null>(null)
  const [open, setOpen] = useState(false)
  const [working, setWorking] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isAdmin) return
    let alive = true
    getPipelineState().then((s) => {
      if (alive) setState(s)
    })
    return () => {
      alive = false
    }
  }, [isAdmin])

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

  if (!isAdmin) return null

  const paused = state === "paused"

  async function run() {
    setWorking(true)
    setMessage(null)
    const result = await togglePipeline(paused ? "resume" : "stop")
    setWorking(false)
    if (result.ok) {
      setState(result.state)
      setMessage({
        ok: true,
        text:
          result.state === "paused"
            ? `Pipeline paused${
                result.cancelled_runs
                  ? ` — cancelled ${result.cancelled_runs} live run${
                      result.cancelled_runs === 1 ? "" : "s"
                    }`
                  : ""
              }.`
            : "Pipeline resumed — the scheduled sweeps are back on.",
      })
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
        title={
          paused
            ? "The scheduled pipeline is paused — resume it (admin)"
            : "Pause the scheduled pipeline (admin)"
        }
      >
        {paused ? (
          <Play className="w-4 h-4 text-amber-600 dark:text-amber-400" />
        ) : (
          <Pause className="w-4 h-4" />
        )}
        {paused ? "Resume pipeline" : "Pause pipeline"}
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-72 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md z-30 overflow-hidden p-4 space-y-3">
          <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
            {paused
              ? "Re-enables the scheduled workflows: the collect + qualify sweeps and the enrich + push job."
              : "Disables the scheduled workflows — sweeps and enrich + push — and cancels any run in flight."}
          </p>

          <button
            onClick={run}
            disabled={working}
            className="w-full inline-flex items-center justify-center gap-1.5 text-sm px-3 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
          >
            {working && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {working
              ? paused
                ? "Resuming…"
                : "Pausing…"
              : paused
                ? "Resume now"
                : "Pause now"}
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
