"""Regenerate the 9 wholly-rejected orders from the 2026-07-10 (2) import.

Matrixify rejected the ENTIRE order when any line dup'd. For each affected
order: keep every non-dup row as-is; replace each dup'd SKU with a same-prefix
$0 in-box substitute the customer doesn't have (current box + history + this
order's own import adds). In-sheet dedupe. READ-ONLY against Shopify.

Substitute pool = the import's own box-content SKUs by prefix (already $0 in-box),
ordered by frequency (proxy for in-stock/common).
"""
from __future__ import annotations

import csv, json, re, sys, time
from collections import OrderedDict, Counter
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

TAG = "RMFG_20260710"
SRC = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-10 (2)_FIXED.csv")
RESULT = Path(r"C:\Users\Work\AppData\Local\Temp\claude\C--Users-Work-Claude-Projects-AppyHour\1ae704c3-e187-4aef-8a02-7494a7e59a2f\scratchpad\ir2\matrixify-export-2026-07-10 (2)_FIXED.csv")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-2026-07-10-2_DUPE-REIMPORT.csv")

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

    # 1. dupe (order -> set of dup'd skus) from result file
    with RESULT.open(encoding="utf-8-sig", newline="") as f:
        rres = list(csv.DictReader(f))
    dupe_map: dict[str, set] = {}
    for r in rres:
        if "already on the order" in (r.get("Import Comment") or ""):
            dupe_map.setdefault(norm(r["Name"]), set()).add(r["child_sku"].strip())

    # 2. source rows + pools by prefix (sku -> (pid,handle)), freq
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f); fields = rdr.fieldnames; rows = list(rdr)
    name_col = next(c for c in fields if re.match(r"^(Name|Order)", c, re.I))
    prod = {}   # sku -> (pid, handle)
    freq = Counter()
    for r in rows:
        sk = r["child_sku"].strip()
        if sk[:3] in ("CH-", "MT-", "AC-"):
            prod.setdefault(sk, (r["Line: Product ID"], r["Line: Product Handle"]))
            freq[sk] += 1
    pools = {p: [s for s, _ in sorted(((s, freq[s]) for s in prod if s.startswith(p)),
                                      key=lambda x: -x[1])] for p in ("CH-", "MT-", "AC-")}
    # deepen the meat pool with high-stock $0 in-box meats (verified) so a
    # meat-heavy box can't exhaust it
    # distinct $0 in-box salamis, preferred first (variety > shuffling the CC line)
    EXTRA = {
        "MT-SFEN": ("9808332325144", "Finocchiona *"),   # $0 variant (NOT paid 9658564706584)
        "MT-PARM": ("10176518357272", "Parm Salami"),
        "MT-STUF": ("10084226760984", "Salami Truffle"),
        "MT-LONZ": ("9657187762456", "Lonza *"),
        "MT-SCHI": ("10264666145048", "Salami Chianti*"),
        "MT-TUSC": ("9880758649112", "Toscano Salame*"),
        "MT-JAMS": ("9690179109144", "Jamón Serrano *"),
    }
    prod["MT-SFEN"] = EXTRA["MT-SFEN"]            # force $0 id (SKU maps to 2 products)
    for s, v in EXTRA.items():
        prod.setdefault(s, v)
    pref = list(EXTRA.keys())
    pools["MT-"] = pref + [s for s in pools["MT-"] if s not in pref]

    print(f"bulk-reading tag:{TAG} ...")
    box = bulk_box(base, h)

    # 3. per order: history + import-adds; assign substitute per dup'd sku
    assign = {}   # (order, dupe_sku) -> sub_sku
    log = []
    for o, dupes in dupe_map.items():
        ever = set(box.get(o, set()))
        ed = gql(base, h, EMAIL, {"q": f"name:#{o}"})["orders"]["edges"]
        em = (ed[0]["node"].get("customer") or {}).get("email") if ed else None
        if em:
            for e in gql(base, h, HIST, {"q": f"email:{em}"})["orders"]["edges"]:
                ever |= li(e["node"])
        import_adds = {r["child_sku"].strip() for r in rows if norm(r[name_col]) == o}
        chosen_this_order = set()
        for dsku in sorted(dupes):
            blocked = ever | import_adds | chosen_this_order
            pref = dsku[:3]
            sub = next((s for s in pools.get(pref, []) if s not in blocked), None)
            assign[(o, dsku)] = sub
            if sub: chosen_this_order.add(sub)
            log.append(f"#{o}  {dsku} -> {sub or 'NONE-LEFT'}")

    # 4. regenerate full rows for the 9 orders
    g = OrderedDict()
    for r in rows: g.setdefault(norm(r[name_col]), []).append(r)
    out = []
    for o in dupe_map:
        seen = set()
        for r in g[o]:
            nr = {k: r[k] for k in fields}; sku = r["child_sku"].strip()
            if (o, sku) in assign and assign[(o, sku)]:
                sub = assign[(o, sku)]; pid, handle = prod[sub]
                nr["Line: Product ID"] = pid; nr["Line: Product Handle"] = handle; nr["child_sku"] = sub
            k = nr["child_sku"].strip()
            if k in seen: continue
            seen.add(k); out.append(nr)

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)

    for x in log: print(x)
    none_left = [k for k, v in assign.items() if not v]
    print(f"\norders: {len(dupe_map)}  dup rows replaced: {len([v for v in assign.values() if v])}  no-sub: {len(none_left)} {none_left}")
    print(f"rows: {len(out)}  OUT: {OUT}")


if __name__ == "__main__":
    main()
