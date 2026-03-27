"""Check Recharge subscriptions for repeat-SKU customers.
Look for duplicate subscriptions or customer choices."""
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


# Read error CSV
CSV_PATH = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\recharge-errors-2026-03-21.csv"
customers = []
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        customers.append({
            "customer_id": row["Customer ID"],
            "email": row["Email"],
            "current_order": row["Current Order"],
            "previous_order": row["Previous Order"],
            "repeat_skus": row["Repeat SKUs"],
        })

print(f"Checking {len(customers)} customers on Recharge...\n")

double_subs = []
customer_chosen = []
normal_repeat = []

for i, cust in enumerate(customers[:20]):  # Check first 20 to start
    email = cust["email"]
    print(f"  [{i+1}] {email} ({cust['current_order']})...")

    # Get customer's subscriptions
    try:
        data = rc_get("/subscriptions", params={
            "email": email,
            "status": "active",
            "limit": 50,
        })
        subs = data.get("subscriptions", [])
    except Exception as e:
        print(f"    ERROR: {e}")
        continue

    # Count box subscriptions
    box_subs = []
    food_subs = []
    for sub in subs:
        sku = (sub.get("sku") or "").strip()
        sub_id = sub.get("id")
        next_charge = sub.get("next_charge_scheduled_at", "")
        if sku.startswith("AHB-"):
            box_subs.append({"sku": sku, "id": sub_id, "next": next_charge})
        elif any(sku.startswith(p) for p in ("CH-", "MT-", "AC-", "PR-", "CEX-", "PK-")):
            food_subs.append({"sku": sku, "id": sub_id, "next": next_charge})

    # Check for multiple box subscriptions
    if len(box_subs) > 1:
        double_subs.append({
            "email": email,
            "order": cust["current_order"],
            "boxes": box_subs,
            "food_count": len(food_subs),
            "repeat_skus": cust["repeat_skus"],
        })
        box_str = ", ".join(f"{b['sku']} (sub {b['id']})" for b in box_subs)
        print(f"    DOUBLE SUBS: {box_str}")
    elif len(box_subs) == 1:
        # Check bundle_selections for customer choice
        box_sub_id = box_subs[0]["id"]
        try:
            bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": box_sub_id})
            selections = bs_data.get("bundle_selections", [])
            upcoming = [s for s in selections if s.get("charge_id") is None]
            if upcoming:
                items = upcoming[0].get("items", [])
                print(f"    1 box ({box_subs[0]['sku']}), {len(items)} bundle items, {len(food_subs)} food subs")
            else:
                print(f"    1 box ({box_subs[0]['sku']}), no upcoming bundle_selection, {len(food_subs)} food subs")
        except Exception:
            print(f"    1 box ({box_subs[0]['sku']}), bundle_selection check failed")
        normal_repeat.append(cust)
    else:
        print(f"    No active box subscription found ({len(subs)} total subs)")
        normal_repeat.append(cust)

    time.sleep(0.5)

print(f"\n{'='*60}")
print(f"  Results (first 20):")
print(f"  Double subscriptions: {len(double_subs)}")
print(f"  Normal repeats: {len(normal_repeat)}")
print(f"{'='*60}")

if double_subs:
    print(f"\n  Double subscription details:")
    for d in double_subs:
        print(f"    {d['email']} {d['order']}")
        for b in d["boxes"]:
            print(f"      {b['sku']} sub={b['id']} next={b['next']}")
        print(f"      Repeat SKUs: {d['repeat_skus']}")
