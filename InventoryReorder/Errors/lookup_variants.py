"""Look up variant and product IDs for swap SKUs."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

SKUS = ["AC-PPCM", "AC-SMAL", "CH-HCGU", "CH-EBRIE"]

for sku in SKUS:
    query = '{ productVariants(first: 5, query: "sku:%s") { edges { node { id sku title price product { id title } } } } }' % sku
    resp = requests.post(GQL_URL, headers=HEADERS, json={"query": query}, timeout=30)
    data = resp.json()["data"]
    print(f"\n{sku}:")
    for edge in data["productVariants"]["edges"]:
        n = edge["node"]
        # Extract numeric IDs from GIDs
        vid = n["id"].split("/")[-1]
        pid = n["product"]["id"].split("/")[-1]
        print(f"  variant={vid} product={pid} ${n['price']} {n['product']['title']} / {n['title']}")
    time.sleep(0.5)
