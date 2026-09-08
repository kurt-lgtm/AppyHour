"""`daily_shipping_sync.run_pp_sync` RESUMES. The last instance of the 2026-09-07 restart class.

🔴 THE BURN THIS SUITE PINS. `backfill_sync.sync_parcel_panel` was fixed on 2026-09-07 after it
spent ~80 hours re-walking orders 1..800 of a 2,576-order queue on every run — no ORDER BY, no
persisted position, and a `SELECT DISTINCT` that sorts TEXT, so six-digit new order numbers
(`101334`) outranked five-digit old ones (`94080`) and the oldest work starved. Measured on the
live DB that day: the oldest unresolved order, `50241` (fulfilled 2025-05-22, 473 days), sat at
position **2,721 of 2,780** — structurally unreachable.

`run_pp_sync` was left carrying the SAME shape, and `SHIPPING_PIPELINE.md` §3 rule 7 named it as
the remaining instance. It had not bitten only because it had no budget to truncate the queue —
which is not a defence, it is the other failure: an unbounded poll is a ~28-minute (once
~180-minute) stage sitting in front of Gorgias, reclassify and the Tue/Fri postmortem on a task
that fires at 12:00.

Both halves land together here, because either alone is a NEW bug:
  * an ordered queue with no budget blocks the rest of the daily sync;
  * a budget over an unordered, position-less queue truncates the queue permanently.

And one thing that is easy to get wrong even with both: the resume CURSOR
(`delivery_poll_attempts.last_attempt_at`) must be banked per FLUSH, in the same transaction as
the parcels. Banked once after the loop — which is where `run_pp_sync` used to do it — a budget
exit leaves no cursor at all and the next run re-asks exactly what it just asked, while the rows
still land and everything looks fine.

Offline by construction: a fake clock, a fake ParcelPanel client, and an in-memory sqlite
fixture. 🔴 No writer's `main()` is called, no live DB is opened, no network is touched.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_GPC = Path(__file__).resolve().parents[1] / "GelPackCalculator"
for _p in (str(_GPC), str(_GPC / "kori"), str(Path(__file__).resolve().parents[1])):
    if _p not in sys.path:
        sys.path.insert(0, _p)

daily_shipping_sync = pytest.importorskip("daily_shipping_sync")
delivery_window = pytest.importorskip("delivery_window")
backfill_sync = pytest.importorskip("backfill_sync")

import appyhour_lib.db as ahdb          # noqa: E402
import appyhour_lib.paths as ahpaths    # noqa: E402
import parcel_panel                     # noqa: E402
import shipping_invoice_db as kdb       # noqa: E402
from parcel_panel import PPThrottled    # noqa: E402


# ── fixtures: scratch DB + a fake PP + a fake clock ──────────────────────────

class _NoCloseConn:
    """Proxy that swallows `.close()`.

    `run_pp_sync` opens and closes a connection per checkpoint — correctly, that is the
    short-hold lock discipline. An in-memory sqlite DB dies with its connection, so the test
    fixture hands out the SAME connection every time and ignores the closes.
    """

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def close(self) -> None:            # noqa: D102 — deliberately inert
        pass

    def __getattr__(self, name):
        return getattr(self._con, name)


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
           delivery_date: str | None = None, pickup_date: str | None = None,
           tracking: str | None = None) -> None:
    trk = tracking if tracking is not None else f"TRK{num}"
    con.execute("INSERT INTO fulfillments (order_number, tracking_number, fulfilled_at) "
                "VALUES (?, ?, datetime('now', ?))", (num, trk, f"-{days_ago} days"))
    if status is not None:
        con.execute("INSERT INTO delivery_status (tracking_number, order_number, status, "
                    "delivery_date, pickup_date) VALUES (?, ?, ?, ?, ?)",
                    (trk, num, status, delivery_date, pickup_date))
    con.commit()


class FakeClock:
    """Monotonic clock we advance by hand. 🔴 A budget test that SLEEPS is a slow test that
    proves the sleep, not the budget."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakePP:
    """ParcelPanel stand-in. Records every order it was ASKED for — the resume evidence."""

    def __init__(self, *, throttle: set[str] | None = None, deliver: set[str] | None = None,
                 clock: FakeClock | None = None, seconds_per_call: float = 1.0,
                 boom: set[str] | None = None) -> None:
        self.asked: list[str] = []
        self.throttle = throttle or set()
        self.deliver = deliver or set()
        self.boom = boom or set()
        self.clock = clock
        self.seconds_per_call = seconds_per_call

    def get_order_tracking(self, order_number: str = "", stats=None):  # noqa: ARG002
        if self.clock is not None:
            self.clock.t += self.seconds_per_call
        if order_number in self.throttle:
            raise PPThrottled(429, 3)          # (status, attempts) — the limiter's own shape
        if order_number in self.boom:
            raise RuntimeError("PP exploded")
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


@pytest.fixture
def wire(tmp_path, monkeypatch):
    """Point `run_pp_sync` at an in-memory DB, a fake PP, and a scratch settings/log file.

    Everything patched here is an EDGE of the function (the DB opener, the PP client, the
    settings file, the invoice writer's upsert). The scheduling behaviour under test is never
    stubbed.
    """
    def _mk(con: sqlite3.Connection, pp: FakePP, *, lock_busy: bool = False):
        appdata = tmp_path / "AppyHour"
        appdata.mkdir(parents=True, exist_ok=True)
        (appdata / "gel_calc_shopify_settings.json").write_text(
            json.dumps({"parcel_panel_api_key": "TEST-KEY"}), encoding="utf-8")
        monkeypatch.setattr(daily_shipping_sync, "APPDATA", appdata)
        monkeypatch.setattr(ahpaths, "db_path", lambda: tmp_path / "shipping.db")

        proxy = _NoCloseConn(con)

        def _connect(path=None, **kw):
            if lock_busy:
                raise ahdb.DBWriterBusy("held by another process")
            return proxy

        monkeypatch.setattr(ahdb, "connect", _connect)
        monkeypatch.setattr(ahdb, "connect_ro", lambda path=None, **kw: proxy)
        monkeypatch.setattr(parcel_panel, "ParcelPanelClient", lambda key: pp)

        def _store(conn, parcels):
            """The invoice writer's schema belongs to `shipping_invoice_db`; here it is a plain
            upsert, so this suite pins THIS module's scheduling and never depends on a file
            carrying another session's uncommitted work."""
            conn.executemany(
                "INSERT INTO delivery_status (tracking_number, order_number, status, "
                "delivery_date, pickup_date) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tracking_number) DO UPDATE SET status=excluded.status, "
                "delivery_date=excluded.delivery_date, pickup_date=excluded.pickup_date",
                [(p["tracking_number"], p["order_number"], p["status"], p["delivery_date"],
                  p["pickup_date"]) for p in parcels])

        monkeypatch.setattr(kdb, "store_delivery_status", _store)
        monkeypatch.setattr(daily_shipping_sync, "PP_FLUSH_EVERY", 5)   # same boundaries, smaller
        return tmp_path / "run.log"
    return _mk


# ── 1. a budget exit preserves commits, and the NEXT run advances ────────────

def test_budget_exit_banks_rows_and_the_next_run_asks_a_DISJOINT_set(wire):
    """🔴 THE 80-HOUR STALL IN ONE ASSERTION, for the second poller.

    Under the old inline query run 2 asked exactly what run 1 asked. Here it must ask what run 1
    did NOT reach, and the union of the two runs must cover the whole queue.
    """
    con = _db()
    for i in range(20):
        _order(con, f"{200000 + i}", days_ago=3)

    clock1 = FakeClock()
    pp1 = FakePP(clock=clock1, seconds_per_call=1.0)
    lf = wire(con, pp1)
    monkeyed_budget(6)
    daily_shipping_sync.run_pp_sync(lf, clock=clock1)

    assert pp1.asked, "run 1 banked nothing"
    assert len(pp1.asked) < 20, "run 1 should have stopped on its budget"

    clock2 = FakeClock()
    pp2 = FakePP(clock=clock2, seconds_per_call=1.0)
    wire(con, pp2)
    daily_shipping_sync.run_pp_sync(lf, clock=clock2)

    assert set(pp1.asked).isdisjoint(pp2.asked), (
        "run 2 re-asked orders run 1 already polled — this IS the non-resumption bug")


def test_repeated_bounded_runs_drain_the_whole_queue(wire):
    """Resumption is only real if repeated bounded runs REACH THE END. Attrition is not enough."""
    con = _db()
    for i in range(12):
        _order(con, f"{300000 + i}", days_ago=3)

    asked_all: list[str] = []
    monkeyed_budget(5)
    for _ in range(4):
        clock = FakeClock()
        pp = FakePP(clock=clock, seconds_per_call=1.0)
        lf = wire(con, pp)
        daily_shipping_sync.run_pp_sync(lf, clock=clock)
        asked_all += pp.asked

    assert sorted(set(asked_all)) == sorted(f"{300000 + i}" for i in range(12))
    assert len(asked_all) == len(set(asked_all)), "an order was polled twice inside one drain"


def test_the_cursor_survives_an_epilogue_that_blows_up(wire, monkeypatch):
    """🔴 THE DEFECT A BUDGET WOULD OTHERWISE REINTRODUCE, in its exact old shape.

    `run_pp_sync` used to bank `record_attempts` in the SAME `try:` as `aged_out_sweep`, once,
    after the whole loop. So a sweep that raised took every attempt with it — no cursor for
    anything polled — and the next run re-asked the identical orders while the delivery rows
    still landed and the log still said the queue had been polled. Attempts now ride the flush,
    in the same transaction as the parcels, so the epilogue cannot lose them.
    """
    con = _db()
    for i in range(20):
        _order(con, f"{400000 + i}", days_ago=3)

    def _boom(*a, **kw):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(delivery_window, "aged_out_sweep", _boom)

    clock = FakeClock()
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert len(pp.asked) == 20
    stamped = {r[0] for r in con.execute(
        "SELECT order_number FROM delivery_poll_attempts WHERE attempts > 0")}
    assert stamped == set(pp.asked), (
        f"the epilogue failure ate the resume cursor: asked {len(pp.asked)}, "
        f"stamped {len(stamped)} — the next run would re-ask what this one just polled")
    assert "aged_out sweep FAILED" in lf.read_text(encoding="utf-8")


def test_a_flush_that_cannot_take_the_lock_holds_the_parcels_AND_the_attempts(wire,
                                                                             monkeypatch):
    """🔴 Attempts are banked in the SAME transaction as the parcels, never separately.

    An attempt counted for an order whose answer was not durably stored inflates the counter
    `aged_out_sweep` gates on, and an inflated counter retires a LIVE box early. So a lock loss
    holds BOTH: deferral, not loss, and never half of each.
    """
    con = _db()
    for i in range(5):
        _order(con, f"{410000 + i}", days_ago=3)

    clock = FakeClock()
    pp = FakePP(deliver={"410000"}, clock=clock)
    lf = wire(con, pp)

    wired_connect = ahdb.connect        # the fixture's stub, NOT the real opener
    calls = {"n": 0}

    def _flaky(path=None, **kw):
        calls["n"] += 1
        if calls["n"] > 1:          # 1st call is the schema/index step; every flush fails
            raise ahdb.DBWriterBusy("held by another process")
        return wired_connect(path, **kw)

    monkeypatch.setattr(ahdb, "connect", _flaky)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert pp.asked, "nothing was polled"
    assert con.execute("SELECT COUNT(*) FROM delivery_poll_attempts").fetchone()[0] == 0, (
        "attempts were banked for a batch whose parcels never landed")
    assert "PP DB-LOCK BUSY" in lf.read_text(encoding="utf-8")


# ── 2. old work is not starved by newly arriving orders ─────────────────────

def test_the_oldest_never_asked_order_is_polled_FIRST_even_against_a_flood_of_new_ones(wire):
    """🔴 THE TEXT-SORT DEFECT. `50241` (2025) vs `101334` (today): as text `1` < `5`, so the old
    `SELECT DISTINCT` put every new six-digit order ahead of it and the queue's tail starved."""
    con = _db()
    _order(con, "50241", days_ago=473)             # the live DB's actual oldest, 2025-05-22
    _order(con, "94080", days_ago=400)
    for i in range(30):                            # a flood of today's orders
        _order(con, f"{101300 + i}", days_ago=0)

    clock = FakeClock()
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    lf = wire(con, pp)
    monkeyed_budget(3)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert pp.asked[0] == "50241", f"oldest work starved; asked {pp.asked[:5]}"
    assert pp.asked[1] == "94080"


def test_an_ancient_undelivered_order_is_still_selectable_at_any_age(wire):
    """🔴 The 604: age must never end an order's life. This leg is the AGE-UNBOUNDED one, so a
    473-day-old undelivered box is still in its work list."""
    con = _db()
    _order(con, "50241", days_ago=473, status="in_transit")
    clock = FakeClock()
    pp = FakePP(clock=clock)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)
    assert pp.asked == ["50241"]


# ── 3. the predicate decisions, measured on the live DB and pinned here ─────

def test_a_delivered_order_missing_its_pickup_date_is_NOT_re_polled(wire):
    """🔴 THE DROPPED `pickup_date IS NULL` RE-PULL.

    The old predicate re-selected any row missing a `pickup_date`. Measured read-only on the
    canonical DB 2026-09-08: all **28,398** rows with a `delivery_date` and no `pickup_date`
    carry `status='delivered'`, and the work list is **2,796 orders with the clause and 2,796
    without** — it selected nothing. Re-asking PP after it has answered "delivered" cannot
    conjure a pickup event it never sent; that is the ask-forever shape the backoff exists to
    kill. If a pickup gap ever needs closing it is a `delivery_window` change plus an invoice-POD
    backfill, NEVER a second predicate here.
    """
    con = _db()
    _order(con, "700001", days_ago=10, status="delivered",
           delivery_date="2026-08-30", pickup_date=None)
    clock = FakeClock()
    pp = FakePP(clock=clock)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)
    assert pp.asked == [], "a delivered order was re-polled to chase a pickup date"


def test_an_order_delivered_under_a_DIFFERENT_tracking_number_leaves_the_queue(wire):
    """🔴 THE JOIN-KEY NARROWING, and why it is the fix rather than a loss.

    The old join was `ds.tracking_number = f.tracking_number`, so an order whose fulfilment
    tracking differs from the delivery_status tracking (99 such tracking numbers on the live DB;
    the OnTrac→LaserShip label switch is the known mechanism) could NEVER match and was re-polled
    forever. Measured: the canonical order-level join drops **87** such orders and adds **0**, and
    all 87 carry a landed order-level `delivery_date`.
    """
    con = _db()
    _order(con, "800001", days_ago=20, tracking="OLD-LABEL-1")
    con.execute("INSERT INTO delivery_status (tracking_number, order_number, status, "
                "delivery_date) VALUES ('NEW-LABEL-1', '800001', 'delivered', '2026-08-25')")
    con.commit()

    clock = FakeClock()
    pp = FakePP(clock=clock)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)
    assert pp.asked == [], "an already-delivered order was polled forever on a stale label"


@pytest.mark.parametrize("status", list(delivery_window.TERMINAL_STATUSES))
def test_a_terminal_order_is_never_polled(wire, status):
    con = _db()
    _order(con, "900001", days_ago=200, status=status)
    clock = FakeClock()
    pp = FakePP(clock=clock)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)
    assert pp.asked == []


# ── 4. throttling: deferral, never a busy-retry, never an attempt ───────────

def test_a_throttle_storm_stops_the_run_instead_of_spinning_the_whole_queue(wire):
    """🔴 `except PPThrottled: pass` IS A BUSY-RETRY. With the limiter refusing everything the
    loop walks the entire queue at full speed doing no work and burns the budget on refusals."""
    con = _db()
    nums = [f"{110000 + i}" for i in range(200)]
    for n in nums:
        _order(con, n, days_ago=3)

    clock = FakeClock()
    pp = FakePP(throttle=set(nums), clock=clock, seconds_per_call=0.0)
    lf = wire(con, pp)
    rc = daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert rc == 1, "a throttle storm is an outage, not a slow day"
    text = lf.read_text(encoding="utf-8")
    assert "THROTTLE STORM" in text
    assert "stopping at order" in text
    streak = backfill_sync.THROTTLE_ABORT_STREAK
    # It stops at the first flush boundary at or past the streak, never after the whole list.
    assert streak <= 200, "fixture too small to exercise the streak"
    assert "200/200" not in text, "the run walked the entire queue while being refused"


def test_a_throttled_order_banks_no_attempt_so_an_outage_cannot_retire_a_live_box(wire):
    """🔴 P13. A refusal is UNKNOWN, not tried. Counting it would let one bad afternoon push a
    day's live shipments over the `aged_out` attempt gate."""
    con = _db()
    _order(con, "120001", days_ago=3)
    _order(con, "120002", days_ago=3)
    clock = FakeClock()
    pp = FakePP(throttle={"120001"}, clock=clock)
    lf = wire(con, pp)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    rows = dict(con.execute("SELECT order_number, attempts FROM delivery_poll_attempts"))
    assert "120001" not in rows, "a throttled order banked an attempt"
    assert rows.get("120002") == 1


# ── 5. the epilogue always runs ─────────────────────────────────────────────

def test_the_aged_out_sweep_still_runs_when_the_run_stops_on_its_budget(wire):
    """🔴 THE REASON THE BUDGET IS SOFT AND BREAKS AT A FLUSH BOUNDARY. `aged_out_sweep` is the
    only live writer of the terminal state; a poll that skips it can only GROW the tail it exists
    to shrink."""
    con = _db()
    old = "130001"
    _order(con, old, days_ago=delivery_window.DELIVERY_MAX_AGE_DAYS + 40)
    delivery_window.record_attempts(con, [old] * delivery_window.DELIVERY_MAX_ATTEMPTS)
    con.execute("UPDATE delivery_poll_attempts SET last_attempt_at = datetime('now','-40 days')")
    for i in range(20):
        _order(con, f"{140000 + i}", days_ago=3)
    con.commit()

    clock = FakeClock()
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    lf = wire(con, pp)
    monkeyed_budget(4)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert len(pp.asked) < 21, "the run did not stop on its budget"
    status = con.execute("SELECT status FROM delivery_status WHERE order_number = ?",
                         (old,)).fetchone()
    assert status and status[0] == "aged_out", "the sweep was skipped by the budget exit"


def test_the_request_cap_bounds_a_run_even_when_time_is_free(wire):
    """🔴 A RATE CAP AND A TIME CAP ARE BOTH REQUIRED. A fast PP day would otherwise let one run
    empty the queue at full tilt against a limit shared per-KEY with other callers."""
    con = _db()
    for i in range(40):
        _order(con, f"{150000 + i}", days_ago=3)

    clock = FakeClock()
    pp = FakePP(clock=clock, seconds_per_call=0.0)      # time never advances
    lf = wire(con, pp)
    monkeyed_requests(10)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert 0 < len(pp.asked) <= 15, f"request cap ignored; asked {len(pp.asked)}"
    assert "request-budget" in lf.read_text(encoding="utf-8")


# ── 6. an honest report of what is LEFT, and honest denominators ────────────

def test_the_log_states_what_is_LEFT_not_only_what_landed(wire):
    """🔴 `written` alone cannot tell 800-of-800 from 800-of-2,576, and for ~80 hours it did
    not. This leg has no heartbeat stamp, so the log is where `remaining` has to appear."""
    con = _db()
    for i in range(20):
        _order(con, f"{160000 + i}", days_ago=3)

    clock = FakeClock()
    pp = FakePP(clock=clock, seconds_per_call=1.0)
    lf = wire(con, pp)
    monkeyed_budget(6)
    daily_shipping_sync.run_pp_sync(lf, clock=clock)

    text = lf.read_text(encoding="utf-8")
    assert "complete=False" in text
    assert "remaining_due=" in text and "oldest_due_days=" in text
    assert "unresolved=" in text


def test_a_lock_busy_schema_step_reports_UNTOUCHED_never_a_drained_queue(wire):
    """🔴 Contention is not a dead feed and not an empty queue. Nothing was polled; say so."""
    con = _db()
    _order(con, "170001", days_ago=3)
    clock = FakeClock()
    pp = FakePP(clock=clock)
    lf = wire(con, pp, lock_busy=True)
    rc = daily_shipping_sync.run_pp_sync(lf, clock=clock)

    assert rc == 1
    text = lf.read_text(encoding="utf-8")
    assert "UNTOUCHED" in text and "NOT a dead feed" in text
    assert pp.asked == []


def test_the_error_rate_is_divided_by_what_the_run_REACHED(wire):
    """🔴 THE SELF-VERIFYING DENOMINATOR. `errors < len(orders) * 0.1` against a queue the run
    never reached lets a run that failed on everything it touched report success."""
    con = _db()
    # Oldest first, so the run reaches these ten in a known order.
    nums = [f"{180000 + i}" for i in range(200)]
    for i, n in enumerate(nums):
        _order(con, n, days_ago=300 - i)

    clock = FakeClock()
    # Of the first ten the budget allows, nine raise and one delivers — so rows DO land and the
    # dead-feed guard cannot be what fails the run. errors=9 over polled=10 is a 90% error rate;
    # over the untouched 200-order due list it reads as 4.5% and passes.
    pp = FakePP(boom=set(nums[:9]), deliver={nums[9]}, clock=clock, seconds_per_call=0.0)
    lf = wire(con, pp)
    monkeyed_requests(10)
    rc = daily_shipping_sync.run_pp_sync(lf, clock=clock)

    text = lf.read_text(encoding="utf-8")
    assert "written=1" in text, "the dead-feed guard, not the error rate, decided this run"
    assert rc == 1, "a 90%-error run passed because it divided by the queue it never reached"


# ── helpers that tune the module-level budgets for one test ─────────────────

@pytest.fixture(autouse=True)
def _restore_budgets():
    orig = (daily_shipping_sync.PP_BUDGET_S, daily_shipping_sync.PP_MAX_REQUESTS)
    yield
    daily_shipping_sync.PP_BUDGET_S, daily_shipping_sync.PP_MAX_REQUESTS = orig


def monkeyed_budget(seconds: float) -> None:
    daily_shipping_sync.PP_BUDGET_S = seconds


def monkeyed_requests(n: int) -> None:
    daily_shipping_sync.PP_MAX_REQUESTS = n
