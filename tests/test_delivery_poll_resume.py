"""The delivery poll RESUMES. Every test here is a negative from the 2026-09-07 stall.

🔴 THE BURN THIS SUITE PINS. `sync_logon`'s `fulfillments` leg had not stamped a success since
**2026-09-04 09:36** — ~80 hours against a 36h threshold — while reporting
`partial:Timeout:600s:3365 rows committed; remainder re-selected next run` on every run. Three
independent defects, each of which alone keeps the leg stuck forever:

  1. **There was no remainder mechanism.** `sync_parcel_panel` rebuilt the same work list every
     run with no ORDER BY and no persisted position, and walked it from index 0. Live log,
     09:45 that morning: **2,576 orders, stopped at order 800**; the next run started at order 1.
     Everything past index 800 was never asked — not once.
  2. **`SELECT DISTINCT` sorts TEXT**, and order numbers had outgrown five digits, so `101334`
     (new) sorted ahead of `94080` (old). The newest work was polled first and the oldest —
     back to 2025-05-22 on the live DB — sat permanently in the unreachable tail.
  3. **Two legs shared one 600s budget and one heartbeat key.** The `3365` was 2,565 Shopify
     `fulfillments` rows PLUS 800 `delivery_status` rows — a sum across two TABLES that reads
     as progress on one queue. And `ok` required BOTH legs, so the Shopify leg (which finished
     every run and advanced its watermark) could never stamp success. The alarm was firing on
     the wrong leg.

Offline by construction: a fake clock, a fake ParcelPanel client, and an in-memory sqlite
fixture. 🔴 No writer's `main()` is called, no live DB is opened, no network is touched.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from appyhour_lib import sync_heartbeat  # noqa: E402

backfill_sync = pytest.importorskip("backfill_sync")
delivery_window = pytest.importorskip("delivery_window")
sync_logon = pytest.importorskip("sync_logon")

from parcel_panel import PPThrottled  # noqa: E402

# ── fixtures: scratch DB + a fake PP + a fake clock ──────────────────────────

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE fulfillments (order_number TEXT, tracking_number TEXT, "
                "order_id TEXT, order_date TEXT, tags TEXT, tracking_company TEXT, "
                "tracking_url TEXT, fulfilled_at TEXT, customer_name TEXT, dest_city TEXT, "
                "dest_state TEXT, dest_zip TEXT)")
    con.execute("CREATE TABLE delivery_status (tracking_number TEXT PRIMARY KEY, "
                "order_number TEXT, carrier TEXT, status TEXT, delivery_date TEXT, "
                "pickup_date TEXT, transit_days INTEGER, origin_hub TEXT, last_event TEXT, "
                "synced_at TEXT)")
    delivery_window.ensure_schema(con)
    return con


def _order(con, num: str, *, days_ago: int, status: str | None = None,
           delivery_date: str | None = None) -> None:
    con.execute("INSERT INTO fulfillments (order_number, tracking_number, fulfilled_at) "
                "VALUES (?, ?, datetime('now', ?))", (num, f"TRK{num}", f"-{days_ago} days"))
    if status is not None:
        con.execute("INSERT INTO delivery_status (tracking_number, order_number, status, "
                    "delivery_date) VALUES (?, ?, ?, ?)",
                    (f"TRK{num}", num, status, delivery_date))
    con.commit()


class FakeClock:
    """Monotonic clock we advance by hand. 🔴 A budget test that SLEEPS is a slow test that
    proves the sleep, not the budget."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakePP:
    """ParcelPanel stand-in. Records every order it was ASKED for — the resume evidence.

    `throttle` names orders it refuses with `PPThrottled` (never an answer, never an attempt,
    P13). `deliver` names orders it answers as delivered, so they leave the queue for real.
    """

    def __init__(self, *, throttle: set[str] | None = None, deliver: set[str] | None = None,
                 clock: FakeClock | None = None, seconds_per_call: float = 1.0) -> None:
        self.asked: list[str] = []
        self.throttle = throttle or set()
        self.deliver = deliver or set()
        self.clock = clock
        self.seconds_per_call = seconds_per_call

    def get_order_tracking(self, order_number: str = "", stats=None):  # noqa: ARG002
        if self.clock is not None:
            self.clock.t += self.seconds_per_call
        if order_number in self.throttle:
            raise PPThrottled(429, 3)          # (status, attempts) — the limiter's own shape
        self.asked.append(order_number)
        if order_number in self.deliver:
            return {"order": {"shipments": [{"tracking_number": f"TRK{order_number}"}]}}
        return {"order": {"shipments": []}}      # answered with nothing — still an attempt

    def _normalize_parcel(self, ship, order_num):
        return {"tracking_number": ship["tracking_number"], "order_number": order_num,
                "carrier": "OnTrac", "status": "delivered",
                "delivery_date": "2026-09-07", "pickup_date": "2026-09-05",
                "transit_days": 2, "origin_hub": "Dallas", "last_event": "Delivered",
                "synced_at": "2026-09-07 00:00:00"}


@pytest.fixture(autouse=True)
def _no_store_side_effects(monkeypatch):
    """`store_delivery_status` belongs to `shipping_invoice_db`; here it is a plain upsert.

    Stubbed so the suite pins THIS module's scheduling behaviour and never depends on the
    invoice writer's schema — which carries another session's uncommitted work.
    """
    def _store(conn, parcels):
        conn.executemany(
            "INSERT INTO delivery_status (tracking_number, order_number, status, delivery_date) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(tracking_number) DO UPDATE SET "
            "status=excluded.status, delivery_date=excluded.delivery_date",
            [(p["tracking_number"], p["order_number"], p["status"], p["delivery_date"])
             for p in parcels])
    monkeypatch.setattr(backfill_sync.db, "store_delivery_status", _store)
    monkeypatch.setattr(backfill_sync, "PP_FLUSH_EVERY", 5)   # small batches, same boundaries


# ── 1. a budget exit preserves commits, and the NEXT run advances ────────────

def test_budget_exit_banks_rows_and_the_next_run_asks_a_DISJOINT_set():
    """🔴 THE 80-HOUR STALL IN ONE ASSERTION.

    Under the old code run 2 asked exactly what run 1 asked. Here it must ask what run 1 did
    NOT reach — and the union of the two runs must cover the whole queue.
    """
    con = _db()
    for i in range(20):
        _order(con, f"{200000 + i}", days_ago=3)

    clock1, out1 = FakeClock(), {}
    pp1 = FakePP(clock=clock1, seconds_per_call=1.0)
    backfill_sync.sync_parcel_panel(con, pp1, max_seconds=6, clock=clock1, stamp_out=out1)

    assert pp1.asked, "run 1 banked nothing"
    assert out1["complete"] is False, "run 1 should have stopped on its budget"
    assert out1["stop_reason"].startswith("elapsed-budget")
    assert out1["remaining"] == 20 - len(pp1.asked), "remaining must be the UNasked tail"

    clock2, out2 = FakeClock(), {}
    pp2 = FakePP(clock=clock2, seconds_per_call=1.0)
    backfill_sync.sync_parcel_panel(con, pp2, max_seconds=6, clock=clock2, stamp_out=out2)

    assert set(pp1.asked).isdisjoint(pp2.asked), (
        "run 2 re-asked orders run 1 already polled — this IS the non-resumption bug")
    assert out2["remaining"] < out1["remaining"], "the backlog did not shrink"


def test_three_bounded_runs_drain_the_whole_queue_and_the_last_one_is_complete():
    """Resumption is only real if repeated bounded runs REACH THE END. Attrition is not enough."""
    con = _db()
    for i in range(12):
        _order(con, f"{300000 + i}", days_ago=3)

    asked_all: list[str] = []
    out: dict = {}
    for _ in range(3):
        clock = FakeClock()
        pp = FakePP(clock=clock, seconds_per_call=1.0)
        out = {}
        backfill_sync.sync_parcel_panel(con, pp, max_seconds=5, clock=clock, stamp_out=out)
        asked_all += pp.asked

    assert sorted(set(asked_all)) == sorted(f"{300000 + i}" for i in range(12))
    assert len(asked_all) == len(set(asked_all)), "an order was polled twice inside one drain"
    assert out["complete"] is True and out["remaining"] == 0


# ── 2. old work is not starved by newly arriving orders ─────────────────────

def test_the_oldest_never_asked_order_is_polled_FIRST_even_against_a_flood_of_new_ones():
    """🔴 THE TEXT-SORT DEFECT. `94080` (2025) vs `101334` (today): as text `1` < `9`, so the
    old query put every new six-digit order ahead of it and the budget never reached the tail."""
    con = _db()
    _order(con, "94080", days_ago=470)            # the stranded 2025 box
    _order(con, "96880", days_ago=400)
    for i in range(30):                            # a flood of today's orders
        _order(con, f"{101300 + i}", days_ago=0)

    clock, out = FakeClock(), {}
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    backfill_sync.sync_parcel_panel(con, pp, max_seconds=3, clock=clock, stamp_out=out)

    assert pp.asked[0] == "94080", f"oldest work starved; asked {pp.asked[:5]}"
    assert pp.asked[1] == "96880"
    assert out["oldest_due_days"] is not None


def test_a_re_polled_order_yields_to_one_that_has_never_been_asked():
    """Bucket 0 (never attempted) before bucket 1 (re-poll), regardless of timestamps."""
    con = _db()
    _order(con, "500001", days_ago=90)
    delivery_window.record_attempts(con, ["500001"])
    con.execute("UPDATE delivery_poll_attempts SET last_attempt_at = datetime('now','-30 days'),"
                " attempts = 1")
    _order(con, "500002", days_ago=1)              # newer, but never asked
    con.commit()

    rows = [r[0] for r in con.execute(delivery_window.due_work_list_sql()).fetchall()]
    assert rows == ["500002", "500001"]


def test_backoff_can_never_outrun_the_age_gate_that_retires_an_order():
    """🔴 A backoff longer than DELIVERY_MAX_AGE_DAYS would strand orders a SECOND time, by a
    new mechanism. Pin the arithmetic, not the constants."""
    total_h = sum(delivery_window.backoff_hours(i)
                  for i in range(delivery_window.DELIVERY_MAX_ATTEMPTS))
    assert total_h / 24.0 < delivery_window.DELIVERY_MAX_AGE_DAYS, (
        f"{total_h}h of backoff to bank {delivery_window.DELIVERY_MAX_ATTEMPTS} attempts, but "
        f"aged_out needs the order to still be under {delivery_window.DELIVERY_MAX_AGE_DAYS}d")
    assert delivery_window.backoff_hours(0) == 0, "a first poll must never wait"


def test_an_order_inside_its_backoff_is_not_re_asked_but_is_still_counted_unresolved():
    con = _db()
    _order(con, "600001", days_ago=5)
    delivery_window.record_attempts(con, ["600001"])
    con.commit()

    clock, out = FakeClock(), {}
    pp = FakePP(clock=clock)
    backfill_sync.sync_parcel_panel(con, pp, stamp_out=out)
    assert pp.asked == [], "an order inside its backoff was re-asked"
    assert out["unresolved"] == 1 and out["remaining"] == 0, (
        "backoff must hide the order from `due`, never from `unresolved` — a leg reporting "
        "0 and 0 looks drained when it is merely resting")


# ── 3. throttling does not busy-retry ───────────────────────────────────────

def test_a_throttle_storm_stops_the_run_instead_of_spinning_the_whole_queue():
    """🔴 `except PPThrottled: continue` walks the ENTIRE list at full speed doing no work, and
    then reports `partial:` as though a backlog were draining. A storm is an outage."""
    con = _db()
    ids = [f"{700000 + i}" for i in range(200)]
    for oid in ids:
        _order(con, oid, days_ago=2)

    clock = FakeClock()
    pp = FakePP(throttle=set(ids), clock=clock, seconds_per_call=0.01)
    out: dict = {}
    streak = backfill_sync.THROTTLE_ABORT_STREAK
    backfill_sync.sync_parcel_panel(con, pp, max_seconds=10_000, clock=clock, stamp_out=out)

    assert pp.asked == [], "a throttled order must never be recorded as served"
    assert out["throttled"] < len(ids), "the run spun the whole queue on refusals"
    # The streak is consulted at flush boundaries, so the worst case is one batch of overshoot.
    assert out["polled"] <= streak + backfill_sync.PP_FLUSH_EVERY
    assert "throttle storm" in out["stop_reason"]
    assert out["complete"] is False


def test_a_throttled_order_banks_no_attempt_so_an_outage_cannot_retire_a_live_box():
    """P13, re-pinned here because the resume cursor now RIDES on the attempt counter: if a
    refusal stamped `last_attempt_at`, an outage would push live boxes to the back of the queue
    AND toward `aged_out` without a single answer."""
    con = _db()
    _order(con, "800001", days_ago=2)
    clock = FakeClock()
    backfill_sync.sync_parcel_panel(con, FakePP(throttle={"800001"}, clock=clock), clock=clock)
    assert con.execute("SELECT COUNT(*) FROM delivery_poll_attempts").fetchone()[0] == 0
    rows = [r[0] for r in con.execute(delivery_window.due_work_list_sql()).fetchall()]
    assert rows == ["800001"], "a refused order must stay at the FRONT of the due list"


# ── 4. terminal records are skipped ─────────────────────────────────────────

@pytest.mark.parametrize("status", delivery_window.TERMINAL_STATUSES)
def test_a_terminal_order_is_never_polled(status):
    con = _db()
    _order(con, "900001", days_ago=200, status=status)
    _order(con, "900002", days_ago=2)
    clock = FakeClock()
    pp = FakePP(clock=clock)
    backfill_sync.sync_parcel_panel(con, pp, clock=clock)
    assert pp.asked == ["900002"]


def test_an_order_with_a_delivery_date_is_terminal_whatever_its_status_says():
    con = _db()
    _order(con, "910001", days_ago=10, status="in_transit", delivery_date="2026-09-01")
    clock = FakeClock()
    pp = FakePP(clock=clock)
    backfill_sync.sync_parcel_panel(con, pp, clock=clock)
    assert pp.asked == []


def test_an_order_that_becomes_delivered_leaves_the_queue_for_good():
    con = _db()
    _order(con, "920001", days_ago=3)
    clock = FakeClock()
    pp = FakePP(deliver={"920001"}, clock=clock)
    out: dict = {}
    backfill_sync.sync_parcel_panel(con, pp, clock=clock, stamp_out=out)
    assert out["written"] == 1 and out["remaining"] == 0 and out["unresolved"] == 0
    pp2 = FakePP(clock=clock)
    backfill_sync.sync_parcel_panel(con, pp2, clock=clock)
    assert pp2.asked == []


def test_an_ancient_undelivered_order_is_still_reachable_after_the_reorder():
    """The 604 contract must survive the scheduling change: age never removes an order."""
    con = _db()
    _order(con, "930001", days_ago=500)
    rows = [r[0] for r in con.execute(delivery_window.due_work_list_sql()).fetchall()]
    assert rows == ["930001"]
    assert delivery_window.count_undelivered_past_window(con) == 1


# ── 5. partial vs success stamps, per phase, INDEPENDENTLY ──────────────────

def test_ok_requires_a_drained_queue_not_merely_rows_landing():
    """🔴 THE STAMP THAT LIED. 800 rows landed every run for four days; none of it was `ok`."""
    banked_but_stuck = {"written": 800, "complete": False, "stop_reason": "elapsed-budget 480s",
                        "remaining": 1776, "unresolved": 2576, "oldest_due_days": 473,
                        "retired": 0}
    s = sync_logon._delivery_poll_stamp(banked_but_stuck)
    assert s.startswith("partial:elapsed-budget 480s:")
    assert "1776 due remaining" in s and "oldest due 473d" in s

    drained = {"written": 812, "complete": True, "stop_reason": "", "remaining": 0,
               "unresolved": 0, "oldest_due_days": None, "retired": 3}
    ok = sync_logon._delivery_poll_stamp(drained)
    assert ok.startswith("ok:")
    assert "oldest due none" in ok, (
        "a drained queue must not wear the word UNKNOWN — an alarm word on the healthiest "
        "line is how a reader learns to skim the stamp")


def test_a_failed_recount_prints_UNKNOWN_and_never_a_fabricated_zero():
    s = sync_logon._delivery_poll_stamp(
        {"written": 5, "complete": False, "stop_reason": "budget", "remaining": None,
         "unresolved": None, "oldest_due_days": None, "retired": 0})
    assert "UNKNOWN due remaining" in s and "0 due remaining" not in s
    assert "oldest due UNKNOWN" in s, (
        "when remaining is unknown the age is unknown too — 'none' would claim a drained queue")


@pytest.fixture
def hb(tmp_path, monkeypatch):
    canon = tmp_path / "sync_heartbeat.json"
    monkeypatch.setattr(sync_heartbeat, "CANONICAL", canon)
    monkeypatch.setattr(sync_heartbeat, "LEGACY", tmp_path / "no_legacy.json")
    return canon


def test_the_two_phases_stamp_independently(hb):
    """🔴 THE SPLIT, ASSERTED. A finished Shopify leg must be able to say `ok` while the
    delivery poll is still partial. Under one key it could not, and the leg reported 80 hours
    of staleness for a table that was current to that morning."""
    sync_heartbeat.write({})
    sync_logon._stamp("fulfillments", "ok:2565 rows upserted")
    sync_logon._stamp("delivery_poll",
                      "partial:elapsed-budget 480s:800 delivery rows committed; "
                      "1776 due remaining of 2576 unresolved; oldest due 473d; retired 0")
    h = json.loads(hb.read_text(encoding="utf-8"))

    assert h["fulfillments"], "the Shopify leg finished and must advance its own last-success"
    assert "fulfillments_partial_since" not in h
    assert "delivery_poll" not in h, "a partial must NOT advance the poll's last-success"
    assert h["delivery_poll_partial_since"], (
        "no _partial_since means automation_health grades the leg from 'now' forever "
        "(the 2026-09-07 inversion) and it can never escalate")
    assert sync_logon._should_run("delivery_poll"), (
        "the 12h throttle must let the NEXT logon drain the remainder")


def test_partial_since_is_set_once_and_cleared_only_by_ok(hb):
    sync_heartbeat.write({})
    sync_logon._stamp("delivery_poll", "partial:budget:1 delivery rows committed; 9 due remaining")
    first = json.loads(hb.read_text(encoding="utf-8"))["delivery_poll_partial_since"]
    sync_logon._stamp("delivery_poll", "partial:budget:2 delivery rows committed; 7 due remaining")
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert h["delivery_poll_partial_since"] == first, (
        "refreshing _partial_since resets the backlog age to 0 every run — the same bug, "
        "one key over")
    sync_logon._stamp("delivery_poll", "ok:7 delivery rows; 0 due remaining")
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert "delivery_poll_partial_since" not in h and h["delivery_poll"]


def test_a_stuck_delivery_poll_escalates_to_CRITICAL_after_36h(hb):
    """The whole point of splitting the key: the poll must be able to page on its OWN behalf."""
    from datetime import datetime, timedelta

    import automation_health as ah

    now = datetime(2026, 9, 9, 12, 0, 0)
    data = {"delivery_poll_partial_since": (now - timedelta(hours=40)).isoformat(timespec="seconds"),
            "delivery_poll_last_attempt": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            "delivery_poll_status": "partial:elapsed-budget 480s:800 delivery rows committed; "
                                    "1776 due remaining of 2576 unresolved; oldest due 473d",
            "fulfillments": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "fulfillments_status": "ok:2565 rows upserted"}
    findings: list[str] = []
    ah._grade_partial_legs(data, findings, now=now)
    assert len(findings) == 1
    assert findings[0].startswith("ingest leg delivery_poll PARTIAL with no ok for 40h")


def test_check_sync_heartbeat_accepts_the_new_stamp_shapes():
    from datetime import datetime, timedelta

    import automation_health as ah

    now = datetime.now()
    data = {"fulfillments": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "fulfillments_status": "ok:2565 rows upserted",
            "delivery_poll_partial_since": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "delivery_poll_status": "partial:elapsed-budget 480s:800 delivery rows committed; "
                                    "1776 due remaining of 2576 unresolved; oldest due 473d"}
    import unittest.mock as _m
    with _m.patch.object(ah, "read_sync_heartbeat", lambda: data):
        findings: list[str] = []
        ah.check_sync_heartbeat(findings)
    assert findings == [], f"the new stamp shapes were graded as failures: {findings}"


def test_a_missing_pp_key_is_not_ok_and_is_visible(hb):
    """🔴 `ok` would advance last-success and claim a queue was drained that was never read."""
    import unittest.mock as _m
    from datetime import datetime, timedelta

    import automation_health as ah

    now = datetime.now()
    data = {"fulfillments": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            "delivery_poll_status": "skipped:no-parcel-panel-api-key"}
    with _m.patch.object(ah, "read_sync_heartbeat", lambda: data):
        findings: list[str] = []
        ah.check_sync_heartbeat(findings)
    assert findings == ["ingest legs not ok: delivery_poll_status"]


# ── the aged-out sweep survives a budget exit ───────────────────────────────

def test_the_aged_out_sweep_still_runs_when_the_run_stops_on_its_budget():
    """🔴 A watchdog cancel raised out of `_flush` and SKIPPED the sweep — so the only live
    writer of the terminal state never ran, and the tail it exists to shrink could only grow.
    A CLEAN budget exit must still run the epilogue."""
    con = _db()
    _order(con, "940001", days_ago=200)
    for _ in range(delivery_window.DELIVERY_MAX_ATTEMPTS):
        delivery_window.record_attempts(con, ["940001"])
    for i in range(20):
        _order(con, f"{950000 + i}", days_ago=2)
    con.commit()

    clock, out = FakeClock(), {}
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    backfill_sync.sync_parcel_panel(con, pp, max_seconds=4, clock=clock, stamp_out=out)

    assert out["complete"] is False, "this test must exercise the BUDGET exit"
    assert out["retired"] == 1, "the aged_out sweep was skipped on a budget exit"
    assert con.execute("SELECT status FROM delivery_status WHERE order_number='940001'"
                       ).fetchone()[0] == "aged_out"


def test_the_request_cap_bounds_a_run_even_when_time_is_free():
    """Elapsed time alone is not a bound: a fast PP day would let one run hammer a shared key."""
    con = _db()
    for i in range(40):
        _order(con, f"{960000 + i}", days_ago=2)
    clock, out = FakeClock(), {}
    pp = FakePP(clock=clock, seconds_per_call=0.0)      # infinitely fast PP
    backfill_sync.sync_parcel_panel(con, pp, max_seconds=10_000, max_requests=10,
                                    clock=clock, stamp_out=out)
    assert out["stop_reason"].startswith("request-budget")
    assert out["requests"] <= 10 + backfill_sync.PP_FLUSH_EVERY
    assert out["complete"] is False and out["remaining"] > 0
