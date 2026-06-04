"use client"

import { useState } from "react"
import { Edit2, Check, X } from "lucide-react"
import { updatePracticeEmail } from "@/lib/api"

interface EmailRecipientProps {
  placeId: string
  email: string | null
  onChange: (email: string) => void
}

export default function EmailRecipient({ placeId, email, onChange }: EmailRecipientProps) {
  const [editing, setEditing] = useState(!email)
  const [draft, setDraft] = useState(email ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    if (!draft.trim()) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updatePracticeEmail(placeId, draft.trim())
      onChange(updated.email ?? draft.trim())
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          type="email"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="contact@practice.com"
          autoFocus
          className="flex-1 text-sm rounded-lg border border-gray-200 bg-white/80 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 px-2 py-1"
        />
        <button
          onClick={save}
          disabled={saving || !draft.trim()}
          className="p-1.5 rounded-lg text-teal-700 dark:text-teal-400 hover:bg-teal-50 dark:hover:bg-[#284b63]/40 disabled:opacity-50"
          title="Save"
        >
          <Check className="w-4 h-4" />
        </button>
        {email && (
          <button
            onClick={() => {
              setDraft(email)
              setEditing(false)
              setError(null)
            }}
            className="p-1.5 rounded-lg text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/10"
            title="Cancel"
          >
            <X className="w-4 h-4" />
          </button>
        )}
        {error && <span className="text-xs text-rose-600">{error}</span>}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-500 dark:text-gray-400">To:</span>
      <span className="text-gray-900 dark:text-white">{email}</span>
      <button
        onClick={() => setEditing(true)}
        className="p-1 rounded text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-[#d9d9d9]"
        title="Edit email"
      >
        <Edit2 className="w-3 h-3" />
      </button>
    </div>
  )
}
