"use client"

import { useState } from "react"
import { Loader2, X, Check } from "lucide-react"
import { changeMyPassword } from "@/lib/api"

interface ChangePasswordModalProps {
  open: boolean
  onClose: () => void
}

const RULES: { label: string; test: (pw: string) => boolean }[] = [
  { label: "At least 8 characters", test: (pw) => pw.length >= 8 },
  { label: "One uppercase letter", test: (pw) => /[A-Z]/.test(pw) },
  { label: "One lowercase letter", test: (pw) => /[a-z]/.test(pw) },
  { label: "One number", test: (pw) => /\d/.test(pw) },
  { label: "One special character", test: (pw) => /[^A-Za-z0-9]/.test(pw) },
]

export default function ChangePasswordModal({ open, onClose }: ChangePasswordModalProps) {
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  if (!open) return null

  const allRulesPassed = RULES.every((r) => r.test(next))
  const matches = next.length > 0 && next === confirm
  const canSubmit = current.length > 0 && allRulesPassed && matches && !submitting

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await changeMyPassword(current, next)
      setSuccess(true)
      setTimeout(() => {
        setCurrent("")
        setNext("")
        setConfirm("")
        setSuccess(false)
        onClose()
      }, 1200)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (message.includes("401")) setError("Current password is incorrect.")
      else if (message.includes("400")) setError("New password doesn't meet the requirements.")
      else setError("Couldn't save — try again.")
    } finally {
      setSubmitting(false)
    }
  }

  function handleClose() {
    if (submitting) return
    setCurrent("")
    setNext("")
    setConfirm("")
    setError(null)
    setSuccess(false)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white dark:bg-night-800 shadow-xl p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-serif text-base font-bold text-gray-900 dark:text-white">Change password</h3>
          <button onClick={handleClose} className="text-gray-400 dark:text-gray-500 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="block">
            <span className="text-xs text-gray-500 dark:text-gray-400">Current password</span>
            <input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 dark:border-white/10 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500"
            />
          </label>

          <label className="block">
            <span className="text-xs text-gray-500 dark:text-gray-400">New password</span>
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 dark:border-white/10 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500"
            />
          </label>

          <ul className="space-y-1 pl-1">
            {RULES.map((r) => {
              const pass = r.test(next)
              return (
                <li key={r.label} className="flex items-center gap-1.5 text-xs">
                  <Check className={`w-3 h-3 ${pass ? "text-teal-600 dark:text-teal-400" : "text-gray-300 dark:text-gray-600"}`} />
                  <span className={pass ? "text-gray-700 dark:text-[#d9d9d9]" : "text-gray-400 dark:text-gray-500"}>{r.label}</span>
                </li>
              )
            })}
          </ul>

          <label className="block">
            <span className="text-xs text-gray-500 dark:text-gray-400">Confirm new password</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={submitting}
              className="w-full text-sm mt-1 rounded-lg border border-gray-200 dark:border-white/10 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500"
            />
            {confirm.length > 0 && !matches && (
              <span className="text-[11px] text-rose-600">Passwords don&apos;t match.</span>
            )}
          </label>

          {error && <p className="text-xs text-rose-600">{error}</p>}
          {success && <p className="text-xs text-teal-700 dark:text-teal-400">Password updated.</p>}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={handleClose}
            disabled={submitting}
            className="text-xs px-4 py-2 rounded-lg text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="inline-flex items-center gap-1 text-xs px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
          >
            {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
            {submitting ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  )
}
