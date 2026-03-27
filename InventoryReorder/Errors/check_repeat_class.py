"""Check if repeat-SKU orders have Class 4B (duplicate items) or Class 13 issues."""
import requests, json, time, csv
from collections import Counter

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

CSV_PATH = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\recharge-errors-2026-03-21.csv"
customers = []
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        customers.append({
            "email": row["Email"],
            "order_num": row["Current Order"].replace("#", ""),
            "repeat_skus": row["Repeat SKUs"],
        })

print(f"Checking {len(customers)} orders for Class 4B/13...\n")

class_4b = []
class_13 = []
valid_double_sub = []
clean = []

for i, cust in enumerate(customers):
    order_num = cust["order_num"]
    resp = requests.get(f"{REST_BASE}/orders.json", headers=HEADERS,
                        params={"name": order_num, "status": "any",
                                "fields": "id,name,line_items,tags"}, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    if not orders:
        continue
    o = orders[0]

    items = o.get("line_items", [])

    # Count active SKUs (fq > 0)
    sku_counts = Counter()
    has_blank_sku_box = False
    ahb_count = 0
    has_class13_box = False
    food_items = []

    for li in items:
        sku = (li.get("sku") or "").strip()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if fq <= 0:
            continue
        if sku:
            sku_counts[sku] += fq
        else:
            # Blank SKU — check if it's a box product
            title = (li.get("title") or "").lower()
            if "appyhour" in title or "box" in title or "membership" in title:
                has_blank_sku_box = True
                # Class 13: month-to-month membership with curation items
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    has_class13_box = True

        if sku.startswith("AHB-"):
            ahb_count += 1
        if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-")):
            food_items.append(sku)

    # Class 4B: any food SKU appears 2+ times
    dupes = {sku: cnt for sku, cnt in sku_counts.items()
             if cnt >= 2 and any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))}

    # Class 13: blank SKU box + curation food items
    has_curation_food = any(
        (li.get("sku") or "").strip().startswith(("CH-", "MT-", "AC-"))
        and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
        and "_rc_bundle" in {p.get("name", "") for p in (li.get("properties", []) or []) if isinstance(p, dict)}
        for li in items
    )

    # Double sub: 2+ AHB- items
    is_double_sub = ahb_count >= 2

    if dupes:
        class_4b.append({"order": f"#{order_num}", "email": cust["email"], "dupes": dupes})
        print(f"  [{i+1}] #{order_num}: CLASS 4B — {dupes}")
    elif has_class13_box and has_curation_food:
        class_13.append({"order": f"#{order_num}", "email": cust["email"]})
        print(f"  [{i+1}] #{order_num}: CLASS 13 — blank SKU box + curation")
    elif is_double_sub:
        valid_double_sub.append({"order": f"#{order_num}", "email": cust["email"], "ahb_count": ahb_count})
        print(f"  [{i+1}] #{order_num}: DOUBLE SUB ({ahb_count} AHB-)")
    else:
        clean.append({"order": f"#{order_num}", "email": cust["email"]})
        print(f"  [{i+1}] #{order_num}: CLEAN (no 4B/13, repeats from recipe overlap)")

    time.sleep(0.5)

print(f"\n{'='*60}")
print(f"  Class 4B (duplicate items): {len(class_4b)}")
print(f"  Class 13 (blank box + curation): {len(class_13)}")
print(f"  Double sub (valid): {len(valid_double_sub)}")
print(f"  Clean (recipe overlap): {len(clean)}")
print(f"{'='*60}")
