# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Swap CH-ALPHA->CH-ALP and MT-BRAS->MT-SBRES on _SHIP_2026-03-30 orders.

Swaps all matching items (curation or paid) for the March 30 ship date.

Usage:
    python swap_alpha_bras_mar30.py              # dry-run
    python swap_alpha_bras_mar30.py --commit     # apply
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
SHIP_TAG = "_SHIP_2026-03-30"

# old_sku -> new_sku  (GIDs looked up at runtime)
SWAP_MAP = {
    "CH-ALPHA": "CH-ALP",
    "MT-BRAS":  "MT-SBRES",
}

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

def find_variant_gid(sku):
    query = '{ productVariants(first: 10, query: "sku:' + sku + '") { edges { node { id sku price product { title } } } } }'
    data = gql(query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == sku:
            variants.append(node)
            print(f"    {node['sku']}: ${node['price']} - {node['product']['title']} ({node['id']})")
    if not variants:
        return None
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"]

def fetch_targets():
    targets = []
    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if SHIP_TAG not in tags:
                continue
            swaps_needed = []
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku not in SWAP_MAP:
                    continue
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                if fq <= 0:
                    continue
                swaps_needed.append(sku)
            if swaps_needed:
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "swaps": swaps_needed,
                })
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.5)
    return targets

def swap_order(order_info, variant_gids):
    order_gid = order_info["order_gid"]
    name = order_info["order_name"]

    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": order_gid})

    edit = data["orderEditBegin"]
    if edit["userErrors"]:
        print(f"    FAILED begin: {edit['userErrors']}")
        return False

    calc = edit["calculatedOrder"]
    calc_id = calc["id"]

    # Map sku -> calc line item node
    calc_items = {}
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        sku = (node.get("sku") or "").strip()
        if sku in SWAP_MAP and node["quantity"] > 0:
            calc_items[sku] = node

    if not calc_items:
        print(f"    SKIP {name}: no swappable items in calculated order")
        # Abort the edit session to avoid leaving it open
        gql("""mutation($id: ID!) { orderEditCommit(id: $id, notifyCustomer: false) { userErrors { message } } }""",
            {"id": calc_id})
        return False

    time.sleep(0.3)
    for old_sku, node in calc_items.items():
        new_sku = SWAP_MAP[old_sku]
        vgid = variant_gids[new_sku]

        data = gql("""
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
            userErrors { field message }
          }
        }""", {"id": calc_id, "lineItemId": node["id"], "quantity": 0})
        if data["orderEditSetQuantity"]["userErrors"]:
            print(f"    FAILED setQty on {name}: {data['orderEditSetQuantity']['userErrors']}")
            return False
        time.sleep(0.3)

        data = gql("""
        mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
          orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
            userErrors { field message }
          }
        }""", {"id": calc_id, "variantId": vgid, "quantity": node["quantity"]})
        if data["orderEditAddVariant"]["userErrors"]:
            print(f"    FAILED addVariant on {name}: {data['orderEditAddVariant']['userErrors']}")
            return False
        time.sleep(0.3)

    swapped_desc = ", ".join(f"{s}->{SWAP_MAP[s]}" for s in calc_items)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap " + $note) {
        order { id name }
        userErrors { field message }
      }
    }""".replace('"Swap " + $note', f'"Swap {swapped_desc} (stock sub)"'), {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        print(f"    FAILED commit on {name}: {data['orderEditCommit']['userErrors']}")
        return False

    print(f"    OK {name}: {swapped_desc}")
    return True

def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  CH-ALPHA->CH-ALP + MT-BRAS->MT-SBRES [{mode}]")
    print(f"  Ship tag: {SHIP_TAG}")
    print(f"{'='*60}\n")

    print("Looking up variant GIDs...")
    variant_gids = {}
    for new_sku in set(SWAP_MAP.values()):
        print(f"  {new_sku}:")
        gid = find_variant_gid(new_sku)
        if not gid:
            print(f"  ERROR: {new_sku} variant not found!")
            return
        variant_gids[new_sku] = gid
        print(f"  -> {gid}")

    print(f"\nFetching orders tagged {SHIP_TAG}...")
    targets = fetch_targets()
    print(f"  {len(targets)} orders need swaps\n")

    for t in targets:
        print(f"  {t['order_name']}: {', '.join(t['swaps'])}")

    if not COMMIT:
        print(f"\nDRY-RUN complete. Run with --commit to apply.")
        return

    print(f"\nApplying swaps...")
    s, f = 0, 0
    for t in targets:
        if swap_order(t, variant_gids):
            s += 1
        else:
            f += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {s} swapped, {f} failed/skipped")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
