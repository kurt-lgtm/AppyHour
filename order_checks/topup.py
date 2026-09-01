"""Top up the compact store from Shopify + Recharge, from its own watermark forward.

  python -m order_checks.topup            # orders + recharge customers + events
  python -m order_checks.topup --orders   # one half only
  python -m order_checks.topup --events

Why this exists: the store is seeded from a bulk pull, and a cohort cut AFTER that pull
has customers the store has never seen. On RMFG_20260901, 156 of 190 customers were
absent and `logged_in_since` returned '' for every one of them -- which reads as "did not
log in" and is really "never heard of them". The login guardrail was blind on 82% of the
cohort. A stale store does not fail; it silently clears people for swapping.

🔴 GOTCHAS:

  * `oi` is append-only per order. An order re-pulled after we edited its line items
    (our own swaps do that) must have its rows DELETED before re-insert, or its contents
    double-count. `_put_order` does that; do not INSERT around it.
  * Recharge `/events` rejects `created_at_min` older than 7 DAYS with a 422. A gap wider
    than that cannot be closed here -- it needs a fresh full export through
    recharge_gate.build. This module refuses rather than silently fetching a short window
    and reporting success.
  * A customer with no Recharge mapping is UNKNOWN, not clear. Topping up orders without
    topping up the Recharge side leaves the guardrail just as blind, so the default runs
    both halves.
  * Shopify `created_at:>=` on the watermark re-pulls the boundary order. That is
    deliberate and safe because writes are idempotent -- do not "optimise" it to `>`.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from .customer_map import _get as rc_get
from .fetch_gql import _auth
from .history_compact import DB
from .recharge_gate import classify, touches_contents

PAGE = """query($q:String!,$after:String){orders(first:100, query:$q, after:$after){
  pageInfo{hasNextPage endCursor}
  edges{node{ id name createdAt tags customer{id email}
    lineItems(first:200){edges{node{sku currentQuantity}}}}}}}"""

EVENT_WINDOW_DAYS = 7          # Recharge's server-side cap on created_at_min


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def watermark(con, key="max_order_ts"):
    r = con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return int(r[0]) if r and r[0] else None


def _cust_id(con, gid, email=None):
    """Internal cust id for a Shopify gid, creating the row if new."""
    shop = str(gid).rsplit("/", 1)[-1]
    r = con.execute("SELECT id FROM cust WHERE shop = ?", (shop,)).fetchone()
    if r:
        return r[0]
    con.execute("INSERT INTO cust(shop, email) VALUES (?,?)", (shop, (email or "").lower()))
    return con.execute("SELECT id FROM cust WHERE shop = ?", (shop,)).fetchone()[0]


def _sku_id(con, code):
    r = con.execute("SELECT id FROM sku WHERE code = ?", (code,)).fetchone()
    if r:
        return r[0]
    con.execute("INSERT INTO sku(code) VALUES (?)", (code,))
    return con.execute("SELECT id FROM sku WHERE code = ?", (code,)).fetchone()[0]


def _put_order(con, node):
    """Idempotent write of one order + its items. Returns (is_new, n_items)."""
    oid = int(node["id"].rsplit("/", 1)[-1])
    cust = node.get("customer") or {}
    cid = _cust_id(con, cust.get("id"), cust.get("email")) if cust.get("id") else None
    ts = int(datetime.strptime(node["createdAt"], "%Y-%m-%dT%H:%M:%SZ")
             .replace(tzinfo=timezone.utc).timestamp())
    new = con.execute("SELECT 1 FROM ord WHERE id = ?", (oid,)).fetchone() is None
    con.execute("INSERT OR REPLACE INTO ord(id, cust, name, ts, tags) VALUES (?,?,?,?,?)",
                (oid, cid, node["name"], ts, ",".join(node.get("tags") or []) or None))
    # 🔴 clear first: a re-pulled order whose lines we edited would otherwise double-count
    con.execute("DELETE FROM oi WHERE ord = ?", (oid,))
    agg = {}
    for e in node["lineItems"]["edges"]:
        li = e["node"]
        q = li.get("currentQuantity") or 0
        s = (li.get("sku") or "").strip()
        if s and q > 0:
            agg[s] = agg.get(s, 0) + q
    for s, q in agg.items():
        con.execute("INSERT INTO oi(ord, sku, qty) VALUES (?,?,?)", (oid, _sku_id(con, s), q))
    return new, len(agg)


def orders(con, since_iso=None, verbose=True):
    url, hdr = _auth()
    import requests
    since = since_iso or _iso(watermark(con))
    q = f"created_at:>={since}"          # >= on purpose: re-pulling the boundary is safe
    after, n_new, n_upd, page = None, 0, 0, 0
    if verbose:
        print(f"  Shopify orders since {since}")
    while True:
        page += 1
        j = {}
        for a in range(6):
            r = requests.post(url, headers={**hdr, "Content-Type": "application/json"},
                              json={"query": PAGE, "variables": {"q": q, "after": after}},
                              timeout=60)
            j = r.json()
            if j.get("data", {}).get("orders"):
                break
            time.sleep(2 * (a + 1))
        else:
            raise SystemExit(f"shopify page {page} failed: {str(j)[:300]}")
        d = j["data"]["orders"]
        for e in d["edges"]:
            new, _ = _put_order(con, e["node"])
            n_new += new
            n_upd += not new
        con.commit()
        if verbose and page % 5 == 0:
            print(f"    page {page}: {n_new} new, {n_upd} updated", flush=True)
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    mx = con.execute("SELECT MAX(ts) FROM ord").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO meta VALUES ('max_order_ts', ?)", (str(mx),))
    con.commit()
    print(f"  orders: {n_new} new, {n_upd} updated   watermark -> {_iso(mx)}")
    return n_new, n_upd


def recharge_customers(con, verbose=True):
    """Map any customer we hold with no Recharge id. Only the gaps, not a full re-pull."""
    gaps = [r[0] for r in con.execute(
        "SELECT shop FROM cust WHERE rc IS NULL AND shop IS NOT NULL AND shop <> ''")]
    if verbose:
        print(f"  Recharge map: {len(gaps)} customers with no recharge id")
    found = 0
    for i, shop in enumerate(gaps, 1):
        try:
            d = rc_get("/customers", {"external_customer_id": shop, "limit": 1})
        except Exception:                                          # noqa: BLE001
            continue
        for c in d.get("customers", []):
            con.execute("UPDATE cust SET rc = ?, email = COALESCE(NULLIF(email,''), ?) "
                        "WHERE shop = ?",
                        (str(c["id"]), (c.get("email") or "").lower(), shop))
            found += 1
        if i % 50 == 0:
            con.commit()
            if verbose:
                print(f"    {i}/{len(gaps)}, {found} mapped", flush=True)
        time.sleep(0.25)
    con.commit()
    print(f"  Recharge map: {found} of {len(gaps)} resolved"
          + (f"   🔴 {len(gaps) - found} still UNKNOWN (not clear)" if found < len(gaps) else ""))
    return found


def events(con, verbose=True):
    """Append login + contents-touching events since the store's newest event.

    🔴 Refuses a gap wider than Recharge's 7-day `created_at_min` cap instead of pulling
    a short window and calling it done -- a partial event pull reads as "no login".
    """
    last = con.execute("SELECT MAX(ts) FROM ev").fetchone()[0]
    since = datetime.fromtimestamp(last, timezone.utc)
    gap = datetime.now(timezone.utc) - since
    if gap > timedelta(days=EVENT_WINDOW_DAYS):
        print(f"  🔴 event gap is {gap.days}d, past Recharge's {EVENT_WINDOW_DAYS}d "
              f"created_at_min cap. The API CANNOT close it.")
        print("     Take a fresh events export and run recharge_gate.build, then re-seed.")
        return 0
    if verbose:
        print(f"  Recharge events since {since:%Y-%m-%d %H:%M} (gap {gap.days}d)")
    cursor, n, kept, page = None, 0, 0, 0
    while True:
        page += 1
        params = ({"cursor": cursor, "limit": 250} if cursor else
                  {"created_at_min": since.strftime("%Y-%m-%dT%H:%M:%S"), "limit": 250})
        d = rc_get("/events", params)
        batch = d.get("events", [])
        if not batch:
            break
        for e in batch:
            n += 1
            verb = e.get("verb") or ""
            touch = touches_contents(verb, e.get("changes"), e.get("description"))
            if verb != "login" and not touch:
                continue
            cid = con.execute("SELECT id FROM cust WHERE rc = ?",
                              (str(e.get("customer_id")),)).fetchone()
            if not cid:
                continue                     # no Shopify order here -> cannot gate a swap
            ts = int(datetime.strptime(e["created_at"][:19], "%Y-%m-%dT%H:%M:%S")
                     .replace(tzinfo=timezone.utc).timestamp())
            kind = {"human": 0, "api": 1, "automated": 2}[classify(e.get("source"))]
            con.execute(
                "INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman) "
                "VALUES (?,?,?,?,?,NULL,NULL,0)",
                (cid[0], ts, 1 if verb == "login" else 0, 1 if touch else 0, kind))
            kept += 1
        con.commit()
        if verbose and page % 10 == 0:
            print(f"    page {page}: {n:,} scanned, {kept} kept", flush=True)
        cursor = d.get("next_cursor")
        if not cursor:                       # absent cursor, NOT a short page
            break
        time.sleep(0.3)
    # 🔴 recompute nearhuman for the new api-origin rows against ALL human events we hold
    con.execute("""UPDATE ev SET nearhuman = 1
                   WHERE kind = 1 AND nearhuman = 0 AND EXISTS (
                     SELECT 1 FROM ev h WHERE h.cust = ev.cust AND h.kind = 0
                       AND ABS(h.ts - ev.ts) <= 300)""")
    con.commit()
    print(f"  events: {n:,} scanned, {kept} kept (login + contents-touch)")
    return kept


def events_csv(con, path, verbose=True):
    """Append login + contents-touching events from a Recharge events EXPORT.

    Preferred over the API path whenever an export exists: no 7-day cap, and it is the
    same file recharge_gate.build consumes.

    🔴 The export is a DELTA, not a superset -- the 2026-09-01 one starts at 08-28, four
    months after the store's floor. Rebuilding `ev` from it would DESTROY the history the
    guardrail depends on. Rows are appended strictly ABOVE the store's newest event, so a
    re-run is idempotent and an overlapping window does not double-insert.
    """
    import csv as _csv
    _csv.field_size_limit(10 ** 9)
    last = con.execute("SELECT MAX(ts) FROM ev").fetchone()[0] or 0
    if verbose:
        print(f"  events export {path}")
        print(f"    appending strictly after {_iso(last)}")
    n = kept = skipped_old = nocust = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in _csv.DictReader(fh):
            n += 1
            verb = r.get("verb") or ""
            touch = touches_contents(verb, r.get("changes"), r.get("description"))
            if verb != "login" and not touch:
                continue
            ts = int(datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=timezone.utc).timestamp())
            if ts <= last:
                skipped_old += 1
                continue
            cid = con.execute("SELECT id FROM cust WHERE rc = ?",
                              (str(r["customer_id"]),)).fetchone()
            if not cid:
                nocust += 1
                continue
            kind = {"human": 0, "api": 1, "automated": 2}[classify(r.get("source"))]
            con.execute(
                "INSERT INTO ev(cust, ts, login, touch, kind, verb, src, nearhuman) "
                "VALUES (?,?,?,?,?,NULL,NULL,0)",
                (cid[0], ts, 1 if verb == "login" else 0, 1 if touch else 0, kind))
            kept += 1
    con.execute("""UPDATE ev SET nearhuman = 1
                   WHERE kind = 1 AND nearhuman = 0 AND EXISTS (
                     SELECT 1 FROM ev h WHERE h.cust = ev.cust AND h.kind = 0
                       AND ABS(h.ts - ev.ts) <= 300)""")
    con.commit()
    print(f"  events: {n:,} scanned, {kept:,} appended"
          f"   ({skipped_old:,} at/below watermark, {nocust:,} with no Shopify order here)")
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks.topup")
    ap.add_argument("--orders", action="store_true")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--events-csv", metavar="PATH",
                    help="Recharge events export; preferred over the API (no 7-day cap)")
    ap.add_argument("--since", help="override the order watermark, ISO")
    a = ap.parse_args(argv)
    both = not (a.orders or a.events or a.events_csv)
    con = sqlite3.connect(DB)
    print(f"\n=== topup  (store watermark {_iso(watermark(con))}) ===")
    if a.orders or both:
        orders(con, a.since)
        recharge_customers(con)
    if a.events_csv:
        events_csv(con, a.events_csv)
    elif a.events or both:
        events(con)
    con.close()
    from .history_compact import stats
    stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
