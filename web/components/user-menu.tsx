"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { LogOut, UserCog, KeyRound, ChevronDown, Plug, Target } from "lucide-react"
import { useAuth } from "@/lib/auth"
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
        className="flex items-center gap-2 text-sm px-2 py-1 rounded-lg hover:bg-gray-100 transition"
      >
        <div className="w-7 h-7 rounded-full bg-teal-600 text-white grid place-items-center text-xs font-semibold">
          {(user.name?.[0] ?? user.email[0]).toUpperCase()}
        </div>
        <span className="text-gray-700 max-w-[140px] truncate">
          {user.name ?? user.email}
        </span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-56 bg-white rounded-lg border border-gray-200 shadow-md z-30 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100">
            <p className="text-sm font-medium text-gray-900 truncate">
              {user.name ?? user.email}
            </p>
            <p className="text-xs text-gray-500 truncate">{user.email}</p>
            <p className="text-[10px] uppercase tracking-wide text-gray-400 mt-0.5">
              {user.role}
              {user.is_bootstrap_admin ? " · bootstrap" : ""}
            </p>
          </div>
          {user.role === "admin" && (
            <>
              <Link
                href="/admin/icp"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                <Target className="w-4 h-4" /> ICP definition
              </Link>
              <Link
                href="/admin/users"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                <UserCog className="w-4 h-4" /> Users
              </Link>
              <Link
                href="/admin/integrations"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                <Plug className="w-4 h-4" /> Integrations
              </Link>
            </>
          )}
          <button
            onClick={() => {
              setOpen(false)
              setPwOpen(true)
            }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 text-left"
          >
            <KeyRound className="w-4 h-4" /> Change password
          </button>
          <button
            onClick={signOut}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 text-left border-t border-gray-100"
          >
            <LogOut className="w-4 h-4" /> Sign out
          </button>
        </div>
      )}

      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </div>
  )
}
