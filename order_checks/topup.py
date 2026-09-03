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
from .recharge_gate import api_event_to_row, classify, touches_contents

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
    """Map every customer we hold with no Recharge id, by sweeping the Recharge roster.

    🔴 NOT a per-customer lookup. `/customers?external_customer_id=<shopify id>` is NOT a
    server-side filter -- it returns the unfiltered first page and mapped 1 of 1,150 on
    2026-09-01 while exiting 0. A full cursor sweep (48,386 records, ~3 min) matched on
    each record's OWN external_customer_id mapped 408 in one pass. The per-customer path
    is gone so it cannot be reached by accident.

    🔴 FAILS LOUD when the sweep maps ~nothing against a real gap list. A run that maps
    1 of 1,000 is broken, not "1 new customer" -- and must never exit 0 quietly.

    Customers still unmapped after a full sweep have NO Recharge account (48,386-roster
    check). For them "no portal login" is TRUE, not unknown -- a first-order or one-time
    buyer cannot log into a portal they have no account on. Print that split, since a
    bare "N unmapped" reads as N blind spots.
    """
    gaps = {r[0] for r in con.execute(
        "SELECT shop FROM cust WHERE rc IS NULL AND shop IS NOT NULL AND shop <> ''")}
    if verbose:
        print(f"  Recharge map: {len(gaps):,} customers with no recharge id; sweeping roster")
    if not gaps:
        return 0
    cursor, seen, found, page = None, 0, 0, 0
    while True:
        page += 1
        params = {"cursor": cursor, "limit": 250} if cursor else {"limit": 250}
        d = rc_get("/customers", params)
        batch = d.get("customers", [])
        if not batch:
            break
        for c in batch:
            ext = c.get("external_customer_id") or {}
            shop = str(ext.get("ecommerce") if isinstance(ext, dict) else ext or "")
            seen += 1
            if shop in gaps:
                con.execute("UPDATE cust SET rc = ?, email = COALESCE(NULLIF(email,''), ?) "
                            "WHERE shop = ? AND rc IS NULL",
                            (str(c["id"]), (c.get("email") or "").lower(), shop))
                found += 1
        con.commit()
        if verbose and page % 40 == 0:
            print(f"    page {page}: {seen:,} seen, {found} mapped", flush=True)
        cursor = d.get("next_cursor")
        if not cursor:                    # absent cursor, NOT a short page
            break
        time.sleep(0.3)
    left = len(gaps) - found
    print(f"  Recharge map: roster {seen:,} · newly mapped {found} · "
          f"{left:,} have NO Recharge account (login impossible, not unknown)")
    # 🔴 a full sweep that saw the roster but mapped nothing against a big gap list means
    # the join key changed shape, not that nobody is a subscriber
    if seen > 1000 and len(gaps) > 100 and found == 0:
        raise SystemExit("recharge_customers: swept the whole roster and mapped 0 of "
                         f"{len(gaps):,} -- the external_customer_id join is broken, "
                         "refusing to report this as success")
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
        for raw in batch:
            n += 1
            e = api_event_to_row(raw)        # 🔴 API shape != CSV shape; see recharge_gate
            verb = e["verb"]
            touch = touches_contents(verb, e["changes"], e["description"])
            if verb != "login" and not touch:
                continue
            cid = con.execute("SELECT id FROM cust WHERE rc = ?",
                              (e["customer_id"],)).fetchone()
            if not cid:
                continue                     # no Shopify order here -> cannot gate a swap
            ts = int(datetime.strptime(e["created_at"], "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=timezone.utc).timestamp())
            kind = {"human": 0, "api": 1, "automated": 2}[classify(e["source"])]
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

    The API is the DEFAULT path and is proven comprehensive for logins: on the window
    2026-08-28..09-01, /events?verb=login returned 5,386 events against 5,386 in the
    Recharge export, with exact event_id overlap and 0 either-only (probe 2026-09-03).
    So a topup run at least every 7 days needs no export at all. The export is for
    closing a gap WIDER than the 7-day created_at_min cap, nothing else.

    Not yet proven via API: the contents-touching events the CUSTOMIZE half reads. The
    probe checked verb=login only.

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
