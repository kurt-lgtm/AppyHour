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

from .history_compact import DB, ev_floor, known, logged_in_since, previous_orders, recharge_id


def export_floor(con):
    """Earliest indexed event -- the date before which a login is INVISIBLE."""
    return ev_floor(con)


def protected(orders, con=None, verbose=True):
    """-> {order_id: (email, login_at)} for orders whose customer logged in since their
    previous order. Those customers are PROTECTED and must not be rotation-swapped.
    """
    close = con is None
    con = con or sqlite3.connect(DB)
    floor = export_floor(con)
    out = {}
    # 🔴 Two different "unmapped"s, and only one of them can reach a swap list.
    #   blind_eligible: recurring order WITH prior history whose customer has no Recharge
    #     id -- check7 would consider it and the login half cannot see it. This is the
    #     number that matters and the only one that gets a 🔴.
    #   blind_other: first orders / no-history / non-recurring -- excluded by check7
    #     before the guardrail runs. Counting these as blind spots is how "blind on 82%"
    #     was reported for RMFG_20260901 when 155 of the 157 were first orders (F3).
    blind_eligible, blind_other, absent = [], 0, 0
    for oid, o in orders.items():
        gid = (o.get("customer") or {}).get("id")
        tags = o.get("tags") or []
        recurring = "Subscription Recurring Order" in tags
        if not gid or not known(con, gid):
            absent += 1
            continue
        prev = previous_orders(con, gid, o["createdAt"], 1)
        if recharge_id(con, gid) is None:
            if recurring and prev:
                blind_eligible.append(oid)
            else:
                blind_other += 1
            continue
        since = prev[0][1] if prev else floor
        hit = logged_in_since(con, gid, since)
        if hit:
            out[oid] = ((o.get("customer") or {}).get("email", ""), hit)
    if verbose:
        n = con.execute("SELECT COUNT(*) FROM ev WHERE login = 1").fetchone()[0]
        print(f"  login events indexed {n:,} since {floor}")
        print(f"  protected by login: {len(out)} of {len(orders)}")
        if blind_eligible:
            print(f"  🔴 {len(blind_eligible)} swap-ELIGIBLE orders have no Recharge id -- the "
                  f"login half cannot see them and they are UNKNOWN, not clear: "
                  f"{', '.join('#' + x for x in blind_eligible[:8])}"
                  + (" …" if len(blind_eligible) > 8 else ""))
        if blind_other or absent:
            print(f"     ({blind_other} unmapped but outside swap scope -- first order / no "
                  f"history / not recurring; {absent} not in the store at all)")
    if close:
        con.close()
    return out
