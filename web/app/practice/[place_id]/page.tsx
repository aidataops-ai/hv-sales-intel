"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { ArrowLeft, MoreVertical } from "lucide-react"
import type { Practice, ScriptSection } from "@/lib/types"
import { getScript, regenerateScript, updatePractice } from "@/lib/api"
import { mockPractices } from "@/lib/mock-data"
import { timeAgo } from "@/lib/utils"
import PracticeInfo from "@/components/practice-info"
import ScriptView from "@/components/script-view"
import NotesPanel from "@/components/notes-panel"
import ActionsPanel from "@/components/actions-panel"
import EmailPanel from "@/components/email-panel"
import CallLogTab from "@/components/call-log-tab"
import StatusBadge, { ALL_STATUSES } from "@/components/status-badge"
import AssignDropdown from "@/components/assign-dropdown"
import { useAuth } from "@/lib/auth"

export default function CallPrepPage() {
  const params = useParams()
  const router = useRouter()
  const placeId = params.place_id as string
  const { user: currentUser } = useAuth()

  const [practice, setPractice] = useState<Practice | null>(null)
  const [sections, setSections] = useState<ScriptSection[]>([])
  const [isLoadingScript, setIsLoadingScript] = useState(true)

  // Load practice data
  useEffect(() => {
    async function load() {
      let loaded = false
      try {
        const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
        const IS_PROD = process.env.NODE_ENV === "production"
        // In production, an empty NEXT_PUBLIC_API_URL means same-origin
        // (Vercel rewrites /api/* to the FastAPI handler).
        if (API_URL || IS_PROD) {
          const res = await fetch(`${API_URL}/api/practices/${placeId}`, { credentials: "include" })
          if (res.ok) {
            setPractice(await res.json())
            loaded = true
          }
        }
      } catch {
        // fallback below
      }

      if (!loaded) {
        const mock = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
        setPractice(mock)
      }
    }
    load()
  }, [placeId])

  // Load script
  useEffect(() => {
    async function loadScript() {
      setIsLoadingScript(true)
      try {
        const script = await getScript(placeId)
        setSections(script.sections)
      } finally {
        setIsLoadingScript(false)
      }
    }
    loadScript()
  }, [placeId])

  const handleRegenerate = useCallback(async () => {
    setIsLoadingScript(true)
    try {
      const script = await regenerateScript(placeId)
      setSections(script.sections)
    } finally {
      setIsLoadingScript(false)
    }
  }, [placeId])

  const handleStatusChange = useCallback(async (newStatus: string) => {
    const updated = await updatePractice(placeId, { status: newStatus })
    setPractice((prev) => (prev ? { ...prev, ...updated } : prev))
  }, [placeId])

  const handleSaveNotes = useCallback(async (notes: string) => {
    const updated = await updatePractice(placeId, { notes })
    setPractice((prev) => (prev ? { ...prev, ...updated } : prev))
  }, [placeId])

  if (!practice) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-cream">
      {/* Header */}
      <header className="sticky top-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push("/")}
            className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 transition"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Map
          </button>
          <span className="font-serif text-lg font-bold text-teal-700 tracking-tight">
            Apex&amp;Virtuals
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">Status:</span>
          <select
            value={practice.status}
            onChange={(e) => handleStatusChange(e.target.value)}
            className="text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40"
          >
            {ALL_STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <StatusBadge status={practice.status} />
          {currentUser?.role === "admin" && (
            <>
              <span className="text-sm text-gray-500 ml-2">Owner:</span>
              <AssignDropdown
                practice={practice}
                onChange={(next) =>
                  setPractice((prev) => (prev ? { ...prev, ...next } : prev))
                }
              />
            </>
          )}
          {practice.last_touched_by_name && (
            <div className="flex items-center gap-2 ml-2">
              <div className="w-8 h-8 rounded-full bg-teal-600 text-white grid place-items-center text-xs font-semibold">
                {practice.last_touched_by_name[0]?.toUpperCase()}
              </div>
              <div className="leading-tight">
                <p className="text-xs font-medium text-gray-700">
                  by {practice.last_touched_by_name}
                </p>
                {practice.last_touched_at && (
                  <p className="text-[11px] text-gray-400">
                    {timeAgo(practice.last_touched_at)}
                  </p>
                )}
              </div>
            </div>
          )}
          <button
            onClick={() =>
              window.open(
                `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
                  `${practice.name} ${practice.address}`,
                )}`,
                "_blank",
                "noopener",
              )
            }
            title="Open in Google Maps"
            className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 transition"
          >
            <MoreVertical className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Three-column layout */}
      <div className="flex h-[calc(100vh-3.5rem)]">
        {/* Left: Practice Info */}
        <aside className="w-[280px] shrink-0 overflow-y-auto p-5 border-r border-gray-200/50">
          <PracticeInfo practice={practice} />
        </aside>

        {/* Center: Call Playbook */}
        <main className="flex-1 overflow-y-auto p-6">
          <ScriptView
            sections={sections}
            isLoading={isLoadingScript}
            onRegenerate={handleRegenerate}
          />
        </main>

        {/* Right: Tabbed actions panel */}
        <aside className="w-[340px] shrink-0 overflow-y-auto p-5 border-l border-gray-200/50">
          <ActionsPanel
            defaultTab="notes"
            tabs={[
              { id: "notes", label: "Notes" },
              { id: "email", label: "Email" },
              { id: "calllog", label: "Call log" },
            ]}
            renderTab={(id) => {
              if (id === "notes") {
                return (
                  <NotesPanel
                    notes={practice.notes ?? ""}
                    onSave={handleSaveNotes}
                  />
                )
              }
              if (id === "email") {
                return (
                  <EmailPanel
                    practice={practice}
                    onPracticeUpdate={(next) =>
                      setPractice((prev) => (prev ? { ...prev, ...next } : prev))
                    }
                  />
                )
              }
              if (id === "calllog") {
                return (
                  <CallLogTab
                    practice={practice}
                    onLogged={(response) => {
                      setPractice((prev) =>
                        prev ? { ...prev, ...response.practice } : prev,
                      )
                      if (response.sf_warning) {
                        console.warn("[SF]", response.sf_warning)
                      }
                    }}
                  />
                )
              }
              return null
            }}
          />
        </aside>
      </div>
    </div>
  )
}
