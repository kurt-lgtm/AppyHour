"""Cross-reference the 89 'standalone' paid AC-MARC orders with Recharge charges."""
import requests, json, time, csv
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

# Load queued charges CSV to find AC-MARC in Recharge
CSV_PATH = r"C:\Users\Work\Claude Projects\charges_queued-2026.03.20-10_31_27-7da7632d082d4eedaffca232ea525511.csv"

# Build set of emails with AC-MARC in Recharge charges
rc_marc_emails = {}  # email -> list of charge details
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        sku = (row.get("line_item_sku") or "").strip()
        if sku == "AC-MARC":
            email = (row.get("email") or "").strip().lower()
            charge_id = row.get("charge_id", "")
            sched = row.get("scheduled_at", "")
            if email not in rc_marc_emails:
                rc_marc_emails[email] = []
            rc_marc_emails[email].append({
                "charge_id": charge_id,
                "scheduled_at": sched,
            })

print(f"Recharge charges with AC-MARC: {len(rc_marc_emails)} unique emails")

# Now fetch the 89 standalone orders and cross-reference
STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items,email",
}
page = 0

from_recharge = []
not_recharge = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue

        items = o.get("line_items", [])
        # Skip if has AHB-X or BL-
        has_bundle = any(
            ((li.get("sku") or "").strip().startswith("AHB-X") or
             (li.get("sku") or "").strip().startswith("BL-"))
            and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
            for li in items
        )
        if has_bundle:
            continue

        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku != "AC-MARC":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                continue

            email = (o.get("email") or "").strip().lower()
            in_rc = email in rc_marc_emails

            info = {"order": o["name"], "email": email, "qty": qty}
            if in_rc:
                from_recharge.append(info)
            else:
                not_recharge.append(info)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"\nStandalone paid AC-MARC (no bundles, no curation):")
print(f"  Customer chose via Recharge: {len(from_recharge)} ({sum(r['qty'] for r in from_recharge)} units)")
print(f"  Shopify-only (not in RC charges): {len(not_recharge)} ({sum(r['qty'] for r in not_recharge)} units)")

if not_recharge:
    print(f"\n  Shopify-only orders (first 15):")
    for r in not_recharge[:15]:
        print(f"    {r['order']} {r['email']}")
    if len(not_recharge) > 15:
        print(f"    ...+{len(not_recharge)-15} more")
