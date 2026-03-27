"""Check repeat-SKU orders for Class 4B: only flag curation dupes on single-box orders."""
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

print(f"Checking {len(customers)} orders for true Class 4B...\n")

class_4b = []
valid = []

for i, cust in enumerate(customers):
    order_num = cust["order_num"]
    resp = requests.get(f"{REST_BASE}/orders.json", headers=HEADERS,
                        params={"name": order_num, "status": "any",
                                "fields": "id,name,line_items"}, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    if not orders:
        continue
    o = orders[0]
    items = o.get("line_items", [])

    # Count AHB- boxes and BL- bundles (active only)
    ahb_count = 0
    bl_count = 0
    for li in items:
        sku = (li.get("sku") or "").strip()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if fq <= 0:
            continue
        if sku.startswith("AHB-"):
            ahb_count += 1
        if sku.startswith("BL-"):
            bl_count += 1

    # Only check single-box orders (no double subs, no bundles adding dupes)
    if ahb_count >= 2 or bl_count >= 1:
        valid.append({"order": f"#{order_num}", "reason": f"multi-box ({ahb_count} AHB, {bl_count} BL)"})
        print(f"  [{i+1}] #{order_num}: VALID (multi-box/bundle)")
        time.sleep(0.5)
        continue

    # Count only _rc_bundle food items
    curation_skus = Counter()
    for li in items:
        sku = (li.get("sku") or "").strip()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if fq <= 0:
            continue
        if not any(sku.startswith(p) for p in ("CH-", "MT-", "AC-")):
            continue
        props = li.get("properties", []) or []
        prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
        if "_rc_bundle" in prop_names:
            curation_skus[sku] += fq

    # Flag 4B: curation item appears 2+ times
    dupes = {sku: cnt for sku, cnt in curation_skus.items() if cnt >= 2}

    if dupes:
        # Check box_contents — if customer chose 2x of something, it's valid
        gql_url = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
        gql_resp = requests.post(gql_url, headers=HEADERS, json={"query": """
        query ($id: ID!) {
          order(id: $id) {
            lineItems(first: 30) {
              edges { node { sku customAttributes { key value } } }
            }
          }
        }""", "variables": {"id": f"gid://shopify/Order/{o['id']}"}}, timeout=30)
        gql_data = gql_resp.json().get("data", {}).get("order", {})
        box_contents = ""
        for edge in gql_data.get("lineItems", {}).get("edges", []):
            node = edge["node"]
            nsku = (node.get("sku") or "").strip()
            if nsku.startswith("AHB-"):
                for attr in node.get("customAttributes", []):
                    if attr.get("key") == "box_contents":
                        box_contents = (attr.get("value") or "")
                        break
                break

        # Parse box_contents for quantities
        true_dupes = {}
        for sku, cnt in dupes.items():
            # Check if box_contents has 2x or 3x of this item
            chosen_qty = 0
            if box_contents:
                for line in box_contents.split("\n"):
                    line_lower = line.lower().strip()
                    # Check if SKU name appears
                    sku_names = {
                        "CH-MAFT": "marinated australian", "CH-BLR": "baked lemon",
                        "CH-BRZ": "prairie breeze", "CH-MCPC": "mccall",
                        "MT-SOP": "sopressata", "AC-RPJ": "red pepper",
                        "AC-ACRISP": "apricot", "AC-HON": "honey",
                        "AC-SRHUB": "strawberry rhubarb", "AC-PPCM": "piri piri",
                        "AC-MUSTCH": "mustard", "AC-SDF": "sun-dried",
                        "MT-PRO": "prosciutto", "CH-CSGOD": "beauty vintage",
                    }
                    name = sku_names.get(sku, "").lower()
                    if name and name in line_lower:
                        # Extract qty: "2x Item Name" or "1x Item Name"
                        if line_lower.startswith(("2x", "3x")):
                            chosen_qty = int(line_lower[0])
                        else:
                            chosen_qty = 1
            if chosen_qty >= cnt:
                pass  # Customer chose this many, valid
            else:
                true_dupes[sku] = cnt

        if true_dupes:
            class_4b.append({"order": f"#{order_num}", "email": cust["email"], "dupes": true_dupes})
            print(f"  [{i+1}] #{order_num}: CLASS 4B -- {true_dupes}")
        else:
            valid.append({"order": f"#{order_num}", "reason": "customer chose dupes"})
            print(f"  [{i+1}] #{order_num}: VALID (customer chose qty in box_contents)")
    else:
        valid.append({"order": f"#{order_num}", "reason": "no curation dupes"})
        print(f"  [{i+1}] #{order_num}: CLEAN")

    time.sleep(0.5)

print(f"\n{'='*60}")
print(f"  True Class 4B (curation dupes, single box): {len(class_4b)}")
print(f"  Valid/Clean: {len(valid)}")
print(f"{'='*60}")

if class_4b:
    print(f"\n  Class 4B orders to fix:")
    for c in class_4b:
        print(f"    {c['order']} {c['email']}: {c['dupes']}")
