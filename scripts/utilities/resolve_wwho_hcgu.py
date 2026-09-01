"""Resolve CH-WWHO (Honey Highlands) cheese duplicates by re-pairing to
CH-HCGU (Honey Clover Gouda) + a good jam (AC-BLBALS), dupe-safe.

For each CH-WWHO dupe order: live current box + customer history.
- cheese: CH-WWHO -> CH-HCGU (flag if customer already has HCGU).
- jam:    AC-BCM  -> AC-BLBALS (fall back AC-SRHUB if they have BLBALS;
          keep AC-BCM if they have both).
Regenerates the FULL rows for each affected order (in-sheet deduped).
READ-ONLY against Shopify.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

DUPES = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-export-2026-07-03_DUPES.csv")
OUT   = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts\matrixify-2026-07-03_WWHO-RESOLVED.csv")

CHEESE = {"sku": "CH-HCGU", "id": "10110685610264", "handle": "Honey Clover Gouda*"}
JAM_PREF = [
    {"sku": "AC-BLBALS", "id": "9751422239000", "handle": "Blackberry Balsamic Jam"},
    {"sku": "AC-SRHUB",  "id": "9891502620952", "handle": "Strawberry Rhubarb Mini Jam*"},
]
DUPE_CHEESE = "CH-WWHO"
DUPE_JAM = "AC-BCM"

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
    with DUPES.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [c for c in reader.fieldnames if c != "Dupe Issue"]
        rows = list(reader)
    name_col = next(c for c in reader.fieldnames if re.match(r"^(Name|Order)", c, re.I))

    wwho_orders = sorted({re.sub(r"\D", "", r[name_col]) for r in rows if r["child_sku"].strip() == DUPE_CHEESE})

    decisions, out_rows = [], []
    for order in wwho_orders:
        od = gql(base, headers, ORDER_Q, {"q": f"name:#{order}"})["orders"]["edges"]
        have = set()
        if od:
            node = od[0]["node"]
            have = skus(node)
            email = (node.get("customer") or {}).get("email") or ""
            if email:
                for e in gql(base, headers, HIST_Q, {"q": f"email:{email}"})["orders"]["edges"]:
                    have |= skus(e["node"])

        cheese_ok = CHEESE["sku"] not in have
        jam = next((j for j in JAM_PREF if j["sku"] not in have), None)
        decisions.append((order, cheese_ok, jam["sku"] if jam else "keep AC-BCM"))

        seen = set()
        for r in [x for x in rows if re.sub(r"\D", "", x[name_col]) == order]:
            sku = r["child_sku"].strip()
            nr = {k: r[k] for k in fieldnames}
            if sku == DUPE_CHEESE and cheese_ok:
                nr["Line: Product ID"] = CHEESE["id"]; nr["Line: Product Handle"] = CHEESE["handle"]; nr["child_sku"] = CHEESE["sku"]
            elif sku == DUPE_JAM and jam:
                nr["Line: Product ID"] = jam["id"]; nr["Line: Product Handle"] = jam["handle"]; nr["child_sku"] = jam["sku"]
            if nr["child_sku"].strip() not in seen:
                seen.add(nr["child_sku"].strip())
                out_rows.append(nr)

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(out_rows)

    hcgu_flags = [d[0] for d in decisions if not d[1]]
    jam_counts: dict[str, int] = {}
    for d in decisions:
        jam_counts[d[2]] = jam_counts.get(d[2], 0) + 1
    print(f"WWHO orders resolved: {len(decisions)}")
    print(f"cheese CH-WWHO->CH-HCGU: {sum(1 for d in decisions if d[1])}  | HCGU-already-present (flagged): {len(hcgu_flags)} {hcgu_flags}")
    print(f"jam choice: " + ", ".join(f"{k}={v}" for k, v in sorted(jam_counts.items())))
    print(f"out rows: {len(out_rows)}  -> {OUT}")


if __name__ == "__main__":
    main()
