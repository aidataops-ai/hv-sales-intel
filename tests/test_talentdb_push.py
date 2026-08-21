"""Per-contact fan-out — N contacts in, N Talent-DB leads out, two markers.

`src.talentdb.import_lead` is stubbed here on purpose: what this module decides
is *how many times* it is called, with which contact, and which markers get
written afterwards. The envelope mapping itself is tests/test_talentdb.py.
"""

from unittest.mock import patch

import pytest

from src import talentdb_push


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _practice(**overrides) -> dict:
    base = {"id": 1024, "name": "Acme Dental", "owner_email": "jane@acme.com",
            "phone": "+13125550100"}
    base.update(overrides)
    return base


def _posting() -> dict:
    return {"id": 5567, "source": "indeed", "title": "Dental Assistant"}


def _lead(**overrides) -> dict:
    base = {"id": 900, "service_line": "Virtual Dental Assistant"}
    base.update(overrides)
    return base


def _contact(cid: int, **overrides) -> dict:
    base = {"id": cid, "first_name": f"Person{cid}", "last_name": "Smith",
            "work_email": f"p{cid}@acme.com", "personal_email": None,
            "phone": None, "title": None, "linkedin_url": None}
    base.update(overrides)
    return base


class _Recorder:
    """Stands in for `talentdb.import_lead` — records every call, replies from
    a queue of (ok, status) so a partial failure is easy to stage."""

    def __init__(self, replies=None):
        self.calls: list[dict | None] = []
        self._replies = list(replies or [])

    async def __call__(self, practice, posting, lead, contact=None):
        self.calls.append(contact)
        ok, status = self._replies.pop(0) if self._replies else (True, "ok")
        return {"ok": ok, "status": status,
                "message": None if ok else "receiver said no",
                "local_entity_id": len(self.calls) if ok else None,
                "http_status": 200}

    @property
    def contact_ids(self) -> list:
        return [(c or {}).get("id") for c in self.calls]


_DEFAULT = object()   # "the fixture", so an explicit None can mean None


async def _push(rows, *, replies=None, exported=None, lead=_DEFAULT,
                practice=_DEFAULT, mark=True):
    """Drive `push_lead_fanout` with the whole data layer stubbed out.

    Returns (result, recorder, marked_leads, marked_contacts) — the last two
    are the two markers, captured as the argument tuples they were called with.
    """
    recorder = _Recorder(replies)
    marked_leads: list[tuple] = []
    marked_contacts: list[tuple] = []

    with patch("src.talentdb_push.talentdb.import_lead", recorder), \
         patch("src.talentdb_push.contacts.list_contacts_for_practice",
               return_value=rows), \
         patch("src.talentdb_push.contacts.list_exported_contact_ids",
               return_value=set(exported or ())), \
         patch("src.talentdb_push.contacts.mark_contact_exported",
               side_effect=lambda lid, cid: marked_contacts.append((lid, cid))), \
         patch("src.talentdb_push.lead_store.mark_lead_exported",
               side_effect=lambda cid, lid: marked_leads.append((cid, lid))):
        result = await talentdb_push.push_lead_fanout(
            _practice() if practice is _DEFAULT else practice,
            _posting(),
            _lead() if lead is _DEFAULT else lead,
            "apex", mark=mark)
    return result, recorder, marked_leads, marked_contacts


# --------------------------------------------------------------------------- #
# eligible_contacts
# --------------------------------------------------------------------------- #

def test_eligible_drops_only_the_unreachable_and_keeps_arrival_order():
    rows = [
        _contact(1),
        _contact(2, work_email=None, personal_email="two@gmail.com"),
        _contact(3, work_email="Not Found", personal_email="  N/A "),
        _contact(4, work_email=None, personal_email=None),
    ]
    assert [c["id"] for c in talentdb_push.eligible_contacts(rows)] == [1, 2]


def test_eligible_of_nothing_is_empty():
    assert talentdb_push.eligible_contacts(None) == []
    assert talentdb_push.eligible_contacts([]) == []


# --------------------------------------------------------------------------- #
# The fan-out
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_three_contacts_become_three_posts_all_marked():
    rows = [_contact(1), _contact(2), _contact(3)]
    result, rec, leads, contacts = await _push(rows)

    assert rec.contact_ids == [1, 2, 3]          # one POST per person, in order
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["sent"] == 3
    assert contacts == [(900, 1), (900, 2), (900, 3)]
    assert leads == [("apex", 900)]               # clean sweep → lead marker set


@pytest.mark.asyncio
async def test_no_contacts_sends_the_single_legacy_lead():
    result, rec, leads, contacts = await _push([])

    assert rec.calls == [None]                    # no contact= → owner_* mapping
    assert result["ok"] is True
    assert result["sent"] == 1
    assert contacts == []                         # nothing per-person to record
    assert leads == [("apex", 900)]


@pytest.mark.asyncio
async def test_contacts_with_no_email_at_all_fall_back_to_the_legacy_lead():
    """Unreachable people must not turn a sendable lead into zero POSTs."""
    rows = [_contact(1, work_email=None, personal_email=None),
            _contact(2, work_email="Not Found", personal_email=None)]
    result, rec, leads, contacts = await _push(rows)

    assert rec.calls == [None]
    assert result["sent"] == 1
    assert leads == [("apex", 900)]


@pytest.mark.asyncio
async def test_legacy_lead_failure_leaves_the_marker_clear():
    result, rec, leads, contacts = await _push([], replies=[(False, "error")])
    assert result["ok"] is False
    assert result["sent"] == 0
    assert leads == []


@pytest.mark.asyncio
async def test_partial_failure_marks_the_successes_but_not_the_lead():
    """The whole reason the contact markers exist: the lead stays in the
    universe, and the retry skips the two people who already landed."""
    rows = [_contact(1), _contact(2), _contact(3)]
    result, rec, leads, contacts = await _push(
        rows, replies=[(True, "ok"), (False, "error"), (True, "ok")])

    assert rec.contact_ids == [1, 2, 3]           # the failure stops nothing
    assert contacts == [(900, 1), (900, 3)]       # only the accepted ones
    assert leads == []                            # NOT a clean sweep
    assert result["ok"] is True                   # something landed
    assert result["status"] == "error"            # first failure surfaces
    assert result["message"] == "receiver said no"
    assert result["sent"] == 2


@pytest.mark.asyncio
async def test_already_exported_contact_is_skipped_without_a_post():
    rows = [_contact(1), _contact(2)]
    result, rec, leads, contacts = await _push(rows, exported={1})

    assert rec.contact_ids == [2]                 # person 1 never re-POSTed
    assert result["ok"] is True
    assert result["sent"] == 1                    # skipped ≠ sent
    assert [r["status"] for r in result["results"]] == ["already_exported", "ok"]
    assert contacts == [(900, 2)]
    # Every eligible contact is now accounted for → the lead is finished.
    assert leads == [("apex", 900)]


@pytest.mark.asyncio
async def test_a_fully_exported_lead_re_entered_posts_nobody_but_stays_ok():
    rows = [_contact(1), _contact(2)]
    result, rec, leads, contacts = await _push(rows, exported={1, 2})

    assert rec.calls == []
    assert result["ok"] is True
    assert result["sent"] == 0
    assert leads == [("apex", 900)]


@pytest.mark.asyncio
async def test_mark_false_writes_neither_marker():
    rows = [_contact(1), _contact(2)]
    result, rec, leads, contacts = await _push(rows, mark=False)

    assert rec.contact_ids == [1, 2]              # still POSTs
    assert result["sent"] == 2
    assert contacts == []
    assert leads == []


@pytest.mark.asyncio
async def test_mark_false_on_the_legacy_path_writes_nothing_either():
    result, rec, leads, contacts = await _push([], mark=False)
    assert rec.calls == [None]
    assert leads == []


@pytest.mark.asyncio
async def test_no_lead_row_fans_out_without_touching_any_marker():
    """A practice with no linked posting has nothing to dedup on — the same
    exemption the lead-level marker already has."""
    rows = [_contact(1), _contact(2)]
    result, rec, leads, contacts = await _push(rows, lead=None)

    assert rec.contact_ids == [1, 2]
    assert result["sent"] == 2
    assert contacts == []
    assert leads == []


@pytest.mark.asyncio
async def test_result_carries_the_first_local_entity_id_and_every_result():
    rows = [_contact(1), _contact(2), _contact(3)]
    result, rec, leads, contacts = await _push(rows)

    assert result["local_entity_id"] == 1          # first non-null of the N
    assert len(result["results"]) == 3
    assert [r["contact_id"] for r in result["results"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_missing_practice_takes_the_legacy_path():
    result, rec, leads, contacts = await _push([], practice=None)
    assert rec.calls == [None]
    assert result["sent"] == 1
