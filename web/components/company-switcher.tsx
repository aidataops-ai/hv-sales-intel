"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Building2, Check, ChevronDown, Plus } from "lucide-react"
import { useAuth } from "@/lib/auth"

/**
 * Topbar dropdown that shows the current company and lets an admin
 * switch between every company they belong to.
 *
 * Visibility rules:
 *   - Hidden entirely for SDRs (per the locked design choice).
 *   - Hidden when the user belongs to a single company (nothing to
 *     switch to, and a "Create new company" CTA shouldn't live in
 *     the topbar permanently — the /signup page handles that).
 */
export default function CompanySwitcher() {
  const { user, companies, currentCompany, switchCompany } = useAuth()
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [open])

  if (!user || user.role !== "admin") return null
  if (!currentCompany) return null
  if (companies.length < 2) return null

  const label =
    currentCompany.branding?.short_name ||
    currentCompany.branding?.display_name ||
    currentCompany.name

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] text-sm font-medium hover:bg-gray-50 dark:hover:bg-white/10 transition max-w-[180px]"
        title="Switch active company"
      >
        <Building2 className="w-4 h-4 shrink-0" />
        <span className="truncate">{label}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400 shrink-0" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-64 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md z-30 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100 dark:border-white/10">
            <p className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500">
              Switch company
            </p>
          </div>
          <div className="max-h-72 overflow-y-auto">
            {companies.map((c) => {
              const isActive = c.is_current
              return (
                <button
                  key={c.id}
                  onClick={() => {
                    if (!isActive) switchCompany(c.id)
                    else setOpen(false)
                  }}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5 text-left"
                >
                  <span className="flex items-center gap-2 truncate">
                    <Building2 className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0" />
                    <span className="truncate">{c.name}</span>
                    <span className="text-[10px] uppercase tracking-wide text-gray-400 dark:text-gray-500 shrink-0">
                      {c.role}
                    </span>
                  </span>
                  {isActive && <Check className="w-3.5 h-3.5 text-teal-600 dark:text-teal-400 shrink-0" />}
                </button>
              )
            })}
          </div>
          <Link
            href="/signup"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-teal-700 dark:text-teal-400 hover:bg-teal-50 dark:hover:bg-[#284b63]/40 border-t border-gray-100 dark:border-white/10"
          >
            <Plus className="w-3.5 h-3.5" /> Create a new company
          </Link>
        </div>
      )}
    </div>
  )
}
