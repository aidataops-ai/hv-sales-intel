/**
 * Client for the job-posting lead API.
 *
 * The route and the operator-facing label say "signals"; the tables and the
 * API say "leads". That split is deliberate — see design doc §4.1.
 *
 * Kept separate from `lib/api.ts` because that module falls back to mock
 * practices when the backend is unreachable. There is no honest mock for a
 * lead: a fabricated hiring signal would send a rep to call a practice that
 * never posted a job. Failures here surface as an empty feed.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
const IS_PROD = process.env.NODE_ENV === "production"

export type Band = "ready" | "check" | "decide"
export type WorkMode = "onsite" | "remote" | "hybrid"
/** The operator's call on a lead — the lightweight approve/reject flag that
 *  replaced the old multi-stage status pipeline. */
export type LeadDisposition = "undecided" | "approved" | "rejected"

export const ALL_DISPOSITIONS: LeadDisposition[] = [
  "undecided", "approved", "rejected",
]
export const ALL_BANDS: Band[] = ["ready", "check", "decide"]
export const ALL_WORK_MODES: WorkMode[] = ["onsite", "remote", "hybrid"]
export const ALL_SOURCES = ["indeed", "linkedin"] as const

/** One row of the feed: the lead's own columns with its posting merged in. */
export interface Lead {
  id: number
  posting_id: number

  // Posting (from job_postings)
  source: string
  external_id: string
  url: string | null
  title: string
  employer_name: string | null
  location_raw: string | null
  city: string | null
  state: string | null
  posted_at: string | null
  salary_min: number | null
  salary_max: number | null
  salary_interval: string | null
  board_remote_flag: boolean | null
  description: string | null
  search_term: string | null

  // Verdict (written by the qualifier)
  decision: "keep" | "discard" | null
  confidence: number | null
  confidence_band: Band | null
  reason: string | null
  employer_type: string | null
  role_suitable: boolean | null
  work_mode: WorkMode | null
  service_line: string | null
  provider_count: number | null
  draft: string | null
  model: string | null
  qualified_at: string | null

  // Workflow (written by operators)
  disposition: LeadDisposition
  reject_reason: string | null
  notes: string | null
  last_touched_at: string | null
  contacted_at: string | null
  created_at: string
}

/** What the feed shows. The API defaults to `keep`; `all` is the opt-out. */
export type DecisionFilter = "keep" | "discard" | "all"

export interface LeadFilters {
  cities: string[]
  tracks: string[]
  band: string
  /** "" means the API default, which is keeps only. */
  decision: string
  work_mode: string
  source: string
  salary: string          // "" | "yes" | "no"
  search: string
}

export const EMPTY_LEAD_FILTERS: LeadFilters = {
  cities: [], tracks: [], band: "", decision: "", work_mode: "",
  source: "", salary: "", search: "",
}

/** Turn the filter state into the query params both the feed and the CSV
 *  export take. One builder, so a filtered view exports what it shows. */
export function filterParams(
  filters: LeadFilters,
): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {}
  if (filters.cities.length) out.cities = filters.cities
  if (filters.tracks.length) out.tracks = filters.tracks
  if (filters.band) out.band = filters.band
  if (filters.decision) out.decision = filters.decision
  if (filters.work_mode) out.work_mode = filters.work_mode
  if (filters.source) out.source = filters.source
  if (filters.salary) out.salary = filters.salary
  if (filters.search) out.search = filters.search
  return out
}

async function leadFetch<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_URL && !IS_PROD) throw new Error("NO_API")
  const res = await fetch(`${API_URL}${path}`, { ...init, credentials: "include" })
  if (res.status === 401 && typeof window !== "undefined") {
    const redirect = encodeURIComponent(window.location.pathname)
    window.location.href = `/login?redirect=${redirect}`
    throw new Error("API 401")
  }
  if (!res.ok) throw new Error(`API ${res.status}`)
  return res.json()
}

function toQuery(params: Record<string, string | string[] | number>): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      if (value.length) qs.set(key, value.join(","))
    } else if (String(value) !== "") {
      qs.set(key, String(value))
    }
  }
  return qs.toString()
}

export interface LeadPage {
  leads: Lead[]
  total: number
  hasMore: boolean
}

export async function listLeads(opts: {
  filters: LeadFilters
  sort?: string
  dir?: "asc" | "desc"
  offset?: number
  limit?: number
}): Promise<LeadPage> {
  try {
    const qs = toQuery({
      ...filterParams(opts.filters),
      sort: opts.sort ?? "band",
      dir: opts.dir ?? "asc",
      offset: opts.offset ?? 0,
      limit: opts.limit ?? 50,
    })
    const data = await leadFetch<{
      leads: Lead[]; total?: number; has_more?: boolean
    }>(`/api/leads?${qs}`)
    return {
      leads: data.leads ?? [],
      total: data.total ?? (data.leads ?? []).length,
      hasMore: !!data.has_more,
    }
  } catch {
    // No mock fallback — an empty feed is honest. A fabricated hiring signal
    // would send a rep to call a practice that never posted a job.
    return { leads: [], total: 0, hasMore: false }
  }
}

export async function getLead(id: number): Promise<Lead | null> {
  try {
    return await leadFetch<Lead>(`/api/leads/${id}`)
  } catch {
    return null
  }
}

export async function updateLead(
  id: number,
  fields: Partial<Pick<Lead, "disposition" | "reject_reason" | "notes">>,
): Promise<Lead> {
  return leadFetch<Lead>(`/api/leads/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  })
}

export interface FilterOptions {
  cities: string[]
  tracks: string[]
  states: string[]
}

export async function getFilterOptions(): Promise<FilterOptions> {
  try {
    return await leadFetch<FilterOptions>("/api/leads/filters")
  } catch {
    return { cities: [], tracks: [], states: [] }
  }
}

export interface LeadAnalytics {
  total: number
  keep_rate: number
  per_day: Array<Record<string, string | number>>
  bands: Record<string, number>
  dispositions: Record<string, number>
  tracks: Record<string, number>
  reject_reasons: Array<{ reason: string; count: number }>
  collector: {
    targets: number
    swept: number
    zero_row_targets: number
    last_run_at: string | null
    last_posting_at: string | null
    alert: string | null
  }
}

export async function getLeadAnalytics(days = 30): Promise<LeadAnalytics | null> {
  try {
    return await leadFetch<LeadAnalytics>(`/api/leads/analytics?days=${days}`)
  } catch {
    return null
  }
}

// --------------------------------------------------------------------------
// Formatting
// --------------------------------------------------------------------------

/** "$18–23/hr", or an em dash when the board gave no salary.
 *  Indeed carries a structured salary on ~44% of rows; LinkedIn on none. */
export function formatSalary(lead: Pick<
  Lead, "salary_min" | "salary_max" | "salary_interval"
>): string {
  if (lead.salary_min == null) return "—"
  const unit = lead.salary_interval === "hourly" ? "/hr"
    : lead.salary_interval === "yearly" ? "/yr"
    : lead.salary_interval ? `/${lead.salary_interval}` : ""
  const min = Math.round(lead.salary_min)
  const max = lead.salary_max != null ? Math.round(lead.salary_max) : null
  return max && max !== min ? `$${min}–${max}${unit}` : `$${min}${unit}`
}

export function formatWorkMode(mode: WorkMode | null): string {
  if (mode === "onsite") return "On-site"
  if (mode === "remote") return "Remote"
  if (mode === "hybrid") return "Hybrid"
  return "—"
}

/** Confidential Indeed listings (~12% of supply) carry no employer name.
 *  They are surfaced rather than suppressed — the role, city and posting URL
 *  are all workable — but outreach cannot address the business by name, so
 *  they are labelled instead of rendered blank. */
export function employerLabel(lead: Pick<Lead, "employer_name">): string {
  return lead.employer_name || "Confidential posting"
}
