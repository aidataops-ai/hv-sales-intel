"""Tests for the deterministic posting -> track resolver."""
from src import track_resolver as tr
from src.track_resolver import CHIRO, DENTAL, ASSISTED_LIVING, HOME_HEALTH


def _p(title="", employer="", description="", hint=None):
    return {"title": title, "employer_name": employer,
            "description": description, "service_line_hint": hint}


# --- title / employer signal (broad patterns) ---------------------------------

def test_specialty_in_title():
    assert tr.from_posting(_p(title="Chiropractic Assistant/Receptionist")) == CHIRO
    assert tr.from_posting(_p(title="Dental Front Office Coordinator")) == DENTAL


def test_specialty_in_employer_generic_title():
    # The classic hard case: title says nothing, employer names the specialty.
    assert tr.from_posting(_p(title="Front Office Clerk", employer="Downtown Chiropractic")) == CHIRO
    assert tr.from_posting(_p(title="Front Desk Manager", employer="Sunrise Senior Living")) == ASSISTED_LIVING


def test_home_health_title():
    assert tr.from_posting(_p(title="Home Health Intake Coordinator")) == HOME_HEALTH


def test_oral_and_maxillofacial_is_dental():
    assert tr.from_posting(_p(title="Practice Coordinator, Oral & Maxillofacial Surgery")) == DENTAL


# --- precision guards: the description is NOT read (it produced ~96% FPs) ------

def test_specialty_named_only_in_description_does_not_fire():
    # Validated 2026-08-19: "chiropractic clinic" in the body of a generically-named
    # posting is not enough — the description is incidental and is not read.
    p = _p(title="Front Desk Receptionist", employer="Peak Wellness",
           description="Our busy chiropractic clinic is seeking a front desk person.",
           hint="Virtual Medical Assistant")
    assert tr.from_posting(p) is None
    assert tr.track_for(p) == "Virtual Medical Assistant"  # no model here -> the hint


def test_incidental_dental_prose_does_not_fire():
    # "clinics or dental practices" + a "Dental insurance" benefit at a dermatology
    # office — the exact false positives that killed description scanning.
    p = _p(title="Medical Receptionist", employer="Lakeside Dermatology",
           description="Support work within clinics or dental practices. Benefits: Dental insurance.")
    assert tr.from_posting(p) is None


def test_ambiguous_multi_specialty_declines():
    # Two specialties named in the title/employer -> decline rather than guess.
    p = _p(title="Front Desk", employer="Dental & Chiropractic Wellness Center")
    assert tr.from_posting(p) is None


# --- track_for() precedence: posting specialty -> model -> hint ---------------

def test_track_for_keeps_the_model_track_for_generic_postings():
    # Generic posting: the model's judgment (MA vs Scheduler) is kept, not the hint.
    p = _p(title="Front Desk Associate", employer="Family Care",
           hint="Virtual Medical Scheduler")
    assert tr.from_posting(p) is None
    assert tr.track_for(p, model_track="Virtual Medical Assistant") == "Virtual Medical Assistant"


def test_track_for_falls_back_to_hint_when_no_model():
    p = _p(title="Front Desk Associate", employer="Family Care",
           hint="Virtual Medical Scheduler")
    assert tr.track_for(p) == "Virtual Medical Scheduler"


def test_track_for_specialty_beats_model_and_hint():
    # Posting names chiro but the model said dental and a dental keyword found it —
    # the deterministic specialty wins over both.
    p = _p(title="Front Desk", employer="Land Chiropractic", hint=DENTAL)
    assert tr.track_for(p, model_track=DENTAL) == CHIRO


def test_track_for_none_when_no_signal_model_or_hint():
    assert tr.track_for(_p(title="Front Desk Associate")) is None
