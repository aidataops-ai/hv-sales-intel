"use client"

import Link from "next/link"
import { Settings } from "lucide-react"

import { useAuth } from "@/lib/auth"

/**
 * Link to the Instant Signals config page (`/signals/config`).
 *
 * Admin only, and rendered as nothing for everyone else — the same choice as
 * `RetriggerButton`: every write behind the page is `require_admin`, so a
 * non-admin gets no control that hints at an action they can't take.
 */
export default function ConfigButton() {
  const { user } = useAuth()
  if (user?.role !== "admin") return null

  return (
    <Link
      href="/signals/config"
      className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] text-sm font-medium hover:bg-gray-50 dark:hover:bg-white/10 transition"
      title="Configure the collector: states, cities, tracks & keywords (admin)"
    >
      <Settings className="w-4 h-4" />
      Configure
    </Link>
  )
}
