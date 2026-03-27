"""Swap 120 curated MT-SOP to MT-CAPO on orders that also have curated MT-JAHH.
Only swaps default recipe (not customer-chosen via box_contents).

Usage:
    python swap_sop_to_capo.py              # dry-run
    python swap_sop_to_capo.py --commit     # apply
"""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

COMMIT = "--commit" in sys.argv
SWAP_LIMIT = 178
CAPO_GID = "gid://shopify/ProductVariant/49936397336856"  # $0 Capocollo*


def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def fetch_candidates():
    """Find orders with both curated MT-SOP and curated MT-JAHH."""
    candidates = []
    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if "_SHIP_2026-03-23" not in tags:
                continue
            has_sop_curation = False
            has_jahh_curation = False
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                if fq <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if sku == "MT-SOP" and "_rc_bundle" in prop_names:
                    has_sop_curation = True
                if sku == "MT-JAHH" and "_rc_bundle" in prop_names:
                    has_jahh_curation = True
            if has_sop_curation and has_jahh_curation:
                candidates.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                })
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return candidates


def check_box_contents(order_gid):
    """Check if customer chose MT-SOP via box_contents. Returns True if customer-chosen."""
    try:
        data = gql("""
        query ($id: ID!) {
          order(id: $id) {
            lineItems(first: 30) {
              edges {
                node {
                  sku
                  customAttributes { key value }
                }
              }
            }
          }
        }""", {"id": order_gid})
        for edge in data["order"]["lineItems"]["edges"]:
            node = edge["node"]
            sku = (node.get("sku") or "").strip()
            if sku.startswith("AHB-"):
                for attr in node.get("customAttributes", []):
                    if attr.get("key") == "box_contents":
                        bc = (attr.get("value") or "").lower()
                        if "sopressata" in bc or "mt-sop" in bc:
                            return True
                        return False  # has box_contents but no SOP = default recipe
                return False  # no box_contents = default recipe
    except Exception:
        pass
    return False  # default: assume swappable


def swap_item(order_info):
    order_gid = order_info["order_gid"]
    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": order_gid})
    if data["orderEditBegin"]["userErrors"]:
        print(f"    FAILED: {data['orderEditBegin']['userErrors']}")
        return False
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]

    # Find curated MT-SOP (first one)
    sop_nodes = [e["node"] for e in calc["lineItems"]["edges"]
                 if (e["node"].get("sku") or "").strip() == "MT-SOP" and e["node"]["quantity"] > 0]
    if not sop_nodes:
        print(f"    MT-SOP not found")
        return False
    li_node = sop_nodes[0]

    time.sleep(0.3)
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) { calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        return False

    time.sleep(0.3)
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) { calculatedLineItem { id } calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "variantId": CAPO_GID, "quantity": 1})
    if data["orderEditAddVariant"]["userErrors"]:
        return False

    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap MT-SOP -> MT-CAPO (curation, short on SOP)") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    print(f"    OK {order_info['order_name']}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Swap curated MT-SOP -> MT-CAPO [{mode}]")
    print(f"  (on orders with MT-JAHH, limit {SWAP_LIMIT})")
    print(f"{'='*60}\n")

    print("Fetching candidates...")
    candidates = fetch_candidates()
    print(f"  Found {len(candidates)} orders with both curated MT-SOP + MT-JAHH\n")

    # Filter out customer-chosen
    print("Checking box_contents (filtering customer-chosen)...")
    swappable = []
    chosen = 0
    for c in candidates:
        if len(swappable) >= SWAP_LIMIT:
            break
        if check_box_contents(c["order_gid"]):
            chosen += 1
        else:
            swappable.append(c)
        time.sleep(0.3)

    print(f"  Customer-chosen (skip): {chosen}")
    print(f"  Swappable: {len(swappable)}")
    print(f"  Will swap: {min(len(swappable), SWAP_LIMIT)}\n")

    targets = swappable[:SWAP_LIMIT]

    if not COMMIT:
        print(f"DRY-RUN. {len(targets)} would be swapped.")
        print("Run with --commit to apply.")
        return

    s, f = 0, 0
    for i, t in enumerate(targets):
        if swap_item(t):
            s += 1
        else:
            f += 1
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(targets)}")
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {s} swapped, {f} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
