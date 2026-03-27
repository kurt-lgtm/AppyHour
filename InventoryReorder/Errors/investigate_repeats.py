"""Investigate #119274 and #118978 on Recharge for unusual patterns."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN = settings["recharge_api_token"]
BASE_URL = "https://api.rechargeapps.com"
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Content-Type": "application/json",
    "X-Recharge-Version": "2021-11",
}

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
SH_HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}


def rc_get(endpoint, params=None):
    for attempt in range(5):
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=RC_HEADERS,
                            params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("retry-after", "5")))
            continue
        resp.raise_for_status()
        time.sleep(0.5)
        return resp.json()
    raise Exception(f"Max retries on GET {endpoint}")


cases = [
    {"email": "damgoodwin@gmail.com", "order": "119274", "issue": "Should have rotated from MONG"},
    {"email": "christyorders@gmail.com", "order": "118978", "issue": "MONG shouldn't go to SS"},
]

for case in cases:
    email = case["email"]
    print(f"\n{'='*70}")
    print(f"  {email} — #{case['order']}")
    print(f"  Issue: {case['issue']}")
    print(f"{'='*70}")

    # Get RC customer
    cust_data = rc_get("/customers", params={"email": email, "limit": 1})
    rc_customers = cust_data.get("customers", [])
    if not rc_customers:
        print("  NO RC CUSTOMER")
        continue
    rc_cust = rc_customers[0]
    rc_cust_id = rc_cust["id"]
    print(f"  RC Customer ID: {rc_cust_id}")
    print(f"  Created: {rc_cust.get('created_at', '')[:10]}")

    # Get all subscriptions (active + cancelled)
    for status in ["active", "cancelled"]:
        subs_data = rc_get("/subscriptions", params={
            "customer_id": rc_cust_id,
            "status": status,
            "limit": 50,
        })
        subs = subs_data.get("subscriptions", [])
        ahb_subs = [s for s in subs if (s.get("sku") or "").startswith("AHB-")]
        if ahb_subs:
            print(f"\n  {status.upper()} box subscriptions ({len(ahb_subs)}):")
            for s in ahb_subs:
                sku = s.get("sku", "")
                sub_id = s.get("id")
                created = (s.get("created_at") or "")[:10]
                updated = (s.get("updated_at") or "")[:10]
                next_charge = (s.get("next_charge_scheduled_at") or "")[:10]
                cancelled = (s.get("cancelled_at") or "")[:10]
                print(f"    {sku:<30} sub={sub_id} created={created} updated={updated} next={next_charge} cancelled={cancelled}")

    # Get recent charges
    charges_data = rc_get("/charges", params={
        "customer_id": rc_cust_id,
        "limit": 5,
        "sort_by": "id-desc",
    })
    charges = charges_data.get("charges", [])
    print(f"\n  Recent charges ({len(charges)}):")
    for ch in charges:
        ch_id = ch.get("id")
        status = ch.get("status", "")
        scheduled = (ch.get("scheduled_at") or "")[:10]
        processed = (ch.get("processed_at") or "")[:10]
        # List box + food items
        box_sku = ""
        food_skus = []
        for li in ch.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku.startswith("AHB-"):
                box_sku = sku
            elif any(sku.startswith(p) for p in ("CH-", "MT-", "AC-")):
                food_skus.append(sku)
        print(f"    Charge {ch_id}: {status} scheduled={scheduled} processed={processed}")
        print(f"      Box: {box_sku}")
        print(f"      Food: {', '.join(sorted(food_skus))}")

    # Get bundle selections for active box sub
    subs_data = rc_get("/subscriptions", params={
        "customer_id": rc_cust_id,
        "status": "active",
        "limit": 50,
    })
    active_subs = subs_data.get("subscriptions", [])
    ahb_active = [s for s in active_subs if (s.get("sku") or "").startswith("AHB-")]
    for s in ahb_active:
        sub_id = s["id"]
        sku = s.get("sku", "")
        print(f"\n  Bundle selections for {sku} (sub {sub_id}):")
        bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
        selections = bs_data.get("bundle_selections", [])
        for bs in selections:
            bs_id = bs.get("id")
            charge_id = bs.get("charge_id")
            items = bs.get("items", [])
            label = "UPCOMING" if charge_id is None else f"charge={charge_id}"
            print(f"    Selection {bs_id} ({label}): {len(items)} items")

    time.sleep(1)
