# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Check what payment gateway is used on a recent order."""
import json
import requests

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

order_id = 7009031717144
resp = requests.get(
    f"{REST_BASE}/orders/{order_id}/transactions.json",
    headers=HEADERS,
    timeout=30,
)
resp.raise_for_status()
txns = resp.json().get("transactions", [])

for t in txns:
    print(f"Kind: {t.get('kind')}, Gateway: {t.get('gateway')}, Status: {t.get('status')}, Amount: {t.get('amount')}")
    receipt = t.get("receipt", {})
    if receipt:
        print(f"  Receipt keys: {list(receipt.keys())[:10]}")
    print()
