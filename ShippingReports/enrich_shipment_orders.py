"""Enrich shipments DB with Shopify order_id, order_name, and _SHIP_<Mon> tag.

Pulls Shopify fulfilled orders, joins fulfillments[].tracking_number(s) back to
shipments table, populates order_id / order_name / ship_tag.

Adds the columns + indexes if missing (safe to run on pre-migration DB).

Universal transit rule applies elsewhere — this script does NOT compute transit.
It only attaches order identity + the canonical _SHIP_<Mon> cohort tag straight
from Shopify (replaces ship_date-derived cohort logic which breaks for Veho).

Usage:
    python enrich_shipment_orders.py [--days 180] [--limit 0]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import requests

DB = r"C:\Users\Work\Claude Projects\AppyHour\ShippingReports\output\shipments.db"
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"

SHIP_TAG_RE = re.compile(r"^_SHIP_[A-Z][a-z]{2}(?:_\d+)?$")


def load_shopify_creds():
    with open(SETTINGS) as f:
        s = json.load(f)
    store = s["shopify_store_url"]
    token = s["shopify_access_token"]
    if not store or not token:
        raise SystemExit("shopify_store_url / shopify_access_token missing")
    return store, token


def ensure_schema(conn):
    """Add order_id / order_name / ship_tag + indexes if absent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()}
    for col in ("order_id", "order_name", "ship_tag"):
        if col not in cols:
            conn.execute(f"ALTER TABLE shipments ADD COLUMN {col} TEXT")
            print(f"  + added column shipments.{col}")
    for idx, expr in (
        ("idx_order_id", "shipments(order_id)"),
        ("idx_order_name", "shipments(order_name)"),
        ("idx_ship_tag", "shipments(ship_tag)"),
        ("idx_tracking", "shipments(tracking)"),
    ):
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {expr}")
    conn.commit()


def extract_ship_tag(tags_field):
    """Pick the _SHIP_<Mon> tag from a Shopify tags string."""
    if not tags_field:
        return None
    for raw in tags_field.split(","):
        t = raw.strip()
        if t.startswith("_SHIP_") and SHIP_TAG_RE.match(t):
            return t
    # fallback: any _SHIP_ prefix even if format unusual
    for raw in tags_field.split(","):
        t = raw.strip()
        if t.startswith("_SHIP_"):
            return t
    return None


def fetch_orders(store, token, after_iso):
    """Yield fulfilled orders since after_iso. Cursor pagination via Link header."""
    url = f"https://{store}.myshopify.com/admin/api/2026-04/orders.json"
    params = {
        "status": "any",
        "fulfillment_status": "fulfilled",
        "limit": 250,
        "updated_at_min": after_iso,
        "fields": "id,name,tags,fulfillments",
    }
    headers = {"X-Shopify-Access-Token": token}
    page = 0
    while url:
        page += 1
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            print(f"  ERR page {page}: {r.status_code} {r.text[:200]}", flush=True)
            break
        orders = r.json().get("orders", [])
        print(f"  page {page}: {len(orders)} orders", flush=True)
        for o in orders:
            yield o
        url = None
        params = None
        link = r.headers.get("Link", "") or ""
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    url = m.group(1)
                    break
        time.sleep(0.4)


def build_tracking_rows(orders_iter):
    """Yield (tracking, order_id, order_name, ship_tag) tuples."""
    seen = 0
    for o in orders_iter:
        oid = str(o.get("id") or "")
        oname = o.get("name") or ""
        ship_tag = extract_ship_tag(o.get("tags", ""))
        if not oid:
            continue
        for f in o.get("fulfillments", []):
            tracks = list(f.get("tracking_numbers") or [])
            tn = f.get("tracking_number")
            if tn and tn not in tracks:
                tracks.append(tn)
            for t in tracks:
                if not t:
                    continue
                seen += 1
                yield (t.strip(), oid, oname, ship_tag)
        if seen and seen % 5000 == 0:
            print(f"    accumulated {seen} tracking rows", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180,
                    help="Shopify lookback (updated_at_min). Default 180d covers ~6mo backfill.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap order count for smoke testing (0=no cap).")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    store, token = load_shopify_creds()

    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    before = conn.execute(
        "SELECT COUNT(*) FROM shipments WHERE order_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    print(f"DB: {total} shipments, {before} already have order_id")

    after_iso = (datetime.now() - timedelta(days=args.days)).isoformat()
    print(f"\nFetching Shopify fulfilled orders updated since {after_iso}...")

    orders_iter = fetch_orders(store, token, after_iso)
    if args.limit:
        def capped(it, n):
            for i, x in enumerate(it):
                if i >= n:
                    return
                yield x
        orders_iter = capped(orders_iter, args.limit)

    rows = list(build_tracking_rows(orders_iter))
    print(f"\n{len(rows)} (tracking, order) pairs from Shopify")
    if not rows:
        print("Nothing to enrich.")
        return

    # Stage in temp table, then UPDATE — much faster than per-row UPDATE.
    conn.execute("DROP TABLE IF EXISTS _enrich_stage")
    conn.execute("""
        CREATE TABLE _enrich_stage (
            tracking   TEXT PRIMARY KEY,
            order_id   TEXT,
            order_name TEXT,
            ship_tag   TEXT
        )
    """)
    # Dedup on tracking — last write wins (rare collisions across carriers).
    conn.executemany(
        "INSERT OR REPLACE INTO _enrich_stage VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()

    print("Updating shipments...")
    cur = conn.execute("""
        UPDATE shipments
           SET order_id   = (SELECT order_id   FROM _enrich_stage s WHERE s.tracking = shipments.tracking),
               order_name = (SELECT order_name FROM _enrich_stage s WHERE s.tracking = shipments.tracking),
               ship_tag   = (SELECT ship_tag   FROM _enrich_stage s WHERE s.tracking = shipments.tracking)
         WHERE tracking IN (SELECT tracking FROM _enrich_stage)
    """)
    updated = cur.rowcount
    conn.execute("DROP TABLE _enrich_stage")
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM shipments WHERE order_id IS NOT NULL"
    ).fetchone()[0]
    coverage = (after / total * 100) if total else 0.0

    # Recent-row coverage check (last 30 days of ship_date)
    cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
    recent_total = conn.execute(
        "SELECT COUNT(*) FROM shipments WHERE ship_date >= ?", (cutoff,)
    ).fetchone()[0]
    recent_with_order = conn.execute(
        "SELECT COUNT(*) FROM shipments WHERE ship_date >= ? AND order_id IS NOT NULL",
        (cutoff,),
    ).fetchone()[0]
    recent_pct = (recent_with_order / recent_total * 100) if recent_total else 0.0

    print(f"\nUpdated rows touched: {updated}")
    print(f"order_id coverage: {after}/{total} ({coverage:.1f}%)")
    print(f"order_id coverage (last 30d ship_date): {recent_with_order}/{recent_total} ({recent_pct:.1f}%)")

    by_carrier = conn.execute("""
        SELECT carrier,
               SUM(CASE WHEN order_id IS NOT NULL THEN 1 ELSE 0 END) AS hit,
               COUNT(*)                                              AS tot
          FROM shipments
         WHERE ship_date >= ?
         GROUP BY carrier
         ORDER BY tot DESC
    """, (cutoff,)).fetchall()
    print("\nLast 30d coverage by carrier:")
    for c, hit, tot in by_carrier:
        pct = (hit / tot * 100) if tot else 0.0
        print(f"  {c}: {hit}/{tot} ({pct:.0f}%)")

    conn.close()


if __name__ == "__main__":
    main()
