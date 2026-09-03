"""Charge-failed gate: a customer whose charge FAILED since their previous order is
excluded from rotation swaps.

🔴 Kurt 2026-09-03: "if any one are failed, that means we don't swap." A failed charge
means the box's footing is unsettled -- retry, re-cut, or no ship -- so it is out of the
swap pool outright. Two event shapes carry it: verb 'failed' on the charge object and
'failed-internal-only' on the subscription; both land in `ev` with failed=1.
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
from order_checks.topup import FAILED_VERBS  # noqa: E402

GID = "gid://shopify/Customer/{}"


def ts(iso):
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture()
def con():
    path = os.path.join(tempfile.mkdtemp(), "c.db")
    d = sqlite3.connect(path)
    d.executescript(hc.SCHEMA)
    d.executescript(hc.INDEXES)
    d.execute("INSERT INTO cust(id, shop, rc) VALUES (1, '111', '900')")
    d.execute("INSERT INTO ord(id, cust, name, ts, tags) VALUES (10, 1, '#10', ?, NULL)",
              (ts("2026-05-01T00:00:00Z"),))
    d.commit()
    yield d
    d.close()


def _fail(d, iso):
    d.execute("INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman, failed) "
              "VALUES (1, ?, 0, 0, 2, NULL, NULL, 0, 1)", (ts(iso),))
    d.commit()


def test_both_failed_verbs_are_recognised():
    assert "failed" in FAILED_VERBS and "failed-internal-only" in FAILED_VERBS


def test_failed_since_previous_order_is_found(con):
    _fail(con, "2026-06-01T00:00:00Z")
    assert hc.charge_failed_since(con, GID.format(111), "2026-05-01T00:00:00Z").startswith("2026-06-01")


def test_failed_before_previous_order_does_not_count(con):
    """The window starts at the previous order -- an old failure is not this box's problem."""
    _fail(con, "2026-04-01T00:00:00Z")
    assert hc.charge_failed_since(con, GID.format(111), "2026-05-01T00:00:00Z") == ""


def test_no_failure_is_empty(con):
    assert hc.charge_failed_since(con, GID.format(111), "2026-05-01T00:00:00Z") == ""


def test_unknown_customer_is_empty_not_error(con):
    assert hc.charge_failed_since(con, GID.format(999), "2026-05-01T00:00:00Z") == ""


def test_schema_default_is_not_failed(con):
    """A login row written without the column set must read as failed=0, not NULL."""
    con.execute("INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman) "
                "VALUES (1, ?, 1, 0, 0, NULL, NULL, 0)", (ts("2026-06-01T00:00:00Z"),))
    con.commit()
    assert con.execute("SELECT failed FROM ev WHERE login = 1").fetchone()[0] == 0
    assert hc.charge_failed_since(con, GID.format(111), "2026-05-01T00:00:00Z") == ""
