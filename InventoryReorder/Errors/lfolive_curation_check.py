"""Check if any AC-LFOLIVE curation items are in 'no nuts' or similar curations."""
import requests, json, time
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0

curation_breakdown = defaultdict(list)

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue

        items = o.get("line_items", [])

        # Check for curation AC-LFOLIVE
        has_lfolive_curation = False
        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku != "AC-LFOLIVE":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                has_lfolive_curation = True

        if not has_lfolive_curation:
            continue

        # Find box SKU to determine curation
        box_sku = ""
        curation = ""
        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku.startswith("AHB-MCUST") or sku.startswith("AHB-LCUST"):
                box_sku = sku
                parts = sku.split("-")
                curation = parts[-1] if parts else ""
                break
            if sku.startswith("AHB-CUR-"):
                curation = sku.replace("AHB-CUR-", "")

        if not curation:
            # Try AHB-CUR- tag items
            for li in items:
                sku = (li.get("sku") or "").strip()
                if sku.startswith("AHB-CUR-"):
                    curation = sku.replace("AHB-CUR-", "")
                    break

        curation_breakdown[curation or "(unknown)"].append(o["name"])

    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Curation AC-LFOLIVE by curation type:")
print(f"{'Curation':<20} {'Count':>6}")
print("-" * 30)
total = 0
for cur in sorted(curation_breakdown.keys()):
    count = len(curation_breakdown[cur])
    total += count
    # Flag nut-related curations
    flag = ""
    lower = cur.lower()
    if "nut" in lower or "nms" in lower or "allerg" in lower or "free" in lower:
        flag = " <-- NO NUTS?"
    print(f"{cur:<20} {count:>6}{flag}")
print(f"{'TOTAL':<20} {total:>6}")

# Show known curation meanings
print(f"\nCuration key:")
print(f"  NMS = No Meat/Seafood")
print(f"  MONG = Monger's Choice")
print(f"  MDT = Mediterranean")
print(f"  OWC = Old World Classics")
print(f"  SPN = Spanish")
print(f"  ALPN/ALPT = Alpine")
print(f"  ISUN = Italian Sunday")
print(f"  HHIGH = Happy Hour Highlights")
print(f"  BYO = Build Your Own")
print(f"  SS = Seasonal Selection")
