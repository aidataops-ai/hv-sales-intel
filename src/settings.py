from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_maps_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""                    # anon key (legacy name preserved)
    supabase_service_role_key: str = ""       # admin client for auth verification
    openai_api_key: str = ""
    # gpt-4.1 is the recommended default for ICP analysis — significantly
    # more accurate than gpt-4o on multi-criteria classification.
    openai_model: str = "gpt-4.1"

    # ----- Job-posting leads (docs/specs/2026-08-05-hiring-signal-collector-*) --
    # Benchmarked against higher reasoning effort on identical inputs: both
    # scored identically on every accuracy test, while high effort cost ~57%
    # more output tokens and ~81% more wall clock (ADR-06).
    qualifier_model: str = "gpt-5.6-terra"
    qualifier_reasoning_effort: str = "medium"
    qualifier_batch_size: int = 20
    # Qualify's batch size. It runs as a serverless invocation behind
    # /api/index.py, so a full drain can't fit one call — it takes a
    # bounded slice and is safe to re-run (ADR-09). Collect's equivalent
    # (`lead_collect_batch`, a target COUNT) was retired with the matrix
    # model — collect is now a wall-clock budget, see `lead_budget_minutes`.
    lead_qualify_batch: int = 60
    # Instant Signals target-dimension collector
    # (docs/refactor/instant-signals-targets.md). Wall-clock budget replaces the old
    # `--targets N` count now that the pipeline runs on GitHub Actions
    # instead of a serverless invocation; per-source staleness thresholds
    # and window buffer drive the adaptive search window; the zero-streak
    # cap bounds how far yield decay can push a dead location's threshold.
    lead_budget_minutes: int = 40
    lead_indeed_stale_hours: int = 6
    lead_linkedin_stale_hours: int = 6
    # LinkedIn runs two tiers. Statewide rows are the INSTANT tier: a
    # statewide query already sees postings from every city, so a fresh
    # posting is caught within `lead_linkedin_stale_hours` above (~63min of
    # scrape per full statewide cycle). City rows are the RECALL tier: they
    # only add postings the statewide query dropped past the board's
    # ~40-results-per-query cap in dense markets, so they rotate on the much
    # slower `lead_linkedin_city_stale_hours` wheel — a full city pass is
    # ~33h of scrape (33 terms x 155 cities x measured 23s/term), which a
    # dedicated LinkedIn workflow turns in roughly 4 days. Statewide-only
    # (`True`) is the escape hatch that drops the city tier entirely — the
    # 2026-08-13 shape, adopted when a UNIFORM threshold across all LinkedIn
    # rows collapsed full-matrix freshness to ~3 days; the tier split is
    # what makes city coverage affordable without giving that back.
    lead_linkedin_statewide_only: bool = False
    lead_linkedin_city_stale_hours: int = 72
    lead_window_buffer_hours: int = 12
    lead_zero_streak_cap: int = 4
    # Phase-reserve + fit-check (Phase 4 livelock fix): when both boards are
    # enabled, Indeed's phase is capped at this fraction of the collect
    # budget so a flood of never-swept locations (e.g. right after a
    # re-seed) can never fully starve LinkedIn's phase — LinkedIn always
    # gets the rest of the budget, even if Indeed still has due locations
    # left. A single enabled source gets the whole budget; there is nothing
    # to reserve against.
    lead_indeed_budget_fraction: float = 0.6
    # Conservative per-term cost estimate used ONLY until this run has its
    # own observed average (from job_boards' `elapsed_s`) — picking a
    # location whose full term list can't fit in what's left of the phase
    # budget is the other half of the livelock fix: it stops the collector
    # from repeatedly claiming, partially sweeping, and abandoning the same
    # stalest location every run without ever finishing it.
    lead_indeed_est_term_s: float = 6.0
    lead_linkedin_est_term_s: float = 25.0
    # The HTTP cron route's own budget — much smaller than
    # `lead_budget_minutes`, which is the GitHub Actions runner's ceiling.
    # This route still runs inside a serverless invocation with its own
    # wall-clock limit, so its default has to leave headroom for the qualify
    # stage and function overhead rather than spending the whole invocation
    # on collect.
    lead_cron_budget_minutes: int = 10
    # Shared secret for the cron stages, matching the existing webhook
    # pattern. Empty disables the cron routes outright rather than leaving
    # them open.
    lead_cron_secret: str = ""
    # The tenant collection runs for. v1 is single-tenant: set this to the
    # company that already owns the practices so places and signals sit
    # together. Left empty, the resolver falls back to the sole company and
    # only errors if there is genuinely more than one to choose between —
    # so a fresh deploy works without it, and a second tenant fails loudly
    # instead of silently collecting for whichever row sorted first.
    lead_company_id: str = ""
    # Manual "retrigger" of the sweep dispatches the GitHub Actions workflow
    # (.github/workflows/leads.yml) rather than running collect/qualify in the
    # API process — a full sweep can outlast a serverless invocation, which is
    # why the scheduled run already lives on a GitHub runner. Needs a token with
    # `actions: write` on the repo. Empty disables the retrigger endpoint (503).
    github_token: str = ""
    github_repo: str = "aidataops-ai/hv-sales-intel"
    github_leads_workflow: str = "leads.yml"
    github_workflow_ref: str = "main"

    # Bootstrap admin (seeded on startup if profiles has zero admins)
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Microsoft Graph (email outreach)
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_refresh_token: str = ""
    ms_sender_email: str = ""
    email_reply_lookback_days: int = 30

    # Salesforce integration (Apex REST endpoint + x-api-key)
    sf_apex_url: str = ""
    sf_api_key: str = ""
    # Lightning base URL used to construct the Lead view link returned
    # to the frontend after a Lead is created. Override per-org.
    sf_lead_view_base_url: str = "https://apexvirtuals.lightning.force.com/lightning/r/Lead"
    # Legacy OAuth fields (no longer used; kept for backwards compatibility with existing .env files)
    sf_client_id: str = ""
    sf_client_secret: str = ""
    sf_username: str = ""
    sf_password: str = ""
    sf_security_token: str = ""
    sf_login_url: str = "https://login.salesforce.com"
    sf_api_version: str = "v60.0"

    # Clay owner enrichment
    clay_table_webhook_url: str = ""
    clay_table_api_key: str = ""
    clay_inbound_secret: str = ""

    # Talent-DB inbound Lead webhook ("Import Lead" button).
    # We POST a signed Lead envelope; body is HMAC-SHA256-signed with the
    # secret (X-HV-Signature). Empty secret/url disables the feature (endpoints
    # return a non-blocking warning). Staging URL by default.
    # See docs/specs/2026-08-11-talentdb-lead-webhook-design.md.
    talentdb_webhook_url: str = ""
    talentdb_webhook_secret: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
