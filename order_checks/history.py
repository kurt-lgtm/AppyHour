"""Index the deep-history bulk export into SQLite for check 7.

  python -m order_checks.history build      (streams hist_deep_raw.jsonl -> order_history.db)
  python -m order_checks.history stats

Check 7 needs two questions answered fast, per customer:
  * has this customer EVER received SKU X?  (the swap-substitute constraint)
  * what was in their previous N orders?    (the repeat constraint)

The raw export is 440 MB of JSONL where each order row is followed by its line-item
rows linked by __parentId, so neither question is answerable without an index.

🔴 currentQuantity, never quantity -- a removed line must not count as "received".
🔴 Keyed on the Shopify CUSTOMER ID, never email: email is blank or inconsistent on a
   meaningful share of accounts (Dan's CONTEXT.md, and confirmed here -- an email
   lookup for the #176919 customer returned zero rows).
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys

RAW = r"C:\Users\Work\Claude Projects\_outputs\cache\hist_deep_raw.jsonl"
DB = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history.db"
CHILD = ("AC-", "MT-", "CH-", "TR-")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_gid   TEXT PRIMARY KEY,
    name        TEXT,
    customer_id TEXT,
    created_at  TEXT,
    tags        TEXT
);
CREATE TABLE IF NOT EXISTS items (
    order_gid   TEXT,
    sku         TEXT,
    qty         INTEGER,
    paid        INTEGER          -- discountedUnitPrice > 0
);
CREATE INDEX IF NOT EXISTS ix_orders_cust ON orders(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_orders_name ON orders(name);
CREATE INDEX IF NOT EXISTS ix_items_order ON items(order_gid);
CREATE INDEX IF NOT EXISTS ix_items_sku   ON items(sku);
"""


def build(raw=RAW, db=DB):
    if not os.path.exists(raw):
        raise SystemExit(f"missing {raw} - run: python -m order_checks.bulk_history poll")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    o_rows, i_rows, n = [], [], 0
    with open(raw, encoding="utf8") as fh:
        for line in fh:
            n += 1
            d = json.loads(line)
            parent = d.get("__parentId")
            if parent is None:
                o_rows.append((d["id"], d.get("name"),
                               ((d.get("customer") or {}).get("id") or ""),
                               d.get("createdAt"), ",".join(d.get("tags") or [])))
            else:
                sku = (d.get("sku") or "").strip()
                if not sku:
                    continue                       # blank-SKU wrapper, not a child
                amt = float((d.get("discountedUnitPriceSet") or {})
                            .get("shopMoney", {}).get("amount") or 0)
                i_rows.append((parent, sku, d.get("currentQuantity") or 0, 1 if amt > 0 else 0))
            if len(o_rows) >= 50000:
                con.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?)", o_rows)
                o_rows = []
            if len(i_rows) >= 200000:
                con.executemany("INSERT INTO items VALUES (?,?,?,?)", i_rows)
                i_rows = []
            if n % 500000 == 0:
                con.commit()
                print(f"  {n:,} lines...", flush=True)
    if o_rows:
        con.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?)", o_rows)
    if i_rows:
        con.executemany("INSERT INTO items VALUES (?,?,?,?)", i_rows)
    con.commit()
    stats(con)
    con.close()


def stats(con=None):
    close = con is None
    con = con or sqlite3.connect(DB)
    q = lambda s: con.execute(s).fetchone()[0]          # noqa: E731
    print(f"  orders      {q('SELECT COUNT(*) FROM orders'):>10,}")
    print(f"  customers   {q('SELECT COUNT(DISTINCT customer_id) FROM orders'):>10,}")
    print(f"  item rows   {q('SELECT COUNT(*) FROM items'):>10,}")
    print(f"  distinct SKU{q('SELECT COUNT(DISTINCT sku) FROM items'):>10,}")
    print(f"  date range  {q('SELECT MIN(created_at) FROM orders')} .. "
          f"{q('SELECT MAX(created_at) FROM orders')}")
    if close:
        con.close()


def ever_received(con, customer_id, skus):
    """-> set of SKUs this customer has EVER received (currentQuantity > 0)."""
    if not skus:
        return set()
    marks = ",".join("?" * len(skus))
    rows = con.execute(
        f"""SELECT DISTINCT i.sku FROM items i JOIN orders o ON o.order_gid = i.order_gid
            WHERE o.customer_id = ? AND i.qty > 0 AND i.sku IN ({marks})""",
        [customer_id, *skus]).fetchall()
    return {r[0] for r in rows}


def previous_orders(con, customer_id, before_iso, limit=4):
    """-> [(name, created_at, [skus])] for the customer's N most recent PRIOR orders."""
    rows = con.execute(
        """SELECT order_gid, name, created_at FROM orders
           WHERE customer_id = ? AND created_at < ? ORDER BY created_at DESC LIMIT ?""",
        (customer_id, before_iso, limit)).fetchall()
    out = []
    for gid, name, created in rows:
        skus = [r[0] for r in con.execute(
            "SELECT sku FROM items WHERE order_gid = ? AND qty > 0", (gid,))]
        out.append((name, created, skus))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    {"build": build, "stats": stats}[cmd]()
