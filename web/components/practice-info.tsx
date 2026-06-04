"use client"

import { useState } from "react"
import { Globe, Star, Bookmark, Info, ArrowRight } from "lucide-react"
import type { Practice } from "@/lib/types"
import type { CallLogResponse } from "@/lib/api"
import { parseJsonArray } from "@/lib/types"
import { cn } from "@/lib/utils"
import CallButton from "./call-button"
import OwnerMiniCard from "./owner-mini-card"

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
            i < full ? "fill-amber-400 text-amber-400" : "text-gray-300 dark:text-gray-600",
          )}
        />
      ))}
      <span className="ml-1 text-sm font-medium text-gray-700 dark:text-[#d9d9d9]">{rating}</span>
    </span>
  )
}

export default function PracticeInfo({
  practice,
  onCallLogged,
}: {
  practice: Practice
  onCallLogged?: (response: CallLogResponse) => void
}) {
  const painPoints = parseJsonArray(practice.pain_points ?? null)
  const salesAngles = parseJsonArray(practice.sales_angles ?? null)
  const [bookmarked, setBookmarked] = useState(false)
  const [showAngles, setShowAngles] = useState(false)

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-start justify-between gap-2">
          <h2 className="font-serif text-xl font-bold text-gray-900 dark:text-white leading-tight">
            {practice.name}
          </h2>
          <button
            onClick={() => setBookmarked((v) => !v)}
            title={bookmarked ? "Bookmarked" : "Bookmark this lead"}
            className="shrink-0 p-1 -mr-1 text-gray-400 dark:text-gray-500 hover:text-teal-600 transition"
          >
            <Bookmark
              className={cn("w-5 h-5", bookmarked && "fill-teal-600 text-teal-600")}
            />
          </button>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{practice.address}</p>
      </div>

      <div className="flex items-center gap-3">
        <StarRating rating={practice.rating} />
        {practice.review_count > 0 && (
          <span className="text-xs text-gray-400 dark:text-gray-500">({practice.review_count})</span>
        )}
      </div>

      {practice.category && (
        <span className="inline-block text-xs px-2 py-0.5 rounded-full bg-teal-50 dark:bg-[#284b63]/40 text-teal-700 dark:text-teal-400 font-medium capitalize">
          {practice.category.replace("_", " ")}
        </span>
      )}

      <div className="flex gap-2">
        {practice.phone && (
          <CallButton
            practice={practice}
            label={practice.phone}
            onLogged={onCallLogged}
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700 transition"
          />
        )}
        {practice.website && (
          <a
            href={practice.website}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-300 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition"
          >
            <Globe className="w-3 h-3" /> Website
          </a>
        )}
      </div>

      {(practice.website_doctor_name || practice.website_doctor_phone) && (
        <div>
          <h4 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-1">From website</h4>
          <div className="space-y-0.5">
            {practice.website_doctor_name && (
              <div className="flex justify-between text-xs">
                <span className="text-gray-500 dark:text-gray-400">Doctor</span>
                <span className="text-gray-900 dark:text-white">{practice.website_doctor_name}</span>
              </div>
            )}
            {practice.website_doctor_phone && (
              <div className="flex justify-between text-xs">
                <span className="text-gray-500 dark:text-gray-400">Direct line</span>
                <a
                  href={`tel:${practice.website_doctor_phone.replace(/\D/g, "")}`}
                  className="text-teal-700 dark:text-teal-400 hover:underline"
                >
                  {practice.website_doctor_phone}
                </a>
              </div>
            )}
          </div>
        </div>
      )}

      <div>
        <h4 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-1">Owner</h4>
        {practice.enrichment_status === "pending" ? (
          <p className="text-xs text-gray-400 dark:text-gray-500">Enriching owner info…</p>
        ) : practice.owner_name || practice.owner_email || practice.owner_phone ? (
          <OwnerMiniCard practice={practice} />
        ) : (
          <p className="text-xs text-gray-400 dark:text-gray-500">
            {practice.enrichment_status === "failed"
              ? "No owner found."
              : "No owner info yet — enrich from the map."}
          </p>
        )}
      </div>

      {practice.lead_score != null && (
        <div className="pt-3 border-t border-gray-200/50 dark:border-white/10 space-y-3">
          {practice.summary && (
            <div className="flex gap-2 rounded-lg bg-teal-50/60 dark:bg-[#284b63]/40 border border-teal-100 dark:border-white/10 p-3">
              <Info className="w-4 h-4 text-teal-600 dark:text-teal-400 shrink-0 mt-0.5" />
              <p className="text-xs text-gray-600 dark:text-[#d9d9d9] leading-relaxed">
                {practice.summary}
              </p>
            </div>
          )}

          {painPoints.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-1">Pain Points</h4>
              <ul className="space-y-0.5">
                {painPoints.map((p, i) => (
                  <li key={i} className="text-xs text-gray-500 dark:text-gray-400 flex gap-1.5">
                    <span className="text-rose-400 shrink-0">&bull;</span>
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {salesAngles.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-700 dark:text-[#d9d9d9] mb-1">Sales Angles</h4>
              {showAngles ? (
                <ul className="space-y-0.5">
                  {salesAngles.map((a, i) => (
                    <li key={i} className="text-xs text-gray-500 dark:text-gray-400 flex gap-1.5">
                      <span className="text-teal-500 shrink-0">&rarr;</span>
                      {a}
                    </li>
                  ))}
                </ul>
              ) : (
                <button
                  onClick={() => setShowAngles(true)}
                  className="inline-flex items-center gap-1 text-xs font-medium text-teal-700 dark:text-teal-400 hover:text-teal-800"
                >
                  View sales angles <ArrowRight className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
