"""Issue refunds for the 27 AC-LFOLIVE orders already edited (items already removed).

The items were set to qty 0 via order edit. This script just processes the refunds.
"""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

COMMIT = "--commit" in sys.argv
TARGET_SKU = "AC-LFOLIVE"

# Find orders that had AC-LFOLIVE removed (fulfillable_quantity=0 but quantity>0)
print("Fetching orders...")
targets = []
url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != TARGET_SKU:
                continue
            qty = li.get("quantity", 0)
            fq = li.get("fulfillable_quantity", qty)
            price = float(li.get("price", "0"))
            # Item was removed if quantity > 0 but fulfillable_quantity == 0
            # Also check properties — only paid items (no _rc_bundle)
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                continue
            if fq == 0 and qty > 0 and price > 0:
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "line_item_id": li["id"],
                    "qty": qty,
                    "price": price,
                })
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Found {len(targets)} orders needing refund\n")

if not targets:
    print("Nothing to refund.")
    sys.exit(0)

mode = "COMMIT" if COMMIT else "DRY-RUN"
print(f"{'Order':<12} {'Qty':>4} {'Refund':>8}")
print("-" * 30)
total = 0
for t in targets:
    amt = t["price"] * t["qty"]
    total += amt
    print(f"{t['order_name']:<12} {t['qty']:>4} ${amt:>7.2f}")
print("-" * 30)
print(f"{'TOTAL':<12} {len(targets):>4} ${total:>7.2f}")

if not COMMIT:
    print(f"\nDRY-RUN. Run with --commit to issue refunds.")
    sys.exit(0)

print(f"\nProcessing refunds...")
success = 0
failed = 0
for t in targets:
    order_id = t["order_id"]
    # Calculate refund
    calc_url = f"{REST_BASE}/orders/{order_id}/refunds/calculate.json"
    calc_body = {
        "refund": {
            "refund_line_items": [{
                "line_item_id": t["line_item_id"],
                "quantity": t["qty"],
            }],
        }
    }
    try:
        calc_resp = requests.post(calc_url, headers=HEADERS, json=calc_body, timeout=30)
        calc_resp.raise_for_status()
        transactions = calc_resp.json().get("refund", {}).get("transactions", [])
    except Exception as e:
        print(f"  {t['order_name']}: CALC FAILED - {e}")
        failed += 1
        continue

    time.sleep(0.3)

    # Issue refund
    refund_url = f"{REST_BASE}/orders/{order_id}/refunds.json"
    refund_body = {
        "refund": {
            "notify": True,
            "refund_line_items": [{
                "line_item_id": t["line_item_id"],
                "quantity": t["qty"],
            }],
            "transactions": transactions,
        }
    }
    try:
        ref_resp = requests.post(refund_url, headers=HEADERS, json=refund_body, timeout=30)
        ref_resp.raise_for_status()
        refund_data = ref_resp.json().get("refund", {})
        refund_total = sum(float(tx.get("amount", 0)) for tx in refund_data.get("transactions", []))
        print(f"  {t['order_name']}: REFUNDED ${refund_total:.2f}")
        success += 1
    except Exception as e:
        print(f"  {t['order_name']}: REFUND FAILED - {e}")
        failed += 1

    time.sleep(0.5)

print(f"\nDone: {success} refunded, {failed} failed")
