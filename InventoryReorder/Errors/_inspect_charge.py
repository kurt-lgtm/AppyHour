"""Check where email/customer_id lives in v2021-11 charge objects."""
import requests, json
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS) as f: settings = json.load(f)
h = {"X-Recharge-Access-Token": settings["recharge_api_token"],
     "Content-Type": "application/json", "X-Recharge-Version": "2021-11"}
r = requests.get("https://api.rechargeapps.com/charges", headers=h,
                  params={"status": "queued", "limit": 1, "sort_by": "id-asc"}, timeout=30)
c = r.json()["charges"][0]
print("Top-level keys:", sorted(c.keys()), flush=True)
print(f"email: {c.get('email')}", flush=True)
print(f"customer_id: {c.get('customer_id')}", flush=True)
print(f"customer: {c.get('customer')}", flush=True)
print(f"billing_address email: {(c.get('billing_address') or {}).get('email')}", flush=True)
print(f"external_customer_id: {c.get('external_customer_id')}", flush=True)
# Check analytics_data or other nested
for k in sorted(c.keys()):
    v = c[k]
    if isinstance(v, dict) and 'email' in str(v).lower():
        print(f"{k} contains email: {v}", flush=True)
    elif isinstance(v, str) and '@' in v:
        print(f"{k} = {v}", flush=True)
