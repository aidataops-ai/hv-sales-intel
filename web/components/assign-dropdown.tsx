"use client"

import { useEffect, useState } from "react"
import { listUsers, updatePractice, type AdminUserSummary } from "@/lib/api"
import type { Practice } from "@/lib/types"

interface Props {
  practice: Practice
  onChange: (next: Partial<Practice>) => void
}

export default function AssignDropdown({ practice, onChange }: Props) {
  const [users, setUsers] = useState<AdminUserSummary[]>([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [])

  async function handleChange(value: string) {
    setSaving(true)
    try {
      const updated = await updatePractice(practice.place_id, { assigned_to: value })
      onChange(updated)
    } finally {
      setSaving(false)
    }
  }

  return (
    <select
      value={practice.assigned_to ?? ""}
      onChange={(e) => handleChange(e.target.value)}
      disabled={saving}
      className="text-sm rounded-lg border border-gray-200 bg-white/80 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 px-3 py-1.5 disabled:opacity-50"
    >
      <option value="">Unassigned</option>
      {users.map((u) => (
        <option key={u.id} value={u.id}>
          {u.name ?? u.email}
        </option>
      ))}
    </select>
  )
}
