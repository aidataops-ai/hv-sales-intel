"use client"

import { useState } from "react"
import { Loader2, Phone } from "lucide-react"
import type { Practice } from "@/lib/types"
import { logCall, type CallLogResponse } from "@/lib/api"
import { openRingCentralCall } from "@/lib/ringcentral"
import CallLogModal from "./call-log-modal"

interface CallButtonProps {
  practice: Practice
  label?: string
  className: string
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void
  onLogged?: (response: CallLogResponse) => void
}

export default function CallButton({
  practice,
  label = "Call",
  className,
  onClick,
  onLogged,
}: CallButtonProps) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!practice.phone) return null

  async function handleClick(event: React.MouseEvent<HTMLButtonElement>) {
    onClick?.(event)
    setBusy(true)
    setError(null)
    try {
      // 1. Create or update the SF Lead immediately (empty note → backend
      //    appends a placeholder line and increments call_count). Returns
      //    the practice with salesforce_lead_id populated.
      const response = await logCall(practice.place_id, "")
      onLogged?.(response)

      // 2. Open the RingCentral dialer right away.
      if (practice.phone) openRingCentralCall(practice.phone)

      // 3. Open the post-call notes modal so the rep can record what
      //    happened. Save in the modal updates the last line's note text
      //    via PUT /api/practices/{id}/call/last-note.
      setOpen(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start call")
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={className}
        title={`Log call + dial via RingCentral: ${practice.phone}`}
      >
        {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Phone className="w-3 h-3" />}
        {busy ? "Starting..." : label}
      </button>
      {error && (
        <span
          className="text-xs text-rose-600 ml-2"
          title={error}
          onClick={(e) => e.stopPropagation()}
        >
          {error}
        </span>
      )}
      <CallLogModal
        practice={practice}
        open={open}
        onClose={() => setOpen(false)}
        onLogged={(response) => onLogged?.(response)}
      />
    </>
  )
}
