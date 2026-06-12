import sys, os, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth
base, headers = get_shopify_auth()
resp = requests.get(f"{base}/orders/7025102094616.json", headers=headers, params={"fields":"name,tags,line_items"}, timeout=30)
o = resp.json()["order"]
print(f"Order: {o['name']}")
tags = [t.strip() for t in o["tags"].split(",")]
print(f"Tags: {tags[:10]}")
print("Line items:")
for li in o["line_items"]:
    sku = li.get("sku","") or ""
    fq = li.get("fulfillable_quantity",0)
    qty = li.get("quantity",0)
    print(f"  {sku:25s} qty={qty} fq={fq}")
