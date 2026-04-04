import json, requests

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS) as f:
    settings = json.load(f)
STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}
resp = requests.get(
    f"{BASE}/orders.json", headers=HEADERS, params={"name": "118751", "status": "any", "limit": 1}, timeout=30
)
o = resp.json()["orders"][0]
oid = o["id"]
print(f"Order: #{o['name']} (ID: {oid})")
print(f"Total: {o['total_price']}, Financial: {o['financial_status']}")
rresp = requests.get(f"{BASE}/orders/{oid}/refunds.json", headers=HEADERS, timeout=30)
refunds = rresp.json()["refunds"]
print(f"\nRefunds ({len(refunds)}):")
for r in refunds:
    for t in r.get("transactions", []):
        print(
            f"  ID:{r['id']} amt:${t['amount']} kind:{t['kind']} status:{t['status']} date:{r['created_at'][:19]} note:{r.get('note', '')}"
        )
tresp = requests.get(f"{BASE}/orders/{oid}/transactions.json", headers=HEADERS, timeout=30)
txns = tresp.json()["transactions"]
print(f"\nAll transactions ({len(txns)}):")
for t in txns:
    print(
        f"  ID:{t['id']} kind:{t['kind']} status:{t['status']} amt:${t['amount']} gw:{t['gateway']} parent:{t.get('parent_id', '')}"
    )
