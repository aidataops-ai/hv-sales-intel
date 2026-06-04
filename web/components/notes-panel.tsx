"use client"

import { useState } from "react"
import { Save, Loader2, Sparkles } from "lucide-react"

interface NotesPanelProps {
  notes: string
  onSave: (notes: string) => Promise<void>
}

export default function NotesPanel({ notes: initialNotes, onSave }: NotesPanelProps) {
  const [notes, setNotes] = useState(initialNotes)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function handleSave() {
    setIsSaving(true)
    setSaved(false)
    try {
      await onSave(notes)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-serif font-semibold text-gray-900 dark:text-white">Lead Notes</h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
          Saved to the Salesforce Lead&apos;s <span className="font-semibold">Call_Notes__c</span> field.
        </p>
      </div>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={handleSave}
        placeholder="Notes about this lead..."
        className="w-full h-48 text-sm p-3 rounded-lg border border-gray-200 bg-white/80
                   dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500
                   placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40
                   resize-none"
      />
      <button
        onClick={handleSave}
        disabled={isSaving}
        className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {isSaving ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Save className="w-4 h-4" />
        )}
        {saved ? "Saved!" : isSaving ? "Saving..." : "Save Notes"}
      </button>

      <div className="flex gap-2 rounded-lg bg-teal-50/60 dark:bg-[#284b63]/40 border border-teal-100 dark:border-white/10 p-3 mt-2">
        <Sparkles className="w-4 h-4 text-teal-600 dark:text-teal-400 shrink-0 mt-0.5" />
        <div>
          <p className="text-xs font-semibold text-gray-800 dark:text-[#d9d9d9]">Pro tips</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-snug">
            Add notes, log calls, or draft emails here to keep your outreach
            organized and your team aligned.
          </p>
        </div>
      </div>
    </div>
  )
}
