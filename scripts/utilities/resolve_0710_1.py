"""Resolve the 2026-07-10 (1) DUPE sheet, dupe-safe, auto-picking replacements.

Each dupe cheese -> first $0 in-box cheese from POOL the customer lacks
(current box + history). Jam dupe -> first $0 jam they lack. PR-CJAM pairing
kept intact (replace, never drop). Full rows regenerated, in-sheet deduped.
READ-ONLY against Shopify.
"""
from __future__ import annotations

import csv, json, re, sys, time
from collections import OrderedDict
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

TAG = "RMFG_20260710"
DUPES = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-10 (1)_DUPES.csv")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-10 (1)_DUPES-RESOLVED.csv")

CHEESE_POOL = [
    {"sku": "CH-HCGU",  "id": "10110685610264", "handle": "Honey Clover Gouda*"},
    {"sku": "CH-UMIN",  "id": "10248167588120", "handle": "Farmstead Smoked Cumin Gouda"},
    {"sku": "CH-ETX",   "id": "10124353405208", "handle": "Etxegarai"},
    {"sku": "CH-YFARM", "id": "10311218037016", "handle": "Young Farmdal*"},
    {"sku": "CH-BLR",   "id": "9843608813848",  "handle": "Baked Lemon Ricotta *"},
]
JAM_POOL = [
    {"sku": "AC-BCM",    "id": "10309492211992", "handle": "Blackcurrant Mint Jam*"},
    {"sku": "AC-BLBALS", "id": "9751422239000",  "handle": "Blackberry Balsamic Jam"},
]

# order -> dupe sku (from warning block)
DUPE = {
    "159961": "CH-RQCAV", "160355": "CH-RQCAV",
    "159964": "CH-BLR", "160137": "CH-BLR", "160441": "CH-BLR", "160807": "CH-BLR",
    "160930": "CH-BLR", "161215": "CH-BLR", "161264": "CH-BLR",
    "160402": "CH-YFARM", "160442": "CH-YFARM", "161048": "CH-YFARM",
    "161114": "CH-YFARM", "161132": "CH-YFARM", "161187": "CH-YFARM",
    "161396": "AC-SRHUB",
}

SUBMIT = 'mutation($q:String!){bulkOperationRunQuery(query:$q){bulkOperation{id status} userErrors{message}}}'
POLL = 'query{currentBulkOperation{status url errorCode}}'
HIST = 'query($q:String!){orders(first:50,query:$q){edges{node{lineItems(first:100){edges{node{sku currentQuantity quantity}}}}}}}'
EMAIL = 'query($q:String!){orders(first:1,query:$q){edges{node{customer{email}}}}}'


def gql(base, h, q, v=None):
    r = requests.post(f"{base}/graphql.json", headers=h, json={"query": q, "variables": v or {}}, timeout=60)
    r.raise_for_status(); d = r.json()
    if d.get("errors"): raise RuntimeError(d["errors"])
    return d["data"]


def li(node):
    out = set()
    for e in node["lineItems"]["edges"]:
        n = e["node"]; q = n.get("currentQuantity"); q = n.get("quantity", 0) if q is None else q
        s = (n.get("sku") or "").strip()
        if s and q and q > 0: out.add(s)
    return out


def bulk_box(base, h):
    inner = ('{ orders(query: "tag:%s") { edges { node { id name '
             "lineItems { edges { node { sku currentQuantity quantity } } } } } } }" % TAG)
    if gql(base, h, SUBMIT, {"q": inner})["bulkOperationRunQuery"]["userErrors"]:
        raise RuntimeError("bulk err")
    url, s, dl = None, 2.0, time.monotonic() + 900
    while True:
        op = gql(base, h, POLL)["currentBulkOperation"] or {}
        if op.get("status") == "COMPLETED": url = op.get("url"); break
        if op.get("status") in ("FAILED", "CANCELED"): raise RuntimeError(op.get("errorCode"))
        if time.monotonic() > dl: raise RuntimeError("timeout")
        time.sleep(s); s = min(s * 1.5, 30)
    box, idx = {}, {}
    if not url: return box
    resp = requests.get(url, stream=True, timeout=180); resp.raise_for_status()
    for line in resp.iter_lines():
        if not line: continue
        row = json.loads(line); pid = row.get("__parentId")
        if pid is None:
            dg = re.sub(r"\D", "", row.get("name", "")); idx[row["id"]] = dg; box[dg] = set()
        else:
            dg = idx.get(pid)
            if dg is None: continue
            q = row.get("currentQuantity"); q = row.get("quantity", 0) if q is None else q
            sk = (row.get("sku") or "").strip()
            if sk and q and q > 0: box[dg].add(sk)
    return box


def main():
    base, h = get_shopify_auth()
    norm = lambda v: re.sub(r"\D", "", v or "")
    with DUPES.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f); fields = [c for c in rdr.fieldnames if c != "Dupe Issue"]; rows = list(rdr)
    name_col = next(c for c in rdr.fieldnames if re.match(r"^(Name|Order)", c, re.I))

    print(f"bulk-reading tag:{TAG} ...")
    box = bulk_box(base, h)

    pick = {}
    for o, dupe in DUPE.items():
        ever = set(box.get(o, set()))
        ed = gql(base, h, EMAIL, {"q": f"name:#{o}"})["orders"]["edges"]
        em = (ed[0]["node"].get("customer") or {}).get("email") if ed else None
        if em:
            for e in gql(base, h, HIST, {"q": f"email:{em}"})["orders"]["edges"]:
                ever |= li(e["node"])
        pool = JAM_POOL if dupe.startswith("AC-") else CHEESE_POOL
        rep = next((c for c in pool if c["sku"] not in ever), None)
        pick[o] = rep
        print(f"#{o}  {dupe} -> {rep['sku'] if rep else 'NONE (all pool present!)'}")

    g = OrderedDict()
    for r in rows: g.setdefault(norm(r[name_col]), []).append(r)
    out = []
    for o, grp in g.items():
        seen = set()
        for r in grp:
            nr = {k: r[k] for k in fields}; sku = r["child_sku"].strip()
            if o in DUPE and sku == DUPE[o] and pick.get(o):
                nr["Line: Product ID"] = pick[o]["id"]; nr["Line: Product Handle"] = pick[o]["handle"]; nr["child_sku"] = pick[o]["sku"]
            k = nr["child_sku"].strip()
            if k in seen: continue
            seen.add(k); out.append(nr)

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
    from collections import Counter
    cc = Counter(pick[o]["sku"] for o in pick if pick[o])
    print("assigned:", dict(cc))
    print(f"rows: {len(out)}  orders: {len({norm(r[name_col]) for r in out})}  OUT: {OUT}")


if __name__ == "__main__":
    main()
