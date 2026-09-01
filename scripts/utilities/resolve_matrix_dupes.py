"""Canonical resolver for a Matrixify add-sheet — dupes, removed-item guard, in-box swaps.

Supersedes the dated resolve_dupes_2026_*.py one-shots (deleted 2026-08-28). Fixes the
bug that shipped a short box: an in-sheet duplicate was COLLAPSED (row dropped) instead of
the 2nd occurrence being SWAPPED — so a LGE box with MT-CAPO x2 lost a meat.

RULES (per order, negatives-first):
  - in-sheet dupe (same child_sku appears >1 in the order): keep the 1st, SWAP each later
    occurrence to a dupe-safe same-prefix $0 in-box SKU. NEVER drop it — that shorts the box.
  - live dupe (child_sku already on the Shopify order, currentQuantity>0): swap CH-/MT- to a
    dupe-safe sub; drop AC- (customer keeps existing).
  - removed AC-FCROSE (currentQuantity==0 & quantity>0, i.e. taken off the order): re-adding
    bounces the import, so swap that row to AC-TOK $0 (product 10108946120984, handle 'toketti').
  - blocked set for any sub = box ∪ removed ∪ history(ever) ∪ this-order-adds ∪ already-chosen.
  - every target verified $0 + UNIQUE product (avoids the handle-collision import failure).

🔴 READ-ONLY vs Shopify. Writes ONE corrected CSV. Never edits an order. `--fresh` bypasses the
order_state_cache (mutable box/removed go stale when an order is edited — the miss that hid dupes).

Usage:
  python resolve_matrix_dupes.py --src "<add.csv>" --out "<resolved.csv>" [--fresh]
"""
from __future__ import annotations
import argparse, json, csv, re, sys
from collections import OrderedDict, Counter
from pathlib import Path

AH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AH)); sys.path.insert(0, str(AH / "InventoryReorder" / "fulfillment_web"))
sys.path.insert(0, str(AH / "scripts" / "utilities"))
from shopify_swap import _gql                       # noqa: E402
from order_state_cache import OrderStateCache       # noqa: E402

S = json.load(open(AH / "InventoryReorder" / "dist" / "inventory_reorder_settings.json", encoding="utf-8"))
SHOP, TOK = "504ac4", S["shopify_access_token"]
norm = lambda v: re.sub(r"\D", "", v or "")

TOK_TOK = ("10108946120984", "toketti", "AC-TOK")   # $0 in-box Toketti (unique slug handle)
CH_POOL = ["CH-OTTA", "CH-SMG", "CH-CARO", "CH-ASST", "CH-ETX", "CH-BARI", "CH-CONI", "CH-CABR",
           "CH-WWHO", "CH-QOTA", "CH-MONT", "CH-BRZ", "CH-UROSE"]
MT_POOL = ["MT-SPAP", "MT-STUF", "MT-SCHI", "MT-TUSC", "MT-BSS", "MT-SBRES", "MT-PARM", "MT-LONZ", "MT-JAMS", "MT-CCBS"]
AC_POOL = ["AC-PRPE", "AC-QUIC", "AC-SDF", "AC-MISS", "AC-DTCH", "AC-MARC", "AC-BRJA", "AC-BLBALS"]
ONE = 'query($q:String!){orders(first:1,query:$q){edges{node{id customer{email} lineItems(first:100){nodes{sku currentQuantity quantity}}}}}}'
HIST = 'query($q:String!){orders(first:20,query:$q){edges{node{lineItems(first:100){nodes{sku currentQuantity quantity}}}}}}'
V = 'query($q:String!){productVariants(first:20,query:$q){edges{node{sku price availableForSale inventoryQuantity product{id title}}}}}'


def _skus(node):
    o = set()
    for n in node["lineItems"]["nodes"]:
        cq = n.get("currentQuantity"); cq = n.get("quantity", 0) if cq is None else cq
        s = (n.get("sku") or "").strip()
        if s and cq and cq > 0: o.add(s)
    return o


def fetch(order):
    d = _gql(SHOP, TOK, ONE, {"q": f"name:#{order}"})["orders"]["edges"]
    if not d:
        return {"box": [], "removed": [], "ever": [], "last": None, "email": None}
    node = d[0]["node"]; em = (node.get("customer") or {}).get("email")
    box, rem = set(), set()
    for n in node["lineItems"]["nodes"]:
        s = (n.get("sku") or "").strip(); cq = n.get("currentQuantity"); q = n.get("quantity", 0)
        if not s: continue
        if (cq or 0) > 0: box.add(s)
        elif q > 0: rem.add(s)     # removed / refunded line
    ever = set(box)
    if em:
        for e in _gql(SHOP, TOK, HIST, {"q": f"email:{em}"})["orders"]["edges"]:
            ever |= _skus(e["node"])
    return {"box": sorted(box), "removed": sorted(rem), "ever": sorted(ever), "last": None, "email": em}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--fresh", action="store_true", help="bypass cache for mutable box/removed")
    a = ap.parse_args()
    cache = OrderStateCache()
    pc = {}

    def prod(sk):
        if sk in pc: return pc[sk]
        e = _gql(SHOP, TOK, V, {"q": f"sku:{sk}"})["productVariants"]["edges"]
        z = [x["node"] for x in e if (x["node"].get("sku") or "") == sk and float(x["node"].get("price") or 0) == 0.0]
        ok = len({x["product"]["id"] for x in z}) == 1 and any((x.get("inventoryQuantity") or 0) > 0 or x.get("availableForSale") for x in z)
        pc[sk] = (z[0]["product"]["id"].split("/")[-1], z[0]["product"]["title"]) if ok else None
        return pc[sk]

    with open(a.src, encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f); fields = rdr.fieldnames; rows = list(rdr)
    g = OrderedDict()
    for i, r in enumerate(rows): g.setdefault(norm(r["Name"]), []).append(i)

    out, rep, log = [], Counter(), []
    for o in g:
        st = cache.get(o, fetch, refresh=a.fresh)
        box, rem, ever = set(st["box"]), set(st["removed"]), set(st["ever"])
        adds = {rows[i]["child_sku"].strip() for i in g[o]}
        placed, chosen = set(), set()
        for i in g[o]:
            r = dict(rows[i]); orig = r["child_sku"].strip(); sk = orig
            insheet = sk in placed              # 2nd+ occurrence THIS order
            livedupe = sk in box
            if sk == "AC-FCROSE" and "AC-FCROSE" in rem:
                if not ({"AC-TOK"} & (box | rem | placed | chosen)):
                    r["Line: Product ID"], r["Line: Product Handle"], r["child_sku"] = TOK_TOK
                    sk = "AC-TOK"; chosen.add(sk); rep["FCROSE->TOK"] += 1; log.append(f"#{o} FCROSE->TOK")
            elif insheet or livedupe:
                pool = MT_POOL if sk.startswith("MT-") else CH_POOL if sk.startswith("CH-") else AC_POOL
                blk = box | rem | ever | adds | placed | chosen
                sub = next((s for s in pool if s not in blk and prod(s)), None)
                if sub:
                    pd = prod(sub); r["Line: Product ID"], r["Line: Product Handle"], r["child_sku"] = pd[0], pd[1], sub
                    chosen.add(sub); sk = sub
                    rep["insheet-swap" if insheet else "live-swap"] += 1
                    log.append(f"#{o} {orig}{'(x2)' if insheet else '(live)'}->{sub}")
                else:
                    rep["NO-SUB"] += 1; log.append(f"#{o} {orig} NO-SUB")
            placed.add(sk); out.append(r)      # NEVER drop — count preserved

    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
    c = Counter((norm(r["Name"]), r["child_sku"].strip()) for r in out)
    for x in log: print(x)
    print("report:", dict(rep))
    print("in-sheet dupes remaining:", sum(1 for v in c.values() if v > 1))
    print(f"rows {len(rows)} -> {len(out)}  (row count preserved per order)  OUT: {a.out}")


if __name__ == "__main__":
    main()
