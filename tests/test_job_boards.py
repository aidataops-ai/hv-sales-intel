"""Tests for board normalisation, run against real recorded board rows.

No network. `tests/fixtures/jobspy_rows.json` holds 51 records captured from
the 2026-08-04 collection runs in the shape JobSpy hands back; see the fixture
README for the pandas quirks it deliberately preserves.
"""

import json
import pathlib

from src import job_boards, lead_config

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "jobspy_rows.json"


def _load_rows() -> list[dict]:
    """JSON has no NaN literal — put pandas' missing-value marker back."""
    raw = json.loads(FIXTURE.read_text())
    for row in raw:
        for key, value in row.items():
            if value == "__NAN__":
                row[key] = float("nan")
    return raw


def _normalised() -> list[dict]:
    out = []
    for row in _load_rows():
        result = job_boards.normalise_row(row["_source"], row)
        if result is not None:
            out.append(result)
    return out


# --------------------------- identity ---------------------------------


def test_indeed_external_id_is_the_jk_param():
    ext = job_boards.external_id(
        "indeed", "https://www.indeed.com/viewjob?jk=a774403a52eeb9b6"
    )
    assert ext == "a774403a52eeb9b6"


def test_linkedin_external_id_is_the_trailing_numeric_id():
    ext = job_boards.external_id(
        "linkedin", "https://www.linkedin.com/jobs/view/dental-receptionist-at-x-4287654321"
    )
    assert ext == "4287654321"


def test_external_id_falls_back_to_the_url():
    """A row is never dropped for want of an id — a URL still dedupes."""
    assert job_boards.external_id("indeed", "https://example.com/j/1") == "https://example.com/j/1"
    assert job_boards.external_id("indeed", "") is None


def test_the_two_sources_live_in_different_id_namespaces():
    """Why the unique key is (source, external_id) and not external_id."""
    rows = _normalised()
    indeed = {r["external_id"] for r in rows if r["source"] == "indeed"}
    linkedin = {r["external_id"] for r in rows if r["source"] == "linkedin"}
    assert indeed and linkedin
    assert all(len(i) == 16 for i in indeed)
    assert all(i.isdigit() for i in linkedin)


def test_every_row_from_the_fixture_yields_a_usable_id():
    for row in _normalised():
        assert row["external_id"]


# --------------------------- pandas quirks ----------------------------


def test_missing_salary_becomes_none_not_nan():
    """`float('nan')` is truthy and `nan != nan` — a naive check leaks it
    into the database as a numeric that never compares equal to itself."""
    for row in _normalised():
        for field in ("salary_min", "salary_max"):
            value = row[field]
            assert value is None or value == value


def test_confidential_postings_keep_a_null_employer_not_the_string_nan():
    """`str(float('nan'))` is `'nan'` — an employer literally named "nan"
    would pass every emptiness check downstream."""
    employers = {r["employer_name"] for r in _normalised()}
    assert None in employers, "fixture should contain confidential postings"
    assert "nan" not in {(e or "").lower() for e in employers}


def test_salary_interval_nan_is_dropped():
    for row in _normalised():
        assert (row["salary_interval"] or "").lower() != "nan"


# --------------------------- prefilter --------------------------------


def test_prefilter_drops_the_veterinary_noise():
    assert job_boards.is_negative("Front Desk Receptionist", "Alliance Animal Health")
    assert job_boards.is_negative("Veterinary Receptionist", "Coastal Pet Care")


def test_prefilter_keeps_a_genuine_independent_practice():
    assert not job_boards.is_negative("Dental Receptionist", "Blanding Dental Associates")


def test_prefiltered_rows_never_reach_the_output():
    pattern = lead_config.negative_pattern()
    for row in _normalised():
        blob = f"{row['title']} {row['employer_name'] or ''}"
        assert not pattern.search(blob), blob


def test_normalise_row_returns_none_for_a_dropped_row():
    dropped = job_boards.normalise_row(
        "indeed",
        {"title": "Veterinary Assistant", "company": "Ethos Veterinary Health",
         "job_url": "https://www.indeed.com/viewjob?jk=deadbeefdeadbeef"},
    )
    assert dropped is None


def test_confidential_postings_are_suppressed_when_config_says_so(monkeypatch):
    """Design doc §11.3 is a config decision, not a hard-coded one."""
    suppressed = {**lead_config.options(), "include_confidential": False}
    monkeypatch.setattr(lead_config, "options", lambda: suppressed)
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Medical Receptionist", "company": float("nan"),
         "job_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef"},
    )
    assert row is None


# --------------------------- shape ------------------------------------


def test_location_splits_into_city_and_state():
    assert job_boards.split_location("Orange Park, FL, US") == ("Orange Park", "FL")
    assert job_boards.split_location("Miami, FL") == ("Miami", "FL")
    assert job_boards.split_location("") == (None, None)


def test_target_state_backfills_an_unparseable_location():
    """'Florida, USA' and 'Remote' carry no 2-letter code; the city query that
    surfaced the row does."""
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Medical Receptionist", "company": "Bay Family Medicine",
         "location": "Florida, USA",
         "job_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef"},
        target={"state": "FL", "term": "medical receptionist",
                "location": "Florida, USA", "service_line": "Virtual Medical Assistant"},
    )
    assert row["state"] == "FL"
    assert row["search_term"] == "medical receptionist"
    assert row["service_line_hint"] == "Virtual Medical Assistant"


def test_missing_posting_date_stays_null():
    """A fabricated 'today' would make every stale lead look fresh — the
    posted-date column is how an operator judges staleness."""
    row = job_boards.normalise_row(
        "linkedin",
        {"title": "Dental Receptionist", "company": "Smile Studio",
         "date_posted": float("nan"),
         "job_url": "https://www.linkedin.com/jobs/view/x-4287654321"},
    )
    assert row["posted_at"] is None


def test_posting_date_is_normalised_to_an_iso_timestamp():
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Medical Receptionist", "company": "Bay Family Medicine",
         "date_posted": "2026-08-03",
         "job_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef"},
    )
    assert row["posted_at"].startswith("2026-08-03T00:00:00")


def test_employer_norm_folds_legal_suffixes_and_punctuation():
    assert job_boards.normalise_employer("Blanding Dental Associates, LLC") == "blanding dental associates"
    assert job_boards.normalise_employer("Palm Valley Family Dentistry P.A.") == "palm valley family dentistry"
    assert job_boards.normalise_employer(None) is None


def test_description_is_capped_at_the_configured_length():
    cap = lead_config.options()["description_max_chars"]
    for row in _normalised():
        if row["description"]:
            assert len(row["description"]) <= cap


def test_rows_match_the_job_postings_columns():
    expected = {
        "source", "external_id", "url", "title", "employer_name",
        "employer_name_norm", "location_raw", "city", "state", "posted_at",
        "salary_min", "salary_max", "salary_interval", "board_remote_flag",
        "description", "search_term", "search_location", "service_line_hint",
    }
    for row in _normalised():
        assert set(row) == expected
