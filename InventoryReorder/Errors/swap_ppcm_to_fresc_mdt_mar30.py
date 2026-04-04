# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Swap curated AC-PPCM -> AC-FRESC on MDT orders for _SHIP_2026-03-30.

Limits to TARGET_COUNT successful swaps. Skips:
  - Orders where customer already has AC-FRESC (Shopify or Recharge)
  - Gift Redemption orders (locked) — moves on to fill the count

Usage:
    python swap_ppcm_to_fresc_mdt_mar30.py              # dry-run
    python swap_ppcm_to_fresc_mdt_mar30.py --commit     # apply
"""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
RC_TOKEN = settings["recharge_api_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
RC_BASE = "https://api.rechargeapps.com"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}
RC_HEADERS = {"X-Recharge-Access-Token": RC_TOKEN, "Content-Type": "application/json"}

COMMIT = "--commit" in sys.argv
SHIP_TAG = "_SHIP_2026-03-30"
OLD_SKU = "AC-PPCM"
NEW_SKU = "AC-FRESC"
TARGET_COUNT = 100

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

def find_variant_gid():
    query = '{ productVariants(first: 10, query: "sku:' + NEW_SKU + '") { edges { node { id sku price product { title } } } } }'
    data = gql(query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == NEW_SKU:
            variants.append(node)
            print(f"    {node['sku']}: ${node['price']} - {node['product']['title']} ({node['id']})")
    if not variants:
        return None
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"]

def fetch_recharge_fresc_emails():
    """Return set of customer emails that already have AC-FRESC in a queued charge around the ship date."""
    fresc_emails = set()
    url = f"{RC_BASE}/charges"
    params = {
        "status": "queued",
        "scheduled_at_min": "2026-03-29",
        "scheduled_at_max": "2026-03-31",
        "limit": 250,
    }
    page = 0
    cursor = None
    while True:
        page += 1
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        resp = requests.get(url, headers=RC_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        charges = data.get("charges", [])
        for charge in charges:
            for li in charge.get("line_items", []):
                if (li.get("sku") or "").strip() == NEW_SKU:
                    email = charge.get("email", "").strip().lower()
                    if email:
                        fresc_emails.add(email)
                    break
        cursor = data.get("next_cursor")
        if not cursor or not charges:
            break
        time.sleep(0.1)
    return fresc_emails

def is_mdt_order(line_items):
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        if "MDT" in sku and sku.startswith("AHB-"):
            return True
    return False

def fetch_targets(fresc_emails):
    targets = []
    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "fields": "id,name,tags,line_items,email,customer"}
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
            line_items = o.get("line_items", [])
            if not is_mdt_order(line_items):
                continue
            # Skip if customer already has AC-FRESC via Recharge
            email = (o.get("email") or "").strip().lower()
            if not email and o.get("customer"):
                email = (o["customer"].get("email") or "").strip().lower()
            if email and email in fresc_emails:
                continue
            # Skip if order already has AC-FRESC in active line items
            active_skus = {(li.get("sku") or "").strip() for li in line_items
                           if li.get("fulfillable_quantity", li.get("quantity", 0)) > 0}
            if NEW_SKU in active_skus:
                continue
            for li in line_items:
                sku = (li.get("sku") or "").strip()
                if sku != OLD_SKU:
                    continue
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                if fq <= 0:
                    continue
                props = li.get("properties") or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    continue  # curation only
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": fq,
                })
                break
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.5)
    return targets

def swap_order(order_info, variant_gid):
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
        print(f"    FAILED begin {name}: {edit['userErrors']}")
        return False

    calc = edit["calculatedOrder"]
    calc_id = calc["id"]

    li_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            li_node = node
            break

    if not li_node:
        print(f"    SKIP {name}: {OLD_SKU} not in calculated order")
        return False

    time.sleep(0.3)

    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
        userErrors { field message }
      }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        print(f"    FAILED setQty {name}: {data['orderEditSetQuantity']['userErrors']}")
        return False

    time.sleep(0.3)

    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        userErrors { field message }
      }
    }""", {"id": calc_id, "variantId": variant_gid, "quantity": li_node["quantity"]})
    if data["orderEditAddVariant"]["userErrors"]:
        print(f"    FAILED addVariant {name}: {data['orderEditAddVariant']['userErrors']}")
        return False

    time.sleep(0.3)

    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap AC-PPCM -> AC-FRESC (MDT curation, stock sub)") {
        order { id name }
        userErrors { field message }
      }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        print(f"    FAILED commit {name}: {data['orderEditCommit']['userErrors']}")
        return False

    print(f"    OK {name}: {OLD_SKU}->{NEW_SKU}")
    return True

def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Curated {OLD_SKU} -> {NEW_SKU} on MDT orders [{mode}]")
    print(f"  Ship tag: {SHIP_TAG}  |  Target: {TARGET_COUNT} swaps")
    print(f"{'='*60}\n")

    print(f"Looking up {NEW_SKU} variant GID...")
    vgid = find_variant_gid()
    if not vgid:
        print(f"  ERROR: {NEW_SKU} variant not found!")
        return
    print(f"  -> {vgid}\n")

    print(f"Checking Recharge for customers already receiving {NEW_SKU}...")
    fresc_emails = fetch_recharge_fresc_emails()
    print(f"  {len(fresc_emails)} emails already have {NEW_SKU} in Recharge — will skip\n")

    print(f"Fetching curated {OLD_SKU} MDT orders tagged {SHIP_TAG}...")
    targets = fetch_targets(fresc_emails)
    print(f"  {len(targets)} eligible orders found (capping at {TARGET_COUNT})\n")

    for t in targets[:TARGET_COUNT]:
        print(f"  {t['order_name']} (qty {t['qty']})")

    if not COMMIT:
        print(f"\nDRY-RUN complete. Run with --commit to apply.")
        return

    print(f"\nApplying swaps (will continue past locked orders to reach {TARGET_COUNT})...")
    success, failed, skipped = 0, 0, 0
    for t in targets:
        if success >= TARGET_COUNT:
            break
        if swap_order(t, vgid):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} swapped, {failed} failed/skipped")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
