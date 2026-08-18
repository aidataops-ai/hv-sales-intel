import pytest


@pytest.fixture(autouse=True)
def _reset_openai_clients():
    """Never let one test's OpenAI client survive into the next.

    The AI modules cache their client in a module global (so the process
    stops paying a TLS handshake per call). The cache key already rebuilds
    when a test patches `settings` or the `AsyncOpenAI` symbol, but tests
    that patch neither — or that patch the real settings object with a key
    a later test also uses — would otherwise share a client. Clearing
    around every test makes that impossible by construction.
    """
    from src import analyzer, email_gen, icp_parser, lead_qualifier, scriptgen

    modules = (analyzer, email_gen, icp_parser, lead_qualifier, scriptgen)
    for module in modules:
        module._reset_client()
    yield
    for module in modules:
        module._reset_client()


@pytest.fixture
def sample_sdr_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "sdr@example.com",
        "name": "Test SDR",
        "role": "sdr",
        "created_at": "2026-04-22T00:00:00Z",
    }


@pytest.fixture
def sample_admin_profile() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "admin@example.com",
        "name": "Test Admin",
        "role": "admin",
        "created_at": "2026-04-22T00:00:00Z",
    }
