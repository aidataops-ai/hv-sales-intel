"use client"

import { useEffect, useRef } from "react"
import type { Practice } from "./types"
import { getPractice } from "./api"

const BASE_INTERVAL_MS = 5_000
const MAX_INTERVAL_MS = 20_000
// 5s + 10s + 20s × 8 ≈ 175s — the same ~3 min watch window the old fixed 5s
// cadence covered, for 10 requests instead of 36.
const MAX_POLLS = 10

/**
 * While `practice.enrichment_status === 'pending'`, re-fetch the practice on a
 * backing-off schedule (5s → 10s → 20s, then every 20s) and hand each result
 * to `onUpdate`. Stops once the status leaves 'pending' or after MAX_POLLS.
 *
 * `/api/practices/{id}` is the heaviest detail route, so the cadence matters:
 * an enriching card used to cost 36 of them. The poll count and the current
 * delay both live in refs because callers typically pass an inline `onUpdate`,
 * which re-runs this effect on every render — without the refs the backoff
 * would reset to 5s each time and never actually back off.
 */
export function useEnrichmentPoll(
  practice: Practice,
  onUpdate: (next: Practice) => void,
) {
  const pollsRef = useRef(0)
  const delayRef = useRef(BASE_INTERVAL_MS)

  useEffect(() => {
    if (practice.enrichment_status !== "pending") {
      pollsRef.current = 0
      delayRef.current = BASE_INTERVAL_MS
      return
    }

    let cancelled = false
    let handle = 0

    function schedule() {
      handle = window.setTimeout(async () => {
        if (cancelled || pollsRef.current >= MAX_POLLS) return
        pollsRef.current += 1
        delayRef.current = Math.min(delayRef.current * 2, MAX_INTERVAL_MS)
        try {
          const fresh = await getPractice(practice.place_id)
          if (cancelled) return
          onUpdate(fresh)
          if (fresh.enrichment_status !== "pending") return
        } catch {
          // swallow — the next tick retries
        }
        if (!cancelled) schedule()
      }, delayRef.current)
    }

    schedule()

    return () => {
      cancelled = true
      window.clearTimeout(handle)
    }
  }, [practice.place_id, practice.enrichment_status, onUpdate])
}
