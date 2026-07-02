"""Replace CH-/MT- duplicate line-adds with items the customer has never had.

For each affected order: pull the order's CURRENT line items + the customer's
FULL order history (by email). Pick a replacement that is neither already on
the order, nor in the import for that order, nor anything they've had before.

- CH dupe -> CH-LOU (only if never had it; else flagged, left unswapped).
- MT dupe -> first meat in MEAT_POOL that clears history+current+import.

Writes a consolidated import CSV (full source minus #155737, with the swaps)
and a decisions log. READ-ONLY against Shopify.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

SRC = Path(r"C:\Users\Work\Downloads\matrixify-export-2026-06-26 (3).csv")
OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts")
DROP_ORDER = "155737"

# order -> dupe sku to replace (only CH-/MT- dupes; AC- ignored per request)
AFFECTED = {
    "154907": "CH-LOSC",
    "154528": "MT-JAHH",
    "145113": "MT-SFEN",
    "154552": "MT-SFEN",
    "155655": "MT-SFEN",
    "156766": "MT-SFEN",
}

CH_TARGET = {"sku": "CH-LOU", "id": "9974090760472", "handle": "Lou Bergier Pichin Tomme*"}

# in-box ($0) meats from this import, priority by stock; all confirmed in stock
MEAT_POOL = [
    {"sku": "MT-PARM", "id": "10176518357272", "handle": "Parm Salami",                        "have": 4091},
    {"sku": "MT-STUF", "id": "10084226760984", "handle": "Salami Truffle",                     "have": 2712},
    {"sku": "MT-CCBS", "id": "10258830950680", "handle": "Belgian Style Quad Ale Salami",      "have": 2146},
    {"sku": "MT-JAMS", "id": "9690179109144",  "handle": "Jamon Serrano *",                    "have": 1476},
    {"sku": "MT-CCCS", "id": "10311275479320", "handle": "Chorizo Seco*",                      "have": 401},
    {"sku": "MT-TICH", "id": "10255631122712", "handle": "Spicy Pecan Smoked Chorizo Iberico", "have": 341},
]

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
    edges { node { name lineItems(first: 100) { edges { node { sku currentQuantity quantity } } } } }
  }
}
"""


def gql(base, headers, query, variables):
    r = requests.post(f"{base}/graphql.json", headers=headers,
                      json={"query": query, "variables": variables}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(d["errors"])
    return d["data"]


def live_skus(node):
    out = set()
    for e in node["lineItems"]["edges"]:
        n = e["node"]
        qty = n.get("currentQuantity")
        if qty is None:
            qty = n.get("quantity", 0)
        sku = (n.get("sku") or "").strip()
        if sku and qty and qty > 0:
            out.add(sku)
    return out


def main():
    base, headers = get_shopify_auth()

    with SRC.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    name_col = next(c for c in fieldnames if re.match(r"^(Name|Order)", c, re.I))

    # what the import adds per order (so we don't pick a SKU also being added)
    import_adds: dict[str, set] = {}
    for r in rows:
        d = re.sub(r"\D", "", r[name_col])
        import_adds.setdefault(d, set()).add((r["child_sku"] or "").strip())

    decisions = {}
    for order, dupe in AFFECTED.items():
        od = gql(base, headers, ORDER_Q, {"q": f"name:#{order}"})["orders"]["edges"]
        if not od:
            decisions[order] = {"dupe": dupe, "pick": None, "note": "ORDER NOT FOUND"}
            continue
        node = od[0]["node"]
        email = (node.get("customer") or {}).get("email") or ""
        current = live_skus(node)
        ever = set(current)
        if email:
            hist = gql(base, headers, HIST_Q, {"q": f"email:{email}"})["orders"]["edges"]
            for e in hist:
                ever |= live_skus(e["node"])
        blocked = ever | import_adds.get(order, set())

        if dupe.startswith("CH-"):
            if CH_TARGET["sku"] in ever:
                decisions[order] = {"dupe": dupe, "pick": None, "email": email,
                                    "note": f"SKIP: customer already had {CH_TARGET['sku']}"}
            else:
                decisions[order] = {"dupe": dupe, "pick": CH_TARGET, "email": email,
                                    "note": "ok"}
        else:
            pick = next((m for m in MEAT_POOL if m["sku"] not in blocked), None)
            decisions[order] = {"dupe": dupe, "pick": pick, "email": email,
                                "note": "ok" if pick else "NO CLEAN MEAT IN POOL",
                                "had": sorted(s for s in ever if s.startswith("MT-"))}

    # build consolidated import: drop #155737, apply swaps to the dupe row
    out_rows = []
    for r in rows:
        d = re.sub(r"\D", "", r[name_col])
        if d == DROP_ORDER:
            continue
        if d in decisions:
            dec = decisions[d]
            if dec["pick"] and (r["child_sku"] or "").strip() == dec["dupe"]:
                r = dict(r)
                r["Line: Product ID"] = dec["pick"]["id"]
                r["Line: Product Handle"] = dec["pick"]["handle"]
                r["child_sku"] = dec["pick"]["sku"]
        out_rows.append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "matrixify-export-2026-06-26 (3)_FINAL.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(out_rows)

    print("=== CH/MT replacement decisions ===")
    for order, dec in decisions.items():
        pick = dec["pick"]["sku"] if dec["pick"] else "-- none --"
        print(f"#{order}  {dec['dupe']} -> {pick}   [{dec['note']}]")
        if dec.get("had"):
            print(f"     meat history: {', '.join(dec['had'])}")
    print("-" * 50)
    print(f"FINAL import: {out_path}")
    print(f"rows: {len(out_rows)}  (dropped #155737, applied {sum(1 for d in decisions.values() if d['pick'])} swaps)")


if __name__ == "__main__":
    main()
