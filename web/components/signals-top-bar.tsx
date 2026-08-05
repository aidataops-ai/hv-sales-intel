"use client"

import Link from "next/link"
import { ArrowLeft, BarChart3 } from "lucide-react"

import CompanySwitcher from "./company-switcher"
import CreditBalance from "./credit-balance"
import ThemeToggle from "./theme-toggle"
import UserMenu from "./user-menu"

/**
 * Shell header for the Instant Signals section.
 *
 * A slim sibling of `top-bar.tsx` rather than a variant of it: that component's
 * props are all practice-search actions (rescan, bulk scan, score loaded) which
 * have no meaning here, and threading a mode flag through it would leave every
 * caller passing no-ops. The right-hand shell controls are the shared ones.
 */
export default function SignalsTopBar({
  children,
}: {
  children?: React.ReactNode
}) {
  return (
    <header className="fixed top-0 left-0 right-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 dark:bg-night-800 backdrop-blur-md border-b border-gray-200/50 dark:border-white/10">
      <div className="flex items-center gap-3">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-400 transition"
          title="Back to practices"
        >
          <ArrowLeft className="w-4 h-4" />
          Practices
        </Link>
        <span className="text-gray-300 dark:text-white/20">/</span>
        <Link
          href="/signals"
          className="font-serif text-lg font-bold text-teal-700 dark:text-teal-400 tracking-tight"
        >
          Instant Signals
        </Link>
      </div>
      <div className="flex items-center gap-3">
        {children}
        <Link
          href="/signals/analytics"
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] text-sm font-medium hover:bg-gray-50 dark:hover:bg-white/10 transition"
        >
          <BarChart3 className="w-4 h-4" />
          Analytics
        </Link>
        <CreditBalance />
        <ThemeToggle />
        <CompanySwitcher />
        <UserMenu />
      </div>
    </header>
  )
}
