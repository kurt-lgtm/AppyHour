# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Count CEX-EC per curation from Recharge queued charges up to 4/4."""
import sys
import time
import json
from pathlib import Path
from collections import defaultdict
from datetime import date

sys.path.insert(0, str(Path(__file__).parent / "AppyHourMCP"))
sys.path.insert(0, str(Path(__file__).parent / "InventoryReorder"))
from utils import get_shopify_auth

import requests

SETTINGS_PATH = str(Path(__file__).parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json")
with open(SETTINGS_PATH) as f:
    settings = json.load(f)

token = settings.get("recharge_api_token", "")
headers = {
    "X-Recharge-Access-Token": token,
    "X-Recharge-Version": "2021-11",
    "Accept": "application/json",
}

# Fetch queued charges through 4/4
all_charges = []
params = {
    "status": "queued",
    "limit": 250,
    "sort_by": "id-asc",
    "scheduled_at_min": "2026-03-29",
    "scheduled_at_max": "2026-04-05",
}
page = 0
while True:
    page += 1
    resp = requests.get("https://api.rechargeapps.com/charges", headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    charges = data.get("charges", [])
    if not charges:
        break
    all_charges.extend(charges)
    sys.stderr.write(f"\r  Fetched {len(all_charges)} charges (page {page})...")
    next_cursor = data.get("next_cursor")
    if not next_cursor:
        break
    params = {"cursor": next_cursor, "limit": 250}
    time.sleep(0.5)

sys.stderr.write(f"\n  Total: {len(all_charges)} charges\n")

# Resolve curation from box SKU
KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
}
MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS", "AHB-CUR-NMS"}

def resolve_curation(box_sku):
    upper = box_sku.upper()
    for pat in MONTHLY_PATTERNS:
        if upper == pat or upper.startswith(pat + "-"):
            if "CMED" in upper:
                return "CMED"
            return "MONTHLY"
    parts = upper.replace("AHB-", "").split("-")
    for p in reversed(parts):
        if p in KNOWN_CURATIONS:
            return p
    return None

def is_large_box(box_sku):
    upper = box_sku.upper()
    return "LCUST" in upper or upper == "AHB-LGE"

# Count CEX-EC per curation
cexec_by_cur = defaultdict(int)
total_charges_counted = 0

for charge in all_charges:
    sched = (charge.get("scheduled_at") or "")[:10]
    if not sched:
        continue
    try:
        d = date.fromisoformat(sched)
    except ValueError:
        continue
    if d > date(2026, 4, 4):
        continue

    # Find box SKU
    box_sku = None
    has_cexec = False
    for item in charge.get("line_items", []):
        sku = (item.get("sku") or "").strip()
        if sku.upper().startswith("AHB-"):
            box_sku = sku
        if sku.upper().startswith("CEX-EC") or sku.upper().startswith("CEX-E"):
            has_cexec = True

    if not has_cexec or not box_sku:
        continue

    if not is_large_box(box_sku):
        continue  # CEX-EC only applies to large boxes

    cur = resolve_curation(box_sku)
    if cur:
        cexec_by_cur[cur] += 1
        total_charges_counted += 1

print(f"CEX-EC counts by curation (charges through 4/4):")
print(f"{'Curation':<12s} {'Count':>6s}")
print(f"{'-'*12} {'-'*6}")
for cur in ["ALPN", "BYO", "CMED", "HHIGH", "ISUN", "MDT", "MONG", "MONTHLY", "MS", "OWC", "SPN"]:
    ct = cexec_by_cur.get(cur, 0)
    print(f"{cur:<12s} {ct:>6d}")
print(f"{'-'*12} {'-'*6}")
print(f"{'TOTAL':<12s} {total_charges_counted:>6d}")
