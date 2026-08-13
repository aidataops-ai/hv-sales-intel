"""Tests for the search-target dimensions, claim ordering, and pure helpers."""

import pytest

from src import lead_config, lead_targets

# --------------------------------------------------------------------------
# Pure helpers — adaptive window and yield-decay threshold (plan §3).
# --------------------------------------------------------------------------


def test_adaptive_window_never_swept_gets_full_week():
    assert lead_targets.adaptive_window_hours(None, buffer_hours=12) == 168


def test_adaptive_window_floors_at_24_for_a_fresh_cursor():
    from datetime import datetime, timezone
    cursor = datetime.now(timezone.utc).isoformat()
    assert lead_targets.adaptive_window_hours(cursor, buffer_hours=12) == 24


def test_adaptive_window_caps_at_168_for_a_stale_cursor():
    from datetime import datetime, timedelta, timezone
    cursor = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert lead_targets.adaptive_window_hours(cursor, buffer_hours=12) == 168


def test_adaptive_window_mid_range_is_hours_since_plus_buffer():
    from datetime import datetime, timedelta, timezone
    cursor = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    window = lead_targets.adaptive_window_hours(cursor, buffer_hours=12)
    # ~20h since + 12h buffer = ~32h, clamped comfortably inside [24, 168]
    assert 30 <= window <= 34


def test_effective_threshold_doubles_per_streak():
    assert lead_targets.effective_threshold_hours(6, zero_streak=0, cap=4) == 6
    assert lead_targets.effective_threshold_hours(6, zero_streak=1, cap=4) == 12
    assert lead_targets.effective_threshold_hours(6, zero_streak=2, cap=4) == 24
    assert lead_targets.effective_threshold_hours(6, zero_streak=3, cap=4) == 48


def test_effective_threshold_caps_growth():
    capped = lead_targets.effective_threshold_hours(6, zero_streak=4, cap=4)
    beyond = lead_targets.effective_threshold_hours(6, zero_streak=10, cap=4)
    assert capped == beyond == 6 * (2 ** 4)


# --------------------------------------------------------------------------
# phase_deadline / location_fits_budget — the livelock fix (plan §3, Phase 4).
# --------------------------------------------------------------------------


def test_phase_deadline_reserves_a_fraction_for_indeed_when_both_enabled():
    d = lead_targets.phase_deadline(
        "indeed", ["indeed", "linkedin"], start=1000.0, budget_seconds=600.0,
        indeed_fraction=0.6,
    )
    assert d == 1000.0 + 360.0


def test_phase_deadline_gives_linkedin_the_full_budget_when_both_enabled():
    """Not a split — indeed's reserve caps ITS phase, but linkedin still
    gets the whole run deadline, not "whatever's left of the total"."""
    d = lead_targets.phase_deadline(
        "linkedin", ["indeed", "linkedin"], start=1000.0, budget_seconds=600.0,
        indeed_fraction=0.6,
    )
    assert d == 1000.0 + 600.0


def test_phase_deadline_gives_a_single_enabled_source_the_full_budget():
    for source, sources in (("linkedin", ["linkedin"]), ("indeed", ["indeed"])):
        d = lead_targets.phase_deadline(
            source, sources, start=1000.0, budget_seconds=600.0, indeed_fraction=0.6,
        )
        assert d == 1000.0 + 600.0


def test_location_fits_budget_true_with_headroom():
    # estimate = 5*3=15s, *0.8 safety = 12s; now(0)+12 <= deadline(100)
    assert lead_targets.location_fits_budget(
        now=0.0, phase_deadline_ts=100.0, avg_term_seconds=5.0, term_count=3,
    ) is True


def test_location_fits_budget_false_when_estimate_exceeds_remaining():
    # estimate = 25*3=75s, *0.8 safety = 60s; now(90)+60=150 > deadline(100)
    assert lead_targets.location_fits_budget(
        now=90.0, phase_deadline_ts=100.0, avg_term_seconds=25.0, term_count=3,
    ) is False


def test_location_fits_budget_zero_terms_always_fits():
    assert lead_targets.location_fits_budget(
        now=99.9, phase_deadline_ts=100.0, avg_term_seconds=999.0, term_count=0,
    ) is True


# --------------------------------------------------------------------------
# build_claim_rows — pure cross of one location with enabled terms, minus
# override-disabled cells. The claim contract callers depend on.
# --------------------------------------------------------------------------


def test_build_claim_rows_crosses_location_with_every_term():
    location = {"id": 1, "location": "Tampa, FL", "state": "FL", "granularity": "city"}
    terms = [
        {"id": 10, "term": "RN", "service_line": "nursing"},
        {"id": 11, "term": "medical assistant", "service_line": "clinical"},
    ]
    rows = lead_targets.build_claim_rows(location, terms, overrides={})
    assert len(rows) == 2
    assert {r["term"] for r in rows} == {"RN", "medical assistant"}


def test_build_claim_rows_contract_keys_are_exact():
    location = {"id": 1, "location": "Tampa, FL", "state": "FL", "granularity": "city"}
    terms = [{"id": 10, "term": "RN", "service_line": "nursing"}]
    rows = lead_targets.build_claim_rows(location, terms, overrides={})
    assert set(rows[0]) == {
        "term", "service_line", "location", "state", "granularity",
        "term_id", "location_id",
    }
    assert rows[0] == {
        "term": "RN", "service_line": "nursing",
        "location": "Tampa, FL", "state": "FL", "granularity": "city",
        "term_id": 10, "location_id": 1,
    }


def test_build_claim_rows_skips_an_override_disabled_cell():
    location = {"id": 1, "location": "Tampa, FL", "state": "FL", "granularity": "city"}
    terms = [
        {"id": 10, "term": "RN", "service_line": "nursing"},
        {"id": 11, "term": "medical assistant", "service_line": "clinical"},
    ]
    overrides = {(10, 1): False}
    rows = lead_targets.build_claim_rows(location, terms, overrides)
    assert len(rows) == 1
    assert rows[0]["term"] == "medical assistant"


def test_build_claim_rows_an_override_true_entry_is_not_skipped():
    """Only an explicit `False` pin drops a cell — an explicit `True` (a
    pin re-enabling a cell) behaves like no override at all."""
    location = {"id": 1, "location": "Tampa, FL", "state": "FL", "granularity": "city"}
    terms = [{"id": 10, "term": "RN", "service_line": "nursing"}]
    overrides = {(10, 1): True}
    rows = lead_targets.build_claim_rows(location, terms, overrides)
    assert len(rows) == 1


# --------------------------------------------------------------------------
# add_terms / add_locations — validate-all-then-upsert-once.
# --------------------------------------------------------------------------


def test_clean_term_row_normalises_and_defaults_enabled():
    row = lead_targets._clean_term_row("co", {"term": " RN ", "service_line": "nursing"})
    assert row == {
        "company_id": "co", "term": "RN", "service_line": "nursing", "enabled": True,
    }


def test_clean_term_row_rejects_empty_term():
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets._clean_term_row("co", {"term": "", "service_line": "nursing"})


def test_clean_term_row_rejects_empty_service_line():
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets._clean_term_row("co", {"term": "RN", "service_line": ""})


def test_clean_location_row_normalises_state_and_granularity():
    row = lead_targets._clean_location_row(
        "co", {"location": "Austin, TX", "state": "tx", "granularity": "City"}
    )
    assert row["state"] == "TX"
    assert row["granularity"] == "city"
    assert row["enabled"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"location": "", "state": "TX", "granularity": "city"},
        {"location": "L", "state": "TEX", "granularity": "city"},
        {"location": "L", "state": "TX", "granularity": "county"},
    ],
)
def test_clean_location_row_rejects_rows_the_db_constraints_would(bad):
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets._clean_location_row("co", bad)


def test_add_terms_validates_the_whole_batch_before_any_write(monkeypatch):
    """One bad row rejects the request — no partial upsert call happens."""
    calls = []
    monkeypatch.setattr(
        "src.storage._get_client", lambda: _FakeUpsertClient(calls)
    )
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets.add_terms(
            "co",
            [
                {"term": "RN", "service_line": "nursing"},
                {"term": "", "service_line": "nursing"},
            ],
        )
    assert calls == []


def test_add_locations_validates_the_whole_batch_before_any_write(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.storage._get_client", lambda: _FakeUpsertClient(calls)
    )
    with pytest.raises(lead_targets.TargetValidationError):
        lead_targets.add_locations(
            "co",
            [
                {"location": "Tampa, FL", "state": "FL", "granularity": "city"},
                {"location": "Bad", "state": "F", "granularity": "city"},
            ],
        )
    assert calls == []


def test_add_terms_makes_one_upsert_call_for_a_valid_batch(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.storage._get_client", lambda: _FakeUpsertClient(calls)
    )
    result = lead_targets.add_terms(
        "co",
        [{"term": "RN", "service_line": "nursing"}, {"term": "LPN", "service_line": "nursing"}],
    )
    assert result == {"requested": 2, "inserted": 2}
    assert len(calls) == 1
    assert calls[0]["table"] == "search_terms"
    assert calls[0]["on_conflict"] == "company_id,term"
    assert calls[0]["ignore_duplicates"] is True


# --------------------------------------------------------------------------
# claim_locations — nulls-first ordering, threshold filter, streak decay.
# --------------------------------------------------------------------------


def test_claim_locations_skips_a_streak_decayed_cell_still_within_threshold(monkeypatch):
    """A location whose cursor is old enough for the BASE threshold but not
    for its own decayed (streak-doubled) threshold should not be claimed."""
    from datetime import datetime, timedelta, timezone
    from src.settings import settings

    monkeypatch.setattr(settings, "lead_indeed_stale_hours", 6)
    monkeypatch.setattr(settings, "lead_zero_streak_cap", 4)

    stale_but_decayed = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    truly_due = None  # never swept — always due

    rows = [
        {
            "id": 1, "location": "Dead City, FL",
            "last_indeed_at": stale_but_decayed, "indeed_zero_streak": 2,  # threshold=24h
        },
        {
            "id": 2, "location": "Fresh City, FL",
            "last_indeed_at": truly_due, "indeed_zero_streak": 0,
        },
    ]
    monkeypatch.setattr("src.storage._get_client", lambda: _FakeSelectClient(rows))

    claimed = lead_targets.claim_locations("co", "indeed", limit=5)
    ids = [r["id"] for r in claimed]
    assert 2 in ids
    assert 1 not in ids


def test_claim_locations_respects_limit(monkeypatch):
    from src.settings import settings
    monkeypatch.setattr(settings, "lead_indeed_stale_hours", 6)
    monkeypatch.setattr(settings, "lead_zero_streak_cap", 4)

    rows = [
        {"id": i, "location": f"City {i}, FL", "last_indeed_at": None, "indeed_zero_streak": 0}
        for i in range(5)
    ]
    monkeypatch.setattr("src.storage._get_client", lambda: _FakeSelectClient(rows))

    claimed = lead_targets.claim_locations("co", "indeed", limit=2)
    assert len(claimed) == 2


def test_claim_locations_rejects_an_unknown_source():
    with pytest.raises(ValueError):
        lead_targets.claim_locations("co", "monster", limit=5)


def test_claim_locations_is_not_starved_by_a_wall_of_decayed_locations(monkeypatch):
    """Regression: many decayed locations sort as STALEST (oldest cursors)
    without being due (their streak-doubled thresholds are huge). A bounded
    over-fetch window (`limit * N`) would fill with them and return a
    false-empty claim while a genuinely due location sits beyond the window.
    claim_locations must fetch all enabled locations so the due one is found."""
    from datetime import datetime, timedelta, timezone
    from src.settings import settings

    monkeypatch.setattr(settings, "lead_indeed_stale_hours", 6)
    monkeypatch.setattr(settings, "lead_zero_streak_cap", 4)

    now = datetime.now(timezone.utc)
    # 10 dead locations: cursors 50h old (stalest, sort first) but streak=4
    # → threshold 6*16=96h → NOT due.
    rows = [
        {
            "id": i, "location": f"Dead {i}, FL",
            "last_indeed_at": (now - timedelta(hours=50)).isoformat(),
            "indeed_zero_streak": 4,
        }
        for i in range(10)
    ]
    # One live location: cursor 8h old (freshest, sorts LAST) with streak=0
    # → threshold 6h → due.
    rows.append({
        "id": 99, "location": "Live City, FL",
        "last_indeed_at": (now - timedelta(hours=8)).isoformat(),
        "indeed_zero_streak": 0,
    })
    monkeypatch.setattr("src.storage._get_client", lambda: _FakeSelectClient(rows))

    claimed = lead_targets.claim_locations("co", "indeed", limit=2)
    assert [r["id"] for r in claimed] == [99]


# --------------------------------------------------------------------------
# Single-tenant resolution (unchanged from the old matrix module).
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
# Config page — catalog (unchanged; still a pure read of lead_config).
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


# --------------------------------------------------------------------------
# Seeding — ensure_targets now diff-seeds unconditionally (Phase 4: the old
# "only seed when zero locations" gate made config expansions invisible to
# already-seeded tenants).
# --------------------------------------------------------------------------


def test_ensure_targets_seeds_even_when_the_tenant_already_has_rows(monkeypatch):
    """The old gate short-circuited on an existence check before ever
    upserting. `_FakeUpsertClient` has no `.select()` at all, so if
    `ensure_targets` regressed back to gating on one, this would fail with
    an AttributeError instead of quietly passing — the fake enforces the
    "no existence check" contract, not just the outcome."""
    calls = []
    monkeypatch.setattr(
        "src.storage._get_client", lambda: _FakeUpsertClient(calls)
    )
    result = lead_targets.ensure_targets("co")
    tables = [c["table"] for c in calls]
    assert "search_terms" in tables
    assert "search_locations" in tables
    assert set(result) == {"terms", "locations"}


def test_seed_search_targets_reports_true_inserted_counts_not_attempted(monkeypatch):
    """`ignore_duplicates` upsert's RETURNING only includes rows Postgres
    actually inserted — a conflicting (already-existing) row never reaches
    RETURNING. `inserted` must reflect that count, not the batch size sent."""

    class _FakePartialInsertClient:
        def __init__(self):
            self._table = None

        def table(self, name):
            self._table = name
            return self

        def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
            # Simulate Postgres: only the first row of the batch was
            # actually new, the rest already existed and were skipped.
            self._returned = rows[:1]
            return self

        def execute(self):
            return type("R", (), {"data": self._returned})()

    monkeypatch.setattr(
        "src.storage._get_client", lambda: _FakePartialInsertClient()
    )
    result = lead_targets.seed_search_targets("co")
    assert result["terms"] == 1
    assert result["locations"] == 1
    # Sanity: the full config batch is bigger than 1 row per table, so this
    # is genuinely exercising "fewer inserted than attempted", not a fluke.
    assert len(lead_config.role_terms()) > 1
    assert len(lead_config.locations()) > 1


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


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


class _FakeUpsertClient:
    """Records every upsert call so validation-before-write tests can assert
    no write happened on a rejected batch."""

    def __init__(self, calls):
        self.calls = calls
        self._table = None

    def table(self, name):
        self._table = name
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.calls.append({
            "table": self._table, "rows": rows,
            "on_conflict": on_conflict, "ignore_duplicates": ignore_duplicates,
        })
        return self

    def execute(self):
        return type("R", (), {"data": None})()


class _FakeSelectClient:
    """Fakes the chained `.table().select().eq().order().order().limit()`
    used by `claim_locations`, returning `rows` regardless of the filters
    applied (the test asserts on the Python-side threshold filtering)."""

    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()
