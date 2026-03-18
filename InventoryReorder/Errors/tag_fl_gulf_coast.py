"""Apply FedEx 2Day routing tag to FL Gulf Coast orders.

FL zip prefixes 329, 335-342, 346 have 91% 3-day rate on OnTrac.
Route these to FedEx 2Day via tag.
"""
import json, os, time, re, requests
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS) as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

TAG = "!FedEx 2Day - Dallas_AHB!"
OVERRIDE_ZIPS = {"329", "335", "336", "337", "338", "339", "341", "342", "346"}


def fetch_unfulfilled():
    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open", "fulfillment_status": "unfulfilled",
        "limit": 250, "created_at_min": cutoff,
        "fields": "id,name,tags,shipping_address",
    }
    orders = []
    while url:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if resp.status_code != 200:
            break
        data = resp.json()
        orders.extend(data.get("orders", []))
        url = None
        params = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.3)
    return orders


def main():
    print("Fetching unfulfilled orders...")
    orders = fetch_unfulfilled()
    print(f"  {len(orders)} orders")

    targets = []
    for o in orders:
        addr = o.get("shipping_address") or {}
        state = (addr.get("province_code") or "").upper()
        zipcode = (addr.get("zip") or "").strip()
        if state != "FL":
            continue
        prefix = zipcode[:3]
        if prefix not in OVERRIDE_ZIPS:
            continue
        tags = o.get("tags", "") or ""
        if TAG in tags:
            continue
        targets.append(o)

    print(f"  {len(targets)} FL Gulf Coast orders need {TAG}")
    if not targets:
        print("Nothing to do.")
        return

    tags_add = """
    mutation tagsAdd($id: ID!, $tags: [String!]!) {
      tagsAdd(id: $id, tags: $tags) {
        node { ... on Order { id name } }
        userErrors { field message }
      }
    }
    """

    success = 0
    failed = 0
    for o in targets:
        gid = f"gid://shopify/Order/{o['id']}"
        addr = o.get("shipping_address") or {}
        city = addr.get("city", "")
        zipcode = (addr.get("zip") or "").strip()

        resp = requests.post(GQL_URL, headers=HEADERS, json={
            "query": tags_add,
            "variables": {"id": gid, "tags": [TAG]},
        }, timeout=30)

        data = resp.json()
        if "errors" in data:
            print(f"  FAILED {o['name']}: {data['errors']}")
            failed += 1
        else:
            result = data.get("data", {}).get("tagsAdd", {})
            ue = result.get("userErrors", [])
            if ue:
                print(f"  FAILED {o['name']}: {ue}")
                failed += 1
            else:
                print(f"  Tagged {o['name']} ({city}, FL {zipcode})")
                success += 1
        time.sleep(0.3)

    print(f"\nDone. Success: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
