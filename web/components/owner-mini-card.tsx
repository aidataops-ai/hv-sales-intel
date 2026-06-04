"use client"

import { Mail, Phone, ExternalLink, User } from "lucide-react"
import type { Practice } from "@/lib/types"

interface OwnerMiniCardProps {
  practice: Practice
  compact?: boolean
}

export default function OwnerMiniCard({ practice, compact = false }: OwnerMiniCardProps) {
  const hasAny =
    practice.owner_name ||
    practice.owner_email ||
    practice.owner_phone ||
    practice.owner_linkedin

  if (!hasAny) return null

  return (
    <div className={compact ? "mt-2" : "mt-3 p-3 rounded-lg bg-white/60 dark:bg-night-800 border border-gray-200/60 dark:border-white/10"}>
      <div className="flex items-center gap-1.5 text-xs">
        <User className="w-3 h-3 text-gray-500 dark:text-gray-400 shrink-0" />
        <span className="font-medium text-gray-800 dark:text-[#d9d9d9] truncate">
          {practice.owner_name ?? "Unknown"}
        </span>
        {practice.owner_title && (
          <span className="text-gray-400 dark:text-gray-500 truncate">· {practice.owner_title}</span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        {practice.owner_email && (
          <a
            href={`mailto:${practice.owner_email}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-[11px] text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400"
            title={practice.owner_email}
          >
            <Mail className="w-3 h-3" />
            <span className="truncate max-w-[140px]">{practice.owner_email}</span>
          </a>
        )}
        {practice.owner_phone && (
          <a
            href={`tel:${practice.owner_phone}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-[11px] text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400"
          >
            <Phone className="w-3 h-3" />
            {practice.owner_phone}
          </a>
        )}
        {practice.owner_linkedin && (
          <a
            href={practice.owner_linkedin}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-0.5 text-[11px] text-blue-600 dark:text-blue-300 hover:text-blue-800"
            title="LinkedIn"
          >
            <ExternalLink className="w-3 h-3" />
            <span>LinkedIn</span>
          </a>
        )}
      </div>
    </div>
  )
}
