"use client"

import { useEffect, useState } from "react"
import { Check, Copy, X } from "lucide-react"

import { employerLabel, formatSalary, formatWorkMode, type Lead } from "@/lib/leads"

const shell =
  "w-full max-w-lg rounded-xl bg-white dark:bg-night-800 shadow-xl p-5 space-y-3"

function Shell({
  title, onClose, children,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div className={shell} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="font-serif text-base font-bold text-gray-900 dark:text-white">
            {title}
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 dark:text-gray-500 hover:text-gray-600"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/**
 * Approve: set status and hand over the drafted message.
 *
 * The draft is editable before copying because the pitch is matched to work
 * mode — an on-site posting with an advertised wage leads with the cost
 * comparison against that number, and a rep will often want to adjust it.
 */
export function ApproveModal({
  lead, onClose, onConfirm, isSaving,
}: {
  lead: Lead
  onClose: () => void
  onConfirm: () => void
  isSaving: boolean
}) {
  const [draft, setDraft] = useState(lead.draft ?? "")
  const [copied, setCopied] = useState(false)

  useEffect(() => setDraft(lead.draft ?? ""), [lead.draft])

  async function copy() {
    try {
      await navigator.clipboard.writeText(draft)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard access can be denied; the textarea is still selectable.
      setCopied(false)
    }
  }

  return (
    <Shell title={`Approve — ${employerLabel(lead)}`} onClose={onClose}>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {lead.title}
        {lead.city ? ` · ${lead.city}` : ""}
        {` · ${formatWorkMode(lead.work_mode)}`}
        {lead.salary_min != null ? ` · ${formatSalary(lead)}` : ""}
      </p>

      {draft ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={7}
            className="w-full text-sm rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 dark:text-white p-3 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          />
          <button
            onClick={copy}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? "Copied" : "Copy message"}
          </button>
        </>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400 rounded-lg border border-gray-200 dark:border-white/10 p-3">
          The qualifier wrote no draft for this lead — it only drafts for keeps.
          You can still approve it and write your own.
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onClose}
          className="text-sm px-3 py-1.5 rounded-md text-gray-600 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 transition"
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={isSaving}
          className="text-sm px-4 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
        >
          {isSaving ? "Saving…" : "Mark approved"}
        </button>
      </div>
    </Shell>
  )
}

/**
 * Reject: always ask for a reason.
 *
 * Reject reasons are the tuning signal for both the prompt and the config
 * prefilter — the analytics page breaks them down, and an ambiguous term that
 * keeps producing the same reason is a term to narrow. Hence one click away,
 * with quick picks so the free-text field is not the only path.
 */
const QUICK_REASONS = [
  "Not an independent practice",
  "Clinical / on-site role",
  "Wrong industry",
  "Posting already filled",
  "No employer name to contact",
  "Already a customer",
]

export function RejectModal({
  lead, onClose, onConfirm, isSaving,
}: {
  lead: Lead
  onClose: () => void
  onConfirm: (reason: string) => void
  isSaving: boolean
}) {
  const [reason, setReason] = useState("")

  return (
    <Shell title={`Reject — ${employerLabel(lead)}`} onClose={onClose}>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Reject reasons tune the qualifier prompt and the search prefilter, so a
        specific one is worth the extra second.
      </p>

      <div className="flex flex-wrap gap-1.5">
        {QUICK_REASONS.map((quick) => (
          <button
            key={quick}
            onClick={() => setReason(quick)}
            className={`text-[11px] px-2 py-1 rounded-full border transition ${
              reason === quick
                ? "border-teal-500 bg-teal-50 text-teal-800 dark:bg-teal-500/20 dark:text-teal-300"
                : "border-gray-200 text-gray-600 dark:border-white/10 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
            }`}
          >
            {quick}
          </button>
        ))}
      </div>

      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Or type a reason…"
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter" && reason.trim()) onConfirm(reason.trim())
        }}
        className="w-full text-sm rounded-md border border-gray-200 dark:border-white/10 bg-transparent dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
      />

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onClose}
          className="text-sm px-3 py-1.5 rounded-md text-gray-600 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 transition"
        >
          Cancel
        </button>
        <button
          onClick={() => onConfirm(reason.trim())}
          disabled={isSaving || !reason.trim()}
          className="text-sm px-4 py-1.5 rounded-md bg-gray-700 text-white hover:bg-gray-800 disabled:opacity-50 transition"
        >
          {isSaving ? "Saving…" : "Reject"}
        </button>
      </div>
    </Shell>
  )
}
