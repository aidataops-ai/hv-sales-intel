"use client"

import { useEffect, useState } from "react"
import { listUsers, type AdminUserSummary } from "@/lib/api"
import type { User } from "@/lib/types"

interface Props {
  selected: string
  onChange: (next: string) => void
  currentUser: User | null
}

export default function OwnerFilter({ selected, onChange, currentUser }: Props) {
  const [users, setUsers] = useState<AdminUserSummary[]>([])

  useEffect(() => {
    if (!currentUser || currentUser.role !== "admin") return
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [currentUser])

  if (!currentUser) {
    return (
      <select
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        className="w-full text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-2 text-gray-700"
      >
        <option value="">All owners</option>
      </select>
    )
  }

  const options =
    currentUser.role === "admin"
      ? [
          { id: "", name: "All owners" },
          ...users.map((u) => ({ id: u.id, name: u.name ?? u.email })),
        ]
      : [
          { id: "", name: "All" },
          { id: currentUser.id, name: "Me" },
        ]

  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
      className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5"
    >
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  )
}
