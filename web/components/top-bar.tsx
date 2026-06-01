"use client"

import { useState } from "react"
import { Brain, RefreshCw, Layers } from "lucide-react"
import SearchBar from "./search-bar"
import UserMenu from "./user-menu"
import ExportButton from "./export-button"
import BulkScanModal from "./bulk-scan-modal"
import CompanySwitcher from "./company-switcher"
import CreditBalance from "./credit-balance"

interface TopBarProps {
  onSearch: (query: string) => void
  isLoading: boolean
  onScoreAll: () => void
  scoreProgress: string | null
  onRescan: () => void
  canRescan: boolean
  isRescanning: boolean
  currentQuery: string
  /** Called after a bulk scan finishes so the page can re-fetch from DB. */
  onBulkScanComplete: () => void
}

export default function TopBar({
  onSearch,
  isLoading,
  onScoreAll,
  scoreProgress,
  onRescan,
  canRescan,
  isRescanning,
  currentQuery,
  onBulkScanComplete,
}: TopBarProps) {
  const [bulkOpen, setBulkOpen] = useState(false)

  return (
    <header className="fixed top-0 left-0 right-0 z-20 h-14 flex items-center justify-between px-6 bg-white/70 backdrop-blur-md border-b border-gray-200/50">
      <div className="flex items-center gap-2">
        <span className="font-serif text-lg font-bold text-teal-700 tracking-tight">
          Apex&amp;Virtuals
        </span>
        <span className="text-xs text-gray-400 font-medium">Sales Intel</span>
      </div>
      <div className="flex items-center gap-3">
        <SearchBar onSearch={onSearch} isLoading={isLoading} currentQuery={currentQuery} />
        <button
          onClick={onRescan}
          disabled={!canRescan || isRescanning}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 text-gray-700 text-sm font-medium hover:bg-gray-50 disabled:opacity-50 transition"
        >
          <RefreshCw className={`w-4 h-4 ${isRescanning ? "animate-spin" : ""}`} />
          {isRescanning ? "Rescanning..." : "Rescan"}
        </button>
        <button
          onClick={() => setBulkOpen(true)}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 text-gray-700 text-sm font-medium hover:bg-gray-50 transition"
          title="Run a batch of targeted lead searches across many cities in sequence"
        >
          <Layers className="w-4 h-4" />
          Bulk Scan
        </button>
        <button
          onClick={onScoreAll}
          disabled={!!scoreProgress}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-teal-600 text-teal-700 text-sm font-medium hover:bg-teal-50 disabled:opacity-50 transition"
        >
          <Brain className="w-4 h-4" />
          {scoreProgress ?? "Score loaded"}
        </button>
        <ExportButton />
        <CreditBalance />
        <CompanySwitcher />
        <UserMenu />
      </div>

      <BulkScanModal
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        onComplete={onBulkScanComplete}
      />
    </header>
  )
}
