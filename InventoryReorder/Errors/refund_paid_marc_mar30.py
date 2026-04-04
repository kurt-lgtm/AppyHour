# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Refund and remove standalone paid AC-MARC line items from _SHIP_2026-03-30 orders.

Targets AC-MARC items that:
  - Have no _rc_bundle property (paid add-on, not curation)
  - Have no discount (full $8 price)
  - Are on unfulfilled _SHIP_2026-03-30 orders

Issues a refund for the AC-MARC amount and removes the line item.

Usage:
    python refund_paid_marc_mar30.py              # dry-run
    python refund_paid_marc_mar30.py --commit     # apply
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
SHIP_TAG = "_SHIP_2026-03-30"
TARGET_SKU = "AC-MARC"

def fetch_targets():
    targets = []
    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if SHIP_TAG not in tags:
                continue
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != TARGET_SKU:
                    continue
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                if fq <= 0:
                    continue
                props = {p.get("name", "") for p in (li.get("properties") or []) if isinstance(p, dict)}
                if "_rc_bundle" in props:
                    continue  # skip curation items
                price = float(li.get("price", "0") or 0)
                total_discount = float(li.get("total_discount", "0") or 0)
                if price <= 0 or total_discount > 0:
                    continue  # skip free or discounted
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "line_item_id": li["id"],
                    "qty": fq,
                    "price": price,
                    "refund_amount": round(price * fq, 2),
                })
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.5)
    return targets

def get_transaction_id(order_id):
    """Get the original payment transaction ID for issuing a refund against."""
    resp = requests.get(f"{REST_BASE}/orders/{order_id}/transactions.json",
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    for txn in resp.json().get("transactions", []):
        if txn.get("kind") in ("sale", "capture") and txn.get("status") == "success":
            return txn["id"], txn.get("gateway", "")
    return None, None

def refund_and_remove(order_info):
    order_id = order_info["order_id"]
    name = order_info["order_name"]
    li_id = order_info["line_item_id"]
    qty = order_info["qty"]
    amount = order_info["refund_amount"]

    txn_id, gateway = get_transaction_id(order_id)
    if not txn_id:
        print(f"    FAILED {name}: no payment transaction found")
        return False

    time.sleep(0.2)

    payload = {
        "refund": {
            "notify": False,
            "note": "AC-MARC removed — out of stock, refund issued",
            "refund_line_items": [
                {
                    "line_item_id": li_id,
                    "quantity": qty,
                    "restock_type": "no_restock",
                }
            ],
            "transactions": [
                {
                    "parent_id": txn_id,
                    "amount": str(amount),
                    "kind": "refund",
                    "gateway": gateway,
                }
            ],
        }
    }

    resp = requests.post(f"{REST_BASE}/orders/{order_id}/refunds.json",
                         headers=HEADERS, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"    FAILED {name}: HTTP {resp.status_code} — {resp.text[:200]}")
        return False

    result = resp.json().get("refund", {})
    if result.get("id"):
        print(f"    OK {name}: refunded ${amount} and removed AC-MARC")
        return True
    else:
        print(f"    FAILED {name}: unexpected response — {resp.text[:200]}")
        return False

def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Refund + remove paid {TARGET_SKU} [{mode}]")
    print(f"  Ship tag: {SHIP_TAG}")
    print(f"{'='*60}\n")

    print(f"Fetching standalone paid {TARGET_SKU} orders...")
    targets = fetch_targets()
    print(f"  {len(targets)} orders found\n")
    total_refund = sum(t["refund_amount"] for t in targets)
    for t in targets:
        print(f"  {t['order_name']} — qty {t['qty']} @ ${t['price']} = ${t['refund_amount']} refund")
    print(f"\n  Total refund: ${total_refund:.2f}")

    if not COMMIT:
        print(f"\nDRY-RUN complete. Run with --commit to apply.")
        return

    print(f"\nApplying refunds...")
    s, f = 0, 0
    for t in targets:
        if refund_and_remove(t):
            s += 1
        else:
            f += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {s} refunded+removed, {f} failed")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
