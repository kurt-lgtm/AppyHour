"""Find orders where curation didn't rotate — same suffix on Recharge sub and Shopify order.

Checks all unfulfilled orders with stale _SHIP prop dates against Recharge subscriptions.
If the subscription still has the same curation suffix as the order box, rotation never happened.

Usage:
    python check_rotation_repeats.py
"""
import requests, json, time, csv, os
from datetime import datetime

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

RC_TOKEN = settings.get("recharge_api_token", "")
RC_BASE = "https://api.rechargeapps.com"
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "X-Recharge-Version": "2021-11",
    "Content-Type": "application/json",
}

CUSTOM_PREFIXES = ("AHB-MCUST", "AHB-LCUST")


def fetch_all_unfulfilled():
    orders = []
    url = f"{BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,tags,line_items,customer,email",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching Shopify page {page}... ({len(orders)} so far)")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        orders.extend(resp.json().get("orders", []))
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return orders


def get_curation_suffix(sku):
    """AHB-MCUST-CORS-MDT -> MDT"""
    parts = sku.split("-")
    return parts[-1] if len(parts) >= 3 else None


def rc_get_subscription(sub_id):
    try:
        resp = requests.get(f"{RC_BASE}/subscriptions/{sub_id}",
                            headers=RC_HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("subscription")
    except Exception:
        pass
    return None


def main():
    print("Fetching unfulfilled orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} orders\n")

    # Find candidates: stale _SHIP date orders with sub IDs
    candidates = []
    for o in orders:
        items = o.get("line_items", [])
        tags = o.get("tags", "")
        if "reship" in tags.lower():
            continue
        if any((li.get("sku") or "").startswith("AHB-X") for li in items):
            continue

        box_sku = ""
        ship_prop = ""
        rc_sub_id = ""
        for li in items:
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0:
                continue
            if sku.startswith(CUSTOM_PREFIXES):
                box_sku = sku
                for p in (li.get("properties") or []):
                    name = p.get("name", "")
                    val = str(p.get("value", "") or "").strip()
                    if name == "_SHIP":
                        ship_prop = val
                    if name == "_rc_bundle":
                        rc_sub_id = val
                break

        if not box_sku or not rc_sub_id:
            continue

        ship_tag_date = ""
        for t in tags.split(","):
            t = t.strip()
            if t.startswith("_SHIP_"):
                ship_tag_date = t.replace("_SHIP_", "")
                break

        if not ship_prop or not ship_tag_date or ship_prop == ship_tag_date:
            continue

        candidates.append({
            "order": o["name"],
            "order_id": o["id"],
            "customer": f"{(o.get('customer') or {}).get('first_name', '')} "
                        f"{(o.get('customer') or {}).get('last_name', '')}".strip(),
            "email": o.get("email", ""),
            "box_sku": box_sku,
            "box_curation": get_curation_suffix(box_sku),
            "rc_sub_id": rc_sub_id,
            "ship_prop": ship_prop,
            "ship_tag": ship_tag_date,
        })

    print(f"Stale-date candidates: {len(candidates)}")
    print("Checking Recharge subscriptions...\n")

    repeats = []
    rotated = 0
    inactive = 0
    errors = 0

    for i, c in enumerate(candidates):
        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{len(candidates)} checked "
                  f"({len(repeats)} repeats, {rotated} rotated, {inactive} inactive)")

        sub = rc_get_subscription(c["rc_sub_id"])
        if not sub:
            errors += 1
            time.sleep(0.3)
            continue

        if sub.get("status") != "active":
            inactive += 1
            time.sleep(0.3)
            continue

        rc_sku = (sub.get("sku") or "").strip()
        rc_curation = get_curation_suffix(rc_sku)

        if rc_curation == c["box_curation"]:
            repeats.append({**c, "rc_sku": rc_sku, "rc_curation": rc_curation})
        else:
            rotated += 1

        time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total candidates:    {len(candidates)}")
    print(f"  Properly rotated:    {rotated}")
    print(f"  Same curation (BAD): {len(repeats)}")
    print(f"  Inactive/cancelled:  {inactive}")
    print(f"  API errors:          {errors}")

    if repeats:
        print(f"\n{'Order':<12} {'Customer':<22} {'Box Curation':<10} {'RC SKU':<24} {'_SHIP':<12} Tag")
        print("-" * 95)
        for r in repeats:
            print(f"{r['order']:<12} {r['customer'][:21]:<22} {r['box_curation']:<10} "
                  f"{r['rc_sku']:<24} {r['ship_prop']:<12} {r['ship_tag']}")

    # Write CSV
    out = os.path.join(os.path.dirname(__file__),
                       f"rotation-repeats-{datetime.now().strftime('%Y-%m-%d')}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "order", "order_id", "customer", "email", "box_sku",
            "box_curation", "rc_sub_id", "rc_sku", "rc_curation",
            "ship_prop", "ship_tag",
        ])
        w.writeheader()
        w.writerows(repeats)
    print(f"\nCSV saved: {out}")


if __name__ == "__main__":
    main()
