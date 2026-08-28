"""The LOGIN half of the login-OR-customize swap guardrail.

🔴 A customer is PROTECTED from a rotation swap if they logged in OR customized -- EITHER
one. Only the "neither" bucket is swappable. Running just the customize half looks like a
gate and is not one.

The burn this exists to stop, 2026-08-28: the wk0831 check-7 swap list was built with the
customize gate alone. A later login scan found **155 of 543** swapped orders had a customer
login after their previous order. By then the vF had been sent to RMFG and the boxes shipped
swapped. The scan must run BEFORE the list is built, never after.

Reads Recharge `verb='login'` events out of the indexed export (recharge_gate), joined on the
RECHARGE customer id via customer_map -- never on email, which returns zero rows for customers
that have 100+ events.

🔴 FLOOR: the export has a start date. A login before it is invisible, so an empty result is
a lower bound and NOT proof the customer stayed away. Absence of evidence is not clearance.
"""
from __future__ import annotations
import sqlite3

from .customer_map import recharge_id
from .history import DB, previous_orders


def export_floor(con):
    """Earliest event in the indexed export -- the date before which logins are invisible."""
    row = con.execute("SELECT MIN(created_at) FROM rc_events").fetchone()
    return row[0] if row else None


def logged_in_since(con, shopify_customer_gid, since_iso):
    """-> the login timestamp, or '' if none since `since_iso`."""
    rid = recharge_id(con, shopify_customer_gid) if shopify_customer_gid else None
    if not rid:
        return ""
    row = con.execute(
        """SELECT created_at FROM rc_events
           WHERE customer_id = ? AND verb = 'login' AND created_at >= ?
           ORDER BY created_at LIMIT 1""", (str(rid), since_iso)).fetchone()
    return row[0] if row else ""


def protected(orders, con=None, verbose=True):
    """-> {order_id: (email, login_at)} for orders whose customer logged in since their
    previous order. Those customers are PROTECTED and must not be rotation-swapped.
    """
    close = con is None
    con = con or sqlite3.connect(DB)
    floor = export_floor(con)
    out, unmapped = {}, 0
    for oid, o in orders.items():
        gid = (o.get("customer") or {}).get("id")
        if not gid or not recharge_id(con, gid):
            unmapped += 1
            continue
        prev = previous_orders(con, gid, o["createdAt"], 1)
        since = prev[0][1] if prev else floor
        hit = logged_in_since(con, gid, since)
        if hit:
            out[oid] = ((o.get("customer") or {}).get("email", ""), hit)
    if verbose:
        n = con.execute("SELECT COUNT(*) FROM rc_events WHERE verb='login'").fetchone()[0]
        print(f"  login events indexed {n:,} since {floor}")
        print(f"  protected by login: {len(out)} of {len(orders)}"
              f"   ({unmapped} unmappable to a Recharge id)")
        if unmapped:
            print("  🔴 an unmappable customer is UNKNOWN, not clear")
    if close:
        con.close()
    return out
