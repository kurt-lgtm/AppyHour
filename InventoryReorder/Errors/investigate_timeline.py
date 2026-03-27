"""Investigate timeline for sub 766730792 (damgoodwin #119274).
Check events, subscription history, and which API token made changes."""
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


SUB_ID = 766730792
CUSTOMER_ID = 232206299

print(f"Subscription {SUB_ID} full details:")
sub_data = rc_get(f"/subscriptions/{SUB_ID}")
sub = sub_data.get("subscription", {})
print(f"  SKU: {sub.get('sku')}")
print(f"  Created: {sub.get('created_at')}")
print(f"  Updated: {sub.get('updated_at')}")
print(f"  Next charge: {sub.get('next_charge_scheduled_at')}")
print(f"  Product title: {sub.get('product_title')}")
print(f"  Variant title: {sub.get('variant_title')}")

# Check for analytics/token info
analytics = sub.get("analytics_data", {})
if analytics:
    print(f"  Analytics: {json.dumps(analytics, indent=4)}")

# Get events for this customer
print(f"\nEvents for customer {CUSTOMER_ID}:")
try:
    events_data = rc_get("/events", params={
        "customer_id": CUSTOMER_ID,
        "limit": 50,
    })
    events = events_data.get("events", [])
    print(f"  Found {len(events)} events")
    for ev in events:
        created = (ev.get("created_at") or "")[:19]
        verb = ev.get("verb", "")
        origin = ev.get("origin", "")
        channel = ev.get("channel", "")
        # Try to get more details
        print(f"  {created} | {verb:<30} | origin={origin} channel={channel}")
except Exception as e:
    print(f"  Events API error: {e}")

# Get all charges with details
print(f"\nAll charges for customer {CUSTOMER_ID}:")
charges_data = rc_get("/charges", params={
    "customer_id": CUSTOMER_ID,
    "limit": 10,
    "sort_by": "id-asc",
})
charges = charges_data.get("charges", [])
for ch in charges:
    ch_id = ch.get("id")
    status = ch.get("status", "")
    scheduled = ch.get("scheduled_at", "")
    processed = ch.get("processed_at", "")
    created = ch.get("created_at", "")
    updated = ch.get("updated_at", "")
    analytics = ch.get("analytics_data", {})

    print(f"\n  Charge {ch_id}: {status}")
    print(f"    Created: {created}")
    print(f"    Updated: {updated}")
    print(f"    Scheduled: {scheduled}")
    print(f"    Processed: {processed}")

    # Line items with subscription IDs
    for li in ch.get("line_items", []):
        sku = (li.get("sku") or "").strip()
        sub_id = li.get("subscription_id")
        purchase_type = li.get("purchase_item_type", "")
        print(f"    {sku:<25} sub={sub_id} type={purchase_type}")

# Check bundle_selections history
print(f"\nBundle selections for sub {SUB_ID}:")
bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": SUB_ID, "limit": 50})
selections = bs_data.get("bundle_selections", [])
for bs in selections:
    bs_id = bs.get("id")
    charge_id = bs.get("charge_id")
    created = (bs.get("created_at") or "")[:19]
    updated = (bs.get("updated_at") or "")[:19]
    items = bs.get("items", [])
    label = "UPCOMING" if charge_id is None else f"charge={charge_id}"
    print(f"\n  Selection {bs_id} ({label}):")
    print(f"    Created: {created}")
    print(f"    Updated: {updated}")
    for item in items:
        vid = item.get("external_variant_id", "")
        print(f"    variant={vid} qty={item.get('quantity')}")

# Check metafields for token info
print(f"\nSubscription metafields:")
try:
    mf_data = rc_get(f"/subscriptions/{SUB_ID}/metafields")
    metafields = mf_data.get("metafields", [])
    for mf in metafields:
        print(f"  {mf.get('key')}: {mf.get('value')}")
except Exception as e:
    print(f"  Metafields error: {e}")
