"""Verify Class 4B duplicate curation orders before removal.

Checks whether "duplicate" items are truly double-written curation errors vs:
1. Customer customization (box_contents shows they chose that item twice)
2. Paid one-time add-ons (no _rc_bundle property)
3. Paid bundle components (BL- parent)

Uses GraphQL to pull customAttributes (including box_contents) for each order.

Usage:
    python verify_class4b.py
"""
import requests, json, sys, time, re
from collections import Counter, defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
NAME_TO_SKU = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\product_name_to_sku.json"

with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

with open(NAME_TO_SKU, encoding="utf-8") as f:
    name_to_sku_map = json.load(f)

# Build a normalized lookup: lowercase product name -> SKU
PRODUCT_NAME_TO_SKU = {}
for name, info in name_to_sku_map.items():
    PRODUCT_NAME_TO_SKU[name.lower().strip()] = info["sku"]
    # Also strip trailing asterisks and "FREE " prefix for fuzzy matching
    clean = re.sub(r'\*+$', '', name).strip().lower()
    PRODUCT_NAME_TO_SKU[clean] = info["sku"]
    if clean.startswith("free "):
        PRODUCT_NAME_TO_SKU[clean[5:]] = info["sku"]

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

FOOD_PREFIXES = ("CH-", "MT-", "AC-", "CEX-")

# Orders to skip per user instruction
SKIP_ORDERS = {"#115034", "#105347"}


def fetch_all_unfulfilled():
    """Fetch all unfulfilled open orders via REST API."""
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,created_at,customer,email,tags,line_items",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("orders", [])
        orders.extend(batch)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return orders


def gql(query, variables=None):
    """Execute a Shopify GraphQL query."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def find_4b_candidates(orders):
    """Identify Class 4B candidate orders from REST data (same logic as fix script)."""
    results = []
    for order in orders:
        order_name = order.get("name", "")
        if order_name in SKIP_ORDERS:
            continue

        tags = order.get("tags", "")
        if "reship" in tags.lower():
            continue

        line_items = order.get("line_items", [])
        if not line_items:
            continue

        # Skip Class 2/3 (blank-SKU promo product)
        has_blank_promo = any(
            not (li.get("sku") or "").strip()
            and ("appyhour box" in (li.get("title") or "").lower() or "appy hour" in (li.get("title") or "").lower())
            for li in line_items
        )
        if has_blank_promo:
            continue

        # Collect food items with curation flag
        food_by_sku = {}
        for li in line_items:
            sku = (li.get("sku") or "").strip()
            qty = li.get("quantity", 1)
            if qty == 0:
                continue
            if not sku.startswith(FOOD_PREFIXES):
                continue
            props = li.get("properties") or []
            prop_names = {p.get("name", "") for p in props}
            is_curation = "_rc_bundle" in prop_names
            has_parent_sub = "_parent_subscription_id" in prop_names
            if sku not in food_by_sku:
                food_by_sku[sku] = []
            food_by_sku[sku].append({
                "id": li["id"],
                "qty": qty,
                "is_curation": is_curation,
                "has_parent_sub": has_parent_sub,
                "title": li.get("title", ""),
                "properties": props,
            })

        # Find SKUs with 2+ curation copies
        dupes = {}
        for sku, items in food_by_sku.items():
            curation_items = [i for i in items if i["is_curation"]]
            if len(curation_items) >= 2:
                dupes[sku] = items  # all copies (curation + non-curation)

        if not dupes:
            continue

        # Check for BL- (paid bundle) parent lines
        has_bl = any(
            (li.get("sku") or "").strip().startswith("BL-")
            for li in line_items if li.get("quantity", 0) > 0
        )

        # Get box SKU
        box_sku = ""
        for li in line_items:
            s = (li.get("sku") or "").strip()
            if s.startswith("AHB-"):
                box_sku = s
                break

        customer = order.get("customer") or {}
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        results.append({
            "order_id": order["id"],
            "order_name": order_name,
            "customer": cust_name,
            "email": order.get("email", ""),
            "box_sku": box_sku,
            "dupes": dupes,
            "has_bl": has_bl,
            "all_items": food_by_sku,
        })

    return results


def fetch_graphql_details(order_id):
    """Fetch line item customAttributes via GraphQL for box_contents check."""
    query = """
    {
      order(id: "gid://shopify/Order/%s") {
        id
        name
        lineItems(first: 50) {
          edges {
            node {
              id
              sku
              quantity
              title
              customAttributes { key value }
              originalUnitPriceSet { shopMoney { amount } }
            }
          }
        }
      }
    }
    """ % order_id

    data = gql(query)
    return data.get("order")


def parse_box_contents(box_contents_str):
    """Parse box_contents format: '1x Product Name\\n2x Product Name\\n...'
    Returns dict of {sku: qty} where SKUs are resolved via product name mapping.
    """
    if not box_contents_str:
        return {}

    result = {}
    unresolved = []
    for line in box_contents_str.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Parse "Nx Product Name" or just "Product Name"
        m = re.match(r'^(\d+)x\s+(.+)$', line, re.IGNORECASE)
        if m:
            qty = int(m.group(1))
            name = m.group(2).strip()
        else:
            qty = 1
            name = line

        # Resolve product name to SKU
        sku = resolve_name_to_sku(name)
        if sku:
            result[sku] = result.get(sku, 0) + qty
        else:
            unresolved.append(f"{qty}x {name}")

    return result, unresolved


def resolve_name_to_sku(name):
    """Try to match a product name to a SKU using the mapping file."""
    # Exact match (case-insensitive)
    lower = name.lower().strip()
    if lower in PRODUCT_NAME_TO_SKU:
        return PRODUCT_NAME_TO_SKU[lower]

    # Strip trailing asterisks
    clean = re.sub(r'\*+$', '', name).strip().lower()
    if clean in PRODUCT_NAME_TO_SKU:
        return PRODUCT_NAME_TO_SKU[clean]

    # Try partial match (name contained in mapping key or vice versa)
    for map_name, sku in PRODUCT_NAME_TO_SKU.items():
        if clean in map_name or map_name in clean:
            return sku

    return None


def verify_order(candidate, gql_data):
    """Verify a single Class 4B candidate order.

    Returns a dict with verdict and reasoning.
    """
    order_name = candidate["order_name"]
    dupes = candidate["dupes"]
    has_bl = candidate["has_bl"]

    # Extract box_contents from GQL customAttributes on AHB- line
    box_contents_str = None
    box_contents_parsed = {}
    box_contents_unresolved = []
    ahb_line = None

    if gql_data:
        for edge in gql_data["lineItems"]["edges"]:
            node = edge["node"]
            sku = (node.get("sku") or "").strip()
            if sku.startswith("AHB-"):
                ahb_line = node
                attrs = node.get("customAttributes") or []
                for attr in attrs:
                    if attr.get("key") == "box_contents":
                        box_contents_str = attr.get("value", "")
                        break
                break

    if box_contents_str:
        box_contents_parsed, box_contents_unresolved = parse_box_contents(box_contents_str)

    # Also check for paid items (price > 0) in GQL data
    paid_items = set()
    if gql_data:
        for edge in gql_data["lineItems"]["edges"]:
            node = edge["node"]
            sku = (node.get("sku") or "").strip()
            price_info = node.get("originalUnitPriceSet", {})
            if price_info:
                amount = float(price_info.get("shopMoney", {}).get("amount", "0"))
                if amount > 0 and sku.startswith(FOOD_PREFIXES):
                    paid_items.add(sku)

    # Analyze each duplicate SKU
    sku_verdicts = []
    all_safe = True

    for sku, items in dupes.items():
        curation_copies = [i for i in items if i["is_curation"]]
        non_curation_copies = [i for i in items if not i["is_curation"]]
        total_copies = len(items)

        verdict = {
            "sku": sku,
            "curation_copies": len(curation_copies),
            "non_curation_copies": len(non_curation_copies),
            "reasons": [],
            "safe": True,
        }

        # Check 1: Is this SKU in box_contents with qty > 1?
        if box_contents_parsed and sku in box_contents_parsed:
            bc_qty = box_contents_parsed[sku]
            if bc_qty >= 2:
                verdict["reasons"].append(
                    f"CUSTOMER CHOSE {bc_qty}x in box_contents — duplicates are INTENTIONAL"
                )
                verdict["safe"] = False
                all_safe = False
                continue

        # Check 2: Does the non-curation copy exist (one-time add-on)?
        if non_curation_copies:
            # If there's a non-curation copy, at least one is a paid add-on or Matrixify item
            for nc in non_curation_copies:
                if nc.get("has_parent_sub"):
                    verdict["reasons"].append(
                        f"Non-curation copy has _parent_subscription_id — paid add-on or bundle component"
                    )
                    verdict["safe"] = False
                    all_safe = False
                else:
                    verdict["reasons"].append(
                        f"Non-curation copy exists (no _rc_bundle) — may be Matrixify/CEX-EC item"
                    )

        # Check 3: Paid bundle parent (BL-)?
        if has_bl and sku in paid_items:
            verdict["reasons"].append(
                f"Order has BL- bundle product and {sku} is paid — may be bundle component"
            )
            verdict["safe"] = False
            all_safe = False

        # Check 4: All copies are curation (_rc_bundle) → true dupe
        if len(curation_copies) >= 2 and not non_curation_copies and not has_bl:
            if not (box_contents_parsed and sku in box_contents_parsed and box_contents_parsed[sku] >= 2):
                verdict["reasons"].append(
                    f"Both copies have _rc_bundle, no paid add-on/bundle — TRUE CURATION DUPE"
                )
                verdict["safe"] = True

        if not verdict["reasons"]:
            verdict["reasons"].append(f"{len(curation_copies)} curation copies — true dupe, safe to remove extras")

        sku_verdicts.append(verdict)

    # Final order verdict
    if has_bl:
        order_verdict = "NEEDS REVIEW"
        order_reason = "Order has BL- paid bundle — verify items are not bundle components"
    elif box_contents_parsed:
        # Check if any dupe SKU has qty >= 2 in box_contents
        intentional_dupes = any(
            sku in box_contents_parsed and box_contents_parsed[sku] >= 2
            for sku in dupes
        )
        if intentional_dupes:
            order_verdict = "NEEDS REVIEW"
            order_reason = "Customer customization shows intentional duplicates in box_contents"
        else:
            order_verdict = "SAFE TO FIX"
            order_reason = "box_contents exists but no intentional duplicates found"
    elif all_safe:
        order_verdict = "SAFE TO FIX"
        order_reason = "All duplicates are _rc_bundle curation copies with no customer intent"
    else:
        order_verdict = "NEEDS REVIEW"
        order_reason = "Some items may be paid add-ons or bundle components"

    return {
        "order_name": order_name,
        "customer": candidate["customer"],
        "email": candidate["email"],
        "box_sku": candidate["box_sku"],
        "has_bl": has_bl,
        "box_contents_raw": box_contents_str,
        "box_contents_parsed": box_contents_parsed,
        "box_contents_unresolved": box_contents_unresolved,
        "sku_verdicts": sku_verdicts,
        "verdict": order_verdict,
        "reason": order_reason,
    }


def print_report(results):
    """Print the verification report."""
    safe = [r for r in results if r["verdict"] == "SAFE TO FIX"]
    review = [r for r in results if r["verdict"] == "NEEDS REVIEW"]
    skip = [r for r in results if r["verdict"] == "SKIP"]

    print(f"\n{'='*90}")
    print(f"CLASS 4B VERIFICATION REPORT — {len(results)} orders analyzed")
    print(f"{'='*90}")
    print(f"  SAFE TO FIX:   {len(safe)}")
    print(f"  NEEDS REVIEW:  {len(review)}")
    print(f"  SKIP:          {len(skip)}")
    print(f"  (Skipped Jamie Finch orders: #115034, #105347)")
    print(f"{'='*90}\n")

    # Print NEEDS REVIEW first (most important)
    if review:
        print(f"\n{'~'*90}")
        print("  NEEDS REVIEW — These orders require manual inspection")
        print(f"{'~'*90}")
        for r in review:
            _print_order_detail(r)

    # Print SAFE TO FIX
    if safe:
        print(f"\n{'~'*90}")
        print("  SAFE TO FIX — These orders can be fixed by removing duplicate curation items")
        print(f"{'~'*90}")
        for r in safe:
            _print_order_detail(r)

    # Print SKIP
    if skip:
        print(f"\n{'~'*90}")
        print("  SKIP — Excluded orders")
        print(f"{'~'*90}")
        for r in skip:
            print(f"  {r['order_name']} | {r['customer']} — {r['reason']}")

    # Summary table
    print(f"\n{'='*90}")
    print("SUMMARY TABLE")
    print(f"{'='*90}")
    print(f"{'Order':<12} {'Customer':<25} {'Box SKU':<22} {'Dupes':<30} {'Verdict':<15}")
    print(f"{'-'*12} {'-'*25} {'-'*22} {'-'*30} {'-'*15}")
    for r in results:
        dupe_skus = ", ".join(v["sku"] for v in r["sku_verdicts"])
        print(f"{r['order_name']:<12} {r['customer'][:24]:<25} {r['box_sku'][:21]:<22} {dupe_skus[:29]:<30} {r['verdict']:<15}")


def _print_order_detail(r):
    """Print detailed info for a single order."""
    print(f"\n  Order {r['order_name']} | {r['customer']} | {r['email']}")
    print(f"  Box: {r['box_sku']}")
    if r["has_bl"]:
        print(f"  ** HAS BL- PAID BUNDLE LINE **")

    # box_contents
    if r["box_contents_raw"]:
        print(f"  box_contents attribute FOUND:")
        for line in r["box_contents_raw"].strip().split("\n"):
            print(f"    {line.strip()}")
        if r["box_contents_parsed"]:
            print(f"  Parsed box_contents SKUs: {r['box_contents_parsed']}")
        if r["box_contents_unresolved"]:
            print(f"  Unresolved names: {r['box_contents_unresolved']}")
    else:
        print(f"  box_contents: NOT PRESENT")

    # Per-SKU verdicts
    for sv in r["sku_verdicts"]:
        print(f"  {sv['sku']}: {sv['curation_copies']} curation + {sv['non_curation_copies']} non-curation copies")
        for reason in sv["reasons"]:
            print(f"    -> {reason}")

    print(f"  >>> VERDICT: {r['verdict']} — {r['reason']}")


def main():
    print("=" * 60)
    print("CLASS 4B DUPLICATE VERIFICATION SCRIPT")
    print("=" * 60)

    print("\nStep 1: Fetching unfulfilled Shopify orders (REST API)...")
    orders = fetch_all_unfulfilled()
    print(f"  Fetched {len(orders)} unfulfilled orders")

    print("\nStep 2: Identifying Class 4B duplicate candidates...")
    candidates = find_4b_candidates(orders)
    print(f"  Found {len(candidates)} candidate orders")

    if not candidates:
        print("\nNo Class 4B duplicate orders found. Done.")
        return

    print(f"\nStep 3: Fetching GraphQL details for {len(candidates)} orders...")
    results = []
    for i, cand in enumerate(candidates, 1):
        order_name = cand["order_name"]
        print(f"  [{i}/{len(candidates)}] {order_name} ({cand['customer']})...")

        try:
            gql_data = fetch_graphql_details(cand["order_id"])
        except Exception as e:
            print(f"    ERROR fetching GQL: {e}")
            gql_data = None

        result = verify_order(cand, gql_data)
        results.append(result)
        time.sleep(0.5)

    print("\nStep 4: Generating report...")
    print_report(results)


if __name__ == "__main__":
    main()
