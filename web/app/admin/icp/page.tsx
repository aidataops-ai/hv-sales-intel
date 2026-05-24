"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { ArrowLeft, Loader2, Sparkles, Save, Check, X } from "lucide-react"
import { useAuth } from "@/lib/auth"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

const VERTICAL_OPTIONS = [
  "medical",
  "mental_health",
  "dental",
  "alf_nh",
  "hotel_resort",
  "medspa_wellness",
] as const
type VerticalCode = typeof VERTICAL_OPTIONS[number]

const VERTICAL_LABELS: Record<VerticalCode, string> = {
  medical: "Medical",
  mental_health: "Mental health",
  dental: "Dental",
  alf_nh: "Assisted living / Nursing",
  hotel_resort: "Hotels / Resorts",
  medspa_wellness: "MedSpa / Wellness",
}

const STATE_CODES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
  "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
  "NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD",
  "TN","TX","UT","VT","VA","WA","WV","WI","WY",
]

const DIMENSION_KEYS = [
  "vertical_fit",
  "operational_pain",
  "decision_maker_access",
  "remote_readiness",
  "role_clarity",
  "budget_maturity",
  "compliance_boundary",
] as const
type DimensionKey = typeof DIMENSION_KEYS[number]

const DIMENSION_LABELS: Record<DimensionKey, string> = {
  vertical_fit: "Vertical fit",
  operational_pain: "Operational pain",
  decision_maker_access: "Decision-maker access",
  remote_readiness: "Remote readiness",
  role_clarity: "Role clarity",
  budget_maturity: "Budget maturity",
  compliance_boundary: "Compliance boundary",
}

interface ICPParsed {
  verticals_in_scope: string[]
  verticals_adjacent: string[]
  geographies: {
    focus_states: string[]
    operating_states: string[]
    outside_us: "exclude" | "allow"
  }
  size_categories: {
    primary: string[]
    opportunistic: string[]
  }
  dimension_weights: Record<DimensionKey, number>
  in_scope_keywords: string[]
  disqualifiers: string[]
  primary_decision_makers: string[]
  service_catalog: string[]
  brand_voice: string
  company_self_description: string
}

interface CompanyResponse {
  id: string
  slug: string
  name: string
  icp_doc_text?: string | null
  icp_parsed?: ICPParsed | null
}

const EMPTY_ICP: ICPParsed = {
  verticals_in_scope: [],
  verticals_adjacent: [],
  geographies: { focus_states: [], operating_states: [], outside_us: "exclude" },
  size_categories: { primary: ["A", "B"], opportunistic: ["C", "D"] },
  dimension_weights: {
    vertical_fit: 15, operational_pain: 20, decision_maker_access: 15,
    remote_readiness: 15, role_clarity: 15, budget_maturity: 10,
    compliance_boundary: 10,
  },
  in_scope_keywords: [],
  disqualifiers: [],
  primary_decision_makers: [],
  service_catalog: [],
  brand_voice: "warm, direct, not pushy",
  company_self_description: "",
}

export default function ICPDefinitionPage() {
  const { user } = useAuth()
  const [company, setCompany] = useState<CompanyResponse | null>(null)
  const [rawText, setRawText] = useState("")
  const [icp, setIcp] = useState<ICPParsed | null>(null)
  const [parsing, setParsing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const fetchCompany = useCallback(async () => {
    if (!API_URL && process.env.NODE_ENV !== "production") return
    try {
      const res = await fetch(`${API_URL}/api/companies/me`, { credentials: "include" })
      if (!res.ok) return
      const c: CompanyResponse = await res.json()
      setCompany(c)
      setRawText(c.icp_doc_text ?? "")
      if (c.icp_parsed) setIcp(c.icp_parsed)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    fetchCompany()
  }, [fetchCompany])

  async function handleParse() {
    if (!rawText.trim()) {
      setError("Paste your ICP document above first.")
      return
    }
    setError(null)
    setParsing(true)
    setSaved(false)
    try {
      const res = await fetch(`${API_URL}/api/companies/me/icp/parse`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_text: rawText }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Parse failed (${res.status})`)
      }
      const data = await res.json()
      setIcp(data.icp_parsed)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setParsing(false)
    }
  }

  async function handleSave() {
    if (!icp) return
    setError(null)
    setSaving(true)
    setSaved(false)
    try {
      const res = await fetch(`${API_URL}/api/companies/me/icp`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ icp_parsed: icp, icp_doc_text: rawText }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Save failed (${res.status})`)
      }
      const updated: CompanyResponse = await res.json()
      setCompany(updated)
      if (updated.icp_parsed) setIcp(updated.icp_parsed)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (!user) {
    return (
      <div className="min-h-screen grid place-items-center text-gray-500">
        Sign in to manage your ICP.
      </div>
    )
  }
  if (user.role !== "admin") {
    return (
      <div className="min-h-screen grid place-items-center text-gray-500">
        Admin only.
      </div>
    )
  }

  const weightsTotal = icp
    ? DIMENSION_KEYS.reduce((s, k) => s + (icp.dimension_weights[k] || 0), 0)
    : 100

  return (
    <div className="min-h-screen bg-cream py-10 px-6">
      <div className="max-w-3xl mx-auto">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-teal-700 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to map
        </Link>

        <header className="mb-6">
          <h1 className="font-serif text-3xl font-bold text-gray-900">
            Define your ICP
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Paste your Ideal Customer Profile document below.
            {" "}<span className="font-medium">Apex AI</span> will extract the
            structured criteria the analyzer uses to score every lead — verticals,
            geographies, dimension weights, disqualifiers, decision-makers, and
            service catalog.
          </p>
          {company && (
            <p className="text-xs text-gray-400 mt-2">
              Editing for company <code>{company.slug}</code>{" "}
              ({company.name}).
            </p>
          )}
        </header>

        <Card title="1. Paste the ICP document">
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            rows={12}
            placeholder="Paste the full ICP definition here — narrative is fine, doesn't need to match any specific format."
            className="w-full text-sm rounded-md border border-gray-200 p-3 font-mono"
          />
          <div className="flex items-center justify-between mt-3">
            <p className="text-xs text-gray-500">
              {rawText.length.toLocaleString()} characters (cap: 16,000)
            </p>
            <button
              onClick={handleParse}
              disabled={parsing || !rawText.trim()}
              className="inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
            >
              {parsing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> Parsing…
                </>
              ) : (
                <>
                  <Sparkles className="w-3.5 h-3.5" /> Parse with AI
                </>
              )}
            </button>
          </div>
        </Card>

        {error && (
          <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-lg px-3 py-2 text-sm mt-4">
            {error}
          </div>
        )}

        {icp && (
          <>
            <Card title="2. Review and edit the extracted criteria">
              <EditableICP
                icp={icp}
                onChange={setIcp}
                weightsTotal={weightsTotal}
              />
            </Card>

            <div className="flex items-center justify-end gap-2 mt-4">
              {saved && (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-700">
                  <Check className="w-4 h-4" /> Saved
                </span>
              )}
              <button
                onClick={handleSave}
                disabled={saving || weightsTotal !== 100}
                className="inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-md bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 transition"
                title={
                  weightsTotal !== 100
                    ? `Dimension weights total ${weightsTotal}, must be 100`
                    : ""
                }
              >
                {saving ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving…
                  </>
                ) : (
                  <>
                    <Save className="w-3.5 h-3.5" /> Save ICP
                  </>
                )}
              </button>
            </div>

            <p className="text-[11px] text-gray-400 mt-2 text-right">
              Saved ICPs drive every <strong>Re-analyze</strong> from this
              point forward. Existing analyses keep their old scores until
              they&apos;re re-analyzed.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function Card({
  title, children,
}: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white/80 rounded-2xl shadow-sm border border-gray-200/50 p-5 mt-4">
      <h2 className="font-serif text-lg font-semibold text-gray-900 mb-3">
        {title}
      </h2>
      {children}
    </section>
  )
}

function EditableICP({
  icp, onChange, weightsTotal,
}: {
  icp: ICPParsed
  onChange: (next: ICPParsed) => void
  weightsTotal: number
}) {
  function set<K extends keyof ICPParsed>(key: K, value: ICPParsed[K]) {
    onChange({ ...icp, [key]: value })
  }

  function toggleArr(arr: string[], value: string): string[] {
    return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value]
  }

  return (
    <div className="space-y-5">
      <Field label="Company self-description">
        <input
          value={icp.company_self_description}
          onChange={(e) => set("company_self_description", e.target.value)}
          className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5"
        />
      </Field>

      <Field label="Brand voice">
        <input
          value={icp.brand_voice}
          onChange={(e) => set("brand_voice", e.target.value)}
          className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5"
        />
      </Field>

      <Field label="Verticals — in scope">
        <ChipGroup
          options={VERTICAL_OPTIONS as readonly string[]}
          labels={VERTICAL_LABELS as Record<string, string>}
          selected={icp.verticals_in_scope}
          onToggle={(v) => set("verticals_in_scope", toggleArr(icp.verticals_in_scope, v))}
        />
      </Field>

      <Field label="Verticals — adjacent (partial credit)">
        <ChipGroup
          options={VERTICAL_OPTIONS as readonly string[]}
          labels={VERTICAL_LABELS as Record<string, string>}
          selected={icp.verticals_adjacent}
          onToggle={(v) => set("verticals_adjacent", toggleArr(icp.verticals_adjacent, v))}
        />
      </Field>

      <Field label={`Focus states (${icp.geographies.focus_states.length})`}>
        <ChipGroup
          options={STATE_CODES}
          selected={icp.geographies.focus_states}
          onToggle={(v) => set("geographies", {
            ...icp.geographies,
            focus_states: toggleArr(icp.geographies.focus_states, v),
          })}
          dense
        />
      </Field>

      <Field label={`Operating states (${icp.geographies.operating_states.length})`}>
        <ChipGroup
          options={STATE_CODES}
          selected={icp.geographies.operating_states}
          onToggle={(v) => set("geographies", {
            ...icp.geographies,
            operating_states: toggleArr(icp.geographies.operating_states, v),
          })}
          dense
        />
      </Field>

      <Field label="Outside US">
        <select
          value={icp.geographies.outside_us}
          onChange={(e) => set("geographies", {
            ...icp.geographies,
            outside_us: e.target.value as "exclude" | "allow",
          })}
          className="text-sm rounded-md border border-gray-200 px-2 py-1.5 bg-white"
        >
          <option value="exclude">Exclude</option>
          <option value="allow">Allow</option>
        </select>
      </Field>

      <Field
        label={`Dimension weights — total ${weightsTotal} / 100`}
        warn={weightsTotal !== 100}
      >
        <div className="space-y-2">
          {DIMENSION_KEYS.map((k) => (
            <div key={k} className="flex items-center gap-3">
              <span className="text-sm text-gray-700 w-44">{DIMENSION_LABELS[k]}</span>
              <input
                type="number"
                min={0}
                max={100}
                value={icp.dimension_weights[k]}
                onChange={(e) => set("dimension_weights", {
                  ...icp.dimension_weights,
                  [k]: Math.max(0, parseInt(e.target.value || "0", 10)),
                })}
                className="w-20 text-sm rounded-md border border-gray-200 px-2 py-1 font-mono text-right"
              />
            </div>
          ))}
        </div>
      </Field>

      <ListField
        label="In-scope keywords"
        placeholder="e.g. dental clinic"
        items={icp.in_scope_keywords}
        onChange={(items) => set("in_scope_keywords", items)}
      />

      <ListField
        label="Disqualifiers"
        placeholder="e.g. wants licensed clinical work"
        items={icp.disqualifiers}
        onChange={(items) => set("disqualifiers", items)}
      />

      <ListField
        label="Primary decision-makers"
        placeholder="e.g. practice manager"
        items={icp.primary_decision_makers}
        onChange={(items) => set("primary_decision_makers", items)}
      />

      <ListField
        label="Service catalog (pitched in sales angles)"
        placeholder="e.g. Virtual Scheduler"
        items={icp.service_catalog}
        onChange={(items) => set("service_catalog", items)}
      />
    </div>
  )
}

function Field({
  label, children, warn = false,
}: { label: string; children: React.ReactNode; warn?: boolean }) {
  return (
    <div>
      <p className={`text-xs font-medium mb-1 ${warn ? "text-rose-600" : "text-gray-700"}`}>
        {label}
      </p>
      {children}
    </div>
  )
}

function ChipGroup({
  options, labels, selected, onToggle, dense = false,
}: {
  options: readonly string[]
  labels?: Record<string, string>
  selected: string[]
  onToggle: (v: string) => void
  dense?: boolean
}) {
  return (
    <div className={`flex flex-wrap gap-${dense ? "1" : "1.5"}`}>
      {options.map((o) => {
        const active = selected.includes(o)
        return (
          <button
            key={o}
            onClick={() => onToggle(o)}
            className={`${dense ? "text-[11px] px-2 py-0.5" : "text-xs px-2 py-1"} rounded-full border transition ${
              active
                ? "bg-teal-50 border-teal-500 text-teal-700"
                : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
            }`}
          >
            {labels?.[o] ?? o}
          </button>
        )
      })}
    </div>
  )
}

function ListField({
  label, items, onChange, placeholder,
}: {
  label: string
  items: string[]
  onChange: (next: string[]) => void
  placeholder?: string
}) {
  const [draft, setDraft] = useState("")
  function add() {
    const v = draft.trim()
    if (!v || items.includes(v)) {
      setDraft("")
      return
    }
    onChange([...items, v])
    setDraft("")
  }
  return (
    <Field label={`${label} (${items.length})`}>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {items.map((item, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 text-xs bg-teal-50 border border-teal-200 text-teal-800 rounded-full pl-2 pr-1 py-0.5"
          >
            {item}
            <button
              onClick={() => onChange(items.filter((_, idx) => idx !== i))}
              className="text-teal-700 hover:text-rose-600"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              add()
            }
          }}
          placeholder={placeholder}
          className="flex-1 text-sm rounded-md border border-gray-200 px-2 py-1.5"
        />
        <button
          onClick={add}
          disabled={!draft.trim()}
          className="text-sm px-3 py-1.5 rounded-md border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          Add
        </button>
      </div>
    </Field>
  )
}
