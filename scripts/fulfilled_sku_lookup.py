"""Look up any fulfilled SKU over time — orders + units + geography + weekly trend.

Generalized from the one-off lfolive lookup (Kurt 2026-06-11: "reuse to look up any fulfilled SKU over
time"). Confirms the variant exists, scans ALL fulfilled orders carrying the SKU, filters to fulfillments
on/after a since-date (fulfilled_at isn't server-searchable, so filtered client-side), writes a CSV and
prints a by-fulfillment-date and by-state breakdown.

Usage:
    python scripts/fulfilled_sku_lookup.py <SKU> [SINCE=YYYY-MM-DD]
Examples:
    python scripts/fulfilled_sku_lookup.py AC-LFOLIVE 2026-05-08
    python scripts/fulfilled_sku_lookup.py CH-FONT            # defaults SINCE to 90 days ago

Run from the AppyHour repo root (paths to get_shopify_auth are resolved absolutely, so cwd-independent).
"""
import collections
import csv
import json
import os
import sys
from datetime import date, timedelta

import requests

_AH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # AppyHour repo root
sys.path.insert(0, os.path.join(_AH, "AppyHourMCP"))
sys.path.insert(0, os.path.join(_AH, "InventoryReorder"))
from utils import get_shopify_auth

OUT_DIR = r"C:\Users\Work\Claude Projects\_outputs\reports"
STORE = "504ac4"   # Shopify store handle for admin links

VARIANT_Q = "query($q:String){productVariants(first:20,query:$q){nodes{sku displayName price}}}"
ORDER_Q = ("query($c:String,$q:String){orders(first:100,query:$q,after:$c,sortKey:CREATED_AT)"
           "{pageInfo{hasNextPage endCursor} nodes{name id createdAt fulfillments(first:5){createdAt}"
           " shippingAddress{city province zip} lineItems(first:60){nodes{sku quantity}}}}}")


def lookup(sku, since):
    base, h = get_shopify_auth()
    url = base + "/graphql.json"

    r = requests.post(url, headers=h, json={"query": VARIANT_Q, "variables": {"q": f"sku:{sku}"}},
                      timeout=60)
    variants = r.json().get("data", {}).get("productVariants", {}).get("nodes", [])
    print(f"=== VARIANT MATCH for sku:{sku} ===")
    print(json.dumps(variants, indent=1))
    if not variants:
        print(f"!! no variant matches sku:{sku} — check the SKU (confirm product identity first).")
        return

    qstr = f"sku:'{sku}' AND fulfillment_status:fulfilled"
    print(f"\n=== order query: {qstr}  (since {since}) ===")
    cur, match, scanned = None, [], 0
    while True:
        r = requests.post(url, headers=h, json={"query": ORDER_Q, "variables": {"c": cur, "q": qstr}},
                          timeout=90)
        try:
            d = r.json()
        except ValueError:
            print(f"NON-JSON resp status={r.status_code} body={r.text[:300]!r}")
            break
        if "errors" in d:
            print("ERR", json.dumps(d["errors"])[:600])
            break
        o = d["data"]["orders"]
        for x in o["nodes"]:
            scanned += 1
            li = [l for l in x["lineItems"]["nodes"] if (l.get("sku") or "") == sku]
            if not li:
                continue
            ffs = [f["createdAt"][:10] for f in x["fulfillments"] if f.get("createdAt")]
            ffdate = min(ffs) if ffs else None
            if ffdate and ffdate >= since:
                match.append((x, sum(l["quantity"] for l in li), ffdate))
        if o["pageInfo"]["hasNextPage"]:
            cur = o["pageInfo"]["endCursor"]
        else:
            break

    match.sort(key=lambda m: m[2])
    total_qty = sum(m[1] for m in match)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{sku}_fulfillments_since_{since}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order", "fulfilled_date", "qty", "city", "state", "zip", "shopify_id", "admin_url"])
        for x, qty, ffdate in match:
            sa = x["shippingAddress"] or {}
            oid = x["id"].split("/")[-1]
            admin = f"https://admin.shopify.com/store/{STORE}/orders/{oid}?link_source=search"
            w.writerow([x["name"], ffdate, qty, sa.get("city", ""), sa.get("province", ""),
                        sa.get("zip", ""), oid, admin])

    print(f"\n=== {len(match)} orders / {total_qty} units of {sku} fulfilled on/after {since} "
          f"(scanned {scanned} all-time {sku} orders) ===")
    print(f"CSV: {out}\n")

    wk, uq = collections.Counter(), collections.Counter()
    for x, qty, ffdate in match:
        wk[ffdate] += 1
        uq[ffdate] += qty
    print("by fulfillment date | orders | units")
    for d2 in sorted(wk):
        print(f"  {d2}   {wk[d2]:4d}   {uq[d2]:4d}")

    st = collections.Counter()
    for x, qty, ffdate in match:
        st[(x["shippingAddress"] or {}).get("province", "?")] += 1
    print("\ntop states | orders")
    for s, cnt in st.most_common(12):
        print(f"  {s or '?':22} {cnt:4d}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/fulfilled_sku_lookup.py <SKU> [SINCE=YYYY-MM-DD]")
        sys.exit(1)
    sku_arg = sys.argv[1]
    since_arg = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=90)).isoformat()
    lookup(sku_arg, since_arg)
