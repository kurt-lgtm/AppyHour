"""Three fixes:
1. Swap 7 CH-EBRIE curation to CH-PBRIE
2. Swap 350 of the FOWC->BRZ swapped orders to CH-WMANG instead
3. Remove+refund paid CH-FOWC until only 49 remain on Shopify

Usage:
    python fix_ebrie_fowc_brz.py              # dry-run
    python fix_ebrie_fowc_brz.py --commit     # apply
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


def swap_order(order_gid, old_sku, new_variant_gid, staff_note):
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
        if (node.get("sku") or "").strip() == old_sku and node["quantity"] > 0:
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
    }""", {"id": calc_id, "variantId": new_variant_gid, "quantity": li_node["quantity"]})
    if data["orderEditAddVariant"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "%s") { order { id name } userErrors { field message } }
    }""" % staff_note, {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    return True


def remove_and_refund(order_info):
    order_gid = order_info["order_gid"]
    order_id = order_info["order_id"]
    # Remove via order edit
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
        if (node.get("sku") or "").strip() == "CH-FOWC" and node["quantity"] > 0:
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
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Remove CH-FOWC (paid, out of stock)") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    # Refund
    if order_info["price"] > 0:
        try:
            calc_resp = requests.post(f"{REST_BASE}/orders/{order_id}/refunds/calculate.json",
                headers=HEADERS,
                json={"refund": {"refund_line_items": [{"line_item_id": order_info["line_item_id"], "quantity": order_info["qty"]}]}},
                timeout=30)
            calc_resp.raise_for_status()
            transactions = calc_resp.json().get("refund", {}).get("transactions", [])
            time.sleep(0.3)
            ref_resp = requests.post(f"{REST_BASE}/orders/{order_id}/refunds.json",
                headers=HEADERS,
                json={"refund": {"notify": True, "refund_line_items": [{"line_item_id": order_info["line_item_id"], "quantity": order_info["qty"]}], "transactions": transactions}},
                timeout=30)
            ref_resp.raise_for_status()
            amt = sum(float(t.get("amount", 0)) for t in ref_resp.json().get("refund", {}).get("transactions", []))
            print(f"      REFUNDED ${amt:.2f}")
        except Exception as e:
            print(f"      REFUND FAILED: {e}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Three fixes [{mode}]")
    print(f"{'='*60}")

    # Look up variants
    print("\nLooking up variants...")
    pbrie_data = gql('{ productVariants(first: 5, query: "sku:CH-PBRIE") { edges { node { id sku price } } } }')
    pbrie_variants = [e["node"] for e in pbrie_data["productVariants"]["edges"] if e["node"]["sku"] == "CH-PBRIE"]
    pbrie_variants.sort(key=lambda v: float(v["price"]))
    PBRIE_GID = pbrie_variants[0]["id"]
    print(f"  CH-PBRIE: {PBRIE_GID} ${pbrie_variants[0]['price']}")

    wmang_data = gql('{ productVariants(first: 5, query: "sku:CH-WMANG") { edges { node { id sku price } } } }')
    wmang_variants = [e["node"] for e in wmang_data["productVariants"]["edges"] if e["node"]["sku"] == "CH-WMANG"]
    wmang_variants.sort(key=lambda v: float(v["price"]))
    WMANG_GID = wmang_variants[0]["id"]
    print(f"  CH-WMANG: {WMANG_GID} ${wmang_variants[0]['price']}")

    # Scan all orders
    print("\nFetching orders...")
    ebrie_targets = []      # Task 1: curation CH-EBRIE to swap
    brz_swap_targets = []   # Task 2: swapped BRZ (from FOWC) to change to WMANG
    fowc_paid = []          # Task 3: paid CH-FOWC to remove+refund

    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    fowc_total_shopify = 0

    while url:
        page += 1
        print(f"  Page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if "_SHIP_2026-03-23" not in tags:
                continue

            has_fowc_removed = False
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                qty_orig = li.get("quantity", 0)
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                price = float(li.get("price", "0"))

                # Track removed FOWC
                if sku == "CH-FOWC" and fq == 0 and qty_orig > 0:
                    has_fowc_removed = True

                # Task 1: curation CH-EBRIE (limit 7)
                if sku == "CH-EBRIE" and fq > 0 and "_rc_bundle" in prop_names and len(ebrie_targets) < 7:
                    ebrie_targets.append({
                        "order_gid": f"gid://shopify/Order/{o['id']}",
                        "order_name": o["name"],
                    })

                # Task 3: paid CH-FOWC
                if sku == "CH-FOWC" and fq > 0:
                    fowc_total_shopify += fq
                    if "_rc_bundle" not in prop_names:
                        fowc_paid.append({
                            "order_id": o["id"],
                            "order_gid": f"gid://shopify/Order/{o['id']}",
                            "order_name": o["name"],
                            "line_item_id": li["id"],
                            "qty": fq,
                            "price": price,
                        })

            # Task 2: non-curation BRZ on order that had FOWC removed
            if has_fowc_removed:
                for li in o.get("line_items", []):
                    sku = (li.get("sku") or "").strip()
                    fq = li.get("fulfillable_quantity", li.get("quantity", 0))
                    props = li.get("properties", []) or []
                    prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                    if sku == "CH-BRZ" and fq > 0 and "_rc_bundle" not in prop_names:
                        brz_swap_targets.append({
                            "order_gid": f"gid://shopify/Order/{o['id']}",
                            "order_name": o["name"],
                        })

        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)

    # Task 3: how many paid FOWC to refund to get to 49?
    fowc_to_remove = max(0, fowc_total_shopify - 49)
    fowc_refund_targets = fowc_paid[:fowc_to_remove]

    print(f"\n--- Task 1: CH-EBRIE -> CH-PBRIE ---")
    print(f"  Found: {len(ebrie_targets)} curation CH-EBRIE")

    print(f"\n--- Task 2: Swapped BRZ -> CH-WMANG ---")
    brz_swap_targets = brz_swap_targets[:350]
    print(f"  Found: {len(brz_swap_targets)} swapped BRZ (from FOWC)")

    print(f"\n--- Task 3: Remove+refund paid CH-FOWC ---")
    print(f"  Current FOWC on Shopify: {fowc_total_shopify}")
    print(f"  Paid FOWC: {len(fowc_paid)}")
    print(f"  Need to remove to reach 49: {fowc_to_remove}")
    print(f"  Will refund: {len(fowc_refund_targets)}")
    if fowc_refund_targets:
        total_refund = sum(f["price"] * f["qty"] for f in fowc_refund_targets)
        print(f"  Total refund: ${total_refund:.2f}")

    if not COMMIT:
        print(f"\nDRY-RUN. Run with --commit to apply.")
        return

    # Execute Task 1
    print(f"\n--- Executing Task 1: EBRIE -> PBRIE ---")
    s1, f1 = 0, 0
    for t in ebrie_targets:
        if swap_order(t["order_gid"], "CH-EBRIE", PBRIE_GID, "Swap CH-EBRIE -> CH-PBRIE"):
            print(f"    OK {t['order_name']}")
            s1 += 1
        else:
            print(f"    FAILED {t['order_name']}")
            f1 += 1
        time.sleep(0.5)
    print(f"  Done: {s1} swapped, {f1} failed")

    # Execute Task 2
    print(f"\n--- Executing Task 2: BRZ -> WMANG ---")
    s2, f2 = 0, 0
    for i, t in enumerate(brz_swap_targets):
        if swap_order(t["order_gid"], "CH-BRZ", WMANG_GID, "Swap CH-BRZ -> CH-WMANG (was FOWC)"):
            s2 += 1
        else:
            f2 += 1
        if (i + 1) % 50 == 0:
            print(f"    Progress: {i+1}/{len(brz_swap_targets)} ({s2} ok, {f2} fail)")
        time.sleep(0.5)
    print(f"  Done: {s2} swapped, {f2} failed")

    # Execute Task 3
    print(f"\n--- Executing Task 3: Remove+refund paid FOWC ---")
    s3, f3 = 0, 0
    for t in fowc_refund_targets:
        print(f"    {t['order_name']}...")
        if remove_and_refund(t):
            s3 += 1
        else:
            f3 += 1
        time.sleep(0.5)
    print(f"  Done: {s3} removed+refunded, {f3} failed")

    print(f"\n{'='*60}")
    print(f"  Summary:")
    print(f"    EBRIE->PBRIE: {s1}/{len(ebrie_targets)}")
    print(f"    BRZ->WMANG: {s2}/{len(brz_swap_targets)}")
    print(f"    FOWC refund: {s3}/{len(fowc_refund_targets)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
