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


# --------------------------- remote flag ------------------------------
#
# JobSpy 1.1.82 keyword-matches "remote" anywhere in the description, so
# Indeed's own "Work Remotely: No" template produces is_remote=True. The
# fixture is the verbatim stored description of posting 1137 (signal 57),
# the measured case study; docs/specs/2026-08-17-remote-flag-hotfix.md.

SIGNAL_57_DESCRIPTION = (
    pathlib.Path(__file__).parent / "fixtures" / "indeed_posting_1137_description.txt"
).read_text()

SIGNAL_57_JOB = {
    "title": "Medical Front Office Receptionist",
    "attributes": [{"label": a} for a in (
        "Full-time", "401(k)", "Dental insurance", "Health insurance",
        "Life insurance", "Paid time off", "Vision insurance",
        "Pulmonology", "Sleep Medicine",
    )],
    "location": {"formatted": {"long": "Jacksonville, FL, US"}},
}


def test_patch_targets_still_exist_in_jobspy():
    """A jobspy upgrade that moves these names would silently un-patch us."""
    import jobspy.indeed
    import jobspy.linkedin

    assert callable(jobspy.indeed.is_job_remote)
    assert callable(jobspy.linkedin.is_job_remote)


def test_upstream_jobspy_still_has_the_bug_and_the_patch_fixes_it():
    """If upstream ever fixes the substring match, this fails and the whole
    patch layer can come out (see the spec's Upstream section)."""
    from jobspy.indeed.util import is_job_remote as upstream

    assert upstream(SIGNAL_57_JOB, SIGNAL_57_DESCRIPTION) is True
    assert job_boards._patched_indeed_is_remote(SIGNAL_57_JOB, SIGNAL_57_DESCRIPTION) is False


def test_explicit_template_beats_even_a_remote_attribute():
    job = dict(SIGNAL_57_JOB, attributes=SIGNAL_57_JOB["attributes"] + [{"label": "Remote"}])
    assert job_boards._patched_indeed_is_remote(job, SIGNAL_57_DESCRIPTION) is False


def test_patched_indeed_keeps_genuinely_remote_postings():
    plain = {"title": "Medical Biller", "attributes": [], "location": {"formatted": {"long": "Miami, FL"}}}
    assert job_boards._patched_indeed_is_remote(
        dict(plain, location={"formatted": {"long": "Remote"}}), "Schedules patients.") is True
    assert job_boards._patched_indeed_is_remote(
        dict(plain, attributes=[{"label": "Remote"}]), "Schedules patients.") is True
    assert job_boards._patched_indeed_is_remote(plain, "This is a fully remote position.") is True
    assert job_boards._patched_indeed_is_remote(plain, "**Work Remotely**\n* Yes") is True
    assert job_boards._patched_indeed_is_remote(
        dict(plain, title="Remote Medical Scheduler"), "Schedules patients.") is True


def test_patched_indeed_rejects_lookalike_remote_mentions():
    plain = {"title": "Medical Biller", "attributes": [], "location": {"formatted": {"long": "Miami, FL"}}}
    assert job_boards._patched_indeed_is_remote(
        plain, "Our practice offers remote patient monitoring to patients.") is False
    assert job_boards._patched_indeed_is_remote(
        plain, "Please note this is not a remote position.") is False
    assert job_boards._patched_indeed_is_remote(
        dict(plain, title="Remote Patient Monitoring Coordinator"), "Runs our RPM program in office.") is False


def test_patched_linkedin_matches_its_signature():
    """LinkedIn passes (title, description, location); description is None
    while `linkedin_fetch_description` stays off."""
    assert job_boards._patched_linkedin_is_remote("Remote Receptionist (PRN)", None, "Waynesboro, TN") is True
    assert job_boards._patched_linkedin_is_remote("Billing Specialist", None, "Clearwater, FL, US") is False
    assert job_boards._patched_linkedin_is_remote("Remote Patient Monitoring Nurse", None, "Tampa, FL") is False


def test_patch_application_is_idempotent():
    import jobspy.indeed
    import jobspy.linkedin

    job_boards._patch_jobspy_remote_flags()
    once = (jobspy.indeed.is_job_remote, jobspy.linkedin.is_job_remote)
    job_boards._patch_jobspy_remote_flags()
    assert (jobspy.indeed.is_job_remote, jobspy.linkedin.is_job_remote) == once
    assert jobspy.indeed.is_job_remote._hvsi_patched
    assert jobspy.linkedin.is_job_remote._hvsi_patched


def test_normalise_row_overrides_a_false_board_flag():
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Medical Front Office Receptionist",
         "company": "Respiratory Critical Care and Sleep Medicine Associates, Inc.",
         "job_url": "https://www.indeed.com/viewjob?jk=06e883132e9a8d46",
         "is_remote": True,
         "description": SIGNAL_57_DESCRIPTION},
    )
    assert row["board_remote_flag"] is False


def test_normalise_row_keeps_a_true_board_flag_without_contradiction():
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Remote Medical Biller", "company": "Bay Family Medicine",
         "job_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef",
         "is_remote": True,
         "description": "This is a fully remote position."},
    )
    assert row["board_remote_flag"] is True


def test_extract_work_arrangement_reads_the_markdown_mangled_template():
    assert job_boards.extract_work_arrangement(SIGNAL_57_DESCRIPTION) == (
        "Work Remotely: No | Work Location: In person"
    )
    assert job_boards.extract_work_arrangement(
        "Great team.\n\nWork Location: Hybrid remote in Miami, FL 33101"
    ) == "Work Location: Hybrid remote in Miami, FL 33101"
    assert job_boards.extract_work_arrangement("No template here.") is None


def test_capped_description_keeps_the_work_arrangement_lines():
    cap = lead_config.options()["description_max_chars"]
    long_desc = ("Busy practice. " * ((cap // 15) + 20)).strip() + "\n\nWork Location: In person"
    row = job_boards.normalise_row(
        "indeed",
        {"title": "Front Desk", "company": "Bay Family Medicine",
         "job_url": "https://www.indeed.com/viewjob?jk=fedcba9876543210",
         "description": long_desc},
    )
    assert len(row["description"]) <= cap
    assert row["description"].endswith("Work Location: In person")
