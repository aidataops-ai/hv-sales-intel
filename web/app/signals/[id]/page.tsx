"use client"

import { useCallback, useEffect, useState, type ReactNode } from "react"
import Link from "next/link"
import {
  ArrowLeft, Check, Copy, ExternalLink, Globe, MapPin, Phone,
} from "lucide-react"

import SignalsTopBar from "@/components/signals-top-bar"
import { BandBadge, DispositionBadge, SourceBadge } from "@/components/signal-badges"
import {
  ALL_DISPOSITIONS, employerLabel, formatSalary, formatWorkMode,
  getLead, updateLead, type Lead, type LeadDisposition,
} from "@/lib/leads"
import { timeAgo } from "@/lib/utils"

export default function SignalDetailPage({ params }: { params: { id: string } }) {
  const leadId = Number(params.id)
  const [lead, setLead] = useState<Lead | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [notes, setNotes] = useState("")
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    getLead(leadId).then((found) => {
      setLead(found)
      setNotes(found?.notes ?? "")
      setIsLoading(false)
    })
  }, [leadId])

  const patch = useCallback(
    async (fields: Parameters<typeof updateLead>[1]) => {
      setIsSaving(true)
      try {
        setLead(await updateLead(leadId, fields))
      } finally {
        setIsSaving(false)
      }
    },
    [leadId],
  )

  async function copyDraft() {
    if (!lead?.draft) return
    try {
      await navigator.clipboard.writeText(lead.draft)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  if (isLoading) {
    return (
      <div className="min-h-screen bg-cream dark:bg-night-900">
        <SignalsTopBar />
        <main className="pt-14 p-8 text-sm text-gray-500 dark:text-gray-400">Loading…</main>
      </div>
    )
  }

  if (!lead) {
    return (
      <div className="min-h-screen bg-cream dark:bg-night-900">
        <SignalsTopBar />
        <main className="pt-14 p-8 space-y-3">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            That signal doesn&apos;t exist, or belongs to another company.
          </p>
          <Link href="/signals" className="text-sm text-teal-700 dark:text-teal-400 hover:underline">
            Back to Instant Signals
          </Link>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-cream dark:bg-night-900">
      <SignalsTopBar />

      <main className="pt-14">
        <div className="max-w-5xl mx-auto p-4 space-y-4">
          <Link
            href="/signals"
            className="inline-flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-400 transition"
          >
            <ArrowLeft className="w-4 h-4" />
            All signals
          </Link>

          {/* -------------------------------------------------- header */}
          <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1
                    className={`font-serif text-xl font-bold ${
                      lead.employer_name
                        ? "text-gray-900 dark:text-white"
                        : "text-gray-400 dark:text-gray-500 italic"
                    }`}
                  >
                    {employerLabel(lead)}
                  </h1>
                  <SourceBadge source={lead.source} />
                  <BandBadge band={lead.confidence_band} />
                  <DispositionBadge disposition={lead.disposition} />
                </div>
                <p className="text-sm text-gray-600 dark:text-[#d9d9d9]">{lead.title}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {lead.location_raw ?? "Location not stated"}
                  {" · "}
                  {formatWorkMode(lead.work_mode)}
                  {" · "}
                  {formatSalary(lead)}
                  {" · posted "}
                  {lead.posted_at ? timeAgo(lead.posted_at) : "date not reported"}
                </p>
                {!lead.employer_name && (
                  <p className="text-xs text-gray-500 dark:text-gray-400 max-w-xl">
                    A confidential listing — the board withheld the employer name.
                    The role and city are still qualified, but outreach can&apos;t
                    address the business by name.
                  </p>
                )}
              </div>

              {lead.url && (
                <a
                  href={lead.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 dark:border-white/10 text-sm text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition whitespace-nowrap"
                >
                  <ExternalLink className="w-4 h-4" />
                  Original posting
                </a>
              )}
            </div>
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* ---------------------------------------------- left column */}
            <div className="lg:col-span-2 space-y-4">
              {/* Verdict — every field the qualifier produced, so an operator
                  can see WHY this surfaced before spending a call on it. */}
              <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
                <h2 className="font-serif font-semibold text-gray-900 dark:text-white">
                  Qualifier verdict
                </h2>
                {lead.reason && (
                  <p className="text-sm text-gray-700 dark:text-[#d9d9d9] leading-relaxed">
                    {lead.reason}
                  </p>
                )}
                <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
                  <Field label="Verdict" value={lead.decision} />
                  <Field
                    label="Confidence"
                    value={lead.confidence != null ? lead.confidence.toFixed(2) : null}
                  />
                  <Field label="Employer type" value={lead.employer_type} />
                  <Field
                    label="Role suitable"
                    value={
                      lead.role_suitable == null ? null : lead.role_suitable ? "yes" : "no"
                    }
                  />
                  <Field label="Providers" value={lead.provider_count?.toString() ?? null} />
                  <Field label="Track" value={lead.service_line} />
                  <Field label="Work mode" value={formatWorkMode(lead.work_mode)} />
                </dl>
              </section>

              {lead.draft && (
                <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <h2 className="font-serif font-semibold text-gray-900 dark:text-white">
                      Outreach draft
                    </h2>
                    <button
                      onClick={copyDraft}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/10 text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition"
                    >
                      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                  <p className="text-sm text-gray-700 dark:text-[#d9d9d9] whitespace-pre-wrap leading-relaxed rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 p-3">
                    {lead.draft}
                  </p>
                </section>
              )}

              <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-2">
                <h2 className="font-serif font-semibold text-gray-900 dark:text-white">
                  Posting text
                </h2>
                <p className="text-sm text-gray-600 dark:text-gray-400 whitespace-pre-wrap leading-relaxed">
                  {lead.description || "The board returned no description for this posting."}
                </p>
              </section>
            </div>

            {/* --------------------------------------------- right column */}
            <div className="space-y-4">
              {/* Linked practice — the provider data to act on this signal:
                  who to call, where, and the site. Null when the employer never
                  matched the Places universe (a system, an unscanned city, or a
                  practice below the Places 60-cap). */}
              <PracticePanel lead={lead} />

              <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
                <h2 className="font-serif font-semibold text-gray-900 dark:text-white">
                  Workflow
                </h2>

                <div>
                  <label className={fieldLabel}>Decision</label>
                  <select
                    value={lead.disposition}
                    onChange={(e) => patch({ disposition: e.target.value as LeadDisposition })}
                    disabled={isSaving}
                    className={control}
                  >
                    {ALL_DISPOSITIONS.map((disposition) => (
                      <option key={disposition} value={disposition}>{disposition}</option>
                    ))}
                  </select>
                </div>

                {lead.disposition === "rejected" && (
                  <div>
                    <label className={fieldLabel}>Reject reason</label>
                    <input
                      defaultValue={lead.reject_reason ?? ""}
                      onBlur={(e) => {
                        if (e.target.value !== (lead.reject_reason ?? "")) {
                          patch({ reject_reason: e.target.value })
                        }
                      }}
                      placeholder="Why was this not a fit?"
                      className={control}
                    />
                  </div>
                )}

                <div>
                  <label className={fieldLabel}>Notes</label>
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    onBlur={() => {
                      if (notes !== (lead.notes ?? "")) patch({ notes })
                    }}
                    rows={5}
                    placeholder="Notes about this signal…"
                    className={`${control} resize-none`}
                  />
                </div>
              </section>

              <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-2 text-xs text-gray-500 dark:text-gray-400">
                <h2 className="font-serif font-semibold text-gray-900 dark:text-white text-sm">
                  History
                </h2>
                <HistoryRow label="First collected" value={lead.created_at} />
                <HistoryRow label="Qualified" value={lead.qualified_at} />
                <HistoryRow label="Contacted" value={lead.contacted_at} />
                <HistoryRow label="Last touched" value={lead.last_touched_at} />
              </section>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

const fieldLabel =
  "block text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"

const control =
  "w-full text-sm rounded-md border border-gray-200 dark:border-white/10 " +
  "bg-transparent dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 " +
  "px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-teal-500/40 disabled:opacity-50"

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className={fieldLabel}>{label}</dt>
      <dd className="text-gray-800 dark:text-[#d9d9d9]">{value || "—"}</dd>
    </div>
  )
}

function HistoryRow({ label, value }: { label: string; value: string | null }) {
  return (
    <p className="flex items-center justify-between gap-2">
      <span>{label}</span>
      <span className="text-gray-700 dark:text-[#d9d9d9]" title={value ?? ""}>
        {value ? timeAgo(value) : "—"}
      </span>
    </p>
  )
}

function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-white/10 text-gray-600 dark:text-gray-300">
      {children}
    </span>
  )
}

function prettyHost(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "")
  } catch {
    return url
  }
}

/** The practice a posting resolved to — a contact card when linked, an honest
 *  explanation when not. `match_status`/`match_confidence` ride on the lead
 *  (flattened from the posting); `practice` is the embedded row or null. */
function PracticePanel({ lead }: { lead: Lead }) {
  const p = lead.practice
  return (
    <section className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl p-5 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-serif font-semibold text-gray-900 dark:text-white">
          Linked practice
        </h2>
        {lead.match_status && (
          <span
            title="How the posting was matched to this practice"
            className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full ${
              lead.match_status === "auto"
                ? "bg-teal-500/15 text-teal-700 dark:text-teal-300"
                : "bg-amber-500/15 text-amber-700 dark:text-amber-300"
            }`}
          >
            {lead.match_status}
            {lead.match_confidence != null ? ` · ${lead.match_confidence.toFixed(2)}` : ""}
          </span>
        )}
      </div>

      {p ? (
        <div className="space-y-2.5 text-sm">
          <p className="font-medium text-gray-900 dark:text-white">{p.name}</p>

          {p.address && (
            <p className="flex items-start gap-2 text-gray-600 dark:text-gray-400">
              <MapPin className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>{p.address}</span>
            </p>
          )}

          {p.phone && (
            <a
              href={`tel:${p.phone}`}
              className="flex items-center gap-2 text-teal-700 dark:text-teal-400 hover:underline"
            >
              <Phone className="w-3.5 h-3.5 shrink-0" />
              {p.phone}
            </a>
          )}

          {p.website && (
            <a
              href={p.website}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-teal-700 dark:text-teal-400 hover:underline"
            >
              <Globe className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">{prettyHost(p.website)}</span>
              <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
            </a>
          )}

          <div className="flex flex-wrap gap-1.5 pt-1">
            {p.service_line && <Pill>{p.service_line}</Pill>}
            {p.category && <Pill>{p.category}</Pill>}
            {p.rating != null && (
              <Pill>
                ★ {p.rating}
                {p.review_count ? ` (${p.review_count})` : ""}
              </Pill>
            )}
          </div>
        </div>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No practice matched this posting yet — the employer may be a hospital
          system, in a city we haven&apos;t scanned, or ranked below the Places
          result cap. It links automatically once a matching practice is in the
          bank.
        </p>
      )}
    </section>
  )
}
