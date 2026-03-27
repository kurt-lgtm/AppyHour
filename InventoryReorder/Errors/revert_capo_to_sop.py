"""Revert 136 extra MT-SOP->MT-CAPO swaps back to MT-SOP.

Finds orders with non-curation MT-CAPO (from our swap) that also have MT-JAHH,
and swaps MT-CAPO back to MT-SOP.

Usage:
    python revert_capo_to_sop.py              # dry-run
    python revert_capo_to_sop.py --commit     # apply
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
REVERT_LIMIT = 136
SOP_GID = "gid://shopify/ProductVariant/49467543257368"  # $0 MT-SOP*


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


def fetch_swapped_capo():
    """Find orders with non-curation MT-CAPO (our swap) + curation MT-JAHH."""
    targets = []
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
            has_capo_swapped = False
            has_jahh = False
            has_sop_removed = False
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                qty = li.get("quantity", 0)
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                # Non-curation MT-CAPO = our swap
                if sku == "MT-CAPO" and fq > 0 and "_rc_bundle" not in prop_names:
                    has_capo_swapped = True
                if sku == "MT-JAHH" and "_rc_bundle" in prop_names and fq > 0:
                    has_jahh = True
                # MT-SOP removed (fq=0, qty>0)
                if sku == "MT-SOP" and fq == 0 and qty > 0:
                    has_sop_removed = True
            if has_capo_swapped and has_jahh and has_sop_removed:
                targets.append({
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
    return targets


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
        return False
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]
    # Find the non-curation MT-CAPO
    capo_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == "MT-CAPO" and node["quantity"] > 0:
            capo_node = node
            break
    if not capo_node:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) { calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "lineItemId": capo_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) { calculatedLineItem { id } calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "variantId": SOP_GID, "quantity": 1})
    if data["orderEditAddVariant"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Revert MT-CAPO back to MT-SOP") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    print(f"    OK {order_info['order_name']}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Revert MT-CAPO -> MT-SOP [{mode}]")
    print(f"  Limit: {REVERT_LIMIT}")
    print(f"{'='*60}\n")

    print("Finding swapped MT-CAPO orders...")
    targets = fetch_swapped_capo()
    print(f"  Found {len(targets)} orders with swapped MT-CAPO\n")

    targets = targets[:REVERT_LIMIT]
    print(f"  Will revert: {len(targets)}")

    if not COMMIT:
        print(f"\nDRY-RUN. {len(targets)} would be reverted.")
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
    print(f"  Done: {s} reverted, {f} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
