"use client"

import {
  Phone, Search, Target, Shield, CheckCircle, Loader2, RefreshCw, MessageSquare, ArrowRight,
} from "lucide-react"
import type { ScriptSection } from "@/lib/types"

const ICON_MAP: Record<string, React.ElementType> = {
  phone: Phone,
  search: Search,
  target: Target,
  shield: Shield,
  check: CheckCircle,
}

// Parse "Objection: ...\nResponse: ..." blocks (separated by blank lines).
function parseObjections(content: string) {
  return content
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean)
    .map((block) => {
      const oIdx = block.search(/objection\s*:/i)
      const rIdx = block.search(/response\s*:/i)
      if (oIdx === -1 || rIdx === -1 || rIdx < oIdx) return null
      return {
        objection: block.slice(oIdx, rIdx).replace(/objection\s*:/i, "").trim(),
        response: block.slice(rIdx).replace(/response\s*:/i, "").trim(),
      }
    })
    .filter((x): x is { objection: string; response: string } => x !== null)
}

function SectionBody({ section }: { section: ScriptSection }) {
  const content = section.content ?? ""
  const lines = content
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)

  // Numbered list (e.g. Discovery Questions) → teal-numbered list.
  if (lines.length > 1 && lines.every((l) => /^\d+[.)]/.test(l))) {
    return (
      <ol className="space-y-2.5">
        {lines.map((l, i) => (
          <li key={i} className="flex gap-3 text-sm text-gray-600 leading-relaxed">
            <span className="text-teal-600 font-semibold tabular-nums shrink-0">
              {i + 1}.
            </span>
            <span>{l.replace(/^\d+[.)]\s*/, "")}</span>
          </li>
        ))}
      </ol>
    )
  }

  // Objection handling → bordered Objection/Response cards.
  if (/objection/i.test(section.title)) {
    const pairs = parseObjections(content)
    if (pairs.length > 0) {
      return (
        <div className="space-y-2.5">
          {pairs.map((p, i) => (
            <div
              key={i}
              className="rounded-lg border border-gray-200/70 bg-gray-50/40 p-3 space-y-1.5"
            >
              <p className="text-sm leading-relaxed flex gap-1.5">
                <MessageSquare className="w-3.5 h-3.5 text-rose-500 shrink-0 mt-0.5" />
                <span>
                  <span className="font-semibold text-gray-800">Objection:</span>{" "}
                  <span className="text-gray-600">{p.objection}</span>
                </span>
              </p>
              <p className="text-sm leading-relaxed flex gap-1.5">
                <ArrowRight className="w-3.5 h-3.5 text-teal-600 shrink-0 mt-0.5" />
                <span>
                  <span className="font-semibold text-gray-800">Response:</span>{" "}
                  <span className="text-gray-600">{p.response}</span>
                </span>
              </p>
            </div>
          ))}
        </div>
      )
    }
  }

  // Default → paragraph.
  return (
    <div className="text-sm text-gray-600 leading-relaxed whitespace-pre-line">
      {content}
    </div>
  )
}

interface ScriptViewProps {
  sections: ScriptSection[]
  isLoading: boolean
  onRegenerate: () => void
}

export default function ScriptView({ sections, isLoading, onRegenerate }: ScriptViewProps) {
  if (isLoading && sections.length === 0) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2" />
        Generating playbook...
      </div>
    )
  }

  return (
    <div className="space-y-7">
      {sections.map((section, i) => {
        const Icon = ICON_MAP[section.icon] ?? Phone
        return (
          <div key={i} className="space-y-3">
            <div className="flex items-center gap-2">
              <Icon className="w-4 h-4 text-teal-600" />
              <h3 className="font-semibold text-gray-900">{section.title}</h3>
            </div>
            <div className="pl-6">
              <SectionBody section={section} />
            </div>
          </div>
        )
      })}

      <button
        onClick={onRegenerate}
        disabled={isLoading}
        className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg border border-teal-600 text-teal-700 hover:bg-teal-50 disabled:opacity-50 transition"
      >
        {isLoading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <RefreshCw className="w-4 h-4" />
        )}
        {isLoading ? "Regenerating..." : "Regenerate Script"}
      </button>
    </div>
  )
}
