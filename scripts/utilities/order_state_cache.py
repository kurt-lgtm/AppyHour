"""Durable per-order Shopify-state cache (sqlite) for the Matrixify matrix workflow.

Never call Shopify twice for the same order. Keyed by order number. Stores:
  box      : SKUs currently on the order (currentQuantity>0)          MUTABLE
  removed  : SKUs removed/refunded (quantity>0 & currentQuantity==0)  MUTABLE
  ever     : box ∪ full order history (currentQuantity>0)             ~append-only
  last     : most-recent PRIOR order's truffle cheese (CH-SOT/CH-MONT/BOTH/None)  IMMUTABLE
  email    : customer email
  fetched_at: iso timestamp

🔴 STALENESS: `box`/`removed` change when an order is edited THIS week — pass
refresh=True (or refresh_current=True in get_many) to re-pull those. `last`/`ever`
are historical and safe to trust forever. Default = reuse cache.

Usage:
    from order_state_cache import OrderStateCache
    c = OrderStateCache()                      # default DB under _outputs/cache
    st = c.get(order, fetch_fn)                # fetch_fn(order)->dict if miss
    # fetch_fn must return {"box":[...],"removed":[...],"ever":[...],"last":str|None,"email":str|None}
"""
from __future__ import annotations
import json, sqlite3, datetime
from pathlib import Path

DEFAULT_DB = Path(r"C:\Users\Work\Claude Projects\_outputs\cache\matrix_order_state.db")


class OrderStateCache:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS order_state ("
            "order_id TEXT PRIMARY KEY, box TEXT, removed TEXT, ever TEXT, "
            "last TEXT, email TEXT, fetched_at TEXT)"
        )
        self.db.commit()

    def get(self, order: str, fetch_fn, refresh: bool = False) -> dict:
        order = str(order)
        if not refresh:
            row = self.db.execute(
                "SELECT box,removed,ever,last,email FROM order_state WHERE order_id=?", (order,)
            ).fetchone()
            if row:
                return {"box": json.loads(row[0]), "removed": json.loads(row[1]),
                        "ever": json.loads(row[2]), "last": row[3], "email": row[4]}
        st = fetch_fn(order)
        self.db.execute(
            "INSERT OR REPLACE INTO order_state VALUES (?,?,?,?,?,?,?)",
            (order, json.dumps(st.get("box", [])), json.dumps(st.get("removed", [])),
             json.dumps(st.get("ever", [])), st.get("last"), st.get("email"),
             datetime.datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()
        return st

    def import_json(self, json_path: Path | str) -> int:
        """One-time migrate a scratch JSON cache ({order: state}) into the DB."""
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        now = datetime.datetime.now().isoformat(timespec="seconds")
        n = 0
        for o, st in data.items():
            self.db.execute(
                "INSERT OR REPLACE INTO order_state VALUES (?,?,?,?,?,?,?)",
                (str(o), json.dumps(st.get("box", [])), json.dumps(st.get("removed", [])),
                 json.dumps(st.get("ever", [])), st.get("last"), st.get("email"), now))
            n += 1
        self.db.commit()
        return n

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM order_state").fetchone()[0]


if __name__ == "__main__":
    import sys
    c = OrderStateCache()
    if len(sys.argv) > 2 and sys.argv[1] == "import":
        print("imported", c.import_json(sys.argv[2]), "-> total", c.count(), "at", c.path)
    else:
        print("rows:", c.count(), "db:", c.path)
