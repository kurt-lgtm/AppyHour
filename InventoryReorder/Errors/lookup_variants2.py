import requests, json, time
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)
STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
GQL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
H = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}
for sku in ["MT-SOP", "AC-FLH", "CH-FONTAL"]:
    q = '{ productVariants(first: 5, query: "sku:%s") { edges { node { id sku title price product { id title } } } } }' % sku
    r = requests.post(GQL, headers=H, json={"query": q}, timeout=30).json()
    print(f"\n{sku}:")
    for e in r["data"]["productVariants"]["edges"]:
        n = e["node"]
        print(f"  v={n['id'].split('/')[-1]} p={n['product']['id'].split('/')[-1]} ${n['price']} {n['product']['title']}")
    time.sleep(0.5)
