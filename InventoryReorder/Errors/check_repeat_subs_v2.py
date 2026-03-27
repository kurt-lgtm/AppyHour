"""Check repeat-SKU customers: count only AHB- box subscriptions, not food items."""
import requests, json, time, csv

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
            wait = int(resp.headers.get("retry-after", "5"))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(0.5)
        return resp.json()
    raise Exception(f"Max retries on GET {endpoint}")


CSV_PATH = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\recharge-errors-2026-03-21.csv"
customers = []
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        customers.append({
            "customer_id": row["Customer ID"],
            "email": row["Email"],
            "current_order": row["Current Order"],
            "repeat_skus": row["Repeat SKUs"],
        })

print(f"Checking {len(customers)} customers...\n")

multi_box = []
single_box = []

for i, cust in enumerate(customers):
    email = cust["email"]

    try:
        data = rc_get("/subscriptions", params={
            "email": email,
            "status": "active",
            "limit": 250,
        })
        subs = data.get("subscriptions", [])
    except Exception as e:
        print(f"  [{i+1}] {email}: ERROR {e}")
        continue

    # Count only AHB- box subscriptions (not food/accessory subs)
    box_subs = []
    for sub in subs:
        sku = (sub.get("sku") or "").strip()
        if sku.startswith("AHB-"):
            box_subs.append({
                "sku": sku,
                "id": sub.get("id"),
                "next": (sub.get("next_charge_scheduled_at") or "")[:10],
            })

    if len(box_subs) > 1:
        multi_box.append({"email": email, "order": cust["current_order"],
                          "boxes": box_subs, "repeat_skus": cust["repeat_skus"]})
        print(f"  [{i+1}] {email}: {len(box_subs)} BOXES - {', '.join(b['sku'] for b in box_subs)}")
    elif len(box_subs) == 1:
        single_box.append({"email": email, "order": cust["current_order"],
                           "box": box_subs[0]["sku"], "repeat_skus": cust["repeat_skus"]})
        print(f"  [{i+1}] {email}: 1 BOX ({box_subs[0]['sku']}) - GENUINE REPEAT")
    else:
        print(f"  [{i+1}] {email}: NO BOX SUB ({len(subs)} total subs)")
        single_box.append({"email": email, "order": cust["current_order"],
                           "box": "none", "repeat_skus": cust["repeat_skus"]})

    time.sleep(0.3)

print(f"\n{'='*60}")
print(f"  Multi-box (valid double subs): {len(multi_box)}")
print(f"  Single-box (genuine repeats): {len(single_box)}")
print(f"{'='*60}")

if single_box:
    print(f"\n  Genuine repeat orders:")
    for s in single_box:
        print(f"    {s['order']:<12} {s['email']:<35} {s['box']}")
        print(f"      Repeats: {s['repeat_skus']}")
