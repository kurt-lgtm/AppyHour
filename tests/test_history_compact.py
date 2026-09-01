"""Semantics tests for the compact history store, over a hand-built fixture.

`history_compact.verify` is a LIVE-DATA PARITY check, not a test: it needs the real store
and a real reference, and it only proves the compact answers match whatever the fat store
said. Three bugs in this file's subject shipped past it in one session because they were
SCHEMA-SEMANTICS errors, not parity errors:

  * events collapsed to a login/touch bit, dropping the `kind` the customize gate reads
  * `items` rolled up to (cust, sku), which cannot answer "what was in the previous N
    orders" -- it keeps only each SKU's LAST receipt, so check7's repeats fell 502 -> 5
  * verify itself compared only order NAMES, so it passed that broken store 400/400

Each of those is a few seconds to catch against a fixture where the right answer is known
by construction. That is what this file is for.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_checks import history_compact as hc  # noqa: E402

GID = "gid://shopify/Customer/{}"


def ts(iso):
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture()
def store():
    """A store with the full compact schema and known contents.

    Customer 1 (recharge 900): three orders, a SKU repeated across two of them.
    Customer 2 (recharge 901): one order, no events.
    Customer 3: in `cust` but with NO recharge id -- the unmapped case.
    """
    path = os.path.join(tempfile.mkdtemp(), "compact.db")
    d = sqlite3.connect(path)
    d.executescript(hc.SCHEMA)
    d.executescript(hc.INDEXES)

    d.executemany("INSERT INTO cust(id, shop, rc, email) VALUES (?,?,?,?)", [
        (1, "111", "900", "a@x.com"),
        (2, "222", "901", "b@x.com"),
        (3, "333", None, "c@x.com"),
    ])
    d.executemany("INSERT INTO sku(id, code) VALUES (?,?)", [
        (1, "CH-ALPHA"), (2, "CH-BETA"), (3, "AC-GAMMA"), (4, "MT-DELTA"),
    ])
    # customer 1: Jan, Apr, Aug. CH-ALPHA in Jan AND Aug -- the case a (cust, sku)
    # rollup destroys, because its LAST receipt is Aug.
    d.executemany("INSERT INTO ord(id, cust, name, ts, tags) VALUES (?,?,?,?,?)", [
        (10, 1, "#1001", ts("2026-01-10T12:00:00Z"), "Subscription Recurring Order"),
        (11, 1, "#1002", ts("2026-04-10T12:00:00Z"), "Subscription Recurring Order"),
        (12, 1, "#1003", ts("2026-08-10T12:00:00Z"), "Subscription Recurring Order"),
        (20, 2, "#2001", ts("2026-05-05T12:00:00Z"), None),
    ])
    d.executemany("INSERT INTO oi(ord, sku, qty) VALUES (?,?,?)", [
        (10, 1, 1), (10, 3, 1),          # Jan: ALPHA, GAMMA
        (11, 2, 1),                      # Apr: BETA
        (12, 1, 1), (12, 4, 2),          # Aug: ALPHA again, DELTA x2
        (20, 2, 1),
    ])
    d.execute("INSERT INTO meta VALUES ('ev_floor', ?)", (str(ts("2026-01-01T00:00:00Z")),))
    d.commit()
    yield path, d
    d.close()


def add_ev(d, cust, iso, login=0, touch=0, kind=hc.KIND["human"], nearhuman=0):
    d.execute("INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman) "
              "VALUES (?,?,?,?,?,NULL,NULL,?)", (cust, ts(iso), login, touch, kind, nearhuman))
    d.commit()


# --------------------------------------------------------------- previous_orders

def test_previous_orders_returns_that_orders_own_items(store):
    """🔴 The regression that cut check7's repeats from 502 to 5.

    A (cust, sku) rollup keeps only each SKU's LAST receipt, so CH-ALPHA -- received in
    January AND August -- vanishes from January's list. check7 asks exactly this question.
    """
    path, _ = store
    con = sqlite3.connect(path)
    prev = hc.previous_orders(con, GID.format(111), "2026-12-01T00:00:00Z", 4)
    by_name = {name: set(skus) for name, _, skus in prev}
    assert by_name["#1001"] == {"CH-ALPHA", "AC-GAMMA"}
    assert by_name["#1002"] == {"CH-BETA"}
    assert by_name["#1003"] == {"CH-ALPHA", "MT-DELTA"}


def test_previous_orders_is_strictly_before(store):
    """🔴 A day-resolution timestamp would include the order being checked against itself,
    making every customer look like they already own their own contents."""
    path, _ = store
    con = sqlite3.connect(path)
    names = [n for n, _, _ in hc.previous_orders(con, GID.format(111),
                                                 "2026-08-10T12:00:00Z", 4)]
    assert "#1003" not in names          # same instant as the boundary
    assert names == ["#1002", "#1001"]   # newest first


def test_previous_orders_respects_limit_and_order(store):
    path, _ = store
    con = sqlite3.connect(path)
    assert [n for n, _, _ in hc.previous_orders(con, GID.format(111),
                                                "2026-12-01T00:00:00Z", 2)] == ["#1003", "#1002"]


def test_previous_orders_unknown_customer_is_empty_not_error(store):
    path, _ = store
    assert hc.previous_orders(sqlite3.connect(path), GID.format(999), "2026-12-01T00:00:00Z") == []


# --------------------------------------------------------------- ever_received

def test_ever_received_spans_all_history_not_recent(store):
    path, _ = store
    con = sqlite3.connect(path)
    got = hc.ever_received(con, GID.format(111), ["CH-ALPHA", "CH-BETA", "MT-DELTA", "CH-NOPE"])
    assert got == {"CH-ALPHA", "CH-BETA", "MT-DELTA"}


def test_ever_received_is_per_customer(store):
    path, _ = store
    con = sqlite3.connect(path)
    assert hc.ever_received(con, GID.format(222), ["CH-ALPHA", "CH-BETA"]) == {"CH-BETA"}


def test_ever_received_empty_sku_list_short_circuits(store):
    path, _ = store
    assert hc.ever_received(sqlite3.connect(path), GID.format(111), []) == set()


# --------------------------------------------------------------- the customize gate

def test_customized_fires_on_human_touch(store):
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", touch=1, kind=hc.KIND["human"])
    was, why = hc.customized(sqlite3.connect(path), GID.format(111))
    assert was and "2026-06-01" in why


def test_customized_does_not_fire_on_api_alone(store):
    """🔴 api-origin alone is neither proof nor clearance -- it needs a nearby human."""
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", touch=1, kind=hc.KIND["api"], nearhuman=0)
    assert hc.customized(sqlite3.connect(path), GID.format(111))[0] is False


def test_customized_fires_on_api_with_nearby_human(store):
    """A portal edit runs as an api call, which is why the nearhuman flag exists."""
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", touch=1, kind=hc.KIND["api"], nearhuman=1)
    assert hc.customized(sqlite3.connect(path), GID.format(111))[0] is True


def test_customized_ignores_automated(store):
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", touch=1, kind=hc.KIND["automated"])
    assert hc.customized(sqlite3.connect(path), GID.format(111))[0] is False


def test_customized_ignores_a_login(store):
    """A login is not a contents change. Collapsing both to one bit conflates them."""
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", login=1, touch=0, kind=hc.KIND["human"])
    assert hc.customized(sqlite3.connect(path), GID.format(111))[0] is False


def test_customized_since_scopes_to_the_window(store):
    """🔴 Kurt 2026-09-01: the gate is for the SPECIFIC order. An ever-scoped test would
    protect every customer who has ever touched the portal and empty the swap list."""
    path, d = store
    add_ev(d, 1, "2026-02-01T10:00:00Z", touch=1, kind=hc.KIND["human"])
    con = sqlite3.connect(path)
    assert hc.customized(con, GID.format(111))[0] is True                          # ever
    assert hc.customized(con, GID.format(111), "2026-05-01T00:00:00Z")[0] is False  # since


# --------------------------------------------------------------- the login gate

def test_logged_in_since_finds_a_login_in_window(store):
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", login=1)
    assert hc.logged_in_since(sqlite3.connect(path), GID.format(111),
                              "2026-05-01T00:00:00Z").startswith("2026-06-01")


def test_logged_in_since_same_day_as_threshold(store):
    """🔴 THE 40-ORDER BUG. The old code compared '2026-06-01 10:00:00' against
    '2026-06-01T09:00:00Z' as TEXT; space is 0x20 and 'T' is 0x54, so every same-day login
    sorted below the threshold and read as absent. 1,208 protected reported vs 1,248 true."""
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", login=1)
    assert hc.logged_in_since(sqlite3.connect(path), GID.format(111),
                              "2026-06-01T09:00:00Z") != ""


def test_logged_in_since_excludes_before_window(store):
    path, d = store
    add_ev(d, 1, "2026-06-01T10:00:00Z", login=1)
    assert hc.logged_in_since(sqlite3.connect(path), GID.format(111),
                              "2026-07-01T00:00:00Z") == ""


def test_unknown_customer_is_distinguishable_from_no_logins(store):
    """🔴 Both return ''. A caller that cannot tell them apart treats 'never heard of them'
    as 'did not log in' -- which is how #177001 was wrongly cleared."""
    path, _ = store
    con = sqlite3.connect(path)
    assert hc.logged_in_since(con, GID.format(999), "2026-01-01T00:00:00Z") == ""
    assert hc.logged_in_since(con, GID.format(222), "2026-01-01T00:00:00Z") == ""
    assert hc.known(con, GID.format(999)) is False      # not in the store
    assert hc.known(con, GID.format(222)) is True       # in the store, simply no login


# --------------------------------------------------------------- the id map

def test_recharge_id_accepts_gid_or_bare(store):
    path, _ = store
    con = sqlite3.connect(path)
    assert hc.recharge_id(con, GID.format(111)) == "900"
    assert hc.recharge_id(con, "111") == "900"


def test_recharge_id_none_for_unmapped_customer_in_store(store):
    """Customer 3 is in `cust` with no recharge id -- known() true, recharge_id() None."""
    path, _ = store
    con = sqlite3.connect(path)
    assert hc.known(con, GID.format(333)) is True
    assert hc.recharge_id(con, GID.format(333)) is None


def test_sku_first_seen_is_the_earliest_appearance(store):
    path, _ = store
    fs = hc.sku_first_seen(sqlite3.connect(path))
    assert fs["CH-ALPHA"].startswith("2026-01-10")   # Jan, not the Aug repeat
    assert fs["MT-DELTA"].startswith("2026-08-10")


def test_ev_floor_reports_the_invisibility_boundary(store):
    path, _ = store
    assert hc.ev_floor(sqlite3.connect(path)).startswith("2026-01-01")


# --------------------------------------------------------------- cohort-scoped directives
# 🔴 A directive stated for one week silently applied to the next: HAVE_OVERRIDE
# "AC-KETT: 21" (for RMFG_20260831) was still overwriting the RMFG_20260901 count four
# days later, and DRAW_DOWN "AC-BLUCAR: 20" outlived the squeeze that motivated it.

def test_for_cohort_keeps_matching_and_drops_stale(capsys):
    from order_checks.check7 import for_cohort
    d = {"AC-KETT": (21, "RMFG_20260831"), "AC-OTHER": (5, "RMFG_20260901")}
    assert for_cohort(d, "RMFG_20260901", "T") == {"AC-OTHER": 5}


def test_for_cohort_announces_what_it_dropped(capsys):
    """Silently dropping is only half the fix -- a stale directive must be VISIBLE."""
    from order_checks.check7 import for_cohort
    for_cohort({"AC-KETT": (21, "RMFG_20260831")}, "RMFG_20260901", "HAVE_OVERRIDE")
    out = capsys.readouterr().out
    assert "DROPPED" in out and "AC-KETT" in out and "RMFG_20260831" in out


def test_for_cohort_refuses_an_untagged_directive(capsys):
    """An untagged entry has no expiry, which is the whole bug. Refuse, do not guess."""
    from order_checks.check7 import for_cohort
    assert for_cohort({"AC-KETT": 21}, "RMFG_20260901", "T") == {}
    assert "NO COHORT" in capsys.readouterr().out
