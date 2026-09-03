"""login_gate.protected: the unmapped count is scoped to SWAP-ELIGIBLE orders.

🔴 "157 of 191 unmapped" was reported as the guardrail being blind on 82% of
RMFG_20260901. 155 of those were Subscription First Orders -- excluded by check7 before
the guardrail ever runs. A count that mixes the two teaches people to ignore the 🔴,
which is the same failure class as a verify that cries wolf.
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
from order_checks.login_gate import protected  # noqa: E402

GID = "gid://shopify/Customer/{}"
RECUR = ["Subscription Recurring Order"]
FIRST = ["Subscription First Order"]


def ts(iso):
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture()
def con():
    path = os.path.join(tempfile.mkdtemp(), "c.db")
    d = sqlite3.connect(path)
    d.executescript(hc.SCHEMA)
    d.executescript(hc.INDEXES)
    # 1: mapped, has history.  2: UNMAPPED, has history (the dangerous one).
    # 3: UNMAPPED, no history (a first order).  4: mapped, has history, logged in.
    d.executemany("INSERT INTO cust(id, shop, rc) VALUES (?,?,?)",
                  [(1, "111", "900"), (2, "222", None), (3, "333", None), (4, "444", "904")])
    d.executemany("INSERT INTO ord(id, cust, name, ts, tags) VALUES (?,?,?,?,?)", [
        (10, 1, "#10", ts("2026-05-01T00:00:00Z"), None),
        (20, 2, "#20", ts("2026-05-01T00:00:00Z"), None),
        (40, 4, "#40", ts("2026-05-01T00:00:00Z"), None),
    ])
    d.execute("INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman) "
              "VALUES (4, ?, 1, 0, 0, NULL, NULL, 0)", (ts("2026-07-01T00:00:00Z"),))
    d.execute("INSERT INTO meta VALUES ('ev_floor', ?)", (str(ts("2026-04-01T00:00:00Z")),))
    d.commit()
    yield d
    d.close()


def _order(shop, tags, created="2026-08-01T00:00:00Z"):
    return {"customer": {"id": GID.format(shop), "email": f"{shop}@x"},
            "tags": tags, "createdAt": created}


def test_unmapped_recurring_with_history_is_flagged_red(con, capsys):
    orders = {"20": _order("222", RECUR)}
    protected(orders, con)
    out = capsys.readouterr().out
    assert "🔴 1 swap-ELIGIBLE" in out and "#20" in out


def test_unmapped_first_order_is_not_flagged_red(con, capsys):
    """A first order has no prior order -- check7 excludes it before the guardrail."""
    orders = {"30": _order("333", FIRST)}
    protected(orders, con)
    out = capsys.readouterr().out
    assert "🔴" not in out
    assert "1 unmapped but outside swap scope" in out


def test_unmapped_non_recurring_with_history_is_not_flagged_red(con, capsys):
    orders = {"20": _order("222", ["Subscription First Order"])}
    protected(orders, con)
    assert "🔴" not in capsys.readouterr().out


def test_absent_customer_counted_separately(con, capsys):
    orders = {"99": _order("999", RECUR)}
    protected(orders, con)
    out = capsys.readouterr().out
    assert "1 not in the store at all" in out and "🔴" not in out


def test_mapped_and_logged_in_is_protected(con):
    orders = {"40": _order("444", RECUR)}
    assert "40" in protected(orders, con, verbose=False)


def test_mapped_no_login_is_not_protected(con):
    orders = {"10": _order("111", RECUR)}
    assert protected(orders, con, verbose=False) == {}
