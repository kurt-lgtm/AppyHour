"""Find Class 2/3 and 4B in Recharge queued charges — with proper customer/sub IDs.

Excludes:
- One-time paid add-ons (EX-, BL-, customer extras without _rc_bundle)
- Customer-chosen duplicates (box_contents shows intentional qty > 1)
- Only flags _rc_bundle curation items as errors
"""
import requests, json, time, csv, re, os
from collections import Counter

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

# Product name -> SKU mapping for parsing box_contents
_NAME_MAP_FILE = os.path.join(os.path.dirname(__file__), "product_name_to_sku.json")
_NAME_TO_SKU = {}
if os.path.exists(_NAME_MAP_FILE):
    with open(_NAME_MAP_FILE, encoding="utf-8") as _f:
        _raw = json.load(_f)
        for _name, _info in _raw.items():
            _key = _name.strip().lower()
            if _key.endswith("*"):
                # Star variants (curation) override non-star
                _NAME_TO_SKU[_key.rstrip("*").strip()] = _info["sku"]
            else:
                if _key not in _NAME_TO_SKU:
                    _NAME_TO_SKU[_key] = _info["sku"]


def _parse_box_contents(text):
    """Parse box_contents string into {sku: qty}."""
    result = {}
    if not text:
        return result
    for line in text.replace("\\n", "\n").split("\n"):
        line = line.strip()
        m = re.match(r"(\d+)x\s+(.+)", line)
        if not m:
            continue
        qty = int(m.group(1))
        name = m.group(2).rstrip("*").strip().lower()
        sku = _NAME_TO_SKU.get(name)
        if sku:
            result[sku] = result.get(sku, 0) + qty
    return result


RC_TOKEN = settings["recharge_api_token"]
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Content-Type": "application/json",
    "X-Recharge-Version": "2021-11",
}

# Fetch queued charges — MUST use cursor pagination (since_id loops forever on Recharge)
print("Fetching queued charges...")
charges = []
cursor = None
while True:
    if cursor:
        params = {"cursor": cursor, "limit": 250}
    else:
        params = {"status": "queued", "limit": 250, "sort_by": "id-asc"}
    resp = requests.get("https://api.rechargeapps.com/charges",
                        headers=RC_HEADERS, params=params, timeout=30)
    time.sleep(0.5)
    if resp.status_code != 200:
        print(f"Error {resp.status_code}: {resp.text[:200]}")
        break
    data = resp.json()
    batch = data.get("charges", [])
    if not batch:
        break
    charges.extend(batch)
    cursor = data.get("next_cursor")
    print(f"  {len(charges)} charges...")
    if not cursor:
        break

print(f"Got {len(charges)} queued charges\n")

# Check what fields are available for customer info
if charges:
    sample = charges[0]
    cust_fields = [k for k in sample.keys() if 'customer' in k.lower() or 'email' in k.lower()]
    print(f"Customer-related fields: {cust_fields}")
    print(f"Sample keys: {list(sample.keys())[:20]}")
    # Print sample billing address
    ba = sample.get("billing_address", {})
    print(f"Sample billing_address keys: {list(ba.keys()) if ba else 'None'}")
    print(f"Sample customer_id: {sample.get('customer_id')}")
    print(f"Sample email: {sample.get('email')}")
    print()

results = []

for c in charges:
    charge_id = c["id"]
    scheduled = (c.get("scheduled_at") or "")[:10]
    customer_id = str(c.get("customer_id", "") or "")
    email = c.get("email", "") or ""
    line_items = c.get("line_items", [])

    # Customer name
    ba = c.get("billing_address") or {}
    first = ba.get("first_name", "") or ""
    last = ba.get("last_name", "") or ""
    customer_name = f"{first} {last}".strip()

    # Parse line items — separate curation (_rc_bundle) from paid/one-time
    box_skus = []
    curation_food = []      # _rc_bundle curation items only
    paid_skus = []           # one-time or non-_rc_bundle items
    has_box_product = False
    has_blank_box = False
    box_contents_text = None
    has_bl_bundle = False    # BL- paid bundle present
    bl_bundle_skus = set()   # SKUs that are BL- bundle components

    # First pass: detect BL- bundles
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        if sku.startswith("BL-"):
            has_bl_bundle = True

    for li in line_items:
        sku = (li.get("sku") or "").strip()
        title = (li.get("title") or "")
        qty = li.get("quantity", 1)
        ptype = li.get("purchase_item_type", "")
        props = li.get("properties", [])
        prop_keys = {p.get("name", "") for p in props}
        is_rc_bundle = "_rc_bundle" in prop_keys

        # Check for box_contents
        for p in props:
            if p.get("name") == "box_contents" and p.get("value"):
                box_contents_text = p["value"]

        if "appyhour box" in title.lower() or "appy hour" in title.lower():
            has_box_product = True
            if not sku:
                has_blank_box = True

        if sku.startswith("AHB-"):
            box_skus.append(sku)

        if sku.startswith("BL-"):
            paid_skus.append(sku)
            continue

        if sku.startswith(("CH-", "MT-", "AC-", "CEX-", "EX-")):
            # Only subscription + _rc_bundle items are true curation
            # One-time items are ALWAYS paid (bundle components, add-ons, etc.)
            # even if they have _rc_bundle (Recharge quirk on BL- bundle expansion)
            if ptype == "subscription" and is_rc_bundle:
                for _ in range(qty):
                    curation_food.append(sku)
            else:
                paid_skus.append(sku)

    has_custom_box = any(s.startswith(("AHB-MCUST", "AHB-LCUST")) for s in box_skus)
    has_monthly_box = any(s in ("AHB-MED", "AHB-LGE", "AHB-CMED") for s in box_skus)

    # CLASS 2/3: blank box SKU with curation items (not just paid extras)
    if has_blank_box and not has_custom_box and not has_monthly_box:
        # Only flag if there are _rc_bundle curation food items (not just paid add-ons)
        curation_skus = [s for s in curation_food if s.startswith(("CH-", "MT-", "AC-", "CEX-"))]
        if curation_skus:
            detail = f"Curation SKUs: {', '.join(sorted(set(curation_skus)))}"
            if paid_skus:
                detail += f" | Paid extras (kept): {', '.join(sorted(set(paid_skus)))}"
            results.append({
                "class": "2/3",
                "class_desc": "Box product with blank SKU + curation items",
                "charge_id": str(charge_id),
                "scheduled": scheduled,
                "customer": customer_name,
                "email": email,
                "rc_customer_id": customer_id,
                "rc_subscription_id": "",
                "box_sku": "(blank)",
                "details": detail,
            })

    # CLASS 4B: duplicate curation items (not customer-chosen)
    if curation_food:
        food_counts = Counter(curation_food)
        dups = {s: cnt for s, cnt in food_counts.items() if cnt > 1}

        # Check box_contents for intentional duplicates
        if dups and box_contents_text:
            bc_skus = _parse_box_contents(box_contents_text)
            dups = {sku: total for sku, total in dups.items()
                    if bc_skus.get(sku, 1) < total}

        if dups:
            box = box_skus[0] if box_skus else ""
            results.append({
                "class": "4B",
                "class_desc": "Doubled curation items in charge",
                "charge_id": str(charge_id),
                "scheduled": scheduled,
                "customer": customer_name,
                "email": email,
                "rc_customer_id": customer_id,
                "rc_subscription_id": "",
                "box_sku": box,
                "details": f"Duplicates: {dups} | Total curation food: {len(curation_food)}",
            })

print(f"Found {len(results)} issues\n")
class_counts = Counter(r["class"] for r in results)
for cls, cnt in class_counts.most_common():
    print(f"  Class {cls}: {cnt}")

# Look up subscription IDs by email
print("\nLooking up subscription IDs...")
cache = {}
for i, row in enumerate(results):
    email = row["email"]
    rc_cust_id = row["rc_customer_id"]

    if email and email in cache:
        row["rc_customer_id"] = cache[email][0]
        row["rc_subscription_id"] = cache[email][1]
        continue

    # If we have customer_id, use it; otherwise lookup by email
    if not rc_cust_id and email:
        resp = requests.get("https://api.rechargeapps.com/customers",
                            headers=RC_HEADERS, params={"email": email})
        time.sleep(0.3)
        if resp.status_code == 200 and resp.json().get("customers"):
            rc_cust_id = str(resp.json()["customers"][0]["id"])
            row["rc_customer_id"] = rc_cust_id

    if not rc_cust_id:
        if email:
            cache[email] = ("", "")
        continue

    resp2 = requests.get("https://api.rechargeapps.com/subscriptions",
                         headers=RC_HEADERS,
                         params={"customer_id": rc_cust_id, "status": "active"})
    time.sleep(0.3)
    sub_id = ""
    if resp2.status_code == 200:
        for s in resp2.json().get("subscriptions", []):
            sku = (s.get("sku") or "").strip()
            if sku.startswith(("AHB-MCUST", "AHB-LCUST", "AHB-MED", "AHB-LGE", "AHB-CMED")):
                sub_id = str(s["id"])
                break
    row["rc_subscription_id"] = sub_id
    if email:
        cache[email] = (rc_cust_id, sub_id)

# Write CSV
outfile = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\rc-upcoming-class234b-2026-03-12.csv"
fieldnames = ["class", "class_desc", "charge_id", "scheduled", "customer", "email",
              "rc_customer_id", "rc_subscription_id", "box_sku", "details"]
with open(outfile, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

print(f"\nWrote {len(results)} rows to {outfile}")

import shutil
shutil.copy2(outfile, r"C:\Users\Work\Downloads\rc-upcoming-class234b-2026-03-12.csv")
print("Copied to Downloads")

print("\n=== ALL RESULTS ===")
for r in results:
    print(f"  [{r['class']}] Charge {r['charge_id']} ({r['scheduled']}) {r['customer']} ({r['email']})")
    print(f"    RC Cust: {r['rc_customer_id']} | RC Sub: {r['rc_subscription_id']} | Box: {r['box_sku']}")
    print(f"    {r['details']}")
