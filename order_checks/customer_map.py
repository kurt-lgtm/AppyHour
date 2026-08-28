"""Shopify <-> Recharge customer id map, for joining the events export to orders.

  python -m order_checks.customer_map build
  python -m order_checks.customer_map stats

🔴 The Recharge events export keys on the RECHARGE customer id. Joining it to Shopify
by EMAIL does not work -- an email lookup for the #176919 customer returned zero rows
while a lookup by id returned 28 events, and email is blank or inconsistent on a
meaningful share of accounts. Every events join must go through this map.

Recharge API rules (recharge-api skill, all mandatory):
  * X-Recharge-Version: 2021-11 -- without it v1 caps at 250 rows with NO next_cursor
    and silently truncates.
  * CURSOR pagination -- page=N is silently ignored and loops on page 1 forever.
    A cursor request may carry ONLY cursor + limit; filters go on the first call.
  * timeout=30 with retry/backoff, never a longer timeout (that hides an outage).
  * Token from %APPDATA%/AppyHour/inventory_reorder_settings.json, never hardcoded.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import time

import requests

DB = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history.db"
BASE = "https://api.rechargeapps.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rc_customers (
    recharge_id  TEXT PRIMARY KEY,
    shopify_id   TEXT,
    email        TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_rc_shopify ON rc_customers(shopify_id);
CREATE INDEX IF NOT EXISTS ix_rc_email   ON rc_customers(email);
"""


def _token():
    p = os.path.expandvars(r"%APPDATA%\AppyHour\inventory_reorder_settings.json")
    return json.load(open(p, encoding="utf8"))["recharge_api_token"]


def _headers():
    return {"X-Recharge-Access-Token": _token(),
            "X-Recharge-Version": "2021-11",
            "Accept": "application/json"}


def _get(path, params, retries=5):
    last = None
    for a in range(retries):
        try:
            r = requests.get(BASE + path, headers=_headers(), params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:       # transient slow window
            last = e
            time.sleep(2 * (a + 1))
        except Exception as e:                          # noqa: BLE001
            last = e
            time.sleep(2)
    raise last


def build(db=DB):
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    cursor, n, page = None, 0, 0
    while True:
        page += 1
        params = {"cursor": cursor, "limit": 250} if cursor else {"limit": 250}
        d = _get("/customers", params)
        batch = d.get("customers", [])
        if not batch:
            break
        rows = []
        for c in batch:
            ext = c.get("external_customer_id") or {}
            shop = ext.get("ecommerce") if isinstance(ext, dict) else ext
            rows.append((str(c["id"]), str(shop) if shop else "",
                         (c.get("email") or "").lower(), c.get("created_at")))
        con.executemany("INSERT OR REPLACE INTO rc_customers VALUES (?,?,?,?)", rows)
        con.commit()
        n += len(rows)
        if page % 20 == 0:
            print(f"  page {page}, {n:,} customers", flush=True)
        cursor = d.get("next_cursor")
        if not cursor:                    # break on absent cursor, NOT on a short page
            break
        time.sleep(0.5)
    print(f"DONE {n:,} Recharge customers")
    stats(con)
    con.close()


def stats(con=None):
    close = con is None
    con = con or sqlite3.connect(DB)
    q = lambda s: con.execute(s).fetchone()[0]          # noqa: E731
    print(f"  recharge customers      {q('SELECT COUNT(*) FROM rc_customers'):>9,}")
    print(f"  with a shopify id       {q(chr(34)+chr(34)) if False else q('SELECT COUNT(*) FROM rc_customers WHERE shopify_id != {}'.format(chr(39)+chr(39))):>9,}")
    print(f"  shopify customers seen  {q('SELECT COUNT(DISTINCT customer_id) FROM orders'):>9,}")
    matched = q("""SELECT COUNT(DISTINCT o.customer_id) FROM orders o
                   JOIN rc_customers r
                     ON r.shopify_id = REPLACE(o.customer_id, 'gid://shopify/Customer/', '')""")
    print(f"  joinable to order history {matched:>7,}")
    if close:
        con.close()


def recharge_id(con, shopify_customer_gid):
    """Shopify customer gid -> Recharge customer id, or None."""
    num = str(shopify_customer_gid).rsplit("/", 1)[-1]
    row = con.execute("SELECT recharge_id FROM rc_customers WHERE shopify_id = ?",
                      (num,)).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    {"build": build, "stats": stats}[cmd]()
