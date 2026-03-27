"""Check if repeat SKUs are paid, customer-chosen, or default curation."""
import requests, json, time, csv

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Non-food SKUs to ignore in repeat analysis
IGNORE_SKUS = {"PK-TCUST", "PK-FCUST", "PK-TSTG", "PR-CJAM-MONG", "PR-CJAM-OWC",
               "PR-CJAM-MDT", "PR-CJAM-SPN", "PR-CJAM-SS", "PR-CJAM-BYO",
               "CEX-EC", "CEX-EC-MONG", "CEX-EC-SS", "CEX-EC-OWC",
               "EX-EC", "EX-EA", "EX-EM", "CEX-EM"}

CSV_PATH = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\recharge-errors-2026-03-21.csv"
customers = []
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        repeat_skus = [s.strip() for s in row["Repeat SKUs"].split(",")]
        # Filter out non-food and AHB- SKUs
        food_repeats = [s for s in repeat_skus if s not in IGNORE_SKUS
                        and not s.startswith("AHB-") and not s.startswith("PK-")
                        and not s.startswith("PR-CJAM") and not s.startswith("CEX-")
                        and not s.startswith("EX-") and not s.startswith("BL-")]
        if food_repeats:
            customers.append({
                "email": row["Email"],
                "order_num": row["Current Order"].replace("#", ""),
                "food_repeats": food_repeats,
            })

print(f"Checking {len(customers)} orders for repeat food item sources...\n")

paid_items = []
chosen_items = []
default_items = []

for i, cust in enumerate(customers):
    order_num = cust["order_num"]

    # Fetch order from Shopify
    resp = requests.get(f"{REST_BASE}/orders.json", headers=HEADERS,
                        params={"name": order_num, "status": "any",
                                "fields": "id,name,line_items"}, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    if not orders:
        continue
    o = orders[0]
    order_gid = f"gid://shopify/Order/{o['id']}"

    # Check box_contents via GQL
    gql_resp = requests.post(GQL_URL, headers=HEADERS, json={"query": """
    query ($id: ID!) {
      order(id: $id) {
        lineItems(first: 30) {
          edges { node { sku customAttributes { key value } } }
        }
      }
    }""", "variables": {"id": order_gid}}, timeout=30)
    gql_data = gql_resp.json().get("data", {}).get("order", {})

    # Get box_contents
    box_contents = ""
    for edge in gql_data.get("lineItems", {}).get("edges", []):
        node = edge["node"]
        nsku = (node.get("sku") or "").strip()
        if nsku.startswith("AHB-"):
            for attr in node.get("customAttributes", []):
                if attr.get("key") == "box_contents":
                    box_contents = (attr.get("value") or "").lower()
                    break
            break

    # Classify each repeat food SKU
    for repeat_sku in cust["food_repeats"]:
        # Find it in line items
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != repeat_sku:
                continue
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            price = float(li.get("price", "0"))

            if "_rc_bundle" not in prop_names:
                paid_items.append({"order": f"#{order_num}", "sku": repeat_sku, "email": cust["email"], "price": price})
            elif box_contents and repeat_sku.lower().replace("-", " ") in box_contents:
                chosen_items.append({"order": f"#{order_num}", "sku": repeat_sku, "email": cust["email"]})
            else:
                # More flexible check for common names
                sku_names = {
                    "CH-MAFT": "marinated australian", "CH-BRZ": "prairie breeze",
                    "CH-BLR": "baked lemon", "MT-LONZ": "lonza", "MT-TUSC": "toscano",
                    "MT-JAHH": "honey & herb", "AC-DTCH": "dried tart cherr",
                    "AC-PRPE": "praline pecan", "AC-TCRISP": "tart cherry",
                    "AC-SRHUB": "strawberry rhubarb", "AC-BLBALS": "blackberry balsamic",
                    "AC-SDF": "sun-dried", "AC-RBOL": "olive oil", "MT-ASPK": "speck",
                    "CH-WWDI": "wooly wooly diablo", "CH-UCONE": "ubriacone",
                    "CH-EBCC": "everything bagel", "AC-MUSTCH": "mustard",
                    "AC-CFPH": "caramelized fig", "MT-SOP": "sopressata",
                    "MT-CAPO": "capocollo", "AC-HON": "honey",
                    "MT-SLRWG": "red wine", "MT-SFEN": "finocchiona",
                    "AC-ACRISP": "apricot", "MT-JAMS": "serrano",
                    "CH-BBLUE": "bay blue", "CH-SOT": "sottocenere",
                    "CH-MAU3": "manchego", "CH-LOSC": "cameros",
                    "CH-LOU": "lou bergier", "MT-PRO": "prosciutto",
                    "AC-MARC": "marcona", "AC-DALM": "chocolate covered almond",
                    "AC-PPCM": "piri piri", "AC-MEMB": "quince",
                    "AC-RPJ": "red pepper",
                }
                name_key = sku_names.get(repeat_sku, "").lower()
                if box_contents and name_key and name_key in box_contents:
                    chosen_items.append({"order": f"#{order_num}", "sku": repeat_sku, "email": cust["email"]})
                else:
                    default_items.append({"order": f"#{order_num}", "sku": repeat_sku, "email": cust["email"]})
            break

    time.sleep(0.5)
    if (i + 1) % 10 == 0:
        print(f"  Progress: {i+1}/{len(customers)}")

print(f"\n{'='*60}")
print(f"  Repeat food items breakdown:")
print(f"  Paid (customer bought): {len(paid_items)}")
print(f"  Customer-chosen (box_contents): {len(chosen_items)}")
print(f"  Default recipe (swappable): {len(default_items)}")
print(f"{'='*60}")

if paid_items:
    print(f"\n  Paid repeats:")
    for p in paid_items[:15]:
        print(f"    {p['order']} {p['sku']} ${p['price']:.2f} ({p['email']})")
    if len(paid_items) > 15:
        print(f"    ...+{len(paid_items)-15} more")

if chosen_items:
    print(f"\n  Customer-chosen repeats:")
    for c in chosen_items[:15]:
        print(f"    {c['order']} {c['sku']} ({c['email']})")
    if len(chosen_items) > 15:
        print(f"    ...+{len(chosen_items)-15} more")

if default_items:
    print(f"\n  Default recipe repeats (swappable):")
    from collections import Counter
    sku_counts = Counter(d["sku"] for d in default_items)
    print(f"  By SKU:")
    for sku, cnt in sku_counts.most_common():
        print(f"    {sku}: {cnt}")
