"use client"

import Link from "next/link"
import {
  ArrowLeft, Target, AlertCircle, UserCheck, Wifi, Briefcase,
  DollarSign, Shield, MapPin, Building2, Award, Sparkles,
} from "lucide-react"

/**
 * Public-facing ICP rubric. Documents the seven-dimension scoring model
 * used by the analyzer, the vertical + tier taxonomy, the AI-bucketing
 * discipline, and the score-interpretation cutoffs. Linked from the
 * user menu and shown during demos.
 */
export default function ICPPage() {
  return (
    <div className="min-h-screen bg-cream py-10 px-6">
      <div className="max-w-3xl mx-auto">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-teal-700 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to map
        </Link>

        <header className="mb-8">
          <h1 className="font-serif text-4xl font-bold text-gray-900">
            ICP scoring rubric
          </h1>
          <p className="text-sm text-gray-500 mt-2 max-w-2xl">
            Every lead is scored 0–100 against the universal Ideal Customer
            Profile. The breakdown on each practice card shows exactly which
            dimensions earned which points so an SDR can trust — and challenge — the ranking.
          </p>
        </header>

        {/* ---------------- 7 dimensions overview ---------------- */}
        <Section icon={<Target className="w-5 h-5" />} title="The seven dimensions">
          <p className="text-sm text-gray-700 mb-4">
            The total of 100 points splits across seven dimensions. One is
            deterministic (Vertical fit), the other six are AI-inferred from
            the practice&apos;s website + reviews.
          </p>

          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                <th className="py-2 pr-3">Dimension</th>
                <th className="py-2 pr-3 w-16 text-right">Max</th>
                <th className="py-2">Source</th>
              </tr>
            </thead>
            <tbody className="text-gray-700">
              <Row name="Vertical fit"          max={15} src="Deterministic" />
              <Row name="Operational pain"      max={20} src="AI" />
              <Row name="Decision-maker access" max={15} src="AI" />
              <Row name="Remote readiness"      max={15} src="AI" />
              <Row name="Role clarity"          max={15} src="AI" />
              <Row name="Budget maturity"       max={10} src="AI" />
              <Row name="Compliance boundary"   max={10} src="AI" />
            </tbody>
            <tfoot>
              <tr className="text-sm font-semibold border-t">
                <td className="py-2 pr-3">Total</td>
                <td className="py-2 pr-3 text-right">100</td>
                <td className="py-2"></td>
              </tr>
            </tfoot>
          </table>
        </Section>

        {/* ---------------- Each dimension in depth ---------------- */}
        <Dimension
          icon={<MapPin className="w-5 h-5" />}
          name="Vertical fit"
          max={15}
          summary="Does the business match the verticals we sell to, and are they in our focus geography?"
          how={[
            "Deterministic — no AI, no run-to-run noise.",
            "Computed from the practice's `state`, the AI-classified `icp_vertical` (medical / mental_health / dental / alf_nh / hotel_resort / medspa_wellness), and `icp_tier` (A / B / C / D).",
            "Tier A: 13 base · Tier B: 15 base (highest-fit growth stage) · Tier C: 9 (selective) · Tier D: 6 (enterprise / longer cycle).",
            "Florida targets get the full base. Other US states scale to 60%. Outside the US scores 0 — and the dimension is the only ceiling on geography, so an off-geography lead can never outrank an in-geography one of comparable quality.",
            "An unclassified vertical (i.e. \"other\") earns a token 3.",
          ]}
        />

        <Dimension
          icon={<AlertCircle className="w-5 h-5" />}
          name="Operational pain"
          max={20}
          summary="How obvious is the admin / scheduling / billing / follow-up burden right now?"
          how={[
            "AI-derived from the website + Google reviews + third-party review aggregators.",
            "Signals that score this UP: negative reviews about wait times, missed calls, slow follow-up; \"overwhelmed\" / \"understaffed\" language; treatment plans / packages / leads not followed up; insurance / billing backlog.",
            "Signals that score this DOWN: glowing reviews with no operational complaints; clearly well-staffed front desk; one-touch responsiveness.",
            "Largest dimension (max 20) — pain is the single best predictor of urgency to buy.",
          ]}
        />

        <Dimension
          icon={<UserCheck className="w-5 h-5" />}
          name="Decision-maker access"
          max={15}
          summary="Is there an identifiable owner / GM / administrator who can approve recurring spend?"
          how={[
            "AI-derived from the website.",
            "Scores UP when there's a named owner / founder / managing partner, a \"Meet the team\" page, clear leadership bios, LinkedIn links on the site.",
            "Scores DOWN when the practice hides behind a faceless brand — no names, no leadership, no \"About\" page.",
            "Why it matters: without an identifiable decision-maker the cold call dies at \"who's the right person to talk to?\".",
          ]}
        />

        <Dimension
          icon={<Wifi className="w-5 h-5" />}
          name="Remote readiness"
          max={15}
          summary="Do they use digital systems we can plug into?"
          how={[
            "AI-derived from the website.",
            "Scores UP when the site mentions an EHR / PMS / CRM (Dentrix, Open Dental, Eaglesoft, PointClickCare, MatrixCare, Opera, Cloudbeds, Aesthetic Record, Boulevard, Zenoti, Mindbody, HubSpot, Salesforce, Weave, NexHealth, etc.); online booking; patient / customer portal; digital intake; e-signature consent forms.",
            "Scores DOWN when the site shows only a phone number and a PDF intake — they're paper-first, hard to remote-staff.",
            "Why it matters: remote staff can't help if every workflow still happens on a clipboard.",
          ]}
        />

        <Dimension
          icon={<Briefcase className="w-5 h-5" />}
          name="Role clarity"
          max={15}
          summary="How easy is it to define ONE narrow remote role that solves a specific pain today?"
          how={[
            "AI-derived from the website + (optional) careers / jobs pages.",
            "Scores UP when there are explicit open roles for non-clinical positions (front desk, scheduler, medical assistant, coordinator, admin, billing). A careers page that says \"Now hiring: Patient Coordinator\" is gold.",
            "Scores DOWN when the only roles posted are licensed-clinical (doctor, RN, hygienist) or when the practice is so small there's no separate admin function at all.",
            "Why it matters: a deal closes when the rep can write a one-line job description; everything else stays in nurture.",
          ]}
        />

        <Dimension
          icon={<DollarSign className="w-5 h-5" />}
          name="Budget maturity"
          max={10}
          summary="Can they support a recurring monthly seat cost (not a one-time project)?"
          how={[
            "AI-derived from the website + Google Places metadata.",
            "Scores UP when the site shows multiple providers / locations, a paid software stack, premium service positioning, evidence of advertising spend, multi-year operation, large team photos.",
            "Scores DOWN for one-provider micro-practices, single-page websites, or businesses that read as side-hustle scale.",
            "Why it matters: a $499/mo retainer is a non-starter for the smallest practices regardless of how much pain they have.",
          ]}
        />

        <Dimension
          icon={<Shield className="w-5 h-5" />}
          name="Compliance boundary"
          max={10}
          summary="Does the engagement stay within our non-clinical, non-physical scope?"
          how={[
            "AI-derived from the practice's pain + sales angles.",
            "Scores UP when the obvious work is admin / scheduling / billing / coordination — all remotable, all non-clinical, all non-physical.",
            "Scores DOWN when the practice's pain points imply licensed clinical work (\"we need another nurse\"), in-person tasks (\"clean rooms between patients\"), or unscoped catch-all duties (\"someone who does everything\").",
            "Why it matters: the wrong scope creates onboarding pain and churn risk — better to disqualify early than refund later.",
          ]}
        />

        {/* ---------------- AI discipline ---------------- */}
        <Section icon={<Sparkles className="w-5 h-5" />} title="How the AI scoring stays stable">
          <p className="text-sm text-gray-700 mb-3">
            Re-analyzing the same lead used to produce different scores
            every time because GPT&apos;s output drifts by a few points per
            dimension. Three things keep it pinned now:
          </p>
          <ol className="text-sm text-gray-700 space-y-2 list-decimal list-inside">
            <li>
              <span className="font-medium">Categorical buckets.</span>{" "}
              The model is told to pick from{" "}
              <code className="bg-gray-100 px-1 rounded">
                &#123;0, 20, 40, 60, 80, 100&#125;
              </code>{" "}
              per dimension — never the continuum in between. Server snaps
              any off-bucket value back to the nearest one.
            </li>
            <li>
              <span className="font-medium">Temperature 0 + fixed seed.</span>{" "}
              Same prompt → same tokens (best-effort per OpenAI).
            </li>
            <li>
              <span className="font-medium">Input-hash caching.</span>{" "}
              A SHA-256 of the practice&apos;s identity fields (name,
              website, category, state, city) is stored with each analysis.
              Re-analyze on an unchanged practice returns the cached
              result instead of re-running the AI; only when Rescan
              materially changes the Google data does the AI run again.
            </li>
          </ol>
        </Section>

        {/* ---------------- Score interpretation ---------------- */}
        <Section icon={<Award className="w-5 h-5" />} title="Score interpretation">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                <th className="py-2 pr-3">Total</th>
                <th className="py-2 pr-3">Classification</th>
                <th className="py-2">Action</th>
              </tr>
            </thead>
            <tbody className="text-gray-700">
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono">85–100</td>
                <td className="py-2 pr-3 font-medium text-rose-700">Strong ICP</td>
                <td className="py-2">Advance to demo / role definition this week.</td>
              </tr>
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono">70–84</td>
                <td className="py-2 pr-3 font-medium text-amber-700">Qualified with conditions</td>
                <td className="py-2">Advance if a narrow first-role scope is obvious.</td>
              </tr>
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono">55–69</td>
                <td className="py-2 pr-3 font-medium text-teal-700">Weak / exploratory</td>
                <td className="py-2">Nurture or defer — don&apos;t spend a calling slot here.</td>
              </tr>
              <tr>
                <td className="py-2 pr-3 font-mono">&lt; 55</td>
                <td className="py-2 pr-3 font-medium text-gray-500">Poor fit</td>
                <td className="py-2">Disqualify.</td>
              </tr>
            </tbody>
          </table>
        </Section>

        {/* ---------------- Verticals & tiers ---------------- */}
        <Section icon={<Building2 className="w-5 h-5" />} title="Verticals">
          <p className="text-sm text-gray-700 mb-4">
            Each lead is classified into exactly one of these five verticals
            by the analyzer at scoring time. The classification drives the
            Vertical fit dimension and tunes the prompt&apos;s vocabulary
            (e.g. &quot;Dentrix&quot; matters for dental, &quot;PointClickCare&quot;
            for nursing homes).
          </p>
          <ul className="text-sm text-gray-700 space-y-2">
            <li>
              <span className="font-semibold">Medical</span> — primary care,
              internal medicine, family medicine, mental health (psychiatry,
              behavioral, therapy), chiropractic, urgent care.
            </li>
            <li>
              <span className="font-semibold">Dental</span> — general + specialty
              (orthodontist, periodontist, endodontist, oral surgeon, pediatric).
            </li>
            <li>
              <span className="font-semibold">ALF / Nursing</span> — assisted
              living, memory care, nursing home, skilled nursing, senior living.
            </li>
            <li>
              <span className="font-semibold">Hotels / Resorts</span> — hotels,
              resorts, vacation rental managers, boutique properties.
            </li>
            <li>
              <span className="font-semibold">MedSpa / Wellness</span> — medspas,
              day spas, wellness clinics, physician-led aesthetics, resort spas.
            </li>
          </ul>
        </Section>

        <Section icon={<Award className="w-5 h-5" />} title="Tiers">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                <th className="py-2 pr-3 w-12">Tier</th>
                <th className="py-2 pr-3">Profile</th>
                <th className="py-2">Sales motion</th>
              </tr>
            </thead>
            <tbody className="text-gray-700">
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono font-bold">A</td>
                <td className="py-2 pr-3">Small / single-location / owner-led</td>
                <td className="py-2">Primary entry motion. Single-seat sell.</td>
              </tr>
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono font-bold">B</td>
                <td className="py-2 pr-3">Growth-stage / mid-sized</td>
                <td className="py-2">Highest fit. Single seat → function expansion.</td>
              </tr>
              <tr className="border-b">
                <td className="py-2 pr-3 font-mono font-bold">C</td>
                <td className="py-2 pr-3">Mid-market / specialty / multi-property</td>
                <td className="py-2">Selective or opportunistic.</td>
              </tr>
              <tr>
                <td className="py-2 pr-3 font-mono font-bold">D</td>
                <td className="py-2 pr-3">Enterprise / corporate / multi-state</td>
                <td className="py-2">Opportunistic only — longer procurement cycle.</td>
              </tr>
            </tbody>
          </table>
        </Section>

        <p className="text-[11px] text-gray-400 mt-8 text-center">
          Source: <code>src/icp_scorer.py</code> + <code>src/analyzer.py</code>{" "}
          + the ICP definitions document. Weights and disqualifiers will be
          per-tenant configurable in a future release — the rubric above is
          the universal default.
        </p>
      </div>
    </div>
  )
}

function Section({
  icon, title, children,
}: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white/80 rounded-2xl shadow-sm border border-gray-200/50 p-6 mb-5">
      <div className="flex items-center gap-2 mb-4">
        <span className="inline-flex w-8 h-8 rounded-md bg-teal-50 text-teal-700 items-center justify-center">
          {icon}
        </span>
        <h2 className="font-serif text-xl font-semibold text-gray-900">
          {title}
        </h2>
      </div>
      {children}
    </section>
  )
}

function Row({ name, max, src }: { name: string; max: number; src: string }) {
  return (
    <tr className="border-b">
      <td className="py-2 pr-3">{name}</td>
      <td className="py-2 pr-3 text-right font-mono">{max}</td>
      <td className="py-2 text-gray-500">{src}</td>
    </tr>
  )
}

function Dimension({
  icon, name, max, summary, how,
}: {
  icon: React.ReactNode
  name: string
  max: number
  summary: string
  how: string[]
}) {
  return (
    <section className="bg-white/80 rounded-2xl shadow-sm border border-gray-200/50 p-6 mb-5">
      <header className="flex items-start justify-between mb-3">
        <div className="flex items-start gap-2">
          <span className="inline-flex w-8 h-8 rounded-md bg-teal-50 text-teal-700 items-center justify-center">
            {icon}
          </span>
          <div>
            <h2 className="font-serif text-xl font-semibold text-gray-900">
              {name}
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">{summary}</p>
          </div>
        </div>
        <span className="text-xs font-mono bg-gray-100 text-gray-700 px-2 py-1 rounded">
          max {max}
        </span>
      </header>
      <ul className="text-sm text-gray-700 space-y-1.5 list-disc list-inside marker:text-gray-400">
        {how.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </section>
  )
}
