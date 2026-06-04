"use client"

import { useState, useEffect, useCallback } from "react"
import Link from "next/link"
import { ArrowLeft, Trash2, Loader2 } from "lucide-react"
import { useAuth } from "@/lib/auth"

interface AdminUser {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
  disabled_at: string | null
  created_at: string
  practices_touched: number
}

export default function AdminUsersPage() {
  const { user, loading: authLoading } = useAuth()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "sdr" })

  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
  const [resetPassword, setResetPassword] = useState("")
  const [resetting, setResetting] = useState(false)

  const [editTarget, setEditTarget] = useState<AdminUser | null>(null)
  const [editName, setEditName] = useState("")
  const [editRole, setEditRole] = useState<"admin" | "sdr">("sdr")
  const [editing, setEditing] = useState(false)

  const API_URL = process.env.NEXT_PUBLIC_API_URL || ""

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, { credentials: "include" })
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setUsers(data.users)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [API_URL])

  useEffect(() => {
    if (user?.role === "admin") load()
  }, [user, load])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setCreating(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/users`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setForm({ email: "", name: "", password: "", role: "sdr" })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this user?")) return
    const res = await fetch(`${API_URL}/api/admin/users/${id}`, {
      method: "DELETE",
      credentials: "include",
    })
    if (res.ok) load()
    else setError(`HTTP ${res.status}`)
  }

  async function patchUser(id: string, body: { name?: string; role?: string; disabled?: boolean }) {
    const res = await fetch(`${API_URL}/api/admin/users/${id}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail ?? `HTTP ${res.status}`)
    }
    return res.json()
  }

  async function handleEditSave() {
    if (!editTarget) return
    setEditing(true)
    setError(null)
    try {
      const body: { name?: string; role?: string } = {}
      if (editName !== (editTarget.name ?? "")) body.name = editName
      if (editRole !== editTarget.role) body.role = editRole
      if (Object.keys(body).length > 0) {
        await patchUser(editTarget.id, body)
      }
      setEditTarget(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setEditing(false)
    }
  }

  function openEdit(u: AdminUser) {
    setEditTarget(u)
    setEditName(u.name ?? "")
    setEditRole(u.role)
  }

  async function handleToggleDisable(u: AdminUser) {
    const action = u.disabled_at ? "enable" : "disable"
    if (!confirm(`${action.charAt(0).toUpperCase() + action.slice(1)} ${u.email}?`)) return
    setError(null)
    try {
      await patchUser(u.id, { disabled: !u.disabled_at })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleReset(target: AdminUser) {
    if (!resetPassword) return
    setResetting(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/admin/users/${target.id}/reset-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: resetPassword }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setResetTarget(null)
      setResetPassword("")
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setResetting(false)
    }
  }

  if (authLoading) return <div className="p-10 text-gray-500 dark:text-gray-400">Loading...</div>

  if (user?.role !== "admin") {
    return (
      <div className="min-h-screen bg-cream dark:bg-night p-10">
        <p className="text-rose-600 font-medium">Admin only</p>
        <Link href="/" className="text-sm text-teal-700 dark:text-teal-400 underline">Back to map</Link>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-cream dark:bg-night">
      <header className="sticky top-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 dark:bg-night-800 backdrop-blur-md border-b border-gray-200/50 dark:border-white/10">
        <Link href="/" className="inline-flex items-center gap-1 text-sm text-gray-600 dark:text-[#d9d9d9] hover:text-gray-900 dark:hover:text-white">
          <ArrowLeft className="w-4 h-4" /> Back to Map
        </Link>
        <span className="font-serif text-lg font-bold text-teal-700 dark:text-teal-400">Users</span>
        <span />
      </header>

      <main className="max-w-4xl mx-auto p-8 space-y-8">
        <section>
          <h2 className="font-serif text-xl font-bold mb-4 dark:text-white">Create user</h2>
          <form onSubmit={handleCreate} className="grid grid-cols-2 gap-3 bg-white/80 dark:bg-night-800 p-4 rounded-xl">
            <input
              placeholder="Email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              required
              className="text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
            />
            <input
              placeholder="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              className="text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
            />
            <input
              placeholder="Initial password (8+ chars, mixed case, number, special)"
              type="text"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              minLength={8}
              className="text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
            />
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white px-3 py-2"
            >
              <option value="sdr">SDR</option>
              <option value="admin">Admin</option>
            </select>
            <button
              type="submit"
              disabled={creating}
              className="col-span-2 text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition inline-flex items-center justify-center gap-2"
            >
              {creating && <Loader2 className="w-4 h-4 animate-spin" />}
              {creating ? "Creating..." : "Create user"}
            </button>
          </form>
          {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
        </section>

        <section>
          <h2 className="font-serif text-xl font-bold mb-4 dark:text-white">All users</h2>
          {loading ? (
            <p className="text-gray-500 dark:text-gray-400">Loading...</p>
          ) : (
            <table className="w-full bg-white/80 dark:bg-night-800 rounded-xl text-sm">
              <thead>
                <tr className="text-left text-gray-500 dark:text-gray-400 text-xs uppercase tracking-wide">
                  <th className="p-3">Email</th>
                  <th className="p-3">Name</th>
                  <th className="p-3">Role</th>
                  <th className="p-3">Touched</th>
                  <th className="p-3">Created</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const blocked = u.role === "admin" && !user.is_bootstrap_admin
                  const disabled = !!u.disabled_at
                  return (
                    <tr
                      key={u.id}
                      className={`border-t border-gray-200/50 dark:border-white/10 dark:text-[#d9d9d9] ${disabled ? "opacity-50" : ""}`}
                    >
                      <td className="p-3">
                        {u.email}
                        {disabled && (
                          <span className="ml-2 text-[10px] uppercase tracking-wide bg-gray-200 dark:bg-white/10 text-gray-700 dark:text-[#d9d9d9] px-1.5 py-0.5 rounded">
                            Disabled
                          </span>
                        )}
                      </td>
                      <td className="p-3">{u.name ?? "—"}</td>
                      <td className="p-3 uppercase">{u.role}</td>
                      <td className="p-3">{u.practices_touched}</td>
                      <td className="p-3 text-gray-500 dark:text-gray-400">{u.created_at.slice(0, 10)}</td>
                      <td className="p-3 text-right space-x-3">
                        {u.id !== user.id && (
                          <>
                            <button
                              onClick={() => !blocked && openEdit(u)}
                              disabled={blocked}
                              title={blocked
                                ? "Only the bootstrap admin can edit another admin."
                                : "Edit user"}
                              className={`text-xs underline ${blocked
                                ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                                : "text-teal-700 dark:text-teal-400 hover:text-teal-900"}`}
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => !blocked && setResetTarget(u)}
                              disabled={blocked}
                              title={blocked
                                ? "Only the bootstrap admin can reset another admin's password."
                                : "Reset password"}
                              className={`text-xs underline ${blocked
                                ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                                : "text-teal-700 dark:text-teal-400 hover:text-teal-900"}`}
                            >
                              Reset password
                            </button>
                            <button
                              onClick={() => !blocked && handleToggleDisable(u)}
                              disabled={blocked}
                              title={blocked
                                ? "Only the bootstrap admin can disable another admin."
                                : disabled ? "Enable user" : "Disable user"}
                              className={`text-xs underline ${blocked
                                ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                                : disabled
                                  ? "text-teal-700 dark:text-teal-400 hover:text-teal-900"
                                  : "text-amber-700 dark:text-amber-400 hover:text-amber-900"}`}
                            >
                              {disabled ? "Enable" : "Disable"}
                            </button>
                            <button
                              onClick={() => !blocked && handleDelete(u.id)}
                              disabled={blocked}
                              title={blocked
                                ? "Only the bootstrap admin can delete another admin"
                                : "Delete user"}
                              className={`align-middle ${blocked
                                ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                                : "text-rose-600 dark:text-rose-400 hover:text-rose-800"}`}
                            >
                              <Trash2 className="w-4 h-4 inline" />
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </section>

        {editTarget && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
            onClick={() => !editing && setEditTarget(null)}
          >
            <div
              className="w-full max-w-sm rounded-xl bg-white dark:bg-night-800 shadow-xl p-5 space-y-3"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="font-serif text-base font-bold dark:text-white">
                Edit {editTarget.email}
              </h3>
              <label className="block">
                <span className="text-xs text-gray-500 dark:text-gray-400">Name</span>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full text-sm mt-1 rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-500 dark:text-gray-400">Role</span>
                <select
                  value={editRole}
                  onChange={(e) => setEditRole(e.target.value as "admin" | "sdr")}
                  disabled={!user.is_bootstrap_admin && (editTarget.role === "admin" || editRole === "admin")}
                  className="w-full text-sm mt-1 rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white px-3 py-2"
                >
                  <option value="sdr">SDR</option>
                  <option value="admin">Admin</option>
                </select>
                {!user.is_bootstrap_admin && (
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">
                    Only the bootstrap admin can change admin roles.
                  </span>
                )}
              </label>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={() => setEditTarget(null)}
                  disabled={editing}
                  className="text-xs px-3 py-1.5 rounded-lg text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleEditSave}
                  disabled={editing}
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
                >
                  {editing && <Loader2 className="w-3 h-3 animate-spin" />}
                  {editing ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </div>
        )}

        {resetTarget && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
            onClick={() => !resetting && setResetTarget(null)}
          >
            <div
              className="w-full max-w-sm rounded-xl bg-white dark:bg-night-800 shadow-xl p-5 space-y-3"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="font-serif text-base font-bold dark:text-white">
                Reset password for {resetTarget.email}
              </h3>
              <input
                type="text"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                placeholder="New password"
                className="w-full text-sm rounded-lg border border-gray-200 dark:border-white/10 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 px-3 py-2"
              />
              <p className="text-[11px] text-gray-500 dark:text-gray-400">
                Min 8 chars · 1 upper · 1 lower · 1 number · 1 special.
              </p>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={() => setResetTarget(null)}
                  disabled={resetting}
                  className="text-xs px-3 py-1.5 rounded-lg text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-100 dark:hover:bg-white/10 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={() => handleReset(resetTarget)}
                  disabled={resetting || !resetPassword}
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
                >
                  {resetting && <Loader2 className="w-3 h-3 animate-spin" />}
                  {resetting ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
