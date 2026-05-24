"use client"

import { useState } from "react"
import Link from "next/link"
import { Cloud, Sparkles, ArrowLeft, Check } from "lucide-react"
import { useAuth } from "@/lib/auth"

/**
 * MOCK integrations page. Visual fidelity for the demo — accepts every
 * credential field, shows a "Saved" toast, but does not actually wire
 * the values to the runtime. Real wiring lands in a follow-up phase
 * along with `companies.integration_secrets` reads.
 */
export default function IntegrationsPage() {
  const { user } = useAuth()

  // Local state per integration. Pre-filled with placeholder/masked
  // values so admins can see the field shape without exposing real keys.
  const [salesforce, setSalesforce] = useState({
    apex_url: "",
    api_key: "",
    lead_view_base_url: "https://acme.lightning.force.com/lightning/r/Lead",
  })
  const [clay, setClay] = useState({
    table_webhook_url: "",
    api_key: "",
    inbound_secret: "",
  })

  const [saved, setSaved] = useState<string | null>(null)

  async function mockSave(name: string) {
    // Phase-9 todo: POST to /api/companies/{id}/integrations to persist
    // into companies.integration_secrets jsonb.
    setSaved(name)
    await new Promise((r) => setTimeout(r, 300))
    setSaved(null)
  }

  if (!user) {
    return (
      <div className="min-h-screen grid place-items-center text-gray-500">
        Sign in to manage integrations.
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

  return (
    <div className="min-h-screen bg-cream py-10 px-6 max-w-3xl mx-auto">
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-teal-700 mb-4"
      >
        <ArrowLeft className="w-3.5 h-3.5" /> Back to map
      </Link>

      <header className="mb-6">
        <h1 className="font-serif text-3xl font-bold text-gray-900">
          Integrations
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Connect your CRM, enrichment, dialer, mail, and AI providers.
          Credentials are encrypted at rest and only used by your tenant&apos;s
          backend calls.
        </p>
      </header>

      <div className="space-y-5">
        <IntegrationCard
          title="Salesforce"
          subtitle="Push every logged call as a Lead update. Apex REST + x-api-key."
          icon={<Cloud className="w-4 h-4" />}
          onSave={() => mockSave("salesforce")}
          saved={saved === "salesforce"}
        >
          <Field
            label="Apex REST URL"
            placeholder="https://acme.my.salesforce-sites.com/services/apexrest/leads/"
            value={salesforce.apex_url}
            onChange={(v) => setSalesforce({ ...salesforce, apex_url: v })}
          />
          <Field
            label="x-api-key"
            type="password"
            placeholder="••••••••••••"
            value={salesforce.api_key}
            onChange={(v) => setSalesforce({ ...salesforce, api_key: v })}
          />
          <Field
            label="Lead-view base URL"
            placeholder="https://your-org.lightning.force.com/lightning/r/Lead"
            value={salesforce.lead_view_base_url}
            onChange={(v) => setSalesforce({ ...salesforce, lead_view_base_url: v })}
          />
        </IntegrationCard>

        <IntegrationCard
          title="Clay"
          subtitle="Auto-enrich owner contacts. Webhook out + secret-protected inbound."
          icon={<Sparkles className="w-4 h-4" />}
          onSave={() => mockSave("clay")}
          saved={saved === "clay"}
        >
          <Field
            label="HTTP API source URL"
            placeholder="https://api.clay.com/v3/sources/webhook/..."
            value={clay.table_webhook_url}
            onChange={(v) => setClay({ ...clay, table_webhook_url: v })}
          />
          <Field
            label="x-clay-webhook-auth token (optional)"
            type="password"
            placeholder="••••••••••••"
            value={clay.api_key}
            onChange={(v) => setClay({ ...clay, api_key: v })}
          />
          <Field
            label="Inbound webhook secret"
            type="password"
            placeholder="••••••••••••"
            value={clay.inbound_secret}
            onChange={(v) => setClay({ ...clay, inbound_secret: v })}
          />
        </IntegrationCard>

      </div>

      <p className="text-[11px] text-gray-400 mt-8 text-center">
        Demo build — credentials are not yet routed to the runtime. Phase 9
        of the multi-tenant rollout will persist these into
        <code className="mx-1">companies.integration_secrets</code> and
        switch each downstream call to the active tenant&apos;s values.
      </p>
    </div>
  )
}

function IntegrationCard({
  title,
  subtitle,
  icon,
  onSave,
  saved,
  children,
}: {
  title: string
  subtitle: string
  icon: React.ReactNode
  onSave: () => void
  saved: boolean
  children: React.ReactNode
}) {
  return (
    <section className="bg-white/80 rounded-2xl shadow-sm border border-gray-200/50 p-5">
      <header className="flex items-start justify-between mb-3">
        <div className="flex items-start gap-2">
          <span className="inline-flex w-7 h-7 rounded-md bg-teal-50 text-teal-700 items-center justify-center">
            {icon}
          </span>
          <div>
            <h2 className="font-serif text-lg font-semibold text-gray-900">
              {title}
            </h2>
            <p className="text-xs text-gray-500">{subtitle}</p>
          </div>
        </div>
        <button
          onClick={onSave}
          className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition ${
            saved
              ? "bg-emerald-100 text-emerald-700"
              : "bg-teal-600 text-white hover:bg-teal-700"
          }`}
        >
          {saved ? (
            <>
              <Check className="w-3 h-3" /> Saved
            </>
          ) : (
            "Save"
          )}
        </button>
      </header>
      <div className="space-y-3">{children}</div>
    </section>
  )
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  hint?: string
}) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wide text-gray-500 mb-1">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full text-sm rounded-md border border-gray-200 px-2 py-1.5 font-mono"
      />
      {hint && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
    </div>
  )
}
