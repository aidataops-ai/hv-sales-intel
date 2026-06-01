import type { EmailDraft, EmailMessage, Practice, Script } from "./types"
import { mockPractices } from "./mock-data"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""
const IS_PROD = process.env.NODE_ENV === "production"

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Empty API_URL in production = same-origin request (Vercel rewrite handles it).
  // Empty API_URL in dev = no backend configured -> caller falls back to mocks.
  if (!API_URL && !IS_PROD) throw new Error("NO_API")
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
  })
  if (res.status === 401 && typeof window !== "undefined") {
    const redirect = encodeURIComponent(window.location.pathname)
    window.location.href = `/login?redirect=${redirect}`
    throw new Error("API 401")
  }
  if (!res.ok) throw new Error(`API ${res.status}`)
  return res.json()
}

export async function searchPractices(
  query: string,
  refresh: boolean = false,
): Promise<Practice[]> {
  try {
    const data = await apiFetch<{ practices: Practice[] }>("/api/practices/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, refresh }),
    })
    return data.practices
  } catch {
    const q = query.toLowerCase()
    const tokens = q.split(/\s+/)
    const matches = mockPractices.filter((p) => {
      const hay = `${p.name} ${p.category} ${p.city}`.toLowerCase()
      return tokens.some((t) => hay.includes(t))
    })
    return matches.length > 0 ? matches : mockPractices
  }
}

export async function listPractices(params?: {
  city?: string
  category?: string
  min_rating?: number
  limit?: number
}): Promise<Practice[]> {
  try {
    const qs = new URLSearchParams()
    if (params?.city) qs.set("city", params.city)
    if (params?.category) qs.set("category", params.category)
    if (params?.min_rating) qs.set("min_rating", String(params.min_rating))
    if (params?.limit) qs.set("limit", String(params.limit))
    const data = await apiFetch<{ practices: Practice[] }>(`/api/practices?${qs}`)
    return data.practices
  } catch {
    return mockPractices
  }
}

export async function analyzePractice(
  placeId: string,
  options?: { force?: boolean; rescan?: boolean }
): Promise<Practice> {
  try {
    return await apiFetch<Practice>(`/api/practices/${placeId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        force: options?.force ?? false,
        rescan: options?.rescan ?? false,
      }),
    })
  } catch {
    return mockAnalysis(placeId)
  }
}

export async function rescanPractice(placeId: string): Promise<Practice> {
  try {
    return await apiFetch<Practice>(`/api/practices/${placeId}/rescan`, {
      method: "POST",
    })
  } catch {
    return mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
  }
}

function mockAnalysis(placeId: string): Practice {
  // Preserve the requested place_id even when the practice isn't in the
  // mock list. Without this, a backend error would silently swap in
  // mockPractices[0] under a *different* place_id, making the original
  // entry appear to vanish from the sidebar.
  const found = mockPractices.find((p) => p.place_id === placeId)
  const practice = found ?? { ...mockPractices[0], place_id: placeId }
  const hiring = Math.floor(Math.random() * 70) + 25
  const urgency = Math.floor(Math.random() * 60) + 20
  const lead = Math.min(100, Math.floor(hiring * 0.5 + urgency * 0.3 + Math.random() * 20))

  const painPoints = [
    "Reviews mention long wait times and difficulty reaching the office",
    "Website shows open positions unfilled for several weeks",
    "Patients report staff seeming overwhelmed during visits",
  ]
  const salesAngles = [
    "Pitch trained front desk staff to handle scheduling overflow",
    "Propose medical assistant staffing to reduce provider burnout",
  ]

  return {
    ...practice,
    summary: `${practice.name} shows staffing challenges typical of ${(practice.category ?? "healthcare").replace("_", " ")} practices. Opportunities exist for Apex&Virtuals staffing solutions.`,
    pain_points: JSON.stringify(painPoints),
    sales_angles: JSON.stringify(salesAngles),
    lead_score: lead,
    urgency_score: urgency,
    hiring_signal_score: hiring,
  }
}

export async function getScript(placeId: string): Promise<Script> {
  try {
    return await apiFetch<Script>(`/api/practices/${placeId}/script`)
  } catch {
    return mockScript(placeId)
  }
}

export async function regenerateScript(placeId: string): Promise<Script> {
  try {
    return await apiFetch<Script>(`/api/practices/${placeId}/script`, {
      method: "POST",
    })
  } catch {
    return mockScript(placeId)
  }
}

export async function updatePractice(
  placeId: string,
  fields: { status?: string; notes?: string; email?: string; assigned_to?: string }
): Promise<Practice> {
  try {
    return await apiFetch<Practice>(`/api/practices/${placeId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    })
  } catch {
    const practice = mockPractices.find((p) => p.place_id === placeId) ?? mockPractices[0]
    return { ...practice, ...fields }
  }
}

function mockScript(placeId: string): Script {
  // Don't fall back to `mockPractices[0]` — that's how Palm Valley Family
  // Dentistry ended up with a script saying "Houston Dental Care". When
  // the placeId isn't in the mock list, leave the practice name as the
  // placeId so the fallback is at least obvious instead of misleading.
  const found = mockPractices.find((p) => p.place_id === placeId)
  const name = found?.name ?? placeId
  return {
    sections: [
      { title: "Opening", icon: "phone", content: `Hi, this is [Your Name] from Apex&Virtuals. I'm calling about ${name} — we specialize in healthcare staffing and I noticed your practice may benefit from our services. Do you have a quick moment?` },
      { title: "Discovery Questions", icon: "search", content: "1. How are you currently handling front desk coverage during peak hours or when staff call out?\n2. Are you finding it challenging to recruit qualified clinical staff in this market?\n3. How much admin time do your providers spend that could be handled by support staff?\n4. If you could add one team member tomorrow, what role would it be?" },
      { title: "Pitch", icon: "target", content: `At Apex&Virtuals, we provide pre-vetted healthcare staff — front desk, medical assistants, and admin support — specifically for practices like ${name}. We handle the recruiting and screening so you can focus on patients.` },
      { title: "Objection Handling", icon: "shield", content: "Objection: \"We already have a recruiter.\"\nResponse: We complement recruiters with healthcare-specific candidates ready from day one.\n\nObjection: \"We can't afford it.\"\nResponse: Our model often saves money by eliminating bad hires and reducing overtime.\n\nObjection: \"We're not hiring.\"\nResponse: Many practices use us proactively so qualified candidates are ready when a need arises." },
      { title: "Closing", icon: "check", content: "I'd love to set up a 15-minute call to learn more about your practice and share how we've helped similar offices. We also offer a free staffing assessment. Would later this week work?" },
    ],
  }
}

// ======================= Email outreach =======================

export async function getEmailDraft(placeId: string): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`)
  } catch {
    return { subject: "", body: "" }
  }
}

export async function regenerateEmailDraft(placeId: string): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`, {
      method: "POST",
    })
  } catch {
    return { subject: "", body: "" }
  }
}

export async function saveEmailDraft(
  placeId: string,
  draft: Partial<EmailDraft>,
): Promise<EmailDraft> {
  try {
    return await apiFetch<EmailDraft>(`/api/practices/${placeId}/email/draft`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    })
  } catch {
    return { subject: draft.subject ?? "", body: draft.body ?? "" }
  }
}

export async function sendEmail(placeId: string): Promise<EmailMessage> {
  return await apiFetch<EmailMessage>(`/api/practices/${placeId}/email/send`, {
    method: "POST",
  })
}

export async function getEmailMessages(placeId: string): Promise<EmailMessage[]> {
  try {
    const data = await apiFetch<{ messages: EmailMessage[] }>(
      `/api/practices/${placeId}/email/messages`,
    )
    return data.messages
  } catch {
    return []
  }
}

export async function pollEmailReplies(
  placeId: string,
): Promise<{ new_messages: EmailMessage[]; total: number }> {
  return await apiFetch<{ new_messages: EmailMessage[]; total: number }>(
    `/api/practices/${placeId}/email/poll`,
    { method: "POST" },
  )
}

export async function markEmailReplied(placeId: string): Promise<EmailMessage> {
  return await apiFetch<EmailMessage>(
    `/api/practices/${placeId}/email/mark-replied`,
    { method: "POST" },
  )
}

export async function updatePracticeEmail(
  placeId: string,
  email: string,
): Promise<Practice> {
  return await apiFetch<Practice>(`/api/practices/${placeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  })
}

export interface CallLogResponse {
  practice: Practice
  sf_warning: string | null
}

export async function logCall(
  placeId: string,
  note: string,
): Promise<CallLogResponse> {
  return await apiFetch<CallLogResponse>(
    `/api/practices/${placeId}/call/log`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    },
  )
}

export async function updateLastCallNote(
  placeId: string,
  note: string,
): Promise<CallLogResponse> {
  return await apiFetch<CallLogResponse>(
    `/api/practices/${placeId}/call/last-note`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    },
  )
}

export async function getPractice(placeId: string): Promise<Practice> {
  return await apiFetch<Practice>(`/api/practices/${placeId}`)
}

export interface EnrichResponse {
  practice: Practice
  clay_warning: string | null
}

export async function enrichPractice(
  placeId: string,
): Promise<EnrichResponse> {
  return await apiFetch<EnrichResponse>(
    `/api/practices/${placeId}/enrich`,
    { method: "POST" },
  )
}

export interface AdminUserSummary {
  id: string
  email: string
  name: string | null
  role: "admin" | "sdr"
}

export async function listUsers(): Promise<AdminUserSummary[]> {
  try {
    const data = await apiFetch<{ users?: AdminUserSummary[] } | AdminUserSummary[]>(
      "/api/admin/users",
    )
    if (Array.isArray(data)) return data
    return data.users ?? []
  } catch {
    return []
  }
}

export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/api/me/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
}
