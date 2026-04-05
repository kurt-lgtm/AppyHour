"""Find what SKUs are in tray subscription charges."""
import json
import os
import sys

# Load settings for API token
settings_paths = [
    os.path.join(os.path.dirname(__file__), "..", "..", "fulfillment_web", "inventory_reorder_settings.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "inventory_reorder_settings.json"),
    os.path.join(os.path.dirname(__file__), "..", "inventory_reorder_settings.json"),
]

settings = {}
for p in settings_paths:
    if os.path.exists(p):
        with open(p) as f:
            settings = json.load(f)
        break

token = settings.get("recharge_api_token", "")
if not token:
    print("No recharge_api_token found in settings")
    sys.exit(1)

import requests

headers = {
    "X-Recharge-Access-Token": token,
    "Accept": "application/json",
    "X-Recharge-Version": "2021-11",
}

# Fetch a page of queued charges
resp = requests.get(
    "https://api.rechargeapps.com/charges",
    headers=headers,
    params={"status": "queued", "limit": 250, "sort_by": "id-asc"},
    timeout=30,
)
resp.raise_for_status()
charges = resp.json().get("charges", [])

tray_items = []
for charge in charges:
    line_items = charge.get("line_items", [])
    has_tray = any("TRAY" in (li.get("sku") or "").upper() for li in line_items)
    if has_tray:
        skus = [(li.get("sku", ""), li.get("title", ""), li.get("quantity", 1)) for li in line_items]
        tray_items.append(skus)
        if len(tray_items) >= 5:
            break

if not tray_items:
    print("No tray charges found in first 250 queued charges")
else:
    print(f"Found {len(tray_items)} tray charges. Line items:\n")
    for i, items in enumerate(tray_items, 1):
        print(f"--- Tray Charge {i} ---")
        for sku, title, qty in items:
            print(f"  {sku:20s}  qty={qty}  {title}")
        print()
