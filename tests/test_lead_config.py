"""Tests for the `config/leads/` reader.

The last test is the important one: it guards the seam. `lead_config` is the
only module allowed to know the config file layout, so downstream code can be
retargeted at `company_search_targets` without a grep-and-pray.
"""

import pathlib
import re

import pytest

from src import lead_config


def test_role_terms_all_carry_a_service_line():
    terms = lead_config.role_terms()
    assert terms, "roles.json should define at least one term"
    for entry in terms:
        assert entry["term"].strip()
        assert entry["service_line"].strip()


def test_role_terms_are_job_titles_not_product_names():
    """ADR-03: terms must be what practices post, not what we sell.

    Searching a service name returns near-zero results, or competitors
    advertising the same service.
    """
    for entry in lead_config.role_terms():
        assert not entry["term"].lower().startswith("virtual "), entry["term"]


def test_service_lines_are_derived_from_the_terms():
    lines = lead_config.service_lines()
    assert lines
    assert set(lines) == {e["service_line"] for e in lead_config.role_terms()}


def test_locations_include_both_granularities():
    granularities = {loc["granularity"] for loc in lead_config.locations()}
    assert granularities == {"state", "city"}


def test_locations_carry_a_two_letter_state():
    for loc in lead_config.locations():
        assert re.fullmatch(r"[A-Z]{2}", loc["state"]), loc


def test_search_params_have_every_knob():
    params = lead_config.search_params()
    assert params["hours_old"] > 0
    assert params["results_wanted"] > 0
    assert params["distance_miles"] > 0


def test_negative_pattern_drops_the_known_noise():
    pattern = lead_config.negative_pattern()
    assert pattern is not None
    # Employers the 2026-08-04 PoC actually surfaced for healthcare terms.
    for noise in (
        "Front Desk Receptionist Alliance Animal Health",
        "Receptionist Ethos Veterinary Health",
        "Dental Receptionist Animal Hospital of Dunedin",
        "Budtender Trulieve",
    ):
        assert pattern.search(noise), noise


def test_negative_pattern_keeps_real_targets():
    pattern = lead_config.negative_pattern()
    for keeper in (
        "Dental Receptionist Blanding Dental Associates",
        "Medical Receptionist Palm Valley Family Medicine",
        "Prior Authorization Specialist Coastal Orthopedics",
    ):
        assert not pattern.search(keeper), keeper


def test_enabled_sources_are_known_boards_heaviest_first():
    sources = lead_config.enabled_sources()
    assert sources
    assert set(sources) <= set(lead_config.KNOWN_SOURCES)
    weights = [lead_config.source_weight(s) for s in sources]
    assert weights == sorted(weights, reverse=True)


def test_options_resolve_the_confidential_posting_question():
    """Design doc §11.3 — the decision must be explicit, not implied."""
    opts = lead_config.options()
    assert isinstance(opts["include_confidential"], bool)
    assert opts["description_max_chars"] > opts["qualifier_excerpt_chars"]


def test_validate_reports_the_full_matrix():
    summary = lead_config.validate()
    assert summary["targets"] == summary["terms"] * summary["locations"]
    assert summary["sources"] >= 1


def test_unknown_source_is_rejected(tmp_path, monkeypatch):
    """A typo'd board name is a bug, not a feature request — job_boards.py
    needs per-source external_id extraction to exist first."""
    (tmp_path / "filters.json").write_text(
        '{"negative_patterns": [], "sources": {"monster": {"enabled": true}}}'
    )
    monkeypatch.setattr(lead_config, "CONFIG_DIR", str(tmp_path))
    lead_config.reload()
    try:
        with pytest.raises(lead_config.LeadConfigError, match="unknown source"):
            lead_config.enabled_sources()
    finally:
        lead_config.reload()


def test_lead_config_is_the_only_reader_of_the_config_files():
    """The seam. If another module starts opening `config/leads/`, the
    delivery plan's Phase 2 verification has failed and per-tenant target
    divergence quietly stops working."""
    src = pathlib.Path(__file__).resolve().parent.parent / "src"
    api = pathlib.Path(__file__).resolve().parent.parent / "api"

    offenders = []
    for path in list(src.rglob("*.py")) + list(api.rglob("*.py")):
        if path.name == "lead_config.py":
            continue
        text = path.read_text()
        if "config/leads" in text or 'CONFIG_DIR' in text:
            offenders.append(path.name)
    assert offenders == [], f"modules reading config/leads/ directly: {offenders}"
