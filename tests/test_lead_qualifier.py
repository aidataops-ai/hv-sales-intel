"""Tests for the qualifier's prompt, parsing and validation.

No network. The model call itself is stubbed; what's covered here is
everything around it that has already broken once during evaluation:
the two-test structure of the prompt, the parameter-rejection ordering,
and the enum validation that keeps one bad field from failing a batch.
"""

import json

import pytest

from src import lead_config, lead_qualifier, lead_store


def _posting(**overrides) -> dict:
    return {
        "id": 1,
        "title": "Dental Receptionist",
        "employer_name": "Blanding Dental Associates",
        "location_raw": "Orange Park, FL, US",
        "board_remote_flag": False,
        "salary_min": 18.0,
        "salary_max": 23.0,
        "salary_interval": "hourly",
        "service_line_hint": "Virtual Dental Assistant",
        "description": "Answer phones, schedule patients, verify insurance.",
        **overrides,
    }


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_prompt_asks_both_tests():
    """ADR / design §7: employer AND role, both must pass. Dropping TEST 2
    makes the qualifier keep clinical roles at perfect-fit practices —
    employer right, lead unusable."""
    prompt = lead_qualifier.build_prompt([_posting()])
    assert "TEST 1 — IS THE EMPLOYER RIGHT?" in prompt
    assert "TEST 2 — IS THE ROLE PLACEABLE REMOTELY?" in prompt
    assert "A posting must pass BOTH tests" in prompt


def test_prompt_forbids_defaulting_the_work_mode():
    """ADR-08: a prompt that says "guess, defaulting to onsite" returns onsite
    for essentially everything, including postings flagged remote."""
    prompt = lead_qualifier.build_prompt([_posting()])
    assert "from EVIDENCE, not assumption" in prompt
    assert "Only answer \"onsite\" when there is no remote or hybrid signal" in prompt


def test_prompt_names_the_clinical_rejections_explicitly():
    prompt = lead_qualifier.build_prompt([_posting()])
    for role in ("Registered Nurse", "Phlebotomist", "Dental Hygienist",
                 "home health aide"):
        assert role in prompt


def test_prompt_constrains_tracks_to_the_configured_service_lines():
    prompt = lead_qualifier.build_prompt([_posting()])
    for line in lead_config.service_lines():
        assert line in prompt


def test_prompt_carries_the_evidence_the_work_mode_call_needs():
    prompt = lead_qualifier.build_prompt([_posting()])
    assert "remote_flag=false" in prompt
    assert 'location="Orange Park, FL, US"' in prompt
    assert "$18-23 hourly" in prompt


def test_prompt_says_not_stated_when_there_is_no_salary():
    prompt = lead_qualifier.build_prompt([_posting(salary_min=None, salary_max=None)])
    assert 'salary="not stated"' in prompt


def test_prompt_numbers_every_posting_by_its_row_id():
    prompt = lead_qualifier.build_prompt([_posting(id=7), _posting(id=9)])
    assert "1. id=7" in prompt
    assert "2. id=9" in prompt


def test_prompt_excerpt_respects_the_configured_cap():
    long_description = "x" * 5000
    prompt = lead_qualifier.build_prompt([_posting(description=long_description)])
    cap = lead_config.options()["qualifier_excerpt_chars"]
    assert "x" * cap in prompt
    assert "x" * (cap + 1) not in prompt


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_payload_reads_the_documented_shape():
    rows = lead_qualifier.parse_payload('{"results": [{"external_id": "1"}]}')
    assert rows == [{"external_id": "1"}]


def test_parse_payload_survives_a_fenced_block():
    """JSON mode is requested, but losing a whole batch to a stray ``` is not
    worth the purity."""
    rows = lead_qualifier.parse_payload('```json\n{"results": [{"external_id": "1"}]}\n```')
    assert rows == [{"external_id": "1"}]


def test_parse_payload_accepts_a_bare_array():
    assert lead_qualifier.parse_payload('[{"external_id": "1"}]') == [{"external_id": "1"}]


def test_parse_payload_returns_empty_on_garbage():
    assert lead_qualifier.parse_payload("not json at all") == []
    assert lead_qualifier.parse_payload(None) == []


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_a_clean_keep_maps_onto_the_lead_columns():
    verdict = lead_qualifier.parse_verdict({
        "external_id": "1", "decision": "keep", "confidence": 0.92,
        "service_line": "Virtual Dental Assistant", "work_mode": "onsite",
        "role_remotable": True, "practice_type": "independent",
        "provider_count": 3, "reason": "Independent two-dentist practice.",
        "draft": "Hi there — saw your front desk opening…",
    }, _posting(), model="gpt-5.6-terra")

    assert verdict["decision"] == "keep"
    assert verdict["confidence_band"] == "ready"
    assert verdict["band_rank"] == lead_store.BAND_RANK["ready"]
    # The prototype's names are mapped, not renamed in the measured prompt.
    assert verdict["employer_type"] == "independent"
    assert verdict["role_suitable"] is True
    assert verdict["posting_id"] == 1


def test_an_unknown_enum_degrades_one_field_not_the_row():
    """Design §7: a malformed field degrades one row rather than failing a
    batch — and here, not even the whole row."""
    verdict = lead_qualifier.parse_verdict({
        "decision": "keep", "confidence": 0.9,
        "work_mode": "wherever", "practice_type": "boutique",
    }, _posting(), model="m")
    assert verdict is not None
    assert verdict["work_mode"] is None
    assert verdict["employer_type"] is None


def test_an_unparseable_decision_drops_the_row():
    """A verdict with no decision is not a discard — it is no verdict, and the
    posting must stay unqualified so the next run retries it."""
    assert lead_qualifier.parse_verdict({"confidence": 0.9}, _posting(), model="m") is None
    assert lead_qualifier.parse_verdict({"decision": "maybe"}, _posting(), model="m") is None
    assert lead_qualifier.parse_verdict("not a dict", _posting(), model="m") is None


def test_confidence_is_clamped_to_the_unit_interval():
    for value, expected in ((1.7, 1.0), (-0.5, 0.0), ("0.5", 0.5), ("high", None)):
        verdict = lead_qualifier.parse_verdict(
            {"decision": "keep", "confidence": value}, _posting(), model="m")
        assert verdict["confidence"] == expected


def test_an_off_menu_service_line_is_rejected_and_falls_back_to_the_track():
    """A kept lead with no track is invisible to the track filter."""
    verdict = lead_qualifier.parse_verdict({
        "decision": "keep", "confidence": 0.9,
        "service_line": "Virtual Astronaut",
    }, _posting(), model="m")
    assert verdict["service_line"] == "Virtual Dental Assistant"


def test_a_discard_carries_no_track():
    verdict = lead_qualifier.parse_verdict(
        {"decision": "discard", "confidence": 0.95}, _posting(), model="m")
    assert verdict["service_line"] is None


def test_track_comes_from_the_posting_not_the_model():
    """The model's `service_line` is ignored; the deterministic resolver decides.
    Here the posting is unmistakably chiropractic, so the model's "Dental" loses."""
    verdict = lead_qualifier.parse_verdict(
        {"decision": "keep", "confidence": 0.9, "service_line": "Virtual Dental Assistant"},
        _posting(title="Front Office Coordinator", employer_name="Downtown Chiropractic",
                 service_line_hint="Virtual Dental Assistant"),
        model="m")
    assert verdict["service_line"] == "Virtual Chiropractic Assistant"


def test_a_generic_posting_keeps_the_model_track_over_the_hint():
    """No specialty in the posting text -> keep the model's own track (the generic
    front-office judgment). The hint is only used if the model's track is invalid."""
    verdict = lead_qualifier.parse_verdict(
        {"decision": "keep", "confidence": 0.9, "service_line": "Virtual Medical Assistant"},
        _posting(title="Front Desk Associate", employer_name="Family Care Clinic",
                 service_line_hint="Virtual Medical Scheduler"),
        model="m")
    assert verdict["service_line"] == "Virtual Medical Assistant"   # model kept, not the hint


def test_an_invalid_model_track_drops_to_the_hint():
    verdict = lead_qualifier.parse_verdict(
        {"decision": "keep", "confidence": 0.9, "service_line": "Virtual Astronaut"},
        _posting(title="Front Desk Associate", employer_name="Family Care Clinic",
                 service_line_hint="Virtual Medical Scheduler"),
        model="m")
    assert verdict["service_line"] == "Virtual Medical Scheduler"   # invalid model -> hint


def test_a_keep_at_a_hospital_system_lands_in_the_review_queue():
    """Self-contradictory against TEST 1. The model's decision is not
    overridden — overriding would hide the signal that the prompt needs
    tuning — but a human sees it before an SDR calls."""
    verdict = lead_qualifier.parse_verdict({
        "decision": "keep", "confidence": 0.97, "practice_type": "system",
    }, _posting(), model="m")
    assert verdict["decision"] == "keep"
    assert verdict["confidence_band"] == "decide"


def test_a_consistent_keep_keeps_its_band():
    verdict = lead_qualifier.parse_verdict({
        "decision": "keep", "confidence": 0.97, "practice_type": "independent",
    }, _posting(), model="m")
    assert verdict["confidence_band"] == "ready"


def test_verdicts_only_contain_verdict_columns():
    """What write_verdicts filters for; producing them clean means the filter
    is defence in depth rather than the only line."""
    verdict = lead_qualifier.parse_verdict(
        {"decision": "keep", "confidence": 0.9}, _posting(), model="m")
    extra = set(verdict) - lead_store.VERDICT_COLUMNS - {"posting_id"}
    assert extra == set()


# --------------------------------------------------------------------------
# The API-parameter trap
# --------------------------------------------------------------------------


def test_a_temperature_rejection_is_read_as_a_parameter_error():
    """The measured trap: this model accepts only the default temperature, and
    its rejection message contains the word "model" — which a naive
    model-not-found branch reads as fatal and aborts a valid run."""
    message = (
        "Error code: 400 - Unsupported value: 'temperature' does not support "
        "0 with this model. Only the default (1) value is supported."
    )
    assert lead_qualifier._is_parameter_rejection(message) == "temperature"


def test_a_reasoning_effort_rejection_is_recoverable():
    assert lead_qualifier._is_parameter_rejection(
        "Unsupported parameter: 'reasoning_effort' is not supported"
    ) == "reasoning_effort"


def test_a_genuine_model_error_is_not_a_parameter_rejection():
    assert lead_qualifier._is_parameter_rejection(
        "The model `gpt-9-imaginary` does not exist"
    ) is None


def test_temperature_is_never_sent(monkeypatch):
    """Sending it at all is what triggers the rejection — the model only
    accepts its default."""
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {
                "choices": [type("Choice", (), {
                    "message": type("Msg", (), {"content": json.dumps({"results": []})})()
                })()],
                "model": "gpt-5.6-terra",
                "usage": None,
            })()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(lead_qualifier, "_client", lambda: FakeClient())
    monkeypatch.setattr("src.usage.record_openai", lambda **kwargs: None)

    lead_qualifier.qualify_batch([_posting()], company_id="c1")
    assert "temperature" not in captured
    assert captured["response_format"] == {"type": "json_object"}


def test_qualification_is_metered_against_the_tenant(monkeypatch):
    """ADR-10 — an unmetered background stage would put real spend outside
    the billing model."""
    recorded = {}

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Response", (), {
                "choices": [type("Choice", (), {
                    "message": type("Msg", (), {"content": '{"results": []}'})()
                })()],
                "model": "gpt-5.6-terra",
                "usage": None,
            })()

    monkeypatch.setattr(
        lead_qualifier, "_client",
        lambda: type("C", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})(),
    )
    monkeypatch.setattr("src.usage.record_openai", lambda **kwargs: recorded.update(kwargs))

    lead_qualifier.qualify_batch([_posting()], company_id="c1", user_id="u1")
    assert recorded["kind"] == "openai_qualify"
    assert recorded["company_id"] == "c1"
    assert recorded["user_id"] == "u1"


def test_openai_qualify_deducts_credits():
    from src.credits import _kind_to_credits
    mapped = _kind_to_credits(
        "openai_qualify", model="gpt-4.1", in_tok=3440, out_tok=3500,
        cached_tok=0, calls=1,
    )
    assert mapped is not None
    action, credits = mapped
    assert action == "qualify"
    assert credits > 0


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------


def test_batches_match_the_configured_size():
    postings = [_posting(id=i) for i in range(45)]
    batches = list(lead_qualifier.batched(postings, size=20))
    assert [len(b) for b in batches] == [20, 20, 5]


def test_a_zero_batch_size_cannot_loop_forever():
    assert len(list(lead_qualifier.batched([_posting()], size=0))) == 1


@pytest.mark.parametrize("model_field", ["external_id"])
def test_verdicts_are_matched_back_by_the_posting_id(monkeypatch, model_field):
    """The prompt sends the database row id as `external_id`; a verdict for an
    id we didn't send must be dropped, not written against the wrong lead."""
    payload = json.dumps({"results": [
        {model_field: "1", "decision": "keep", "confidence": 0.9},
        {model_field: "999", "decision": "keep", "confidence": 0.9},
    ]})

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Response", (), {
                "choices": [type("Choice", (), {
                    "message": type("Msg", (), {"content": payload})()
                })()],
                "model": "m", "usage": None,
            })()

    monkeypatch.setattr(
        lead_qualifier, "_client",
        lambda: type("C", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})(),
    )
    monkeypatch.setattr("src.usage.record_openai", lambda **kwargs: None)

    verdicts, stats = lead_qualifier.qualify_batch([_posting(id=1)])
    assert [v["posting_id"] for v in verdicts] == [1]
    assert stats["missing"] == 0


def test_prompt_demotes_the_remote_flag_to_a_hint():
    """2026-08-17 hotfix: remote_flag ratified JobSpy keyword false positives
    ("Work Remotely: No" contains "remote") until the prompt stopped treating
    it as proof. The pinned ADR-08 strings above must survive alongside this —
    demoting the flag must not resurrect the default-onsite regression."""
    prompt = lead_qualifier.build_prompt([_posting()])
    assert "known false positives" in prompt
    assert "ALWAYS overrides" in prompt


def test_snippet_carries_the_work_arrangement_template():
    """The template sits past the head excerpt; without the appended line the
    model physically cannot see the evidence the work_mode call needs."""
    description = ("Busy practice front desk. " * 20) + "\n* **Work Remotely**\n* No\n\nWork Location: In person"
    prompt = lead_qualifier.build_prompt([_posting(description=description)])
    assert "[Work Remotely: No | Work Location: In person]" in prompt
