# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""CEX-EC counts by curation — Recharge (through 4/4) + Shopify (_SHIP_2026-04-06)."""

import sys, time, json
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

KNOWN_CURATIONS = {"MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT", "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS"}
MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS", "AHB-CUR-NMS"}

def resolve_curation(box_sku):
    upper = box_sku.upper()
    for pat in MONTHLY_PATTERNS:
        if upper == pat or upper.startswith(pat + "-"):
            return "CMED" if "CMED" in upper else "MONTHLY"
    parts = upper.replace("AHB-", "").split("-")
    for p in reversed(parts):
        if p in KNOWN_CURATIONS:
            return p
    return None

def is_large_box(box_sku):
    upper = box_sku.upper()
    return "LCUST" in upper or upper == "AHB-LGE"

# --- Recharge ---
token = settings.get("recharge_api_token", "")
rc_headers = {"X-Recharge-Access-Token": token, "X-Recharge-Version": "2021-11", "Accept": "application/json"}

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
    resp = requests.get("https://api.rechargeapps.com/charges", headers=rc_headers, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    charges = data.get("charges", [])
    if not charges:
        break
    all_charges.extend(charges)
    sys.stderr.write(f"\r  RC: {len(all_charges)} charges (page {page})...")
    nc = data.get("next_cursor")
    if not nc:
        break
    params = {"cursor": nc, "limit": 250}
    time.sleep(0.5)
sys.stderr.write(f"\n  RC total: {len(all_charges)}\n")

rc_cexec = defaultdict(int)
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
    box_sku = None
    has_cexec = False
    for item in charge.get("line_items", []):
        sku = (item.get("sku") or "").strip()
        if sku.upper().startswith("AHB-"):
            box_sku = sku
        if sku.upper().startswith("CEX-EC") or sku.upper().startswith("CEX-E"):
            has_cexec = True
    if not has_cexec or not box_sku or not is_large_box(box_sku):
        continue
    cur = resolve_curation(box_sku)
    if cur:
        rc_cexec[cur] += 1

# --- Shopify ---
base, sh_headers = get_shopify_auth()
SHIP_TAG = "_SHIP_2026-04-06"

all_orders = []
url = f"{base}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
while url:
    resp = requests.get(url, headers=sh_headers, params=params, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    for o in orders:
        if SHIP_TAG in (o.get("tags") or ""):
            all_orders.append(o)
    link = resp.headers.get("Link", "")
    url = None
    params = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                break
    time.sleep(0.1)

sys.stderr.write(f"  Shopify: {len(all_orders)} orders with {SHIP_TAG}\n")

sh_cexec = defaultdict(int)
for o in all_orders:
    items = o.get("line_items", [])
    box_sku = None
    has_cexec = False
    for li in items:
        sku = (li.get("sku") or "").strip()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if fq <= 0:
            continue
        if sku.upper().startswith("AHB-"):
            box_sku = sku
        if sku.upper().startswith("CEX-EC") or sku.upper().startswith("CEX-E"):
            has_cexec = True
    if not has_cexec or not box_sku or not is_large_box(box_sku):
        continue
    cur = resolve_curation(box_sku)
    if cur:
        sh_cexec[cur] += 1

# --- Combined ---
all_curs = ["ALPN", "BYO", "CMED", "HHIGH", "ISUN", "MDT", "MONG", "MONTHLY", "MS", "OWC", "SPN"]
print(f"\nCEX-EC counts — Recharge (thru 4/4) + Shopify ({SHIP_TAG})")
print(f"{'Curation':<12s} {'RC':>6s} {'Shopify':>8s} {'Total':>7s}")
print(f"{'-' * 12} {'-' * 6} {'-' * 8} {'-' * 7}")
grand_rc = grand_sh = grand_total = 0
for cur in all_curs:
    rc = rc_cexec.get(cur, 0)
    sh = sh_cexec.get(cur, 0)
    t = rc + sh
    grand_rc += rc
    grand_sh += sh
    grand_total += t
    print(f"{cur:<12s} {rc:>6d} {sh:>8d} {t:>7d}")
print(f"{'-' * 12} {'-' * 6} {'-' * 8} {'-' * 7}")
print(f"{'TOTAL':<12s} {grand_rc:>6d} {grand_sh:>8d} {grand_total:>7d}")
