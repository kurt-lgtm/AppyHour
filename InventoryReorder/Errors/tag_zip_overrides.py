"""Apply FedEx 2Day routing tag to ALL orders matching zip_routing_overrides.

Reads force_2day zips from GelPack settings, checks for conflicts, applies tags.
"""
import json, os, time, re, requests
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
GELCALC_SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator\gel_calc_shopify_settings.json"

with open(SETTINGS) as f:
    settings = json.load(f)
with open(GELCALC_SETTINGS) as f:
    gc = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

TAG = "!FedEx 2Day - Dallas_AHB!"
ROUTING_PREFIXES = ("!ANY", "!NO ", "!FedEx", "!UPS", "!OnTrac")

overrides = gc.get("zip_routing_overrides", {})
force_zips = {z for z, v in overrides.items() if v.get("action") == "force_2day"}
print(f"Force 2Day zips: {len(force_zips)}")


def fetch_unfulfilled():
    cutoff = (datetime.now() - timedelta(days=21)).isoformat()
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
        zipcode = (addr.get("zip") or "").strip()
        prefix = zipcode[:3]
        if prefix not in force_zips:
            continue
        tags = o.get("tags", "") or ""
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        routing = [t for t in tag_list if any(t.startswith(p) for p in ROUTING_PREFIXES)]
        conflicts = [t for t in routing if t != TAG]
        already = TAG in tag_list

        if already and not conflicts:
            continue

        targets.append({
            "id": o["id"],
            "name": o.get("name", ""),
            "city": addr.get("city", ""),
            "state": addr.get("province_code", ""),
            "zip": zipcode,
            "conflicts": conflicts,
            "already": already,
        })

    print(f"  {len(targets)} orders need tagging")
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
    tags_remove = """
    mutation tagsRemove($id: ID!, $tags: [String!]!) {
      tagsRemove(id: $id, tags: $tags) {
        node { ... on Order { id name } }
        userErrors { field message }
      }
    }
    """

    success = 0
    conflicts_fixed = 0
    failed = 0

    for t in targets:
        gid = f"gid://shopify/Order/{t['id']}"

        # Remove conflicts first
        if t["conflicts"]:
            resp = requests.post(GQL_URL, headers=HEADERS, json={
                "query": tags_remove,
                "variables": {"id": gid, "tags": t["conflicts"]},
            }, timeout=30)
            data = resp.json()
            ue = data.get("data", {}).get("tagsRemove", {}).get("userErrors", [])
            if not ue:
                conflicts_fixed += 1
                print(f"  Removed conflicts on {t['name']}: {t['conflicts']}")
            time.sleep(0.3)

        # Add tag
        if not t["already"]:
            resp = requests.post(GQL_URL, headers=HEADERS, json={
                "query": tags_add,
                "variables": {"id": gid, "tags": [TAG]},
            }, timeout=30)
            data = resp.json()
            if "errors" in data:
                print(f"  FAILED {t['name']}: {data['errors']}")
                failed += 1
            else:
                ue = data.get("data", {}).get("tagsAdd", {}).get("userErrors", [])
                if ue:
                    print(f"  FAILED {t['name']}: {ue}")
                    failed += 1
                else:
                    print(f"  Tagged {t['name']} ({t['city']}, {t['state']} {t['zip']})")
                    success += 1
            time.sleep(0.3)

    print(f"\nDone. Tagged: {success}, Conflicts fixed: {conflicts_fixed}, Failed: {failed}")


if __name__ == "__main__":
    main()
