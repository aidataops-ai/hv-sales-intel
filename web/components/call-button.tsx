"use client"

import { useEffect, useState } from "react"
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

  // Keep `latest` in sync with the parent's practice prop (e.g. after the
  // home page refreshes a card) so the duplicate-Lead check below sees
  // freshly-stored salesforce_lead_id values.
  useEffect(() => {
    setLatest(practice)
  }, [practice])

  if (!practice.phone) return null

  async function handleClick(event: React.MouseEvent<HTMLButtonElement>) {
    onClick?.(event)

    // Lead already exists in Salesforce — skip the API round-trip, just
    // open the popup with the existing Lightning link from our DB.
    if (latest.salesforce_lead_id) {
      setError(null)
      setOpen(true)
      return
    }

    setBusy(true)
    setError(null)
    try {
      // No Lead yet — create one via the call/log endpoint. Backend
      // returns the practice with salesforce_lead_id + salesforce_lead_url.
      const response = await logCall(practice.place_id, "")
      onLogged?.(response)
      setLatest(response.practice)
      setOpen(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create Lead")
    } finally {
      setBusy(false)
    }
  }

  const hasExistingLead = Boolean(latest.salesforce_lead_id)

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={className}
        title={
          hasExistingLead
            ? "Open the Salesforce Lead for this practice"
            : `Create Salesforce Lead for ${practice.name}`
        }
      >
        {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Phone className="w-3 h-3" />}
        {busy ? "Creating..." : hasExistingLead ? "Open Lead" : label}
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
