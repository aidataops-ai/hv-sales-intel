# HV Sales Intel — Solution Brief

**What it is.** A map-driven sales intelligence workbench for Health & Virtuals' outreach team. Reps search healthcare practices by city and specialty, see each as a pin on an interactive map, and work them through a CRM pipeline from first contact to close — with AI doing the heavy lifting on research, call prep, and first-touch drafting.

## Scope (what ships today)

- **Lead Discovery** — search by city and specialty; pins on an interactive map colored by lead heat; synchronized list in the sidebar.
- **AI Research** — per-practice analysis surfaces staffing pain points, sales angles, and three 0–100 scores (Lead, Urgency, Hiring Signal). A single "Score All" action processes the whole visible list.
- **Call Prep** — a five-section cold-call playbook (Opening, Discovery, Pitch, Objections, Closing) tailored to the practice, plus a notepad for the rep.
- **Email Outreach** — personalized email drafts, edit-and-send from inside the app, on-demand reply detection, threaded per lead.
- **CRM Pipeline** — nine statuses from `NEW` to `CLOSED WON/LOST`. Key events auto-advance the stage (analyzed, script generated, email sent, reply received); manual transitions from `CONTACTED` onward.
- **Team Accountability** — every lead is stamped with "last touched by" and timestamp, so the team sees who's driving what.
- **Admin-Controlled Access** — login required; no self-signup. Admins create, reset, and remove rep accounts.
- **One-click Integrations** — click-to-call via RingCentral; click-to-send via Microsoft 365 / Outlook.

## Data flow (high level)

1. Rep **signs in**. The app loads the current lead list from the central store.
2. Rep **searches** a city + specialty. The backend queries the discovery layer and upserts new practices.
3. Rep clicks **Analyze**. The backend gathers public signals about the practice, runs them through a proprietary analysis layer, and persists the results.
4. Rep opens **Call Prep**. The backend returns the cached playbook, or generates one on first visit.
5. Rep **drafts and sends** an outreach email. The message goes through the company mailbox; replies are pulled back on demand.
6. Every mutating action is **attributed** to the signed-in rep and can **auto-advance** the lead through the pipeline.

## How a rep uses it

1. **Log in** at the company URL.
2. **Search** for a city + specialty (e.g. *"dental clinics in Houston"*).
3. **Score All** or click **Analyze** on individual cards. Hot leads float to the top.
4. Open a card → **Call Prep** for the full playbook, or the **Email** tab for an AI-drafted outreach email.
5. **Send.** Status auto-advances to `CONTACTED`. Replies flip it to `FOLLOW UP`.
6. Move the lead manually through `MEETING SET` → `PROPOSAL` → `CLOSED WON` / `CLOSED LOST` as the deal progresses.

## How an admin uses it

- **Create rep accounts** under *Users*.
- **Reset or remove** accounts as the team changes.
- **Review who's touching which lead** via each card's attribution line.

## Environment

- Runs on modern browsers; backend and frontend deploy to standard cloud hosting.
- Integrates with Microsoft 365 (email), Google Places (discovery), RingCentral (calling), and an LLM layer (analysis + drafting).
- Mock mode: the full UI runs without any external keys, useful for demos and onboarding.
- Test and production share the same stack; environment is toggled via config.
