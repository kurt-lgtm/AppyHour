"""Fix the rc-upcoming CSV by looking up customer_id, email, and subscription_id
from the Recharge charges API using charge IDs already in the file."""
import requests, json, time, csv

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN = settings["recharge_api_token"]
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Content-Type": "application/json",
    "X-Recharge-Version": "2021-11",
}

CSV_FILE = r"C:\Users\Work\Downloads\rc-upcoming-class23-4b-2026-03-12.csv"
with open(CSV_FILE, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

print(f"Loaded {len(rows)} rows")

# Batch: fetch charges by ID and extract customer info + subscription ID
# Group by charge_id to avoid duplicate lookups
charge_ids = list(set(r["charge_id"] for r in rows))
print(f"Unique charge IDs: {len(charge_ids)}")

# Build lookup: charge_id -> {rc_customer_id, email, rc_subscription_id, customer}
charge_info = {}
for i, cid in enumerate(charge_ids):
    resp = requests.get(f"https://api.rechargeapps.com/charges/{cid}", headers=RC_HEADERS)
    time.sleep(0.25)
    if resp.status_code != 200:
        print(f"  Charge {cid}: error {resp.status_code}")
        continue
    c = resp.json().get("charge", {})
    cust = c.get("customer", {}) or {}
    rc_cust_id = str(cust.get("id", ""))
    email = cust.get("email", "") or ""
    ba = c.get("billing_address", {}) or {}
    name = f"{ba.get('first_name','')} {ba.get('last_name','')}".strip()

    # Get subscription ID from line_items purchase_item_id
    sub_id = ""
    for li in c.get("line_items", []):
        sku = (li.get("sku") or "").strip()
        pid = li.get("purchase_item_id")
        if pid and sku.startswith(("AHB-MCUST", "AHB-LCUST", "AHB-MED", "AHB-LGE", "AHB-CMED")):
            sub_id = str(pid)
            break
    # If no box SKU line, grab first purchase_item_id
    if not sub_id:
        for li in c.get("line_items", []):
            pid = li.get("purchase_item_id")
            if pid:
                sub_id = str(pid)
                break

    charge_info[cid] = {
        "rc_customer_id": rc_cust_id,
        "email": email,
        "rc_subscription_id": sub_id,
        "customer": name,
    }

    if (i + 1) % 50 == 0:
        print(f"  Looked up {i+1}/{len(charge_ids)}...")

print(f"Looked up {len(charge_info)} charges")

# Update rows
for row in rows:
    info = charge_info.get(row["charge_id"])
    if info:
        row["rc_customer_id"] = info["rc_customer_id"]
        row["rc_subscription_id"] = info["rc_subscription_id"]
        row["email"] = info["email"]
        if not row["customer"]:
            row["customer"] = info["customer"]

# Write back
fieldnames = ["class", "charge_id", "scheduled", "customer", "email",
              "rc_customer_id", "rc_subscription_id", "box_sku", "details"]
with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Updated {CSV_FILE}")

# Verify
filled = sum(1 for r in rows if r["rc_customer_id"])
print(f"Rows with RC Customer ID: {filled}/{len(rows)}")
filled_sub = sum(1 for r in rows if r["rc_subscription_id"])
print(f"Rows with RC Subscription ID: {filled_sub}/{len(rows)}")
