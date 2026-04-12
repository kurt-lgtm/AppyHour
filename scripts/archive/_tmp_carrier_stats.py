# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Temp script: count fulfilled orders by carrier for a ship tag."""

import sys
import time
import re

sys.path.insert(0, ".")
sys.path.insert(0, "../GelPackCalculator")
from utils import get_shopify_auth
import requests

base, headers = get_shopify_auth()
ship_tag = "_SHIP_2026-03-29"

carrier_counts = {}
total = 0
url = f"{base}/orders.json"
params = {
    "status": "any",
    "fulfillment_status": "shipped",
    "limit": 250,
    "fields": "id,name,tags,fulfillments",
    "created_at_min": "2026-03-18T00:00:00",
    "created_at_max": "2026-03-30T00:00:00",
}
page = 0

while url:
    page += 1
    resp = requests.get(url, headers=headers, params=params if page == 1 else None, timeout=30)
    # Handle rate limiting
    if resp.status_code == 429:
        retry = float(resp.headers.get("Retry-After", 2))
        print(f"  Rate limited, waiting {retry}s...")
        time.sleep(retry)
        continue
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    if not orders:
        break
    for o in orders:
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if ship_tag not in tags:
            continue
        total += 1
        carrier = "Unknown"
        for f in o.get("fulfillments") or []:
            co = (f.get("tracking_company") or "").strip()
            if co:
                cl = co.lower()
                if "ontrac" in cl:
                    carrier = "OnTrac"
                elif "fedex" in cl:
                    carrier = "FedEx"
                elif "ups" in cl:
                    carrier = "UPS"
                elif "veho" in cl:
                    carrier = "veho"
                elif "usps" in cl:
                    carrier = "USPS"
                else:
                    carrier = co
                break
        carrier_counts[carrier] = carrier_counts.get(carrier, 0) + 1

    print(f"  Page {page}: {len(orders)} orders, {total} matching so far")
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.5)  # Be gentle with rate limits

print()
print(f"Ship tag: {ship_tag}")
print(f"Total fulfilled: {total}")
print(f"Pages fetched: {page}")
print()
for c in sorted(carrier_counts, key=carrier_counts.get, reverse=True):
    pct = carrier_counts[c] / total * 100 if total else 0
    print(f"{c}: {carrier_counts[c]} ({pct:.1f}%)")

# Issue rates - issues from March 23-29 by carrier (from ops sheet)
issues = {"OnTrac": 21, "veho": 8, "FedEx": 5, "UPS": 2}
print()
print("ISSUE RATES (March 23-29):")
for c in sorted(carrier_counts, key=carrier_counts.get, reverse=True):
    shipments = carrier_counts[c]
    iss = issues.get(c, 0)
    rate = iss / shipments * 100 if shipments else 0
    print(f"{c}: {iss} issues / {shipments} shipments = {rate:.2f}%")
