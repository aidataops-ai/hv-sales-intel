export interface Practice {
  place_id: string
  name: string
  address: string | null
  city: string | null
  state: string | null
  phone: string | null
  website: string | null
  rating: number | null
  review_count: number
  category: string | null
  lat: number | null
  lng: number | null
  opening_hours: string | null
  status: string

  // Phase 2 (AI analysis) — optional, absent on unscored practices
  summary?: string | null
  pain_points?: string | null  // JSON string of string[]
  sales_angles?: string | null // JSON string of string[]
  lead_score?: number | null   // ICP score (0-100), computed by icp_scorer
  urgency_score?: number | null
  hiring_signal_score?: number | null
  icp_breakdown?: string | null  // JSON string of IcpBreakdownRow[]

  // Phase 3 (Call Playbook)
  call_script?: string | null // JSON string of Script
  notes?: string | null

  // Email outreach
  email: string | null
  email_draft: string | null
  email_draft_updated_at: string | null

  // Attribution (last-touched)
  last_touched_by: string | null
  last_touched_by_name: string | null
  last_touched_at: string | null

  // Salesforce integration + call log
  salesforce_lead_id: string | null
  salesforce_lead_url: string | null
  salesforce_owner_id: string | null
  salesforce_owner_name: string | null
  salesforce_synced_at: string | null
  call_count: number
  call_notes: string | null

  // Clay owner enrichment
  owner_name: string | null
  owner_email: string | null
  owner_phone: string | null
  owner_title: string | null
  owner_linkedin: string | null
  enrichment_status: "pending" | "enriched" | "failed" | null
  enriched_at: string | null

  // Tags + assignment
  tags: string[]
  assigned_to: string | null
  assigned_at: string | null
  assigned_by: string | null
  assigned_to_name?: string | null

  // Website-extracted doctor info
  website_doctor_name: string | null
  website_doctor_phone: string | null
}

export interface EmailMessage {
  id: number
  practice_id: number
  user_id: string | null
  direction: "out" | "in"
  subject: string | null
  body: string | null
  message_id: string | null
  in_reply_to: string | null
  sent_at: string
  error: string | null
}

export interface EmailDraft {
  subject: string
  body: string
}

export interface ScriptSection {
  title: string
  icon: string
  content: string
}

export interface IcpBreakdownRow {
  label: string
  score: number
  max: number
  reason: string
}

export function parseIcpBreakdown(value: string | null | undefined): IcpBreakdownRow[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export interface Script {
  sections: ScriptSection[]
}

/** Parse a JSON string array field, returning [] on failure. */
export function parseJsonArray(value: string | null): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export interface User {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
  created_at?: string
  is_bootstrap_admin?: boolean
}
