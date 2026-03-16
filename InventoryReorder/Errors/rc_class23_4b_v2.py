"""Find Class 2/3 and 4B in Recharge queued charges with customer + subscription IDs.
Uses cursor-based pagination (required for Recharge API 2021-11)."""
import requests, json, time, csv
from collections import Counter

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN = settings["recharge_api_token"]
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Content-Type": "application/json",
    "X-Recharge-Version": "2021-11",
}

# Fetch queued charges using cursor pagination
print("Fetching queued charges...")
charges = []
cursor = None
page = 0
while True:
    page += 1
    if cursor:
        params = {"cursor": cursor, "limit": 250}
    else:
        params = {"status": "queued", "limit": 250, "sort_by": "id-asc"}
    resp = requests.get("https://api.rechargeapps.com/charges", headers=RC_HEADERS, params=params, timeout=30)
    time.sleep(0.5)
    if resp.status_code != 200:
        print(f"  Page {page} error {resp.status_code}: {resp.text[:100]}")
        break
    data = resp.json()
    batch = data.get("charges", [])
    if not batch:
        break
    charges.extend(batch)
    print(f"  Page {page}: {len(batch)} charges (total: {len(charges)})")
    next_cursor = data.get("next_cursor")
    if not next_cursor:
        break
    cursor = next_cursor

print(f"Got {len(charges)} queued charges\n")

results = []

for c in charges:
    charge_id = c["id"]
    scheduled = (c.get("scheduled_at") or "")[:10]
    cust_obj = c.get("customer") or {}
    customer_id = str(cust_obj.get("id", "") or "")
    email = cust_obj.get("email", "") or ""
    line_items = c.get("line_items", [])
    ba = c.get("billing_address") or {}
    customer_name = f"{ba.get('first_name','')} {ba.get('last_name','')}".strip()

    box_skus = []
    food_skus = []
    food_skus_by_sub = {}  # {sku: set of subscription_ids} — to distinguish add-ons from bugs
    has_blank_box = False

    # First pass: find subscription_ids of paid bundles (BL- products)
    bundle_sub_ids = set()
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        if sku.startswith("BL-"):
            sub_id = str(li.get("subscription_id") or li.get("purchase_item_id") or "")
            bundle_sub_ids.add(sub_id)

    # Second pass: collect food items, skipping paid bundle items
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        title = (li.get("title") or "")
        qty = li.get("quantity", 1)
        sub_id = str(li.get("subscription_id") or li.get("purchase_item_id") or "")

        if "appyhour box" in title.lower() and not sku:
            has_blank_box = True
        if sku.startswith("AHB-"):
            box_skus.append(sku)
        if sku.startswith(("CH-", "MT-", "AC-")):
            # Skip food items from paid bundle subscriptions (BL- products)
            if sub_id in bundle_sub_ids:
                continue
            for _ in range(qty):
                food_skus.append(sku)
            food_skus_by_sub.setdefault(sku, set()).add(sub_id)

    has_real_box = any(s.startswith(("AHB-MCUST", "AHB-LCUST")) or s in ("AHB-MED", "AHB-LGE", "AHB-CMED") for s in box_skus)

    # CLASS 2/3
    if has_blank_box and not has_real_box:
        all_skus = [li.get("sku", "") for li in line_items if li.get("sku")]
        results.append({
            "class": "2/3", "charge_id": str(charge_id), "scheduled": scheduled,
            "customer": customer_name, "email": email, "rc_customer_id": customer_id,
            "rc_subscription_id": "", "box_sku": "(blank)",
            "details": f"SKUs: {', '.join(all_skus) if all_skus else '(none)'}",
        })

    # CLASS 4B — only flag duplicates from the SAME subscription (system bug).
    # If a SKU appears across different subscription IDs, customer added it intentionally.
    if food_skus:
        dups = {s: cnt for s, cnt in Counter(food_skus).items() if cnt > 1}
        # Filter out customer add-ons: keep only dupes where all instances share one subscription
        system_dups = {s: cnt for s, cnt in dups.items() if len(food_skus_by_sub.get(s, set())) <= 1}
        if system_dups:
            results.append({
                "class": "4B", "charge_id": str(charge_id), "scheduled": scheduled,
                "customer": customer_name, "email": email, "rc_customer_id": customer_id,
                "rc_subscription_id": "", "box_sku": box_skus[0] if box_skus else "",
                "details": f"Dups: {system_dups} | Total food: {len(food_skus)}",
            })

print(f"Found {len(results)} issues")
for cls, cnt in Counter(r["class"] for r in results).most_common():
    print(f"  Class {cls}: {cnt}")

# Look up subscription IDs
print("\nLooking up subscription IDs...")
cache = {}
for i, row in enumerate(results):
    cid = row["rc_customer_id"]
    if cid in cache:
        row["rc_subscription_id"] = cache[cid]
        continue
    if not cid:
        continue
    resp = requests.get("https://api.rechargeapps.com/subscriptions",
                        headers=RC_HEADERS, params={"customer_id": cid, "status": "active"}, timeout=30)
    time.sleep(0.3)
    sub_id = ""
    if resp.status_code == 200:
        for s in resp.json().get("subscriptions", []):
            sku = (s.get("sku") or "").strip()
            if sku.startswith(("AHB-MCUST", "AHB-LCUST", "AHB-MED", "AHB-LGE", "AHB-CMED")):
                sub_id = str(s["id"])
                break
    row["rc_subscription_id"] = sub_id
    cache[cid] = sub_id

# Write CSV to Downloads
outfile = r"C:\Users\Work\Downloads\rc-upcoming-class23-4b-2026-03-12-v3.csv"
fieldnames = ["class", "charge_id", "scheduled", "customer", "email",
              "rc_customer_id", "rc_subscription_id", "box_sku", "details"]
with open(outfile, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)
print(f"\nWrote {len(results)} rows to {outfile}")

for r in results:
    print(f"  [{r['class']}] Charge {r['charge_id']} ({r['scheduled']}) {r['customer']} ({r['email']})")
    print(f"    RC Cust: {r['rc_customer_id']} | RC Sub: {r['rc_subscription_id']} | Box: {r['box_sku']}")
    print(f"    {r['details']}")
