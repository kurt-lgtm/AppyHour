"""Scan unfulfilled Shopify orders for Class 2/3, 4B, 6, and ROT errors.

Class 2/3: Box product with blank SKU
Class 4B:  Duplicate food SKUs (same SKU on multiple line items) — excludes paid bundles (BL-)
           and customer-chosen duplicates (box_contents shows intentional qty > 1)
Class 6:   Curation mismatch (food items from wrong curation vs box SKU suffix)
Class ROT: Rotation bug — _SHIP prop date != ship tag date, or Recharge subscription SKU
           no longer matches the box SKU (charge kept old items after rotation)
"""
import requests, json, re, csv, time, os
from collections import Counter
from datetime import datetime

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

CURATION_RECIPES = settings.get("curation_recipes", {})
FOOD_PREFIXES = ("CH-", "MT-", "AC-")
CUSTOM_BOX_PREFIXES = ("AHB-MCUST", "AHB-LCUST")
MONTHLY_BOXES = {"AHB-MED", "AHB-CMED", "AHB-LGE"}

# Recharge API for rotation checks
RC_TOKEN = settings.get("recharge_api_token", "")
RC_BASE = "https://api.rechargeapps.com"
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "X-Recharge-Version": "2021-11",
    "Content-Type": "application/json",
}
_rc_sub_cache = {}


def rc_get_subscription(sub_id):
    """Fetch Recharge subscription by ID (cached)."""
    if not RC_TOKEN or not sub_id:
        return None
    sub_id = str(sub_id)
    if sub_id in _rc_sub_cache:
        return _rc_sub_cache[sub_id]
    try:
        resp = requests.get(f"{RC_BASE}/subscriptions/{sub_id}",
                            headers=RC_HEADERS, timeout=30)
        if resp.status_code == 200:
            sub = resp.json().get("subscription", {})
            _rc_sub_cache[sub_id] = sub
            return sub
        _rc_sub_cache[sub_id] = None
    except Exception:
        _rc_sub_cache[sub_id] = None
    return None

# Product name -> SKU mapping for parsing box_contents
_NAME_MAP_FILE = os.path.join(os.path.dirname(__file__), "product_name_to_sku.json")
_NAME_TO_SKU = {}  # normalized name -> sku
_NAME_TO_SKU_STAR = {}  # name with star -> sku (curation variants, preferred)
if os.path.exists(_NAME_MAP_FILE):
    with open(_NAME_MAP_FILE, encoding="utf-8") as f:
        _raw = json.load(f)
        for name, info in _raw.items():
            key = name.strip().lower()
            if key.endswith("*"):
                _NAME_TO_SKU_STAR[key.rstrip("*").strip()] = info["sku"]
            _NAME_TO_SKU[key.rstrip("*").strip()] = info["sku"]
    # Star variants override non-star (curation items are what box_contents refers to)
    _NAME_TO_SKU.update(_NAME_TO_SKU_STAR)


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


def parse_box_contents(text):
    """Parse box_contents string into {sku: qty}. Format: '2x Product Name\\n1x Other'."""
    result = {}
    if not text:
        return result
    for line in text.replace("\\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"(\d+)x\s+(.+)", line)
        if not m:
            continue
        qty = int(m.group(1))
        name = m.group(2).rstrip("*").strip().lower()
        sku = _NAME_TO_SKU.get(name)
        if sku:
            result[sku] = result.get(sku, 0) + qty
    return result


def get_box_contents_for_order(order_id):
    """Fetch box_contents via GraphQL customAttributes for an order."""
    data = gql("""{
      order(id: "gid://shopify/Order/%s") {
        lineItems(first: 50) {
          edges { node { sku customAttributes { key value } } }
        }
      }
    }""" % order_id)
    for edge in data["order"]["lineItems"]["edges"]:
        node = edge["node"]
        for attr in (node.get("customAttributes") or []):
            if attr["key"] == "box_contents" and attr.get("value"):
                return attr["value"]
    return None


def fetch_all_unfulfilled():
    orders = []
    url = f"{BASE}/orders.json"
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


def get_curation_from_box(sku):
    """AHB-MCUST-CORS-MDT -> MDT (last segment)."""
    parts = sku.split("-")
    if len(parts) >= 3:
        return parts[-1]
    return None


def analyze_order(order):
    tags = order.get("tags", "")
    if "reship" in tags.lower():
        return []

    line_items = order.get("line_items", [])
    if not line_items:
        return []

    # Skip specialty boxes
    if any((li.get("sku") or "").startswith("AHB-X") for li in line_items):
        return []

    errors = []

    box_skus = []
    food_skus = []
    has_blank_box = False

    # First pass: find BL- (paid bundle) — on Shopify we don't have subscription_id,
    # so we track which SKUs appear alongside a BL- product
    has_bundle = any((li.get("sku") or "").startswith("BL-") for li in line_items)

    # Check for box_contents in REST API properties first
    box_contents_text = None
    for li in line_items:
        for p in (li.get("properties") or []):
            if p.get("name") == "box_contents" and p.get("value"):
                box_contents_text = p["value"]
                break
        if box_contents_text:
            break

    for li in line_items:
        sku = (li.get("sku") or "").strip()
        title = (li.get("title") or "")
        qty = li.get("quantity", 0)
        props = li.get("properties") or []
        prop_names = {p.get("name", "") for p in props}
        is_curation = "_rc_bundle" in prop_names

        # Use fulfillable_quantity — REST API keeps quantity at original value
        # even after order edits set it to 0, but fulfillable_quantity reflects edits
        fq = li.get("fulfillable_quantity", qty)
        if fq <= 0:
            continue

        # Class 2/3: blank box SKU
        if ("appyhour box" in title.lower() or "appy hour" in title.lower()) and not sku:
            has_blank_box = True
        if sku.startswith("AHB-"):
            box_skus.append(sku)
        if sku.startswith(FOOD_PREFIXES):
            food_skus.append((sku, fq, is_curation))

    has_custom = any(s.startswith(CUSTOM_BOX_PREFIXES) for s in box_skus)
    has_monthly = any(s in MONTHLY_BOXES for s in box_skus)

    # ========== CLASS 2/3: Blank box SKU ==========
    if has_blank_box and not has_custom and not has_monthly:
        all_skus = [li.get("sku", "") for li in line_items if li.get("sku")]
        errors.append(("2/3", f"SKUs: {', '.join(all_skus) if all_skus else '(none)'}"))

    # ========== CLASS 4B: Duplicate food SKUs ==========
    # Count food SKUs across line items (each line item is a separate occurrence)
    sku_line_counts = Counter()  # how many separate line items per food SKU
    sku_total_qty = Counter()    # total quantity per food SKU
    sku_curation_count = Counter()  # how many line items per SKU are from curation (_rc_bundle)
    for sku, qty, is_curation in food_skus:
        sku_line_counts[sku] += 1
        sku_total_qty[sku] += qty
        if is_curation:
            sku_curation_count[sku] += 1

    # Duplicates: same SKU on multiple line items, OR qty > 1 on a single line
    dups = {}
    for sku in sku_total_qty:
        total = sku_total_qty[sku]
        if total > 1:
            # If order has BL- bundle, only flag if SKU appears on multiple line items
            # (bundle legitimately has high qty on ONE line item)
            if has_bundle and sku_line_counts[sku] <= 1:
                continue
            # If some copies are one-time Recharge add-ons (no _rc_bundle prop),
            # only the curation copies count as potential dupes.
            # Flag only if curation alone produced duplicates (2+ curation line items)
            curation_lines = sku_curation_count.get(sku, 0)
            non_curation_lines = sku_line_counts[sku] - curation_lines
            if non_curation_lines > 0 and curation_lines <= 1:
                # Has legitimate one-time purchases — not a real duplicate
                continue
            dups[sku] = total

    # Before flagging 4B, check box_contents for intentional customer-chosen duplicates
    if dups:
        bc_text = box_contents_text
        if not bc_text:
            try:
                bc_text = get_box_contents_for_order(order["id"])
                if bc_text:
                    box_contents_text = bc_text  # cache for Class 6 check below
            except Exception:
                pass
        if bc_text:
            bc_skus = parse_box_contents(bc_text)
            dups = {sku: total for sku, total in dups.items()
                    if bc_skus.get(sku, 1) < total}

    if dups:
        box = box_skus[0] if box_skus else ""
        total_food = sum(sku_total_qty.values())
        errors.append(("4B", f"Box: {box} | Dups: {dups} | Total food: {total_food}"))

    # ========== CLASS 6: Curation mismatch ==========
    # Only compare curation items (_rc_bundle) against expected recipe.
    # Paid extras / one-time add-ons are not curation errors.
    # Skip if customer has box_contents (they customized — items won't match standard recipe)
    custom_boxes = [s for s in box_skus if s.startswith(CUSTOM_BOX_PREFIXES)]
    if custom_boxes:
        box_curation = get_curation_from_box(custom_boxes[0])
        if box_curation and box_curation in CURATION_RECIPES:
            expected_skus = set(s for s, q in CURATION_RECIPES[box_curation])
            # Only include curation items (is_curation=True)
            actual_food = set(s for s, qty, is_cur in food_skus if is_cur)
            if actual_food:
                best_cur = None
                best_pct = 0
                for cur, recipe in CURATION_RECIPES.items():
                    recipe_skus = set(s for s, q in recipe)
                    if recipe_skus:
                        overlap = len(actual_food & recipe_skus)
                        pct = overlap / len(actual_food)
                        if pct > best_pct:
                            best_pct = pct
                            best_cur = cur

                expected_pct = len(actual_food & expected_skus) / len(actual_food)
                if best_cur and best_cur != box_curation and best_pct > expected_pct + 0.2:
                    # Before flagging, check if customer customized via box_contents
                    # (customized boxes won't match any standard recipe — not an error)
                    bc = box_contents_text
                    if not bc:
                        try:
                            bc = get_box_contents_for_order(order["id"])
                        except Exception:
                            pass
                    if not bc:
                        errors.append(("6", f"Box says {box_curation} but items match {best_cur} "
                                           f"({best_pct:.0%} vs {expected_pct:.0%})"))

    # ========== CLASS ROT: Rotation bug ==========
    # Check 1: _SHIP prop date vs actual ship tag date mismatch
    # Check 2: Recharge subscription SKU doesn't match box SKU
    custom_boxes = custom_boxes if custom_boxes else [s for s in box_skus if s.startswith(CUSTOM_BOX_PREFIXES)]
    if custom_boxes:
        box_sku = custom_boxes[0]
        # Extract _SHIP prop and _rc_bundle sub ID from box line item
        ship_prop = None
        rc_sub_id = None
        for li in line_items:
            if (li.get("sku") or "").strip() == box_sku:
                for p in (li.get("properties") or []):
                    if p.get("name") == "_SHIP":
                        ship_prop = (p.get("value") or "").strip()
                    if p.get("name") == "_rc_bundle":
                        rc_sub_id = (p.get("value") or "").strip()
                break
        # Also check blank box line for sub ID
        if not rc_sub_id:
            for li in line_items:
                if not (li.get("sku") or "").strip() and float(li.get("price", 0)) > 30:
                    for p in (li.get("properties") or []):
                        if p.get("name") == "_rc_bundle":
                            rc_sub_id = (p.get("value") or "").strip()
                    break

        # Extract ship tag date from order tags
        ship_tag_date = None
        for t in tags.split(","):
            t = t.strip()
            if t.startswith("_SHIP_"):
                ship_tag_date = t.replace("_SHIP_", "")
                break

        # Check 1: _SHIP prop doesn't match ship tag (stale charge from prior month)
        if ship_prop and ship_tag_date and ship_prop != ship_tag_date:
            errors.append(("ROT", f"_SHIP prop={ship_prop} but tag={ship_tag_date} "
                                  f"(charge from old month carried forward)"))

        # Check 2: Recharge subscription SKU rotated away from box SKU
        elif rc_sub_id:
            rc_sub = rc_get_subscription(rc_sub_id)
            if rc_sub and rc_sub.get("status") == "active":
                rc_sku = (rc_sub.get("sku") or "").strip()
                if rc_sku and rc_sku != box_sku:
                    rc_curation = get_curation_from_box(rc_sku)
                    box_curation_name = get_curation_from_box(box_sku)
                    errors.append(("ROT", f"Box={box_sku} but Recharge sub now={rc_sku} "
                                          f"(rotated {box_curation_name}->{rc_curation})"))

    return errors


def main():
    print("Fetching unfulfilled Shopify orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} unfulfilled orders\n")

    results = []
    class_counts = Counter()

    for order in orders:
        order_name = order.get("name", "")
        email = order.get("email", "")
        customer = order.get("customer") or {}
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        errs = analyze_order(order)
        for cls, detail in errs:
            class_counts[cls] += 1
            results.append({
                "class": cls,
                "order": order_name,
                "customer": cust_name,
                "email": email,
                "details": detail,
            })

    # Summary
    print(f"Found {len(results)} issues across {len(set(r['order'] for r in results))} orders")
    for cls, cnt in class_counts.most_common():
        print(f"  Class {cls}: {cnt}")

    # Write CSV
    outfile = os.path.join(os.path.dirname(__file__),
                           f"shopify-errors-{datetime.now().strftime('%Y-%m-%d')}.csv")
    fieldnames = ["class", "order", "customer", "email", "details"]
    with open(outfile, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)
    print(f"\nWrote to {outfile}")

    # Print all
    for r in results:
        print(f"  [{r['class']}] {r['order']} | {r['customer']} | {r['details']}")


if __name__ == "__main__":
    main()
