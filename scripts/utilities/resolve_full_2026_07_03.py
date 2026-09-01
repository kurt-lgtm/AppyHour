"""Full resolved sheet for the 2026-07-03 Matrixify import.

Rules (all targets are $0 in-box products, verified):
- CH-WWHO dupes (HHIGH) -> CH-HCGU + jam AC-BLBALS>AC-SRHUB>keep AC-BCM (dupe-safe, current box).
    collision (HCGU already in current box) -> CH-BLR unless customer had CH-BLR
    in their last 2 orders; else flag (left as CH-WWHO).
- CH-BLR / CH-SMG dupe group -> CH-YFARM (dupe-safe).
- CFPH overflow: only 215 CFPH in stock -> keep CFPH on first 215 orders (by #),
    convert the rest to AC-BLBALS (else AC-SRHUB, else keep CFPH), dupe-safe.
- AC dupes untouched. Affected orders in-sheet deduped.
READ-ONLY against Shopify. Writes full sheet + decision log.
"""
from __future__ import annotations

import csv
import re
import sys
import time
import json
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

TAG = "RMFG_20260703"
SRC = Path(r"C:\Users\Work\Downloads\matrixify-export-2026-07-03.csv")
DUPES = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-03_DUPES.csv")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-2026-07-03_FULL-RESOLVED.csv")
LOG = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-2026-07-03_FULL-RESOLVED_LOG.txt")

HCGU  = {"sku": "CH-HCGU",  "id": "10110685610264", "handle": "Honey Clover Gouda*"}
YFARM = {"sku": "CH-YFARM", "id": "10311218037016", "handle": "Young Farmdal*"}
BLR   = {"sku": "CH-BLR",   "id": "9843608813848",  "handle": "Baked Lemon Ricotta *"}
BLBALS = {"sku": "AC-BLBALS", "id": "9751422239000", "handle": "Blackberry Balsamic Jam"}
SRHUB  = {"sku": "AC-SRHUB",  "id": "9891502620952", "handle": "Strawberry Rhubarb Mini Jam*"}
JAM_PREF = [BLBALS, SRHUB]
CFPH_CAP = 215

SUBMIT = 'mutation($q:String!){bulkOperationRunQuery(query:$q){bulkOperation{id status} userErrors{message}}}'
POLL = 'query{currentBulkOperation{status url errorCode}}'
HIST_Q = """
query($q: String!) {
  orders(first: 50, query: $q, sortKey: CREATED_AT, reverse: true) {
    edges { node { name createdAt lineItems(first: 100) { edges { node { sku currentQuantity quantity } } } } }
  }
}
"""


def gql(base, headers, q, v=None):
    r = requests.post(f"{base}/graphql.json", headers=headers, json={"query": q, "variables": v or {}}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(d["errors"])
    return d["data"]


def li_skus(node):
    out = set()
    for e in node["lineItems"]["edges"]:
        n = e["node"]
        qty = n.get("currentQuantity")
        if qty is None:
            qty = n.get("quantity", 0)
        s = (n.get("sku") or "").strip()
        if s and qty and qty > 0:
            out.add(s)
    return out


def bulk_current_box(base, headers) -> dict[str, set]:
    inner = ('{ orders(query: "tag:%s") { edges { node { id name '
             "lineItems { edges { node { sku currentQuantity quantity } } } } } } }" % TAG)
    d = gql(base, headers, SUBMIT, {"q": inner})
    if d["bulkOperationRunQuery"]["userErrors"]:
        raise RuntimeError(d["bulkOperationRunQuery"]["userErrors"])
    url, sleep, deadline = None, 2.0, time.monotonic() + 1200
    while True:
        op = gql(base, headers, POLL)["currentBulkOperation"] or {}
        if op.get("status") == "COMPLETED":
            url = op.get("url"); break
        if op.get("status") in ("FAILED", "CANCELED"):
            raise RuntimeError(op.get("errorCode"))
        if time.monotonic() > deadline:
            raise RuntimeError("bulk timeout")
        time.sleep(sleep); sleep = min(sleep * 1.5, 30)
    box: dict[str, set] = {}
    idx: dict[str, str] = {}
    if not url:
        return box
    resp = requests.get(url, stream=True, timeout=180); resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        row = json.loads(line)
        pid = row.get("__parentId")
        if pid is None:
            dg = re.sub(r"\D", "", row.get("name", "")); idx[row["id"]] = dg; box[dg] = set()
        else:
            dg = idx.get(pid)
            if dg is None:
                continue
            qty = row.get("currentQuantity")
            if qty is None:
                qty = row.get("quantity", 0)
            s = (row.get("sku") or "").strip()
            if s and qty and qty > 0:
                box[dg].add(s)
    return box


def order_history(base, headers, order):
    """Return (ever_skus, last2_skus) for the customer who owns `order`.

    ever = all SKUs across the customer's orders; last2 = SKUs in their two
    most-recent orders (by created_at). Empty sets if email can't be resolved.
    """
    d = gql(base, headers,
            "query($q:String!){orders(first:1,query:$q){edges{node{customer{email}}}}}",
            {"q": f"name:#{order}"})
    ed = d["orders"]["edges"]
    email = (ed[0]["node"].get("customer") or {}).get("email") if ed else None
    if not email:
        return set(), set()
    hist = gql(base, headers, HIST_Q, {"q": f"email:{email}"})["orders"]["edges"]
    ever = set()
    for h in hist:
        ever |= li_skus(h["node"])
    last2 = set()
    for h in hist[:2]:
        last2 |= li_skus(h["node"])
    return ever, last2


def main():
    base, headers = get_shopify_auth()
    norm = lambda v: re.sub(r"\D", "", v or "")

    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f); fields = rdr.fieldnames; rows = list(rdr)
    name_col = next(c for c in fields if re.match(r"^(Name|Order)", c, re.I))

    with DUPES.open(encoding="utf-8-sig", newline="") as f:
        drows = list(csv.DictReader(f))
    wwho = sorted({norm(r[name_col]) for r in drows if r["child_sku"].strip() == "CH-WWHO"})
    blrsmg = sorted({norm(r[name_col]) for r in drows if r["child_sku"].strip() in ("CH-BLR", "CH-SMG")})

    print(f"bulk-reading tag:{TAG} ...")
    box = bulk_current_box(base, headers)
    print(f"current boxes: {len(box)}")

    # history for CH-BLR recency, only for HCGU-collision WWHO orders
    log = []
    cheese_dec, jam_dec = {}, {}
    for o in wwho:
        cur = box.get(o, set())
        ever, last2 = order_history(base, headers, o)
        if HCGU["sku"] not in ever:                 # never had HCGU -> give it
            cheese_dec[o] = HCGU
        elif BLR["sku"] not in last2 and BLR["sku"] not in cur:   # had HCGU; CH-BLR not in last 2 orders
            cheese_dec[o] = BLR
            log.append(f"#{o} had CH-HCGU -> CH-BLR (not in last 2 orders)")
        else:
            cheese_dec[o] = None                    # had HCGU AND CH-BLR recently -> flag
            log.append(f"#{o} had CH-HCGU AND CH-BLR in last 2 orders -> FLAG (kept CH-WWHO)")
        jam_dec[o] = next((j for j in JAM_PREF if j["sku"] not in cur), None)  # jam dupe-safe (current box)

    yfarm_dec = {}
    for o in blrsmg:
        yfarm_dec[o] = YFARM if YFARM["sku"] not in box.get(o, set()) else None
        if yfarm_dec[o] is None:
            log.append(f"#{o} already has CH-YFARM -> FLAG")

    # CFPH cap: order list (by #) that have a CFPH add row
    cfph_orders = sorted({norm(r[name_col]) for r in rows if r["child_sku"].strip() == "AC-CFPH"}, key=lambda x: int(x))
    keep_cfph = set(cfph_orders[:CFPH_CAP])
    cfph_dec = {}
    for o in cfph_orders:
        if o in keep_cfph:
            cfph_dec[o] = None  # keep CFPH
        else:
            cur = box.get(o, set())
            cfph_dec[o] = next((j for j in JAM_PREF if j["sku"] not in cur), None)  # None => keep CFPH (both present)

    # apply to full sheet
    out_rows = []
    affected = set(wwho) | set(blrsmg) | {o for o, v in cfph_dec.items() if v}
    for o, grp in _group(rows, name_col, norm):
        seen = set()
        for r in grp:
            nr = dict(r); sku = r["child_sku"].strip()
            if o in wwho and sku == "CH-WWHO" and cheese_dec.get(o):
                _set(nr, cheese_dec[o])
            elif o in wwho and sku == "AC-BCM" and jam_dec.get(o):
                _set(nr, jam_dec[o])
            elif o in blrsmg and sku in ("CH-BLR", "CH-SMG") and yfarm_dec.get(o):
                _set(nr, yfarm_dec[o])
            elif sku == "AC-CFPH" and cfph_dec.get(o):
                _set(nr, cfph_dec[o])
            if o in affected:
                k = nr["child_sku"].strip()
                if k in seen:
                    continue
                seen.add(k)
            out_rows.append(nr)

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out_rows)
    LOG.write_text("\n".join(log), encoding="utf-8")

    # report
    def cnt(dec, key):
        return sum(1 for v in dec.values() if v and v["sku"] == key)
    print("== WWHO (79) ==")
    print(f"  -> CH-HCGU: {cnt(cheese_dec,'CH-HCGU')}  -> CH-BLR(collision): {cnt(cheese_dec,'CH-BLR')}  flagged: {sum(1 for v in cheese_dec.values() if v is None)}")
    print(f"  jam -> BLBALS {cnt(jam_dec,'AC-BLBALS')}  SRHUB {cnt(jam_dec,'AC-SRHUB')}  keep BCM {sum(1 for v in jam_dec.values() if v is None)}")
    print("== BLR/SMG dupe group ==")
    print(f"  -> CH-YFARM: {cnt(yfarm_dec,'CH-YFARM')}  flagged: {sum(1 for v in yfarm_dec.values() if v is None)}")
    print("== CFPH cap ==")
    conv_b = cnt(cfph_dec, 'AC-BLBALS'); conv_s = cnt(cfph_dec, 'AC-SRHUB')
    kept = len(cfph_orders) - conv_b - conv_s
    print(f"  CFPH orders: {len(cfph_orders)}  kept CFPH: {kept}  -> BLBALS: {conv_b}  -> SRHUB: {conv_s}")
    print(f"rows: {len(out_rows)}  orders: {len({norm(r[name_col]) for r in out_rows})}")
    print(f"OUT: {OUT}")
    print(f"LOG: {LOG}")


def _group(rows, name_col, norm):
    from collections import OrderedDict
    g = OrderedDict()
    for r in rows:
        g.setdefault(norm(r[name_col]), []).append(r)
    return g.items()


def _set(nr, t):
    nr["Line: Product ID"] = t["id"]; nr["Line: Product Handle"] = t["handle"]; nr["child_sku"] = t["sku"]


if __name__ == "__main__":
    main()
