"""Check for conflicting routing tags on FL Gulf Coast orders."""
import json, requests, re, time
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS) as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

cutoff = (datetime.now() - timedelta(days=14)).isoformat()
url = f"{REST}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled",
          "limit": 250, "created_at_min": cutoff,
          "fields": "id,name,tags,shipping_address"}

orders = []
while url:
    resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
    if resp.status_code != 200:
        break
    orders.extend(resp.json().get("orders", []))
    url = None
    params = None
    link = resp.headers.get("Link", "")
    if 'rel="next"' in link:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.3)

TAG = "!FedEx 2Day - Dallas_AHB!"
ROUTING_PREFIXES = ("!ANY", "!NO ", "!FedEx", "!UPS", "!OnTrac")

conflicts = []
clean = 0
for o in orders:
    tags = o.get("tags", "") or ""
    if TAG not in tags:
        continue
    tag_list = [t.strip() for t in tags.split(",")]
    routing = [t for t in tag_list if any(t.startswith(p) for p in ROUTING_PREFIXES)]
    if len(routing) > 1:
        conflicts.append((o.get("name", ""), routing))
    else:
        clean += 1

print(f"Orders with {TAG}: {clean + len(conflicts)}")
print(f"Clean (only FedEx 2Day): {clean}")
print(f"Conflicts (multiple routing tags): {len(conflicts)}")
for name, rtags in conflicts:
    print(f"  {name}: {rtags}")
