"use client"

import { ExternalLink, X } from "lucide-react"
import type { Practice } from "@/lib/types"

interface SfLeadCreatedModalProps {
  practice: Practice
  open: boolean
  onClose: () => void
}

export default function CallLogModal({
  practice,
  open,
  onClose,
}: SfLeadCreatedModalProps) {
  if (!open) return null

  const leadUrl = practice.salesforce_lead_url
  const leadId = practice.salesforce_lead_id

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white dark:bg-night-800 shadow-xl p-5 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-serif text-base font-bold text-gray-900 dark:text-white">
            Lead created — {practice.name}
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 dark:text-gray-500 hover:text-gray-600"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {leadUrl ? (
          <>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Salesforce Lead ID:&nbsp;
              <span className="font-mono text-gray-700 dark:text-[#d9d9d9]">{leadId}</span>
            </p>
            <div className="rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 p-3">
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">Lead link</p>
              <a
                href={leadUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-teal-700 dark:text-teal-400 break-all hover:underline"
              >
                {leadUrl}
              </a>
            </div>
          </>
        ) : (
          <p className="text-xs text-rose-600">
            Lead was not created in Salesforce. Check the call log response for the error.
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="text-xs px-4 py-2 rounded-lg text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10"
          >
            Close
          </button>
          {leadUrl && (
            <a
              href={leadUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700"
            >
              <ExternalLink className="w-3 h-3" />
              Take me there
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
