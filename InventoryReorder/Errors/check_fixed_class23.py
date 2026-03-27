"""Check which class 2/3 charges were fixed (404 = date-shuffled = fixed)."""
import csv
import json
import time
import requests

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
INPUT = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\rc-upcoming-class23-4b-2026-03-12-v2-enriched.csv"

with open(SETTINGS) as f:
    settings = json.load(f)

RC_TOKEN = settings.get("recharge_api_token", "")
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Accept": "application/json",
    "X-Recharge-Version": "2021-11",
}

# Read CSV
with open(INPUT, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# Filter class 2/3 only (Excel mangled "2/3" to "3-Feb")
class23 = [r for r in rows if r.get("class", "").strip() in ("3-Feb", "2/3")]
print(f"Checking {len(class23)} class 2/3 charges against Recharge API...")

fixed = []
unfixed = []
errors = []

for i, row in enumerate(class23):
    cid = row["charge_id"].strip()
    if not cid:
        continue

    for attempt in range(3):
        try:
            resp = requests.get(
                f"https://api.rechargeapps.com/charges/{cid}",
                headers=RC_HEADERS, timeout=30,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                time.sleep(retry_after)
                continue
            if resp.status_code == 404:
                fixed.append(row)
                break
            resp.raise_for_status()
            charge = resp.json().get("charge", {})
            # Check if still has blank box - if box_sku is populated now, it's fixed
            line_items = charge.get("line_items", [])
            has_blank = any(
                not li.get("sku") or li.get("sku") == "(blank)"
                for li in line_items
                if li.get("purchase_item_type") == "subscription"
            )
            if has_blank:
                unfixed.append(row)
            else:
                fixed.append(row)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                errors.append((cid, str(e)))
                break

    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(class23)} checked — {len(fixed)} fixed, {len(unfixed)} unfixed, {len(errors)} errors")

    time.sleep(0.3)

print(f"\n=== RESULTS ===")
print(f"Fixed (404 or no blank box): {len(fixed)}")
print(f"Unfixed (still have blank box): {len(unfixed)}")
print(f"Errors: {len(errors)}")

if fixed:
    print(f"\n--- Fixed charge IDs ---")
    for r in fixed:
        print(f"  {r['charge_id']}  {r['customer']}  {r.get('scheduled','')}")

if unfixed:
    print(f"\n--- Unfixed charge IDs (first 20) ---")
    for r in unfixed[:20]:
        print(f"  {r['charge_id']}  {r['customer']}  {r.get('scheduled','')}")

if errors:
    print(f"\n--- Errors ---")
    for cid, err in errors:
        print(f"  {cid}: {err}")
