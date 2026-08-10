"""Tests for the search-target matrix and its rotation."""

import pytest

from src import lead_config, lead_targets


def test_build_targets_is_the_full_matrix():
    targets = lead_targets.build_targets()
    assert len(targets) == len(lead_config.role_terms()) * len(lead_config.locations())


def test_build_targets_is_location_major():
    """A contiguous slice should span several terms in one city, not one term
    across every city — otherwise a run's results cover a thin geographic
    stripe instead of a readable market."""
    targets = lead_targets.build_targets()
    first_location = targets[0]["location"]
    term_count = len(lead_config.role_terms())
    assert all(t["location"] == first_location for t in targets[:term_count])
    assert len({t["term"] for t in targets[:term_count]}) == term_count


def test_every_target_carries_its_service_line_and_state():
    for target in lead_targets.build_targets():
        assert target["service_line"]
        assert len(target["state"]) == 2
        assert target["granularity"] in ("state", "city")


def test_indeed_runs_every_firing_linkedin_on_a_slower_cycle():
    """ADR-02 / design §6: Indeed is ~15x faster, so it rotates broadly while
    LinkedIn supplements. Weights live in filters.json."""
    runs = [lead_targets.sources_for_run(i) for i in range(6)]
    indeed_runs = sum(1 for r in runs if "indeed" in r)
    linkedin_runs = sum(1 for r in runs if "linkedin" in r)
    assert indeed_runs == 6
    assert 0 < linkedin_runs < indeed_runs


def test_a_run_never_selects_zero_sources():
    """A no-op run is indistinguishable from a board outage in the logs."""
    for i in range(12):
        assert lead_targets.sources_for_run(i)


def test_sources_for_run_only_returns_enabled_boards():
    enabled = set(lead_config.enabled_sources())
    for i in range(12):
        assert set(lead_targets.sources_for_run(i)) <= enabled


# --------------------------------------------------------------------------
# Single-tenant resolution (v1 pins one company at run time; the schema stays
# multi-tenant so this is a pin to remove, not a migration to write).
# --------------------------------------------------------------------------


def test_the_configured_company_wins(monkeypatch):
    from src.settings import settings
    monkeypatch.setattr(settings, "lead_company_id", "pinned-company")
    assert lead_targets.resolve_company_id() == "pinned-company"


def test_a_lone_company_needs_no_configuration(monkeypatch):
    """A fresh deploy shouldn't need the env var to work at all."""
    from src.settings import settings
    monkeypatch.setattr(settings, "lead_company_id", "")
    monkeypatch.setattr(
        "src.storage._get_client",
        lambda: _FakeCompanies([{"id": "only-company"}]),
    )
    assert lead_targets.resolve_company_id() == "only-company"


def test_two_companies_without_a_pin_is_an_error_not_a_guess(monkeypatch):
    """Picking whichever row sorted first would quietly bill the wrong tenant."""
    from src.settings import settings
    monkeypatch.setattr(settings, "lead_company_id", "")
    monkeypatch.setattr(
        "src.storage._get_client",
        lambda: _FakeCompanies([{"id": "a"}, {"id": "b"}]),
    )
    with pytest.raises(lead_targets.NoLeadCompany, match="LEAD_COMPANY_ID"):
        lead_targets.resolve_company_id()


def test_no_companies_at_all_is_an_error(monkeypatch):
    from src.settings import settings
    monkeypatch.setattr(settings, "lead_company_id", "")
    monkeypatch.setattr("src.storage._get_client", lambda: _FakeCompanies([]))
    with pytest.raises(lead_targets.NoLeadCompany):
        lead_targets.resolve_company_id()


# --------------------------------------------------------------------------
# Config page — catalog and hand-added-target validation.
# --------------------------------------------------------------------------


def test_catalog_regroups_locations_back_into_states_with_cities():
    """The UI shows states-with-cities, but `locations()` is flat. The catalog
    must rebuild the grouping (and pick out the statewide query) so the config
    file round-trips to the same shape it was authored in."""
    cat = lead_targets.catalog()
    codes = {s["code"] for s in cat["states"]}
    assert "FL" in codes
    fl = next(s for s in cat["states"] if s["code"] == "FL")
    assert fl["statewide_query"] == "Florida, USA"
    assert "Miami, FL" in fl["cities"]
    assert fl["statewide_query"] not in fl["cities"]


def test_catalog_tracks_group_every_term_under_its_service_line():
    cat = lead_targets.catalog()
    tracks = {t["service_line"]: t["terms"] for t in cat["tracks"]}
    assert set(tracks) == set(lead_config.service_lines())
    flat = [term for terms in tracks.values() for term in terms]
    assert len(flat) == len(lead_config.role_terms())


def test_clean_target_row_normalises_state_and_granularity():
    row = lead_targets._clean_target_row(
        "co",
        {"term": " Nurse ", "service_line": "X", "location": "Austin, TX",
         "state": "tx", "granularity": "City"},
    )
    assert row["term"] == "Nurse"          # trimmed
    assert row["state"] == "TX"            # upper-cased
    assert row["granularity"] == "city"    # lower-cased
    assert row["enabled"] is True          # defaulted
    assert row["company_id"] == "co"


@pytest.mark.parametrize(
    "bad",
    [
        {"term": "", "service_line": "X", "location": "L", "state": "TX", "granularity": "city"},
        {"term": "t", "service_line": "", "location": "L", "state": "TX", "granularity": "city"},
        {"term": "t", "service_line": "X", "location": "", "state": "TX", "granularity": "city"},
        {"term": "t", "service_line": "X", "location": "L", "state": "TEX", "granularity": "city"},
        {"term": "t", "service_line": "X", "location": "L", "state": "TX", "granularity": "county"},
    ],
)
def test_clean_target_row_rejects_rows_the_db_constraints_would(bad):
    """Same invariants as the table's CHECK/NOT NULL constraints, caught early
    so the add endpoint answers 400 instead of leaking a Postgres error."""
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets._clean_target_row("co", bad)


class _FakeCompanies:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return self

    def select(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()
