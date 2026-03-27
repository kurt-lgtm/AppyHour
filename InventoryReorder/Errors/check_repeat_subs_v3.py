"""Check repeat-SKU customers: valid if 2+ active subs with MONG or SS curation."""
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

valid_double = []
genuine_repeat = []

for i, cust in enumerate(customers):
    email = cust["email"]

    try:
        # Look up RC customer ID by email first
        cust_data = rc_get("/customers", params={"email": email, "limit": 1})
        rc_customers = cust_data.get("customers", [])
        if not rc_customers:
            print(f"  [{i+1}] {email}: NO RC CUSTOMER FOUND")
            genuine_repeat.append({"email": email, "order": cust["current_order"],
                                   "mong_ss": [], "all_boxes": [],
                                   "repeat_skus": cust["repeat_skus"]})
            continue
        rc_cust_id = rc_customers[0]["id"]
        data = rc_get("/subscriptions", params={
            "customer_id": rc_cust_id,
            "status": "active",
            "limit": 250,
        })
        subs = data.get("subscriptions", [])
    except Exception as e:
        print(f"  [{i+1}] {email}: ERROR {e}")
        continue

    # Count AHB- subs with MONG or SS in the SKU
    mong_ss_subs = []
    all_box_subs = []
    for sub in subs:
        sku = (sub.get("sku") or "").strip().upper()
        if sku.startswith("AHB-"):
            all_box_subs.append(sku)
            # Check if MONG or SS curation
            parts = sku.split("-")
            curation = parts[-1] if parts else ""
            if curation in ("MONG", "SS"):
                mong_ss_subs.append(sku)

    if len(mong_ss_subs) >= 2:
        valid_double.append({"email": email, "order": cust["current_order"],
                             "mong_ss": mong_ss_subs, "all_boxes": all_box_subs})
        print(f"  [{i+1}] {email}: VALID ({len(mong_ss_subs)} MONG/SS subs)")
    else:
        genuine_repeat.append({"email": email, "order": cust["current_order"],
                               "mong_ss": mong_ss_subs, "all_boxes": all_box_subs,
                               "repeat_skus": cust["repeat_skus"]})
        print(f"  [{i+1}] {email}: GENUINE REPEAT ({len(mong_ss_subs)} MONG/SS, boxes: {', '.join(all_box_subs[:5])})")

    time.sleep(0.3)

print(f"\n{'='*60}")
print(f"  Valid double MONG/SS subs: {len(valid_double)}")
print(f"  Genuine repeats (need fix): {len(genuine_repeat)}")
print(f"{'='*60}")

if genuine_repeat:
    print(f"\n  Genuine repeat orders needing attention:")
    for g in genuine_repeat:
        boxes = ", ".join(g["all_boxes"][:3])
        if len(g["all_boxes"]) > 3:
            boxes += f" +{len(g['all_boxes'])-3} more"
        print(f"    {g['order']:<12} {g['email']:<35} boxes: {boxes}")
        print(f"      Repeats: {g['repeat_skus']}")
