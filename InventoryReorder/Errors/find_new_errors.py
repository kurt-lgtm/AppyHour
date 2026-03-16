"""
Scan unfulfilled Shopify orders for error patterns BEYOND classes 1/2/3.

New error classes to detect:
  Class 4: Double curation write — entire box contents duplicated (Anik's tool wrote twice)
  Class 5: Monthly box with individual food items — AHB-MED/LGE/CMED should only have box SKU + PR-CJAM-GEN
  Class 6: Curation mismatch — food items from wrong curation track vs box SKU suffix
  Class 7: Missing PR-CJAM on curated box — AHB-MCUST/LCUST with "for Life" but no PR-CJAM line
  Class 8: Stale/orphaned CEX-EC cheese — old resolved cheese from previous month still on order
  Class 9: Multiple box SKUs on single order — should only have one AHB- line
  Class 10: Ghost items — line items with qty=0 or $0 price that shouldn't be free
"""
import requests
import json
import re
import csv
import time
from collections import Counter, defaultdict
from datetime import datetime

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
OUT_DIR = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors"

with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Curation recipes from settings (used for mismatch detection)
CURATION_RECIPES = settings.get("curation_recipes", {})
PR_CJAM = settings.get("pr_cjam", {})
CEX_EC = settings.get("cex_ec", {})

# Known curations
CURATIONS = ["MONG", "MDT", "OWC", "SPN", "ALPN", "ISUN", "HHIGH"]

# Food SKU prefixes
FOOD_PREFIXES = ("CH-", "MT-", "AC-")
# Box SKU prefix
BOX_PREFIX = "AHB-"
# Monthly box SKUs (not custom curated)
MONTHLY_BOXES = {"AHB-MED", "AHB-CMED", "AHB-LGE"}
# Custom curated box prefixes
CUSTOM_BOX_PREFIXES = ("AHB-MCUST", "AHB-LCUST")
# Specialty boxes to skip
SPECIALTY_PREFIX = "AHB-X"


def fetch_all_unfulfilled():
    """Fetch all unfulfilled orders from Shopify."""
    orders = []
    url = f"{BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,created_at,customer,email,tags,line_items,total_price,note",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("orders", [])
        orders.extend(batch)
        # Pagination via Link header
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None  # URL already has params
        time.sleep(0.5)
    return orders


def extract_skus(line_items):
    """Extract (sku, qty, title, price) tuples from line items."""
    result = []
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        qty = li.get("quantity", 0)
        title = li.get("title", "")
        price = float(li.get("price", "0") or "0")
        result.append((sku, qty, title, price))
    return result


def get_curation_from_box(sku):
    """Extract curation suffix from box SKU like AHB-MCUST-MDT -> MDT."""
    m = re.match(r"AHB-[ML]CUST-([A-Z]+)", sku)
    return m.group(1) if m else None


def get_curation_from_pr(sku):
    """Extract curation from PR-CJAM-MDT -> MDT."""
    m = re.match(r"PR-CJAM-([A-Z]+)", sku)
    return m.group(1) if m else None


def is_reship(tags):
    return "reship" in (tags or "").lower()


def is_first_order(tags):
    return "Subscription First Order" in (tags or "")


def is_specialty_box(skus):
    return any(s.startswith(SPECIALTY_PREFIX) for s, _, _, _ in skus)


def analyze_order(order):
    """Analyze a single order for new error classes. Returns list of (class, detail) tuples."""
    tags = order.get("tags", "")
    if is_reship(tags):
        return []

    items = extract_skus(order.get("line_items", []))
    if not items:
        return []

    skus_only = [s for s, q, t, p in items if s]

    # Skip specialty boxes
    if is_specialty_box(items):
        return []

    errors = []

    # Separate by type
    box_skus = [(s, q, t, p) for s, q, t, p in items if s.startswith(BOX_PREFIX)]
    food_items = [(s, q, t, p) for s, q, t, p in items if s.startswith(FOOD_PREFIXES)]
    pr_items = [(s, q, t, p) for s, q, t, p in items if s.startswith("PR-CJAM")]
    cex_items = [(s, q, t, p) for s, q, t, p in items if s.startswith("CEX-EC")]
    all_items_with_sku = [(s, q, t, p) for s, q, t, p in items if s]

    # =========================================
    # CLASS 4: Double curation write
    # =========================================
    # Detect when the entire set of food items appears to be duplicated
    # (same SKU set appears twice — Anik's tool wrote the bundle twice)
    sku_list = [s for s, q, t, p in items if s.startswith(FOOD_PREFIXES + ("CEX-",))]
    if len(sku_list) >= 6:
        half = len(sku_list) // 2
        first_half = sorted(sku_list[:half])
        second_half = sorted(sku_list[half:2*half])
        if first_half == second_half and len(first_half) >= 3:
            errors.append(("Class 4: Double curation write",
                          f"Food items duplicated: {', '.join(first_half)}"))

    # Also check: multiple identical box SKU lines
    box_sku_counts = Counter(s for s, q, t, p in box_skus)
    for bs, cnt in box_sku_counts.items():
        if cnt > 1:
            errors.append(("Class 4: Double curation write",
                          f"Box SKU {bs} appears {cnt} times"))

    # Also check via line item quantities — same SKU line with qty > 1 for food
    for s, q, t, p in items:
        if q > 1 and s.startswith(FOOD_PREFIXES) and not s.startswith("EX-"):
            errors.append(("Class 4: Double curation write (qty>1)",
                          f"{s} has quantity {q} on single line item"))

    # =========================================
    # CLASS 5: Monthly box with food items from curation tool
    # =========================================
    # AHB-MED/LGE/CMED orders should only have box SKU + PR-CJAM-GEN
    monthly_box = [s for s, q, t, p in box_skus
                   if s in MONTHLY_BOXES or s.startswith(("AHB-MED", "AHB-LGE", "AHB-CMED"))]
    custom_box = [s for s, q, t, p in box_skus
                  if s.startswith(CUSTOM_BOX_PREFIXES)]

    if monthly_box and not custom_box:
        # This is a monthly-only order — should NOT have individual food SKUs
        # (food items come from the monthly assignment, not curation tool)
        food_count = len(food_items)
        if food_count > 0:
            food_skus_str = ", ".join(s for s, q, t, p in food_items[:5])
            errors.append(("Class 5: Monthly box has curation food items",
                          f"Box: {monthly_box[0]}, {food_count} food items: {food_skus_str}"))

    # =========================================
    # CLASS 6: Curation mismatch
    # =========================================
    # Box says one curation but food items belong to a different one
    if custom_box:
        box_curation = get_curation_from_box(custom_box[0])
        if box_curation and box_curation in CURATION_RECIPES:
            expected_skus = set(s for s, q in CURATION_RECIPES[box_curation])
            actual_food = set(s for s, q, t, p in food_items)
            if actual_food:
                # Check if food items match a DIFFERENT curation better
                best_match_cur = None
                best_match_pct = 0
                for cur, recipe in CURATION_RECIPES.items():
                    recipe_skus = set(s for s, q in recipe)
                    if recipe_skus:
                        overlap = len(actual_food & recipe_skus)
                        pct = overlap / len(actual_food) if actual_food else 0
                        if pct > best_match_pct:
                            best_match_pct = pct
                            best_match_cur = cur

                expected_overlap = len(actual_food & expected_skus) / len(actual_food) if actual_food else 1
                if best_match_cur and best_match_cur != box_curation and best_match_pct > expected_overlap + 0.2:
                    errors.append(("Class 6: Curation mismatch",
                                  f"Box says {box_curation} but items match {best_match_cur} "
                                  f"({best_match_pct:.0%} vs {expected_overlap:.0%})"))

    # =========================================
    # CLASS 7: Missing PR-CJAM on curated box
    # =========================================
    if custom_box and food_items:
        has_pr = len(pr_items) > 0
        # Check product title for "for Life" (should have PR-CJAM)
        box_titles = [t for s, q, t, p in box_skus if s.startswith(CUSTOM_BOX_PREFIXES)]
        is_for_life = any("for life" in t.lower() or "pairings" in t.lower() for t in box_titles)
        # Also check if any line item title mentions "Free Brie" — those get CH-EBRIE instead
        is_free_brie = any("free brie" in t.lower() for s, q, t, p in items)

        if is_for_life and not has_pr and not is_free_brie:
            errors.append(("Class 7: Missing PR-CJAM",
                          f"Curated box ({custom_box[0]}) with 'for Life' product but no PR-CJAM line"))

    # =========================================
    # CLASS 8: Stale CEX-EC cheese
    # =========================================
    # CEX-EC-{suffix} should resolve to the CURRENT month's cheese
    # If there are multiple different CEX-EC-* lines, old ones are stale
    cex_suffixed = [s for s, q, t, p in items if re.match(r"CEX-EC-[A-Z]+", s)]
    if len(cex_suffixed) > 1:
        errors.append(("Class 8: Multiple CEX-EC variants (stale cheese?)",
                      f"Found: {', '.join(cex_suffixed)}"))

    # CEX-EC bare (no suffix) + a suffixed one = Matrixify partial resolution
    bare_cex = [s for s, q, t, p in items if s == "CEX-EC"]
    if bare_cex and cex_suffixed:
        errors.append(("Class 8: Bare CEX-EC alongside resolved CEX-EC",
                      f"Bare CEX-EC + {', '.join(cex_suffixed)}"))

    # =========================================
    # CLASS 9: Multiple box SKUs
    # =========================================
    distinct_box_bases = set()
    for s, q, t, p in box_skus:
        if s.startswith(SPECIALTY_PREFIX):
            continue
        # Normalize: AHB-MCUST-MDT -> AHB-MCUST, AHB-MED -> AHB-MED
        base = re.match(r"(AHB-[A-Z]+)", s)
        if base:
            distinct_box_bases.add(base.group(1))

    if len(distinct_box_bases) > 1:
        errors.append(("Class 9: Multiple box types",
                      f"Box SKUs: {', '.join(s for s, q, t, p in box_skus)}"))

    # =========================================
    # CLASS 10: Ghost items (qty=0 or unexpected $0 food)
    # =========================================
    for s, q, t, p in items:
        if q == 0 and s:
            errors.append(("Class 10: Ghost item (qty=0)",
                          f"{s} ({t}) has quantity 0"))
        if p == 0 and s.startswith(FOOD_PREFIXES) and not s.startswith("PR-") and q > 0:
            # Food items at $0 might be legitimate (included in box) — skip for now
            pass

    # =========================================
    # CLASS 11: Wrong item count for box type
    # =========================================
    # Not overfill (caught by existing rules), but UNDERFILL
    if custom_box and food_items:
        food_count = sum(q for s, q, t, p in food_items)
        is_medium = any(s.startswith("AHB-MCUST") for s in [s for s, q, t, p in box_skus])
        is_large = any(s.startswith("AHB-LCUST") for s in [s for s, q, t, p in box_skus])
        expected_min = 5 if is_medium else (7 if is_large else 0)
        if food_count < expected_min and food_count > 0:
            box_type = "MCUST" if is_medium else "LCUST"
            errors.append(("Class 11: Underfilled box",
                          f"{box_type} box has only {food_count} food items (expected {expected_min}+)"))

    # =========================================
    # CLASS 12: Order has food but no tasting guide
    # =========================================
    has_pk = any(s.startswith("PK-") for s, q, t, p in items)
    if custom_box and food_items and not has_pk and not is_first_order(tags):
        errors.append(("Class 12: Missing tasting guide",
                      f"Curated box with food items but no PK- SKU"))

    return errors


def main():
    print("Fetching unfulfilled Shopify orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} unfulfilled orders")

    # Analyze each order
    results = []
    class_counts = Counter()

    for order in orders:
        order_name = order.get("name", "")
        tags = order.get("tags", "")
        email = order.get("email", "")
        customer = order.get("customer", {})
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        total = order.get("total_price", "0")
        created = order.get("created_at", "")[:10]

        items = extract_skus(order.get("line_items", []))
        items_str = "; ".join(f"{q}x {s} ({t})" for s, q, t, p in items if s)

        errors = analyze_order(order)
        if errors:
            for cls, detail in errors:
                class_counts[cls] += 1
                results.append({
                    "Order": order_name,
                    "Date": created,
                    "Customer": cust_name,
                    "Email": email,
                    "Total": total,
                    "Error Class": cls,
                    "Detail": detail,
                    "Tags": tags,
                    "All Items": items_str,
                })

    # Write CSV
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = f"{OUT_DIR}\\new-error-classes-{today}.csv"
    if results:
        fieldnames = ["Order", "Date", "Customer", "Email", "Total",
                      "Error Class", "Detail", "Tags", "All Items"]
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    # Print summary
    print(f"\n{'='*60}")
    print(f"NEW ERROR CLASS SCAN — {today}")
    print(f"{'='*60}")
    print(f"Total orders scanned: {len(orders)}")
    print(f"Orders with new errors: {len(set(r['Order'] for r in results))}")
    print(f"Total error flags: {len(results)}")
    print()

    for cls, cnt in sorted(class_counts.items(), key=lambda x: -x[1]):
        print(f"  {cls}: {cnt}")

    print(f"\nSaved to: {out_path}")

    # Print sample orders per class
    printed_classes = set()
    for r in results:
        cls = r["Error Class"]
        if cls not in printed_classes:
            printed_classes.add(cls)
            print(f"\n--- Example: {cls} ---")
            print(f"  Order: {r['Order']} | Customer: {r['Customer']}")
            print(f"  Detail: {r['Detail']}")
            items_preview = r['All Items'][:200]
            print(f"  Items: {items_preview}...")


if __name__ == "__main__":
    main()
