"""Find unfulfilled orders with unresolved parent SKUs.

Detects cases where Matrixify silently rejected child SKU imports
(e.g., CEX-EC without resolved cheese, PR-CJAM without paired cheese/jam).

Usage:
    python find_unresolved_parents.py
"""

import csv
import json
import re
import requests
import time
from collections import Counter
from datetime import datetime

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
OUT_DIR = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors"

with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

PR_CJAM = settings.get("pr_cjam", {})
CEX_EC = settings.get("cex_ec", {})
CEXEC_SPLITS = settings.get("cexec_splits", {})

KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
}

MONTHLY_BOXES = {"AHB-MED", "AHB-LGE", "AHB-CMED"}
CUSTOM_BOX_PREFIXES = ("AHB-MCUST", "AHB-LCUST")
SPECIALTY_PREFIX = "AHB-X"


def resolve_curation_from_box_sku(sku):
    if not sku:
        return None
    sku = sku.strip().upper()
    if sku in MONTHLY_BOXES:
        return "MONTHLY"
    if "CUST" in sku:
        parts = sku.split("-")
        for seg in reversed(parts):
            if seg in KNOWN_CURATIONS:
                return seg
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


def check_order(order):
    """Check one order for unresolved parent SKUs. Returns list of issues."""
    tags = order.get("tags", "")
    tags_lower = tags.lower()

    # Skip exclusions
    if "gift redemption" in tags_lower:
        return []
    if "reship" in tags_lower:
        return []

    items = order.get("line_items", [])
    if not items:
        return []

    # Collect all SKUs on the order (fulfillable only)
    all_skus = set()
    sku_qtys = {}
    for li in items:
        sku = (li.get("sku") or "").strip().upper()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if sku and fq > 0:
            all_skus.add(sku)
            sku_qtys[sku] = sku_qtys.get(sku, 0) + fq

    # Find box SKU and curation
    box_sku = None
    for li in items:
        sku = (li.get("sku") or "").strip().upper()
        if sku.startswith("AHB-"):
            box_sku = sku
            break

    # Skip monthly and specialty boxes
    if box_sku:
        if box_sku in MONTHLY_BOXES:
            return []
        if box_sku.startswith(SPECIALTY_PREFIX):
            return []

    curation = resolve_curation_from_box_sku(box_sku)
    is_custom = box_sku and any(box_sku.startswith(p) for p in CUSTOM_BOX_PREFIXES) if box_sku else False

    issues = []

    # ── Check CEX-EC ────────────────────────────────────────────
    for sku in list(all_skus):
        if sku == "CEX-EC":
            # Bare CEX-EC — should have been resolved to CEX-EC-{suffix}
            has_suffixed = any(s.startswith("CEX-EC-") and s != "CEX-EC" for s in all_skus)
            if not has_suffixed:
                issues.append({
                    "parent_sku": "CEX-EC",
                    "expected_child": f"CEX-EC-{curation or '?'} → {CEX_EC.get(curation, '?')}",
                    "issue": "Bare CEX-EC with no resolved suffix",
                })

        elif sku.startswith("CEX-EC-"):
            suffix = sku.replace("CEX-EC-", "")
            if suffix in CEX_EC:
                expected_cheese = CEX_EC[suffix].upper()
                if expected_cheese not in all_skus:
                    issues.append({
                        "parent_sku": sku,
                        "expected_child": expected_cheese,
                        "issue": f"CEX-EC cheese missing: expected {expected_cheese}",
                    })
            elif suffix in CEXEC_SPLITS:
                for split_sku in CEXEC_SPLITS[suffix]:
                    if split_sku.upper() not in all_skus:
                        issues.append({
                            "parent_sku": sku,
                            "expected_child": split_sku.upper(),
                            "issue": f"CEX-EC split cheese missing: expected {split_sku}",
                        })

    # ── Check PR-CJAM ───────────────────────────────────────────
    for sku in list(all_skus):
        if not sku.startswith("PR-CJAM"):
            continue

        # Determine which curation this PR-CJAM maps to
        suffix = sku.replace("PR-CJAM-", "").replace("PR-CJAM", "")
        if suffix == "GEN" or not suffix:
            pr_cur = curation or "GEN"
        elif suffix in KNOWN_CURATIONS:
            pr_cur = suffix
        else:
            pr_cur = curation or suffix

        mapping = PR_CJAM.get(pr_cur, {})
        expected_cheese = (mapping.get("cheese") or "").upper()
        expected_jam = (mapping.get("jam") or "").upper()

        # Check cheese
        if expected_cheese and expected_cheese not in all_skus:
            issues.append({
                "parent_sku": sku,
                "expected_child": expected_cheese,
                "issue": f"PR-CJAM cheese missing: expected {expected_cheese} (curation {pr_cur})",
            })

        # Check jam (direct line item, not a BL- bundle)
        if expected_jam and expected_jam not in all_skus:
            issues.append({
                "parent_sku": sku,
                "expected_child": expected_jam,
                "issue": f"PR-CJAM jam missing: expected {expected_jam} (curation {pr_cur})",
            })

    # ── Check CEX-EM (extra meat) ───────────────────────────────
    if "CEX-EM" in all_skus:
        # CEX-EM should resolve to a specific meat — check if any MT- items
        # exist beyond the curation recipe
        has_resolved_meat = False
        mt_count = sum(1 for s in all_skus if s.startswith("MT-"))
        # If there's a CEX-EM and the order has meat items, it's likely resolved
        # But if CEX-EM is the ONLY meat-related item, it wasn't resolved
        if mt_count == 0:
            issues.append({
                "parent_sku": "CEX-EM",
                "expected_child": "MT-? (resolved meat)",
                "issue": "CEX-EM present but no MT- items on order",
            })

    return issues


def main():
    print("Fetching unfulfilled Shopify orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} unfulfilled orders\n")

    results = []
    issue_counts = Counter()

    for order in orders:
        order_name = order.get("name", "")
        order_id = order.get("id", "")
        email = order.get("email", "")
        customer = order.get("customer", {})
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        issues = check_order(order)
        for issue in issues:
            issue_type = issue["issue"].split(":")[0]
            issue_counts[issue_type] += 1
            results.append({
                "order_number": order_name,
                "order_id": str(order_id),
                "customer": cust_name,
                "email": email,
                "parent_sku": issue["parent_sku"],
                "expected_child": issue["expected_child"],
                "issue": issue["issue"],
            })

    # Summary
    print(f"Found {len(results)} unresolved parent issues across {len(set(r['order_number'] for r in results))} orders\n")
    print("By issue type:")
    for issue_type, count in issue_counts.most_common():
        print(f"  {issue_type}: {count}")

    # Write CSV
    if results:
        date_tag = datetime.now().strftime("%Y-%m-%d")
        out_path = f"{OUT_DIR}/unresolved-parents-{date_tag}.csv"
        fieldnames = ["order_number", "order_id", "customer", "email",
                      "parent_sku", "expected_child", "issue"]
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote {len(results)} rows to {out_path}")

    # Print details
    print("\n=== ALL ISSUES ===")
    for r in results:
        print(f"  {r['order_number']} | {r['customer']:<25} | {r['parent_sku']:<18} | {r['issue']}")


if __name__ == "__main__":
    main()
