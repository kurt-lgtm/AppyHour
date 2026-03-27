"""Find orders matching Anik's token pattern:
- Subscription was swapped shortly after creation
- Charge processed with OLD SKU (MONG) but subscription is now different
- Bundle selection created same day as subscription
"""
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
            time.sleep(int(resp.headers.get("retry-after", "5")))
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
            "email": row["Email"],
            "order_num": row["Current Order"].replace("#", ""),
        })

print(f"Checking {len(customers)} customers for Anik pattern...\n")

matches = []
clean = []

for i, cust in enumerate(customers):
    email = cust["email"]

    # Get RC customer
    cust_data = rc_get("/customers", params={"email": email, "limit": 1})
    rc_customers = cust_data.get("customers", [])
    if not rc_customers:
        continue
    rc_cust_id = rc_customers[0]["id"]

    # Get active box sub
    subs_data = rc_get("/subscriptions", params={
        "customer_id": rc_cust_id,
        "status": "active",
        "limit": 50,
    })
    subs = subs_data.get("subscriptions", [])
    ahb_subs = [s for s in subs if (s.get("sku") or "").startswith("AHB-")]

    if not ahb_subs:
        continue

    for sub in ahb_subs:
        sub_id = sub["id"]
        current_sku = sub.get("sku", "")
        sub_created = sub.get("created_at", "")
        sub_updated = sub.get("updated_at", "")
        sub_created_date = sub_created[:10] if sub_created else ""

        # Get charges — look for March charge with different box SKU
        charges_data = rc_get("/charges", params={
            "customer_id": rc_cust_id,
            "limit": 10,
            "sort_by": "id-desc",
        })
        charges = charges_data.get("charges", [])

        for ch in charges:
            if ch.get("status") != "success":
                continue
            scheduled = (ch.get("scheduled_at") or "")[:10]
            if not scheduled.startswith("2026-03"):
                continue

            # Find box SKU on this charge
            charge_box = ""
            for li in ch.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku.startswith("AHB-"):
                    charge_box = sku
                    break

            # Pattern: charge has MONG but sub is now something else
            charge_curation = charge_box.split("-")[-1] if charge_box else ""
            current_curation = current_sku.split("-")[-1] if current_sku else ""

            if charge_box and charge_curation != current_curation:
                # Check if sub was created and updated same day (rapid swap)
                created_date = sub_created[:10]
                updated_date = sub_updated[:10]
                rapid_swap = created_date == updated_date if created_date and updated_date else False

                # Check bundle_selection creation date
                bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
                selections = bs_data.get("bundle_selections", [])
                bs_created = ""
                for bs in selections:
                    if bs.get("charge_id") is None:
                        bs_created = (bs.get("created_at") or "")[:10]

                matches.append({
                    "email": email,
                    "order": f"#{cust['order_num']}",
                    "sub_id": sub_id,
                    "sub_created": sub_created[:19],
                    "sub_updated": sub_updated[:19],
                    "current_sku": current_sku,
                    "charge_box": charge_box,
                    "rapid_swap": rapid_swap,
                    "bs_created": bs_created,
                })
                print(f"  [{i+1}] {email}: MATCH — charge={charge_box} now={current_sku} rapid_swap={rapid_swap} bs_created={bs_created}")
                break
        else:
            clean.append(email)

    time.sleep(0.3)

print(f"\n{'='*60}")
print(f"  Anik pattern matches: {len(matches)}")
print(f"  Clean: {len(clean)}")
print(f"{'='*60}")

if matches:
    print(f"\n  Matches:")
    for m in matches:
        print(f"    {m['order']:<12} {m['email']:<35}")
        print(f"      Charge box: {m['charge_box']} -> Now: {m['current_sku']}")
        print(f"      Sub created: {m['sub_created']} updated: {m['sub_updated']} rapid_swap: {m['rapid_swap']}")
        print(f"      Bundle selection created: {m['bs_created']}")
