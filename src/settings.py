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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
