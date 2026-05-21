from pydantic import BaseModel


class Practice(BaseModel):
    place_id: str
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int = 0
    category: str | None = None
    lat: float | None = None
    lng: float | None = None
    opening_hours: str | None = None

    # Phase 2 (AI)
    summary: str | None = None
    pain_points: str | None = None
    sales_angles: str | None = None
    recommended_service: str | None = None
    lead_score: int | None = None
    urgency_score: int | None = None
    hiring_signal_score: int | None = None

    # H&V Universal ICP — vertical + tier classified by the analyzer
    icp_vertical: str | None = None  # medical | dental | alf_nh | hotel_resort | medspa_wellness
    icp_tier: str | None = None      # A | B | C | D
    # Hash of analyzer inputs — used to short-circuit Re-analyze when nothing changed.
    analysis_input_hash: str | None = None
    # AI-extracted decision-maker contacts from the website (JSON string).
    website_contacts: str | None = None
    # Number of times this row has been included in a bulk CSV export.
    export_count: int = 0
    # When the row was last included in a CSV export, and by which user.
    last_exported_at: str | None = None
    last_exported_by: str | None = None
    # Resolved at read-time via a profile join — not a stored column.
    last_exported_by_name: str | None = None

    # Phase 3 (CRM)
    status: str = "NEW"
    notes: str | None = None

    # Email outreach
    email: str | None = None
    email_draft: str | None = None
    email_draft_updated_at: str | None = None

    # Attribution
    last_touched_by: str | None = None
    last_touched_by_name: str | None = None
    last_touched_at: str | None = None

    # Salesforce integration + call log
    salesforce_lead_id: str | None = None
    salesforce_lead_url: str | None = None
    salesforce_owner_id: str | None = None
    salesforce_owner_name: str | None = None
    salesforce_synced_at: str | None = None
    call_count: int = 0
    call_notes: str | None = None

    # Clay owner enrichment
    owner_name: str | None = None
    owner_email: str | None = None
    owner_phone: str | None = None
    owner_title: str | None = None
    owner_linkedin: str | None = None
    enrichment_status: str | None = None
    enriched_at: str | None = None

    # Tags (multi-status — append-only in DB; transient on out-of-domain results)
    tags: list[str] = []
