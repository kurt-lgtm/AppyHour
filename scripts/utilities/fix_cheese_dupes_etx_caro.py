"""Corrective delta import: replace CH-SSIM / CH-LOU / CH-GFLEECE duplicate
line-adds (rejected by Shopify as 'already on the order') with CH-ETX or
CH-CARO — whichever the customer does NOT already have.

Builds a small re-import CSV containing ONLY the swapped replacement rows
(MERGE adds). READ-ONLY against Shopify (current state + history per order).
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

SRC = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-06-26 (3)_FINAL.csv")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-2026-06-26_CHEESE-DUPE-FIX.csv")

ETX  = {"sku": "CH-ETX",  "id": "10124353405208", "handle": "Etxegarai"}
CARO = {"sku": "CH-CARO", "id": "10303238603032", "handle": "Cacio de Roma*"}
PREF = [ETX, CARO]  # preference order

# order -> dupe cheeses to replace (from import-result 'already on the order')
SSIM = ["155620","155700","155701","155713","155872","155874","155974","155983",
        "156061","156193","156396","156401","156415","156518","156547","156624",
        "156662","156671","156699","156722","156745"]
AFFECTED: dict[str, list[str]] = {}
for o in SSIM:
    AFFECTED.setdefault(o, []).append("CH-SSIM")
for o in ["154907", "155016", "155305"]:
    AFFECTED.setdefault(o, []).append("CH-LOU")
AFFECTED.setdefault("155305", []).append("CH-GFLEECE")

ORDER_Q = """
query($q: String!) {
  orders(first: 1, query: $q) {
    edges { node { name customer { email } lineItems(first: 100) { edges { node { sku currentQuantity quantity } } } } }
  }
}
"""
HIST_Q = """
query($q: String!) {
  orders(first: 100, query: $q) {
    edges { node { lineItems(first: 100) { edges { node { sku currentQuantity quantity } } } } }
  }
}
"""


def gql(base, headers, q, v):
    r = requests.post(f"{base}/graphql.json", headers=headers, json={"query": q, "variables": v}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(d["errors"])
    return d["data"]


def skus(node):
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


def main():
    base, headers = get_shopify_auth()

    with SRC.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        src_rows = list(reader)
    name_col = next(c for c in fieldnames if re.match(r"^(Name|Order)", c, re.I))

    # index source rows by (order, child_sku) -> first matching row (template)
    src_idx: dict[tuple, dict] = {}
    for r in src_rows:
        key = (re.sub(r"\D", "", r[name_col]), (r["child_sku"] or "").strip())
        src_idx.setdefault(key, r)

    out_rows, decisions = [], []
    for order, dupes in AFFECTED.items():
        od = gql(base, headers, ORDER_Q, {"q": f"name:#{order}"})["orders"]["edges"]
        if not od:
            decisions.append((order, dupes, "ORDER NOT FOUND"))
            continue
        node = od[0]["node"]
        email = (node.get("customer") or {}).get("email") or ""
        have = skus(node)
        if email:
            for e in gql(base, headers, HIST_Q, {"q": f"email:{email}"})["orders"]["edges"]:
                have |= skus(e["node"])

        assigned = set()
        for dupe in dupes:
            repl = next((c for c in PREF if c["sku"] not in have and c["sku"] not in assigned), None)
            if repl is None:
                decisions.append((order, dupe, "SKIP: both CH-ETX & CH-CARO already present"))
                continue
            assigned.add(repl["sku"])
            tmpl = src_idx.get((order, dupe))
            if tmpl is None:
                decisions.append((order, dupe, f"no source row for {dupe}"))
                continue
            row = dict(tmpl)
            row["Line: Product ID"] = repl["id"]
            row["Line: Product Handle"] = repl["handle"]
            row["child_sku"] = repl["sku"]
            out_rows.append(row)
            decisions.append((order, f"{dupe} -> {repl['sku']}", "ok"))

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(out_rows)

    print("=== cheese dupe -> ETX/CARO ===")
    for d in decisions:
        print(f"#{d[0]}  {d[1]}   [{d[2]}]")
    print("-" * 50)
    et = sum(1 for r in out_rows if r["child_sku"] == "CH-ETX")
    ca = sum(1 for r in out_rows if r["child_sku"] == "CH-CARO")
    print(f"replacement rows: {len(out_rows)}  (CH-ETX {et}, CH-CARO {ca})")
    print(f"re-import: {OUT}")


if __name__ == "__main__":
    main()
