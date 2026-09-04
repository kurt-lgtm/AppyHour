"""Local store of ROUTING-relevant customer profile tags (Fixed_Route, Military, pins).

  python -m order_checks.customer_tags build     # pull from Shopify into the compact store
  python -m order_checks.customer_tags stats
  python -m order_checks.customer_tags show <order|email>

Why local: the profile pin is the AUTHORITY for a Fixed_Route customer, but it lives on the
CUSTOMER, not the order. Reading it per-run meant every check re-fetched customer tags
through the order query, and a cohort that does not happen to include a pinned customer
told you nothing about who is pinned. This table answers "who is pinned, and to what"
without Shopify.

🔴 GOTCHAS:

  * The "Customer Specific Routing" Flow fires ONLY on order_created. An order that already
    existed when the pin was set never re-triggers, so the profile says pinned while the
    live order routes on the default carrier. 3 of 4 pinned customers in _SHIP_2026-08-31
    were exactly this. The pin is applied to the CUSTOMER **and** their OPEN ORDERS --
    profile alone is not the job ([[fixed-route-apply-customer-and-open-orders]]).
  * The profile is AUTHORITATIVE and the order takes its pin, APPENDED never overwritten.
  * A bare `!ANY FedEx - <hub>_AHB!` pin means RMFG picks the FedEx service; never rewrite
    it to a specific service, and never onto an OnTrac-dead zip
    ([[any-rows-rmfg-defaults-fedex]], [[fixed-route-pin-use-any-fedex]]).
  * MILITARY profiles must never land on OnTrac (Kurt 2026-08-13) -- tracked here too,
    because a military customer is not always Fixed_Route-pinned.
  * 🔴 A tag pull is a SNAPSHOT. `built_at` is stored and printed; a pin set after the last
    build is invisible, which reads as "not pinned". Rebuild before trusting a clean run.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "AppyHourMCP"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "AppyHourMCP", "tools"))

from order_edit import shopify_graphql  # noqa: E402

from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

from .checks import FIXED_ROUTE_TAG, MILITARY_TAG, route_tags  # noqa: E402
from .history_compact import DB  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS cust_tag (
    shop     TEXT PRIMARY KEY,      -- bare numeric Shopify customer id
    email    TEXT,
    tags     TEXT,                  -- full comma string, as Shopify holds it
    pinned   INTEGER,               -- Fixed_Route on the profile
    military INTEGER,
    route    TEXT                   -- the routing tag(s) on the PROFILE
);
CREATE INDEX IF NOT EXISTS ix_cust_tag_pin ON cust_tag(pinned);
"""

Q = """query($q:String!,$after:String){customers(first:250,query:$q,after:$after){
  pageInfo{hasNextPage endCursor}
  nodes{id email tags}}}"""


def build(db=DB, verbose=True):
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    base, headers = get_shopify_auth()
    seen = 0
    # one query per tag: Shopify's customer search has no OR across tag: terms
    for term in (f"tag:{FIXED_ROUTE_TAG}", f"tag:{MILITARY_TAG}"):
        after = None
        while True:
            d = shopify_graphql(base, headers, Q, {"q": term, "after": after})["customers"]
            for n in d["nodes"]:
                tags = ", ".join(n.get("tags") or [])
                low = tags.lower()
                con.execute(
                    "INSERT OR REPLACE INTO cust_tag(shop, email, tags, pinned, military, route) "
                    "VALUES (?,?,?,?,?,?)",
                    (n["id"].rsplit("/", 1)[-1], (n.get("email") or "").lower(), tags,
                     int(FIXED_ROUTE_TAG in low), int(MILITARY_TAG in low),
                     ", ".join(route_tags(tags))))
                seen += 1
            con.commit()
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = d["pageInfo"]["endCursor"]
            time.sleep(0.2)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('cust_tag_built_at', ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    con.commit()
    if verbose:
        stats(db)
    con.close()
    return seen


def profile(con, shopify_customer_gid):
    """-> {tags, pinned, military, route} for a customer, or None if not in the store.

    🔴 None means "not pulled", NOT "not pinned". Callers must not read an absent row as
    clearance -- that is the unmapped-is-not-clear class.
    """
    if not shopify_customer_gid:
        return None
    r = con.execute("SELECT tags, pinned, military, route FROM cust_tag WHERE shop = ?",
                    (str(shopify_customer_gid).rsplit("/", 1)[-1],)).fetchone()
    return {"tags": r[0], "pinned": bool(r[1]), "military": bool(r[2]), "route": r[3]} if r else None


def built_at(con):
    r = con.execute("SELECT v FROM meta WHERE k = 'cust_tag_built_at'").fetchone()
    return r[0] if r else None


def stats(db=DB):
    c = sqlite3.connect(db)
    try:
        n = c.execute("SELECT COUNT(*) FROM cust_tag").fetchone()[0]
    except sqlite3.OperationalError:
        print("  cust_tag: not built yet -- run `python -m order_checks.customer_tags build`")
        return
    pin = c.execute("SELECT COUNT(*) FROM cust_tag WHERE pinned = 1").fetchone()[0]
    mil = c.execute("SELECT COUNT(*) FROM cust_tag WHERE military = 1").fetchone()[0]
    noroute = c.execute("SELECT COUNT(*) FROM cust_tag WHERE pinned = 1 AND "
                        "(route IS NULL OR route = '')").fetchone()[0]
    print(f"  cust_tag {n:,} customers   Fixed_Route {pin}   Military {mil}   built {built_at(c)}")
    if noroute:
        print(f"  🔴 {noroute} pinned with NO routing tag on the profile -- nothing to pin to")
    for route, k in c.execute("SELECT route, COUNT(*) FROM cust_tag WHERE pinned = 1 "
                              "GROUP BY route ORDER BY 2 DESC"):
        print(f"     {k:>4}  {route or '(none)'}")
    c.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks.customer_tags")
    ap.add_argument("cmd", choices=["build", "stats", "show"])
    ap.add_argument("who", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build()
    elif a.cmd == "stats":
        stats()
    else:
        c = sqlite3.connect(DB)
        for r in c.execute("SELECT shop, email, pinned, military, route, tags FROM cust_tag "
                           "WHERE shop = ? OR email = ?", (a.who, (a.who or "").lower())):
            print(f"  {r[0]}  {r[1]}\n    pinned={bool(r[2])} military={bool(r[3])} "
                  f"route={r[4]!r}\n    tags: {r[5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
