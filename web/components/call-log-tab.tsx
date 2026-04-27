"use client"

import type { Practice } from "@/lib/types"
import type { CallLogResponse } from "@/lib/api"
import { timeAgo } from "@/lib/utils"
import CallButton from "./call-button"

interface CallLogTabProps {
  practice: Practice
  onLogged: (response: CallLogResponse) => void
}

export default function CallLogTab({ practice, onLogged }: CallLogTabProps) {
  const entries = (practice.call_notes ?? "").split("\n").filter(Boolean)

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs text-gray-500 leading-relaxed">
          {practice.call_count > 0 ? (
            <>
              {practice.call_count}{" "}
              {practice.call_count === 1 ? "call" : "calls"}
              {practice.salesforce_owner_name && (
                <> · owner: {practice.salesforce_owner_name}</>
              )}
              {practice.salesforce_synced_at && (
                <> · synced {timeAgo(practice.salesforce_synced_at)}</>
              )}
            </>
          ) : (
            "No calls logged yet."
          )}
        </div>
        <CallButton
          practice={practice}
          label="Log call"
          onLogged={onLogged}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 shrink-0"
        />
      </div>

      {entries.length === 0 ? (
        <p className="text-xs text-gray-400">Nothing here yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {entries.map((entry, i) => (
            <li
              key={i}
              className="text-xs text-gray-700 p-2 rounded-lg bg-white/60 border border-gray-200/60 whitespace-pre-line"
            >
              {entry}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
