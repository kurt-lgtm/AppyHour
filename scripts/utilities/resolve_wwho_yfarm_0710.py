"""Resolve the 2026-07-10 DUPE sheet: swap the CH-WWHO cheese dupe -> CH-YFARM,
dupe-safe (current box). Flags any order that already has CH-YFARM.
- #159922 dupe IS CH-YFARM -> cannot sub with YFARM; flagged, left as-is.
- AC-SRHUB jam dupe (#159938) -> dropped (already on order).
Regenerates full rows for affected orders, in-sheet deduped. READ-ONLY.
"""
from __future__ import annotations

import csv, json, re, sys, time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

TAG = "RMFG_20260710"
DUPES = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-10_DUPES.csv")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-10_DUPES-RESOLVED.csv")

YFARM = {"sku": "CH-YFARM", "id": "10311218037016", "handle": "Young Farmdal*"}
SUBMIT = 'mutation($q:String!){bulkOperationRunQuery(query:$q){bulkOperation{id status} userErrors{message}}}'
POLL = 'query{currentBulkOperation{status url errorCode}}'


def gql(base, h, q, v=None):
    r = requests.post(f"{base}/graphql.json", headers=h, json={"query": q, "variables": v or {}}, timeout=60)
    r.raise_for_status(); d = r.json()
    if d.get("errors"): raise RuntimeError(d["errors"])
    return d["data"]


def bulk_box(base, h):
    inner = ('{ orders(query: "tag:%s") { edges { node { id name '
             "lineItems { edges { node { sku currentQuantity quantity } } } } } } }" % TAG)
    if gql(base, h, SUBMIT, {"q": inner})["bulkOperationRunQuery"]["userErrors"]:
        raise RuntimeError("bulk submit error")
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
            q = row.get("currentQuantity");  q = row.get("quantity", 0) if q is None else q
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
    print(f"current boxes: {len(box)}")

    wwho = sorted({norm(r[name_col]) for r in rows if r["child_sku"].strip() == "CH-WWHO"})
    flags = []
    out = []
    from collections import OrderedDict
    g = OrderedDict()
    for r in rows: g.setdefault(norm(r[name_col]), []).append(r)

    swapped = 0
    for o, grp in g.items():
        seen = set()
        for r in grp:
            sku = r["child_sku"].strip()
            nr = {k: r[k] for k in fields}
            # drop AC-SRHUB dupe row for #159938
            if sku == "AC-SRHUB" and o == "159938":
                continue
            if o in wwho and sku == "CH-WWHO":
                if YFARM["sku"] in box.get(o, set()):
                    flags.append(f"#{o} already has CH-YFARM -> left CH-WWHO")
                else:
                    nr["Line: Product ID"] = YFARM["id"]; nr["Line: Product Handle"] = YFARM["handle"]; nr["child_sku"] = YFARM["sku"]
                    swapped += 1
            k = nr["child_sku"].strip()
            if k in seen:  # in-sheet dedupe
                continue
            seen.add(k)
            out.append(nr)

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)

    print(f"CH-WWHO orders: {len(wwho)}  -> CH-YFARM: {swapped}  flagged(already have YFARM): {len(flags)}")
    for x in flags: print("  " + x)
    print(f"#159922 (CH-YFARM dupe): NOT swapped -> needs a different cheese (your call)")
    print(f"#159938 AC-SRHUB dupe row: dropped")
    print(f"rows: {len(out)}  orders: {len({norm(r[name_col]) for r in out})}")
    print(f"OUT: {OUT}")


if __name__ == "__main__":
    main()
