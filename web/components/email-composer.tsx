"use client"

import { useState, useEffect } from "react"
import { RefreshCw, Save, Send, Loader2, AlertTriangle } from "lucide-react"
import type { EmailDraft } from "@/lib/types"

interface EmailComposerProps {
  draft: EmailDraft
  canSend: boolean
  recipient: string | null
  onSave: (draft: EmailDraft) => Promise<void>
  onRegenerate: () => Promise<void>
  onSend: () => Promise<void>
  isRegenerating: boolean
}

export default function EmailComposer({
  draft,
  canSend,
  recipient,
  onSave,
  onRegenerate,
  onSend,
  isRegenerating,
}: EmailComposerProps) {
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [saving, setSaving] = useState(false)
  const [sending, setSending] = useState(false)
  const [confirm, setConfirm] = useState(false)

  useEffect(() => {
    setSubject(draft.subject)
    setBody(draft.body)
  }, [draft.subject, draft.body])

  async function handleSave() {
    setSaving(true)
    try {
      await onSave({ subject, body })
    } finally {
      setSaving(false)
    }
  }

  async function handleSendConfirmed() {
    setSending(true)
    try {
      if (subject !== draft.subject || body !== draft.body) {
        await onSave({ subject, body })
      }
      await onSend()
      setConfirm(false)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-3">
      <input
        type="text"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        onBlur={handleSave}
        placeholder="Subject"
        className="w-full text-sm font-medium rounded-lg border border-gray-200 bg-white/80 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onBlur={handleSave}
        placeholder="Email body..."
        className="w-full h-64 text-sm p-3 rounded-lg border border-gray-200 bg-white/80 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 resize-none"
      />
      {confirm ? (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30">
          <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-300 shrink-0" />
          <span className="text-xs text-amber-900 dark:text-amber-200 flex-1">Send to {recipient}?</span>
          <button
            onClick={() => setConfirm(false)}
            disabled={sending}
            className="text-xs px-3 py-1 rounded-lg text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10"
          >
            Cancel
          </button>
          <button
            onClick={handleSendConfirmed}
            disabled={sending}
            className="text-xs px-3 py-1 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {sending && <Loader2 className="w-3 h-3 animate-spin" />}
            {sending ? "Sending..." : "Yes, send"}
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            onClick={onRegenerate}
            disabled={isRegenerating}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 disabled:opacity-50"
          >
            {isRegenerating ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <RefreshCw className="w-3 h-3" />
            )}
            Regenerate
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            Save draft
          </button>
          <button
            onClick={() => setConfirm(true)}
            disabled={!canSend}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 ml-auto"
          >
            <Send className="w-3 h-3" />
            Send
          </button>
        </div>
      )}
    </div>
  )
}
