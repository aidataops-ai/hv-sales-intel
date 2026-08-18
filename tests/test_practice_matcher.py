"""Tests for posting-to-practice matching, in particular the state-aware
bucket key (instant-signals refactor, Phase 4).

The 5-state geography expansion introduced cross-state duplicate city names
(Greenville, NC vs Greenville, SC; Smyrna, GA vs Smyrna, TN; ...). Before this
fix, `city_key` alone bucketed candidates, so a posting could auto-link to a
practice with the same city name in the WRONG state. These tests pin the
regression: state must be part of the bucket key, and a posting with no
recorded state must fall back to city-only matching capped at 'review'.
"""

import pytest

from src import practice_matcher
from src.job_boards import normalise_employer

# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_location_key_distinguishes_same_city_different_state():
    """The whole bug this module fixes, in one assertion: city_key alone
    would collide these two; location_key must not."""
    assert practice_matcher.city_key("Greenville") == practice_matcher.city_key("Greenville")
    assert (
        practice_matcher.location_key("Greenville", "NC")
        != practice_matcher.location_key("Greenville", "SC")
    )


def test_location_key_folds_a_missing_state_to_a_blank_segment():
    assert practice_matcher.location_key("Tampa", None) == "tampa|"
    assert practice_matcher.location_key("Tampa", "") == "tampa|"


def test_location_key_normalises_state_case():
    assert practice_matcher.location_key("Tampa", "fl") == practice_matcher.location_key(
        "Tampa", "FL"
    )


# --------------------------------------------------------------------------
# link_postings — cross-state collision, NULL-state fallback, happy path.
# --------------------------------------------------------------------------


def _posting(id_, employer, city, state):
    return {
        "id": id_,
        "employer_name_norm": normalise_employer(employer),
        "city": city,
        "state": state,
    }


def _practice(id_, name, city, state, service_line="Virtual Dental Assistant"):
    return {"id": id_, "name": name, "city": city, "state": state, "service_line": service_line}


def test_cross_state_same_city_name_links_to_the_same_state_practice_only(monkeypatch):
    """Two practices named identically in two states that share a city name
    (Greenville, NC / Greenville, SC). A state-scoped posting must link to
    ITS state's practice, never the other — regardless of which one a
    city-only lookup would have found first."""
    practices = [
        _practice(1, "Sunrise Dental", "Greenville", "NC"),
        _practice(2, "Sunrise Dental", "Greenville", "SC"),
    ]
    kept = [{"posting_id": 100}]
    postings = [_posting(100, "Sunrise Dental", "Greenville", "NC")]

    client = _FakeClient({
        "company_job_leads": kept,
        "job_postings": postings,
        "practices": practices,
    })
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    stats = practice_matcher.link_postings("co", dry_run=True)
    assert stats["candidates"] == 1
    assert stats["auto"] == 1

    # Re-run for real to inspect the actual write, not just the count.
    stats = practice_matcher.link_postings("co")
    updates = [e for e in client.log if e[0] == "update" and "practice_id" in e[2]]
    assert len(updates) == 1
    assert updates[0][2]["practice_id"] == 1        # the NC practice
    assert updates[0][2]["match_status"] == "auto"


def test_cross_state_collision_the_other_direction_also_stays_in_state(monkeypatch):
    """Same setup, but the posting is in SC this time — must link to the SC
    practice, proving the NC case above isn't just "whichever sorts first"."""
    practices = [
        _practice(1, "Sunrise Dental", "Greenville", "NC"),
        _practice(2, "Sunrise Dental", "Greenville", "SC"),
    ]
    kept = [{"posting_id": 200}]
    postings = [_posting(200, "Sunrise Dental", "Greenville", "SC")]

    client = _FakeClient({
        "company_job_leads": kept,
        "job_postings": postings,
        "practices": practices,
    })
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    practice_matcher.link_postings("co")
    updates = [e for e in client.log if e[0] == "update" and "practice_id" in e[2]]
    assert len(updates) == 1
    assert updates[0][2]["practice_id"] == 2        # the SC practice


def test_a_posting_with_no_state_falls_back_to_city_only_and_is_capped_at_review(monkeypatch):
    """No state on the posting means no state to scope by — the match falls
    back to every practice in the city. That is exactly the ambiguity
    state-scoping exists to remove, so it must never come back 'auto', even
    at a perfect name score."""
    practices = [
        _practice(1, "Sunrise Dental", "Greenville", "NC"),
        _practice(2, "Sunrise Dental", "Greenville", "SC"),
    ]
    kept = [{"posting_id": 300}]
    postings = [_posting(300, "Sunrise Dental", "Greenville", None)]

    client = _FakeClient({
        "company_job_leads": kept,
        "job_postings": postings,
        "practices": practices,
    })
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    stats = practice_matcher.link_postings("co", dry_run=True)
    assert stats["candidates"] == 1
    assert stats["auto"] == 0
    assert stats["review"] == 1


def test_a_state_scoped_high_confidence_match_is_still_auto_with_no_collision(monkeypatch):
    """The fix must not turn every match into 'review' — an unambiguous,
    state-scoped, high-confidence match stays 'auto'."""
    practices = [_practice(1, "Bay Family Dentistry", "Tampa", "FL")]
    kept = [{"posting_id": 400}]
    postings = [_posting(400, "Bay Family Dentistry", "Tampa", "FL")]

    client = _FakeClient({
        "company_job_leads": kept,
        "job_postings": postings,
        "practices": practices,
    })
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    stats = practice_matcher.link_postings("co", dry_run=True)
    assert stats["auto"] == 1
    assert stats["review"] == 0


def test_a_posting_state_with_no_matching_practice_in_that_state_is_unmatched(monkeypatch):
    """A practice exists in the city but only in a DIFFERENT state than the
    posting — state-scoping must not fall back and grab it."""
    practices = [_practice(1, "Sunrise Dental", "Greenville", "SC")]
    kept = [{"posting_id": 500}]
    postings = [_posting(500, "Sunrise Dental", "Greenville", "NC")]

    client = _FakeClient({
        "company_job_leads": kept,
        "job_postings": postings,
        "practices": practices,
    })
    monkeypatch.setattr(practice_matcher, "_client", lambda: client)

    stats = practice_matcher.link_postings("co", dry_run=True)
    assert stats["candidates"] == 1
    assert stats["auto"] == 0
    assert stats["review"] == 0
    assert stats["linked"] == 0


# --------------------------------------------------------------------------
# load_practices_by_city — indexes both ways from one pass.
# --------------------------------------------------------------------------


def test_load_practices_by_city_indexes_both_by_location_and_city(monkeypatch):
    practices = [
        _practice(1, "Sunrise Dental", "Greenville", "NC"),
        _practice(2, "Sunrise Dental", "Greenville", "SC"),
    ]
    client = _FakeClient({"practices": practices})

    by_location, by_city = practice_matcher.load_practices_by_city(client)

    assert len(by_location[practice_matcher.location_key("Greenville", "NC")]) == 1
    assert len(by_location[practice_matcher.location_key("Greenville", "SC")]) == 1
    assert len(by_city[practice_matcher.city_key("Greenville")]) == 2


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeQuery:
    """Fakes the chained Supabase query builder for the calls
    practice_matcher.py actually makes: select/eq/in_/not_.is_/range/update/
    execute. Filtering (`eq`/`in_`/`not_.is_`) is intentionally a no-op — the
    test tables are already shaped to exactly what the query would return,
    matching the style of the FakeQuery in tests/test_lead_store.py. `update`
    calls are recorded on the shared log instead, since the assertions here
    care about what got written and to which id(s)."""

    def __init__(self, table_name, rows, log):
        self.table_name = table_name
        self.rows = rows
        self.log = log
        self._payload = None
        self._filters: dict = {}

    def select(self, *a, **k):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def in_(self, key, values):
        self._filters[key] = list(values)
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._payload is not None:
            payload, filters = dict(self._payload), dict(self._filters)
            self.log.append(("update", self.table_name, payload, filters))
            ids = filters.get("id")
            count = len(ids) if isinstance(ids, list) else 1
            return type("R", (), {"data": [dict(payload) for _ in range(count)]})()
        return type("R", (), {"data": self.rows, "count": len(self.rows or [])})()


class _FakeClient:
    def __init__(self, by_table: dict):
        self.by_table = by_table
        self.log: list = []

    def table(self, name):
        return _FakeQuery(name, list(self.by_table.get(name, [])), self.log)
