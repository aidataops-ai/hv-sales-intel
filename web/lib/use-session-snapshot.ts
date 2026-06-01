"use client"

import { useEffect, useRef } from "react"
import type { Practice } from "@/lib/types"
import type { FilterState } from "./use-url-state"

const KEY = "leads-workspace-snapshot-v1"
const TTL_MS = 30 * 60 * 1000 // 30 min

export interface Snapshot {
  practices: Practice[]
  filters: FilterState
  total: number
  scrollTop: number
  savedAt: number
}

export function readSnapshot(): Snapshot | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.sessionStorage.getItem(KEY)
    if (!raw) return null
    const snap = JSON.parse(raw) as Snapshot
    if (Date.now() - snap.savedAt > TTL_MS) return null
    return snap
  } catch {
    return null
  }
}

export function writeSnapshot(snap: Omit<Snapshot, "savedAt">) {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(
      KEY,
      JSON.stringify({ ...snap, savedAt: Date.now() }),
    )
  } catch {
    // sessionStorage might be full; fail silently.
  }
}

export function clearSnapshot() {
  if (typeof window === "undefined") return
  window.sessionStorage.removeItem(KEY)
}

export function useSessionSnapshot(
  practices: Practice[],
  filters: FilterState,
  total: number,
  scrollContainerRef: React.RefObject<HTMLElement | null>,
) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      writeSnapshot({
        practices,
        filters,
        total,
        scrollTop: scrollContainerRef.current?.scrollTop ?? 0,
      })
    }, 200)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [practices, filters, total, scrollContainerRef])
}
