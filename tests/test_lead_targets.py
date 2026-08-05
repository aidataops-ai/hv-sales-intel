"""Tests for the search-target matrix and its rotation."""

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
