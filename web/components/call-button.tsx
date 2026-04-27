"use client"

import { useState } from "react"
import { Loader2, Phone } from "lucide-react"
import type { Practice } from "@/lib/types"
import { logCall, type CallLogResponse } from "@/lib/api"
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
  const [latest, setLatest] = useState<Practice>(practice)

  if (!practice.phone) return null

  async function handleClick(event: React.MouseEvent<HTMLButtonElement>) {
    onClick?.(event)
    setBusy(true)
    setError(null)
    try {
      // 1. Create or update the SF Lead. Backend returns the practice with
      //    salesforce_lead_id + salesforce_lead_url populated.
      const response = await logCall(practice.place_id, "")
      onLogged?.(response)
      // 2. Show the popup with the SF Lead link. RingCentral is integrated
      //    inside Salesforce, so dialing happens after the rep clicks
      //    "Take me there" — no separate dialer popup from us.
      setLatest(response.practice)
      setOpen(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create Lead")
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
        title={`Create Salesforce Lead for ${practice.name}`}
      >
        {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Phone className="w-3 h-3" />}
        {busy ? "Creating..." : label}
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
        practice={latest}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  )
}
