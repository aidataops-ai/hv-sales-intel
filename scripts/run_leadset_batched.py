"""Batched enrichment runner: walk a lead set through the SAME per-lead pipeline
as `scripts/run_leadset_pipeline.py`, but as a two-stage producer/consumer so the
cheap Places call is never blocked behind the slow analyzer/playbook generation.

WHY THIS EXISTS
---------------
The serial runner (`run_leadset_pipeline.py`) does, for each lead in order:
Places → analyze → playbook → email → Clay, then starts the next lead. The AI
generation (analyze + 2 completions, 60s timeouts each) is the slow step, so the
next lead's ~4¢ Places call sits idle behind it. Over a set this serialises the
whole run behind OpenAI latency.

This runner splits the pipeline into two stages that run at the same time:

    Stage A — Places producer (ONE fast serial track)
        upsert posting → ONE Places Text Search → link posting↔practice
        → put the resolved practice on a bounded queue and move on.
        Races ahead to keep ~`--prefetch` leads buffered; never waits on AI.

    Stage B — enrichment consumers (a pool of `--concurrency` workers)
        pull a practice off the queue and run the slow half:
        analyze_practice → (generate_script ∥ generate_email_draft) → Clay.

The queue's `maxsize` (= --prefetch) is the backpressure: Places stays at most
`prefetch` leads ahead of the consumers ("fetch 10-20, enrich 5 at a time").

Every step calls the exact same module the serial runner / app calls, with the
same writes and the same usage-ledger accounting — this is a *scheduling* change,
not a behaviour change. Enrichment only: qualify is skipped (leads are already
`keep`) and the Talent-DB webhook is NOT sent (posting is a separate later step;
`talentdb_exported_at` is left NULL).

SEEING WHAT'S HAPPENING (logging)
---------------------------------
Every event prints a timestamped, flushed line AND is appended to a live log file
you can follow in another terminal:

    tail -f docs/runbooks/leadset-batched-<stamp>.live.log

Per lead you get: producer admit (spend / in-flight / queue depth), a worker
"picked up" line, then a completion line with per-stage timings
(analyze · gen · clay · total) and the tier/vertical. A heartbeat every
`--heartbeat` seconds prints running done/total, ok/fail, in-flight, queue depth,
live spend, and elapsed — so a long AI call never looks like a hang. `--verbose`
also streams the underlying `analyzer`/`scriptgen` module logs (surfaces OpenAI
errors instead of a silent mock fallback). The end-of-run JSON summary is written
to `docs/runbooks/leadset-batched-<stamp>.log.json`.

SPEND CAP (preserves the 2026-08-07 uncapped-Places incident control)
---------------------------------------------------------------------
Places is the expensive, historically-uncapped resource. Here it has exactly ONE
caller — the serial producer — which reads real spend back from `usage_events`
and checks the ceiling BEFORE every Places call, so it can never cross
`--max-places-calls`. The USD ceiling additionally reserves headroom for leads
already admitted but not yet finished (`in_flight × per-lead estimate`), because
the consumers' OpenAI spend lands in the ledger slightly after the fact. If the
next lead would breach `--max-usd` or `--max-places-calls`, the producer stops
admitting cleanly, the consumers drain, and the log is written — the ceiling is
never crossed. Defaults: $10 / 120 Places calls (a full 100-lead set ≈ $6).

Clay is a prepaid subscription (data credits), never metered cash, so the USD cap
does NOT bound it — use `--max-leads` / `--skip-enrich` to bound Clay volume.

Safety model mirrors the serial runner: DRY RUN by default; a live run needs
`--yes`; one lead's failure is logged and never kills the batch; re-running
re-spends (no skip-if-processed). The cap bounds a SINGLE run.

Usage:
    # preview — no external calls, shows plan + estimate + caps
    .venv/bin/python -m scripts.run_leadset_batched --dry-run

    # canary — a few real leads, tight cap
    .venv/bin/python -m scripts.run_leadset_batched --max-leads 3 --max-usd 1 --yes

    # full run — concurrency 5, prefetch 15, default caps, Clay on
    .venv/bin/python -m scripts.run_leadset_batched --yes

Flags: --leadset, --company-id, --concurrency (5), --prefetch (15), --max-usd (10),
--max-places-calls (120), --max-leads, --places-delay (0.3s between Places calls),
--heartbeat (15s), --verbose, --skip-enrich, --dry-run/--yes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from src import lead_store
from src.analyzer import analyze_practice
from src.clay import trigger_enrichment
from src.job_boards import normalise_employer
from src.models import Practice
from src.practice_matcher import AUTO_SCORE, score
from src.scriptgen import generate_script
from src.email_gen import generate_email_draft
from src.settings import settings
from src.storage import (
    _get_client,
    _practice_id_by_place,
    get_practice,
    update_practice_analysis,
    update_practice_fields,
    upsert_practices,
)

# Reuse the proven per-lead helpers + cap accounting so this path stays
# byte-for-byte consistent with the serial runner it parallelises.
from scripts.posting_to_talentdb import _one_places_call, _resolve_posting_row, _now
from scripts.run_leadset_pipeline import (
    EST_LEAD_CENTS,
    PLACES_CALL_CENTS,
    CLAY_DATA_CREDITS_PER_LEAD,
    _sanitize,
    _spent_since,
)

# Sentinel that tells a consumer the queue is exhausted and it should exit.
_DONE = object()


def _emit(state: dict, msg: str) -> None:
    """Print a timestamped line to stdout (flushed) AND the live log file.

    Single-threaded asyncio, so writing to the shared file handle from any
    coroutine is safe — each call runs to completion before the loop yields.
    """
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    logf = state.get("logf")
    if logf:
        try:
            logf.write(line + "\n")
            logf.flush()
        except Exception:  # noqa: BLE001 — logging must never break the run
            pass


async def _aretry(state: dict, label: str, fn, *args, tries: int = 5):
    """Call a SYNC fn, retrying transient errors (network resets, etc.) with
    async backoff so a single blip never kills the run. Re-raises after `tries`.
    """
    delay = 1.0
    for attempt in range(1, tries + 1):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            if attempt == tries:
                raise
            _emit(state, f"⚠ {label} error (attempt {attempt}/{tries}): "
                         f"{type(e).__name__}: {e} — retry in {delay:.0f}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)


def _safe_spent(client, run_start: str, company_id: str, state: dict):
    """Best-effort ledger read for display/summary; on failure fall back to the
    last known good values so a transient error never crashes the run."""
    try:
        c, v = _spent_since(client, run_start, company_id)
        state["last_calls"], state["last_cents"] = c, v
        return c, v
    except Exception:  # noqa: BLE001
        return state.get("last_calls", 0), state.get("last_cents", 0.0)


def _map_place(place: dict) -> Practice:
    # Imported lazily-shaped: _map_google_place lives in src.places.
    from src.places import _map_google_place
    return _map_google_place(place)


# ---------------------------------------------------------------------------
# Heartbeat — a live pulse so a long AI call never looks like a hang
# ---------------------------------------------------------------------------


async def _heartbeat(client, company_id: str, n: int, queue: "asyncio.Queue",
                     state: dict, stop: "asyncio.Event", interval: float,
                     run_start: str) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break  # stop was set → exit
        except asyncio.TimeoutError:
            pass  # interval elapsed → print a pulse
        done = state["ok"] + state["failed"] + state["skipped"]
        calls, cents = _safe_spent(client, run_start, company_id, state)
        el = int(time.monotonic() - state["t0"])
        _emit(state, f"⏱  progress: done={done}/{n}  ok={state['ok']} fail={state['failed']} "
                     f"skip={state['skipped']}  ·  in-flight={state['in_flight']} "
                     f"queued={queue.qsize()}  ·  spent=${cents/100:.2f}/{calls} calls "
                     f"·  {el}s elapsed")


# ---------------------------------------------------------------------------
# Stage A — Places producer (serial, sole Places caller, cap-gated)
# ---------------------------------------------------------------------------


async def _produce(
    leads: list[dict],
    company_id: str,
    queue: "asyncio.Queue",
    state: dict,
    *,
    run_start: str,
    max_usd_cents: float,
    max_places_calls: int,
    concurrency: int,
    places_delay: float,
) -> None:
    """Fetch Places for each lead and enqueue the resolved practice.

    The ONLY place a Places call is made — kept serial so the ledger look-ahead
    below is exact for the Places ceiling and conservative for the USD ceiling.
    """
    client = _get_client()
    n = len(leads)
    for idx, raw in enumerate(leads, 1):
        posting_input = _sanitize(raw)
        emp = posting_input.get("employer_name")
        city = posting_input.get("city")

        # ---- CAP CHECK (before spending a Places call) -------------------
        # Ledger read is a network call; retry transient blips. If it fails
        # even after retries we CANNOT verify spend, so stop admitting rather
        # than risk the cap — consumers drain and the log is written.
        try:
            places_calls, spent_cents = await _aretry(
                state, "ledger-read", _spent_since, client, run_start, company_id)
        except Exception as e:  # noqa: BLE001
            state["cap_hit"] = (f"ledger read failed repeatedly ({type(e).__name__}) "
                                f"— halting to protect the spend cap")
            _emit(state, f"🛑 {state['cap_hit']}. Stopping admission before lead {idx}.")
            break
        state["last_calls"], state["last_cents"] = places_calls, spent_cents
        if places_calls + 1 > max_places_calls:
            state["cap_hit"] = (f"Places-call ceiling: {places_calls} made, "
                                f"limit {max_places_calls} — next lead would exceed it")
            _emit(state, f"🛑 CAP HIT — {state['cap_hit']}. Halting admission before lead {idx}.")
            break
        # Reserve headroom for admitted-but-unfinished leads: their OpenAI spend
        # may not be in the ledger yet. in_flight is a lower bound (single-thread
        # asyncio), and EST is conservative, so this errs toward stopping early.
        projected = spent_cents + (state["in_flight"] + 1) * EST_LEAD_CENTS
        if projected > max_usd_cents:
            state["cap_hit"] = (f"USD ceiling: ${spent_cents/100:.2f} spent + "
                                f"{state['in_flight']} in-flight — next lead would exceed "
                                f"${max_usd_cents/100:.2f}")
            _emit(state, f"🛑 CAP HIT — {state['cap_hit']}. Halting admission before lead {idx}.")
            break

        if not emp or not str(emp).strip():
            _emit(state, f"A ⤫ #{idx}/{n} SKIP — no employer_name to search Places with")
            state["skipped"] += 1
            continue

        query = f"{emp} {city}".strip() if city else emp
        _emit(state, f"A ▶ #{idx}/{n} {emp!r} · Places {query!r}  "
                     f"(spent ${spent_cents/100:.2f}/{places_calls} calls · "
                     f"in-flight {state['in_flight']} · queued {queue.qsize()})")

        try:
            # 1. Persist the posting, resolve its DB id (idempotent upsert).
            lead_store.upsert_postings([posting_input])
            posting = _resolve_posting_row(posting_input["source"], posting_input["external_id"])
            if not posting:
                raise RuntimeError("posting upsert did not resolve to a row")
            posting_id = posting["id"]

            # 2. ONE billable Places Text Search (records the spend to the ledger).
            #    Retry transient network errors so a blip fails-soft only after
            #    a real failure, not on the first reset.
            t_places = time.monotonic()
            places = await _aretry(state, f"places #{idx}", _one_places_call, query, tries=3)
            try:
                from src.usage import record_places
                record_places(kind="places_search", calls=1, company_id=None,
                              metadata={"query": query, "results": len(places),
                                        "flow": "run_leadset_batched"})
            except Exception:
                pass
            if not places:
                _emit(state, f"A ⤫ #{idx}/{n} {emp!r} — Google returned 0 results "
                             f"(no practice; 1 call spent, {time.monotonic()-t_places:.1f}s)")
                state["failed"] += 1
                state["results"].append({"idx": idx, "employer": emp,
                                         "outcome": "no_places_result"})
                if places_delay:
                    await asyncio.sleep(places_delay)
                continue

            practice_model = _map_place(places[0])

            # 3. Upsert into the shared universe + link posting↔practice, exactly
            #    as posting_to_talentdb does (same matcher, same match_method tag).
            upsert_practices([practice_model], touched_by=None, company_id=company_id)
            place_id = practice_model.place_id
            practice_pk = _practice_id_by_place(place_id)
            if not practice_pk:
                raise RuntimeError(f"practice upsert ok but no id for place_id={place_id}")
            conf = round(score(normalise_employer(emp),
                               normalise_employer(practice_model.name)), 2)
            match_status = "auto" if conf >= AUTO_SCORE else "review"
            client.table("job_postings").update({
                "practice_id": practice_pk,
                "match_confidence": conf,
                "match_status": match_status,
                "match_method": "run_leadset_batched",
                "matched_at": _now(),
            }).eq("id", posting_id).execute()

            # 4. Hand the resolved practice to the consumers and count it in-flight.
            state["in_flight"] += 1
            await queue.put({"idx": idx, "posting_id": posting_id, "place_id": place_id,
                             "practice": practice_model, "employer": emp, "city": city})
            _emit(state, f"A ✓ #{idx}/{n} {emp!r} → {practice_model.name!r} "
                         f"(match {conf} {match_status}, {time.monotonic()-t_places:.1f}s) → queued")
        except Exception as e:  # noqa: BLE001 — one bad lead must not kill the run
            state["failed"] += 1
            state["results"].append({"idx": idx, "employer": emp,
                                     "outcome": f"produce_error: {type(e).__name__}: {e}"})
            _emit(state, f"A ✗ #{idx}/{n} PRODUCE ERROR {emp!r} — {type(e).__name__}: {e}")

        if places_delay:
            await asyncio.sleep(places_delay)

    # Signal every consumer to drain and exit.
    for _ in range(concurrency):
        await queue.put(_DONE)
    _emit(state, f"A ■ producer done — {n} leads seen; sent {concurrency} drain signals")


# ---------------------------------------------------------------------------
# Stage B — enrichment consumers (concurrency workers)
# ---------------------------------------------------------------------------


async def _consume(
    worker_id: int,
    company_id: str,
    n: int,
    queue: "asyncio.Queue",
    state: dict,
    *,
    skip_enrich: bool,
) -> None:
    """Run the slow half (analyze + playbook∥email + Clay) for queued practices."""
    while True:
        item = await queue.get()
        try:
            if item is _DONE:
                return
            idx = item["idx"]
            place_id = item["place_id"]
            pm = item["practice"]
            emp = item["employer"]
            entry = {"idx": idx, "employer": emp, "posting_id": item["posting_id"],
                     "worker": worker_id, "at": _now()}
            _emit(state, f"w{worker_id} ▶ #{idx} {emp!r} — analyzing…")
            t0 = time.monotonic()
            try:
                # analyze (crawls site + reviews + 1 completion)
                analysis = await analyze_practice(
                    place_id=place_id, name=pm.name, website=pm.website,
                    category=pm.category, city=pm.city, state=pm.state,
                    rating=pm.rating, review_count=pm.review_count or 0,
                    company_id=company_id, user_id=None,
                )
                update_practice_analysis(place_id, analysis, touched_by=None,
                                         company_id=company_id)
                t_analyze = time.monotonic() - t0

                # playbook ∥ email — independent, so run them together.
                tg = time.monotonic()
                script, draft = await asyncio.gather(
                    generate_script(
                        name=pm.name, category=pm.category,
                        summary=analysis.get("summary"),
                        pain_points=analysis.get("pain_points"),
                        sales_angles=analysis.get("sales_angles"),
                        city=pm.city, state=pm.state, rating=pm.rating,
                        review_count=pm.review_count,
                        website_doctor_name=analysis.get("website_doctor_name"),
                        company_id=company_id, user_id=None,
                    ),
                    generate_email_draft(
                        name=pm.name, category=pm.category,
                        summary=analysis.get("summary"),
                        pain_points=analysis.get("pain_points"),
                        sales_angles=analysis.get("sales_angles"),
                        company_id=company_id, user_id=None,
                    ),
                )
                update_practice_fields(place_id, {"call_script": json.dumps(script)},
                                       touched_by=None, company_id=company_id)
                update_practice_fields(
                    place_id,
                    {"email_draft": json.dumps(draft), "email_draft_updated_at": _now()},
                    touched_by=None, company_id=company_id,
                )
                t_gen = time.monotonic() - tg

                # Clay owner enrichment (async write-back; prepaid credits).
                tc = time.monotonic()
                if skip_enrich:
                    enrich = "skipped"
                else:
                    try:
                        res = await trigger_enrichment(Practice(**get_practice(place_id)))
                        if res.get("skipped"):
                            enrich = f"skipped:{res.get('reason')}"
                        else:
                            update_practice_fields(place_id,
                                                   {"enrichment_status": "pending"},
                                                   touched_by=None, company_id=company_id)
                            enrich = "pending"
                    except Exception as e:  # noqa: BLE001 — enrichment never blocks the lead
                        enrich = f"failed:{type(e).__name__}"
                t_clay = time.monotonic() - tc

                state["ok"] += 1
                done = state["ok"] + state["failed"] + state["skipped"]
                entry.update(outcome="ok", enrich=enrich,
                             tier=analysis.get("icp_tier"),
                             vertical=analysis.get("icp_vertical"),
                             secs=round(time.monotonic() - t0, 1))
                _emit(state, f"w{worker_id} ✓ #{idx} {emp!r} — tier={analysis.get('icp_tier')} "
                             f"vertical={analysis.get('icp_vertical')} enrich={enrich}  |  "
                             f"analyze {t_analyze:.1f}s · gen {t_gen:.1f}s · clay {t_clay:.1f}s "
                             f"· total {time.monotonic()-t0:.1f}s  [done {done}/{n} · "
                             f"ok {state['ok']} · fail {state['failed']}]")
            except Exception as e:  # noqa: BLE001
                state["failed"] += 1
                entry["outcome"] = f"enrich_error: {type(e).__name__}: {e}"
                _emit(state, f"w{worker_id} ✗ #{idx} {emp!r} — ERROR {type(e).__name__}: {e} "
                             f"(after {time.monotonic()-t0:.1f}s)")
            state["results"].append(entry)
            state["in_flight"] -= 1
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run(leadset_path: str, company_id: str, *, dry_run: bool, yes: bool,
              concurrency: int, prefetch: int, max_usd: float, max_places_calls: int,
              max_leads: int | None, places_delay: float, heartbeat: float,
              skip_enrich: bool) -> None:
    client = _get_client()
    if not client:
        sys.exit("No Supabase client — check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.")

    try:
        leads = json.load(open(leadset_path))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"could not read leadset {leadset_path!r}: {e}")
    if not isinstance(leads, list) or not leads:
        sys.exit(f"{leadset_path!r} must be a non-empty JSON list of postings.")
    if max_leads is not None:
        leads = leads[:max_leads]
    n = len(leads)

    max_usd_cents = max_usd * 100.0
    est_cents = n * EST_LEAD_CENTS
    est_clay = n * CLAY_DATA_CREDITS_PER_LEAD

    print(f"[batch] leadset: {leadset_path}  ({n} leads)")
    print(f"[batch] company_id: {company_id}")
    print(f"[batch] shape: Places producer (serial) → queue(maxsize={prefetch}) "
          f"→ {concurrency} enrichment workers")
    print(f"[batch] per lead: 1 Places call + analyze + (script ∥ email)"
          f"{'' if skip_enrich else ' + Clay enrich'}  (qualify + webhook skipped)")
    print(f"[batch] CAP: hard stop at ${max_usd:.2f} real spend OR {max_places_calls} Places calls")
    print(f"[batch] estimate: ~${est_cents/100:.2f} vendor cost"
          f"{'' if skip_enrich else f' + ~{est_clay:.0f} prepaid Clay credits'}"
          f"  (~{EST_LEAD_CENTS:.1f}¢/lead)")
    if est_cents > max_usd_cents:
        halt_at = int(max_usd_cents // EST_LEAD_CENTS)
        print(f"[batch] ⚠  estimate ${est_cents/100:.2f} EXCEEDS the ${max_usd:.2f} cap — "
              f"the run will stop cleanly at ~{halt_at} leads. Raise --max-usd for all {n}.")
    print(f"[batch] mode: {'DRY RUN (no external calls, no writes)' if dry_run else 'LIVE'}")

    if dry_run:
        print("\n[dry-run] leads that would be processed (sanitized), in order:")
        for idx, raw in enumerate(leads, 1):
            p = _sanitize(raw)
            emp, city = p.get("employer_name"), p.get("city")
            missing = [k for k in ("source", "external_id", "employer_name") if not p.get(k)]
            flag = f"  ⚠ missing {missing}" if missing else ""
            print(f"  [{idx:>3}/{n}] {p.get('source')}:{p.get('external_id')}  "
                  f"{emp!r} · {city}{flag}")
        print(f"\n[dry-run] No calls made. Re-run with --yes to execute live "
              f"(cap ${max_usd:.2f} / {max_places_calls} Places calls).")
        return

    if not yes:
        print("\n[batch] REFUSING to run live without --yes. "
              "Re-run with --dry-run to preview, or add --yes to execute.")
        return

    os.makedirs("docs/runbooks", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    live_path = f"docs/runbooks/leadset-batched-{stamp}.live.log"
    log_path = f"docs/runbooks/leadset-batched-{stamp}.log.json"

    print(f"\n[batch] live log → {live_path}")
    print(f"[batch] watch it live in another terminal:\n         tail -f {live_path}\n")
    print("[batch] ⚠  reprocess-always: every lead runs this pass, even if a prior "
          "run already processed it — re-running re-spends. The cap bounds THIS run only.\n")

    run_start = _now()
    # Shared mutable state (single-threaded asyncio — plain ints are safe).
    state = {"ok": 0, "failed": 0, "skipped": 0, "in_flight": 0,
             "cap_hit": None, "results": [], "logf": open(live_path, "w"),
             "t0": time.monotonic(), "last_calls": 0, "last_cents": 0.0}
    queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, prefetch))
    stop_hb = asyncio.Event()

    _emit(state, f"■ START — {n} leads · concurrency={concurrency} · prefetch={prefetch} "
                 f"· cap ${max_usd:.2f}/{max_places_calls} calls · clay={'off' if skip_enrich else 'on'}")

    try:
        hb = asyncio.create_task(_heartbeat(
            client, company_id, n, queue, state, stop_hb, heartbeat, run_start))
        producer = asyncio.create_task(_produce(
            leads, company_id, queue, state, run_start=run_start,
            max_usd_cents=max_usd_cents, max_places_calls=max_places_calls,
            concurrency=concurrency, places_delay=places_delay))
        consumers = [asyncio.create_task(_consume(
            w + 1, company_id, n, queue, state, skip_enrich=skip_enrich))
            for w in range(concurrency)]

        await producer
        await asyncio.gather(*consumers)
        stop_hb.set()
        await hb

        final_calls, final_cents = _safe_spent(client, run_start, company_id, state)
        elapsed = int(time.monotonic() - state["t0"])
        _emit(state, f"■ DONE — ok={state['ok']} failed={state['failed']} "
                     f"skipped={state['skipped']} of {n}  ·  ${final_cents/100:.2f} / "
                     f"{final_calls} Places calls  ·  {elapsed}s elapsed"
                     + (f"  ·  STOPPED BY CAP: {state['cap_hit']}" if state["cap_hit"] else ""))
    finally:
        stop_hb.set()
        if state.get("logf"):
            try:
                state["logf"].close()
            except Exception:  # noqa: BLE001
                pass

    final_calls, final_cents = _safe_spent(client, run_start, company_id, state)
    summary = {
        "run_at": stamp, "company_id": company_id, "leadset": leadset_path,
        "shape": {"concurrency": concurrency, "prefetch": prefetch},
        "elapsed_seconds": int(time.monotonic() - state["t0"]),
        "requested": n, "ok": state["ok"], "failed": state["failed"],
        "skipped_no_employer": state["skipped"],
        "cap_usd": max_usd, "cap_places_calls": max_places_calls,
        "cap_hit": state["cap_hit"],
        "final_places_calls": final_calls,
        "final_spend_usd": round(final_cents / 100.0, 4),
        "clay_enrich": not skip_enrich,
        "live_log": live_path,
        "results": sorted(state["results"], key=lambda r: r.get("idx", 0)),
    }
    with open(log_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n[batch] done: ok={state['ok']} failed={state['failed']} "
          f"skipped={state['skipped']} of {n}")
    print(f"[batch] real spend this run: ${final_cents/100:.2f}  ·  {final_calls} Places calls")
    if state["cap_hit"]:
        print(f"[batch] stopped early by cap — {state['cap_hit']}")
    print(f"[batch] live log → {live_path}")
    print(f"[batch] summary  → {log_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batched (producer/consumer) enrichment runner — Places never "
                    "blocked behind AI generation. Enrichment only; webhook skipped.")
    ap.add_argument("--leadset", default="leadset-100.json",
                    help="path to the lead set JSON (default: leadset-100.json)")
    ap.add_argument("--company-id", default=None,
                    help="tenant to attribute to (default: settings.lead_company_id)")
    ap.add_argument("--concurrency", type=int, default=5,
                    help="number of concurrent enrichment workers (default: 5)")
    ap.add_argument("--prefetch", type=int, default=15,
                    help="how many leads Places may run ahead (queue maxsize; default: 15)")
    ap.add_argument("--max-usd", type=float, default=10.0,
                    help="hard ceiling on real vendor spend for this run (default: $10)")
    ap.add_argument("--max-places-calls", type=int, default=120,
                    help="hard ceiling on Places calls for this run (default: 120)")
    ap.add_argument("--max-leads", type=int, default=None,
                    help="process at most N leads (e.g. 3 for a canary)")
    ap.add_argument("--places-delay", type=float, default=0.3,
                    help="seconds between Places calls in the producer (default: 0.3)")
    ap.add_argument("--heartbeat", type=float, default=15.0,
                    help="seconds between live progress pulses (default: 15)")
    ap.add_argument("--verbose", action="store_true",
                    help="also stream the underlying analyzer/scriptgen module logs (INFO)")
    ap.add_argument("--skip-enrich", action="store_true",
                    help="skip Clay enrichment (no data-credit burn)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan + estimate + caps, make NO external calls (default-safe)")
    ap.add_argument("--yes", action="store_true",
                    help="required to run live — confirms real Places/OpenAI/Clay spend")
    args = ap.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(name)s %(message)s",
            datefmt="%H:%M:%S",
        )

    company_id = args.company_id or settings.lead_company_id
    if not company_id:
        sys.exit("No company_id — pass --company-id or set LEAD_COMPANY_ID.")
    if args.max_leads is not None and args.max_leads <= 0:
        sys.exit("--max-leads must be positive.")
    if args.concurrency <= 0:
        sys.exit("--concurrency must be positive.")

    dry_run = args.dry_run or not args.yes
    asyncio.run(run(
        args.leadset, company_id,
        dry_run=dry_run, yes=args.yes,
        concurrency=args.concurrency, prefetch=args.prefetch,
        max_usd=args.max_usd, max_places_calls=args.max_places_calls,
        max_leads=args.max_leads, places_delay=args.places_delay,
        heartbeat=args.heartbeat, skip_enrich=args.skip_enrich,
    ))


if __name__ == "__main__":
    main()
