"use client"

import { useState } from "react"
import { Loader2, Sparkles } from "lucide-react"
import type { Practice } from "@/lib/types"
import { enrichPractice, type EnrichResponse } from "@/lib/api"
import { SHOW_BILLING } from "@/lib/flags"

interface EnrichButtonProps {
  practice: Practice
  onEnriched: (response: EnrichResponse) => void
  className: string
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void
}

export default function EnrichButton({
  practice,
  onEnriched,
  className,
  onClick,
}: EnrichButtonProps) {
  const [submitting, setSubmitting] = useState(false)
  const isPending = practice.enrichment_status === "pending" || submitting
  const isAlreadyEnriched =
    practice.enrichment_status === "enriched" ||
    practice.enrichment_status === "failed"

  async function handleClick(e: React.MouseEvent<HTMLButtonElement>) {
    onClick?.(e)
    if (isPending) return
    setSubmitting(true)
    try {
      const response = await enrichPractice(practice.place_id)
      onEnriched(response)
      if (response.clay_warning) {
        console.warn("[Clay]", response.clay_warning)
      }
    } finally {
      setSubmitting(false)
    }
  }

  const label = isPending
    ? "Enriching…"
    : isAlreadyEnriched
      ? "Re-enrich"
      : "Enrich owner"

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isPending}
      title={
        isAlreadyEnriched
          ? SHOW_BILLING
            ? "Re-enrich (uses Clay credits)"
            : "Re-enrich owner"
          : "Find owner via Clay"
      }
      className={className}
    >
      {isPending ? (
        <Loader2 className="w-3 h-3 animate-spin" />
      ) : (
        <Sparkles className="w-3 h-3" />
      )}
      {label}
    </button>
  )
}
