"use client"

import { useState } from "react"
import Link from "next/link"
import { Globe, Star, Brain, Loader2, FileText, ChevronDown, ChevronUp } from "lucide-react"
import type { Practice } from "@/lib/types"
import type { CallLogResponse } from "@/lib/api"
import { parseJsonArray, parseIcpBreakdown } from "@/lib/types"
import { cn, timeAgo } from "@/lib/utils"
import StatusBadge from "./status-badge"
import CallButton from "./call-button"
import EnrichButton from "./enrich-button"
import OwnerMiniCard from "./owner-mini-card"
import { useEnrichmentPoll } from "@/lib/use-enrichment-poll"

function StarRating({ rating }: { rating: number | null }) {
  if (!rating) return null
  const full = Math.floor(rating)
  return (
    <span className="inline-flex items-center gap-0.5">
      {Array.from({ length: 5 }, (_, i) => (
        <Star
          key={i}
          className={cn(
            "w-3.5 h-3.5",
            i < full ? "fill-amber-400 text-amber-400" : "text-gray-300"
          )}
        />
      ))}
      <span className="ml-1 text-sm font-medium text-gray-700">{rating}</span>
    </span>
  )
}

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 75
      ? "bg-rose-100 text-rose-700"
      : score >= 50
        ? "bg-amber-100 text-amber-700"
        : "bg-teal-100 text-teal-700"
  return (
    <span className={cn("text-xs font-bold px-1.5 py-0.5 rounded-full", color)} title="ICP score (0-100)">
      ICP {score}
    </span>
  )
}

interface PracticeCardProps {
  practice: Practice
  isSelected: boolean
  onSelect: (placeId: string) => void
  onAnalyze: (placeId: string, refresh?: boolean) => void
  isAnalyzing: boolean
  onCallLogged?: (response: CallLogResponse) => void
  onEnrichmentUpdate?: (next: Practice) => void
}

export default function PracticeCard({
  practice,
  isSelected,
  onSelect,
  onAnalyze,
  isAnalyzing,
  onCallLogged,
  onEnrichmentUpdate,
}: PracticeCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  useEnrichmentPoll(practice, (next) => onEnrichmentUpdate?.(next))
  const isScored = practice.lead_score != null
  const isIrrelevant = (practice.tags ?? []).includes("IRRELEVANT")
  const painPoints = parseJsonArray(practice.pain_points ?? null)
  const salesAngles = parseJsonArray(practice.sales_angles ?? null)
  const icpBreakdown = parseIcpBreakdown(practice.icp_breakdown ?? null)

  function handleCardClick() {
    onSelect(practice.place_id)
    if (isScored) setIsExpanded((v) => !v)
  }

  return (
    <div
      onClick={isIrrelevant ? undefined : handleCardClick}
      className={cn(
        "w-full text-left p-4 rounded-xl transition-all",
        isIrrelevant
          ? "bg-gray-100/40 opacity-60 cursor-default"
          : "cursor-pointer hover:bg-ivory-200/60",
        isSelected && !isIrrelevant ? "bg-teal-50 ring-1 ring-teal-600/30" : "bg-white/60"
      )}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        {isIrrelevant ? (
          <span className="font-serif font-semibold text-gray-500 text-base leading-tight">
            {practice.name}
          </span>
        ) : (
          <Link
            href={`/practice/${practice.place_id}`}
            onClick={(e) => e.stopPropagation()}
            className="font-serif font-semibold text-gray-900 text-base leading-tight hover:text-teal-700 transition"
          >
            {practice.name}
          </Link>
        )}
        <div className="flex items-center gap-1.5 shrink-0">
          {isIrrelevant ? (
            <span className="text-[10px] font-semibold uppercase tracking-wide bg-gray-200 text-gray-600 rounded-full px-2 py-0.5">
              Irrelevant
            </span>
          ) : (
            <>
              <StatusBadge status={practice.status} />
              {isScored && <ScoreBadge score={practice.lead_score!} />}
            </>
          )}
        </div>
      </div>
      {practice.last_touched_by_name && practice.last_touched_at && (
        <p className="text-[11px] text-gray-400 mt-1">
          Last touched by {practice.last_touched_by_name} · {timeAgo(practice.last_touched_at)}
        </p>
      )}
      {practice.call_count > 0 && (
        <p className="text-[11px] text-gray-500 mt-0.5">
          📞 {practice.call_count} {practice.call_count === 1 ? "call" : "calls"}
          {practice.salesforce_synced_at && (
            <> · last synced {timeAgo(practice.salesforce_synced_at)}</>
          )}
          {practice.salesforce_owner_name && (
            <> · owner: {practice.salesforce_owner_name} (SF)</>
          )}
        </p>
      )}
      <OwnerMiniCard practice={practice} compact />
      {practice.enrichment_status === "failed" && !practice.owner_name && (
        <p className="text-[11px] text-rose-600 mt-1">
          No owner found — try Re-enrich
        </p>
      )}
      {practice.website_doctor_name && (
        <div className="flex items-center gap-1.5 text-xs text-gray-600 mt-1">
          <span className="font-medium">{practice.website_doctor_name}</span>
          {practice.website_doctor_phone && (
            <a
              href={`tel:${practice.website_doctor_phone.replace(/\D/g, "")}`}
              onClick={(e) => e.stopPropagation()}
              className="text-teal-700 hover:underline"
            >
              {practice.website_doctor_phone}
            </a>
          )}
          <span className="ml-1 text-[10px] uppercase tracking-wide bg-purple-100 text-purple-700 rounded px-1.5 py-0.5">
            direct
          </span>
        </div>
      )}
      <p className="text-xs text-gray-500 mt-0.5">{practice.address}</p>

      <div className="flex items-center gap-3 mt-2">
        <StarRating rating={practice.rating} />
        {practice.review_count > 0 && (
          <span className="text-xs text-gray-400">({practice.review_count})</span>
        )}
      </div>

      {practice.category && (
        <span className="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 font-medium capitalize">
          {practice.category.replace("_", " ")}
        </span>
      )}

      {/* Action buttons */}
      {!isIrrelevant && (
      <div className="flex gap-2 mt-3 flex-wrap">
        {practice.phone && (
          <CallButton
            practice={practice}
            onClick={(e) => e.stopPropagation()}
            onLogged={(response) => onCallLogged?.(response)}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
          />
        )}
        {practice.website && (
          <a
            href={practice.website}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 transition"
          >
            <Globe className="w-3 h-3" /> Website
          </a>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onAnalyze(practice.place_id, isScored)
          }}
          disabled={isAnalyzing}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-teal-600 text-teal-700 hover:bg-teal-50 disabled:opacity-50 transition"
        >
          {isAnalyzing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Brain className="w-3 h-3" />
          )}
          {isAnalyzing ? "Analyzing..." : isScored ? "Re-analyze" : "Analyze"}
        </button>
        <EnrichButton
          practice={practice}
          onClick={(e) => e.stopPropagation()}
          onEnriched={(response) => onEnrichmentUpdate?.(response.practice)}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-amber-500 text-amber-700 hover:bg-amber-50 disabled:opacity-50 transition"
        />
        <Link
          href={`/practice/${practice.place_id}`}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
        >
          <FileText className="w-3 h-3" /> Call Prep
        </Link>
        {isScored && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setIsExpanded((v) => !v)
            }}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50 transition ml-auto"
            title={isExpanded ? "Hide analysis" : "Show analysis"}
          >
            {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            {isExpanded ? "Hide" : "Details"}
          </button>
        )}
      </div>
      )}

      {/* Inline analysis results — only when expanded */}
      {!isIrrelevant && isScored && isExpanded && (
        <div className="mt-3 pt-3 border-t border-gray-200/50 space-y-3">
          {practice.summary && (
            <p className="text-xs text-gray-600 leading-relaxed">{practice.summary}</p>
          )}

          {painPoints.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Pain Points</h4>
              <ul className="space-y-0.5">
                {painPoints.map((p, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-rose-400 shrink-0">&bull;</span>
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {salesAngles.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 mb-1">Sales Angles</h4>
              <ul className="space-y-0.5">
                {salesAngles.map((a, i) => (
                  <li key={i} className="text-xs text-gray-500 flex gap-1.5">
                    <span className="text-teal-500 shrink-0">&rarr;</span>
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h4 className="text-xs font-semibold text-gray-700 mb-1">
              ICP score breakdown ({practice.lead_score}/100)
            </h4>
            {icpBreakdown.length > 0 ? (
              <ul className="space-y-1">
                {icpBreakdown.map((row, i) => (
                  <li key={i} className="text-[11px] text-gray-600 flex items-start gap-2">
                    <span className="font-mono text-gray-400 shrink-0 w-12 tabular-nums">
                      {row.score}/{row.max}
                    </span>
                    <span className="font-medium text-gray-700 shrink-0 w-28">
                      {row.label}
                    </span>
                    <span className="text-gray-500">{row.reason}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[11px] text-gray-400 italic">
                Legacy score — click &quot;Re-analyze&quot; to populate the ICP breakdown.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
