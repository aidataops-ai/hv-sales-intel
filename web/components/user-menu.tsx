"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { LogOut, UserCog, KeyRound, ChevronDown, Plug, Target, Activity, Coins } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { SHOW_BILLING, SHOW_INTEGRATIONS } from "@/lib/flags"
import ChangePasswordModal from "./change-password-modal"

export default function UserMenu() {
  const { user, loading, signOut } = useAuth()
  const [pwOpen, setPwOpen] = useState(false)
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

  if (loading || !user) return null

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-sm px-2 py-1 rounded-lg hover:bg-gray-100 dark:hover:bg-white/10 transition"
      >
        <div className="w-7 h-7 rounded-full bg-teal-600 text-white grid place-items-center text-xs font-semibold">
          {(user.name?.[0] ?? user.email[0]).toUpperCase()}
        </div>
        <span className="text-gray-700 dark:text-[#d9d9d9] max-w-[140px] truncate">
          {user.name ?? user.email}
        </span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-56 bg-white dark:bg-night-800 rounded-lg border border-gray-200 dark:border-white/10 shadow-md z-30 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100 dark:border-white/10">
            <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
              {user.name ?? user.email}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{user.email}</p>
            <p className="text-[10px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mt-0.5">
              {user.role}
              {user.is_bootstrap_admin ? " · bootstrap" : ""}
            </p>
          </div>
          {user.role === "admin" && (
            <>
              <Link
                href="/admin/icp"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
              >
                <Target className="w-4 h-4" /> ICP definition
              </Link>
              <Link
                href="/admin/users"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
              >
                <UserCog className="w-4 h-4" /> Users
              </Link>
              {/* Integrations page is a mock — hidden for demo (SHOW_INTEGRATIONS in lib/flags.ts). */}
              {SHOW_INTEGRATIONS && (
                <Link
                  href="/admin/integrations"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
                >
                  <Plug className="w-4 h-4" /> Integrations
                </Link>
              )}
              {/* Cost/credit pages hidden for demo — see SHOW_BILLING in lib/flags.ts. */}
              {SHOW_BILLING && (
                <>
                  <Link
                    href="/admin/usage"
                    onClick={() => setOpen(false)}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
                  >
                    <Activity className="w-4 h-4" /> Usage &amp; cost
                  </Link>
                  <Link
                    href="/admin/credits"
                    onClick={() => setOpen(false)}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5"
                  >
                    <Coins className="w-4 h-4" /> Credits
                  </Link>
                </>
              )}
            </>
          )}
          <button
            onClick={() => {
              setOpen(false)
              setPwOpen(true)
            }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5 text-left"
          >
            <KeyRound className="w-4 h-4" /> Change password
          </button>
          <button
            onClick={signOut}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/5 text-left border-t border-gray-100 dark:border-white/10"
          >
            <LogOut className="w-4 h-4" /> Sign out
          </button>
        </div>
      )}

      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </div>
  )
}
