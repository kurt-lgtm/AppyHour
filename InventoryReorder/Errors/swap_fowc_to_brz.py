"""Swap curated CH-FOWC to CH-BRZ, keeping paid, customer-chosen, and BYO.

Keep enough to match available inventory (49). Swap the rest.

Usage:
    python swap_fowc_to_brz.py              # dry-run
    python swap_fowc_to_brz.py --commit     # apply
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
OLD_SKU = "CH-FOWC"
NEW_SKU = "CH-BRZ"
AVAILABLE = 49
BRZ_GID = None  # will look up


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


def find_brz_variant():
    data = gql('{ productVariants(first: 5, query: "sku:CH-BRZ") { edges { node { id sku price product { title } } } } }')
    variants = []
    for edge in data["productVariants"]["edges"]:
        n = edge["node"]
        if n["sku"] == NEW_SKU:
            variants.append(n)
            print(f"  {n['sku']}: ${n['price']} - {n['product']['title']} ({n['id']})")
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"]


def fetch_all_fowc():
    """Fetch all FOWC orders, classify as keep vs swap."""
    keep = []  # paid, will check box_contents + BYO later
    check_curation = []  # curation, need to check box_contents + BYO
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
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != OLD_SKU:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                is_curation = "_rc_bundle" in prop_names
                # Check if BYO box
                is_byo = False
                for li2 in o.get("line_items", []):
                    s2 = (li2.get("sku") or "").strip()
                    if "BYO" in s2.upper():
                        is_byo = True
                        break

                info = {
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": qty,
                    "is_curation": is_curation,
                    "is_byo": is_byo,
                }
                if not is_curation:
                    keep.append(info)
                elif is_byo:
                    keep.append(info)
                else:
                    check_curation.append(info)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return keep, check_curation


def check_box_contents_fowc(order_gid):
    """Check if customer chose CH-FOWC via box_contents."""
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
                        if "clothbound" in bc or "fowc" in bc or "california cloth" in bc:
                            return True
                        return False
                return False
    except Exception:
        pass
    return False


def swap_item(order_info, brz_gid):
    order_gid = order_info["order_gid"]
    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": order_gid})
    if data["orderEditBegin"]["userErrors"]:
        return False
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]
    li_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            li_node = node
            break
    if not li_node:
        return False
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
    }""", {"id": calc_id, "variantId": brz_gid, "quantity": li_node["quantity"]})
    if data["orderEditAddVariant"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap CH-FOWC -> CH-BRZ (curation, out of stock)") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    print(f"    OK {order_info['order_name']}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Swap {OLD_SKU} -> {NEW_SKU} [{mode}]")
    print(f"  Keep: paid, BYO, customer-chosen")
    print(f"  Available inventory: {AVAILABLE}")
    print(f"{'='*60}\n")

    print("Looking up CH-BRZ variant...")
    global BRZ_GID
    BRZ_GID = find_brz_variant()

    print("\nFetching all CH-FOWC orders...")
    keep, check = fetch_all_fowc()
    print(f"  Keep (paid/BYO): {len(keep)} ({sum(k['qty'] for k in keep)} units)")
    print(f"  Check curation: {len(check)}")

    # Check box_contents for curation orders
    print("\nChecking box_contents for customer-chosen...")
    chosen = []
    swappable = []
    for c in check:
        if check_box_contents_fowc(c["order_gid"]):
            chosen.append(c)
        else:
            swappable.append(c)
        time.sleep(0.3)

    keep_total = sum(k["qty"] for k in keep) + sum(c["qty"] for c in chosen)
    print(f"  Customer-chosen (keep): {len(chosen)} ({sum(c['qty'] for c in chosen)} units)")
    print(f"  Default recipe (swappable): {len(swappable)} ({sum(s['qty'] for s in swappable)} units)")
    print(f"\n  Total keeping: {keep_total}")
    print(f"  Available: {AVAILABLE}")

    # How many do we need to swap?
    need_to_swap = max(0, keep_total + sum(s["qty"] for s in swappable) - AVAILABLE)
    # But we can only swap the swappable ones
    swap_count = min(len(swappable), need_to_swap)
    targets = swappable[:swap_count]

    print(f"  Need to swap: {need_to_swap}")
    print(f"  Will swap: {len(targets)}\n")

    if not targets:
        print("Nothing to swap.")
        return

    if not COMMIT:
        print(f"DRY-RUN. {len(targets)} would be swapped.")
        print("Run with --commit to apply.")
        return

    s, f = 0, 0
    for i, t in enumerate(targets):
        if swap_item(t, BRZ_GID):
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
