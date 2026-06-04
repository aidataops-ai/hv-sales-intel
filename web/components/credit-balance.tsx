"use client"

import Link from "next/link"
import { Coins } from "lucide-react"
import { useCredits, formatCredits, creditsToDollars } from "@/lib/credits"
import { useAuth } from "@/lib/auth"
import { SHOW_BILLING } from "@/lib/flags"

/**
 * Topbar pill showing the active company's prepaid credit balance.
 *
 * Colour-coded by remaining headroom:
 *   green  > 50      — comfortable
 *   amber  10–50     — heads-up to top up soon
 *   red    < 10      — block warning
 *
 * Clicks route to /admin/credits for admins (manage / top-up) and to
 * a read-only tooltip-style hover for SDRs.
 */
export default function CreditBalance() {
  const { data, loading } = useCredits()
  const { user } = useAuth()

  // Hidden for demo — see SHOW_BILLING in lib/flags.ts.
  if (!SHOW_BILLING) return null

  if (loading && !data) {
    return (
      <div className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-gray-100 dark:bg-white/5 text-gray-500 dark:text-gray-400 text-xs animate-pulse">
        <Coins className="w-3.5 h-3.5" />
        …
      </div>
    )
  }

  if (!data) return null

  const balance = data.balance
  const tone =
    balance < 10
      ? "bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/20 dark:text-rose-300 dark:border-rose-500/30"
      : balance < 50
        ? "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/20 dark:text-amber-300 dark:border-amber-500/30"
        : "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-500/20 dark:text-emerald-300 dark:border-emerald-500/30"

  const inner = (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs font-medium ${tone}`}
      title={`${formatCredits(balance)} credits remaining (~${creditsToDollars(
        balance,
      )}). 1 credit = $0.33.`}
    >
      <Coins className="w-3.5 h-3.5" />
      <span className="tabular-nums">{formatCredits(balance)}</span>
      <span className="text-[10px] opacity-70 uppercase tracking-wide">credits</span>
    </span>
  )

  // Admins can manage credits; SDRs see a read-only pill.
  if (user?.role === "admin") {
    return (
      <Link href="/admin/credits" className="hover:opacity-90 transition">
        {inner}
      </Link>
    )
  }
  return inner
}
