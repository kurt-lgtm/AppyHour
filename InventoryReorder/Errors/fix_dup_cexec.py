"""Remove duplicate CEX-EC-* from orders that have the same CEX-EC SKU twice."""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}


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


def fetch_all_unfulfilled():
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,tags,line_items,customer",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        orders.extend(resp.json().get("orders", []))
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return orders


def find_dup_cexec(orders):
    results = []
    for o in orders:
        tags = o.get("tags", "")
        if "reship" in tags.lower():
            continue
        items = o.get("line_items", [])
        cex_lines = []
        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku.startswith("CEX-EC-"):
                cex_lines.append({"id": li["id"], "sku": sku, "qty": li.get("quantity", 1)})

        if len(cex_lines) < 2:
            continue
        # Only same-SKU duplicates
        if len(set(c["sku"] for c in cex_lines)) > 1:
            continue

        cust = o.get("customer", {}) or {}
        name = f"{cust.get('first_name', '')} {cust.get('last_name', '')}".strip()
        results.append({
            "order_id": o["id"],
            "order_name": o.get("name", ""),
            "customer": name,
            "sku": cex_lines[0]["sku"],
            "count": len(cex_lines),
        })
    return results


def fix_order(r):
    order_gid = f"gid://shopify/Order/{r['order_id']}"
    order_name = r["order_name"]
    target_sku = r["sku"]

    print(f"\n  Editing {order_name} ({r['customer']})...")

    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) { edges { node { id sku quantity } } }
        }
        userErrors { field message }
      }
    }
    """, {"id": order_gid})

    edit = data["orderEditBegin"]
    if edit["userErrors"]:
        print(f"    FAILED: {edit['userErrors']}")
        return False

    calc_id = edit["calculatedOrder"]["id"]

    calc_cex = [
        e["node"] for e in edit["calculatedOrder"]["lineItems"]["edges"]
        if e["node"]["sku"] == target_sku and e["node"]["quantity"] > 0
    ]

    if len(calc_cex) < 2:
        print(f"    Only {len(calc_cex)} {target_sku} in edit session, skipping")
        return False

    time.sleep(0.3)

    # Remove all but the first
    for node in calc_cex[1:]:
        data = gql("""
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """, {"id": calc_id, "lineItemId": node["id"], "quantity": 0})

        errors = data["orderEditSetQuantity"]["userErrors"]
        if errors:
            print(f"    FAILED to remove: {errors}")
            return False
        print(f"    Removed duplicate {target_sku}")
        time.sleep(0.3)

    # Commit
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Remove duplicate CEX-EC line item") {
        order { id name }
        userErrors { field message }
      }
    }
    """, {"id": calc_id})

    errors = data["orderEditCommit"]["userErrors"]
    if errors:
        print(f"    COMMIT FAILED: {errors}")
        return False

    print(f"    COMMITTED {order_name}")
    return True


commit = "--commit" in sys.argv

print("Fetching unfulfilled orders...")
orders = fetch_all_unfulfilled()
print(f"Fetched {len(orders)} orders\n")

results = find_dup_cexec(orders)
print(f"Found {len(results)} orders with duplicate same-SKU CEX-EC-*\n")

for r in results:
    print(f"  {r['order_name']} {r['customer']}: {r['sku']} x{r['count']} -> remove {r['count']-1}")

if not results:
    print("Nothing to fix.")
elif not commit:
    print(f"\nDRY RUN — use --commit to apply.")
else:
    print(f"\nApplying changes...")
    success = 0
    failed = 0
    for r in results:
        if fix_order(r):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)
    print(f"\nRESULTS: {success} succeeded, {failed} failed")
