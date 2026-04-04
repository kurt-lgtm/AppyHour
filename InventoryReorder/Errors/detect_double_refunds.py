# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "urllib3"]
# ///

"""Detect duplicate refunds on orders from recent refund batches.

Scans all orders that were targeted by the LFOLIVE and MARC refund scripts,
checks each order's refund history, and flags orders with multiple refunds
for the same SKU/note.

Usage:
    python detect_double_refunds.py
"""

import json
import sys
import time
from collections import defaultdict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

session = requests.Session()
retries = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

# Also check Blue Lemo log
BLUE_LEMO_LOG = r"C:\Users\Work\Claude Projects\AppyHour\Blue Lemo_refund_log.txt"

# Keywords that identify each refund batch
REFUND_KEYWORDS = [
    "AC-MARC",
    "AC-LFOLIVE",
    "LFOLIVE",
    "Blue Lemo",
]

def fetch_ship_tag_orders(ship_tag: str) -> list[dict]:
    """Fetch all unfulfilled orders with a given ship tag."""
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "any",
        "limit": 250,
        "fields": "id,name,tags,line_items,refunds",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page} for {ship_tag}...")
        resp = session.get(
            url, headers=HEADERS, params=params if page == 1 else None, timeout=30
        )
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if ship_tag in tags:
                orders.append(o)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.5)
    return orders

def get_refunds(order_id: int) -> list[dict]:
    """Get all refunds for an order."""
    resp = session.get(
        f"{REST_BASE}/orders/{order_id}/refunds.json",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("refunds", [])

def load_blue_lemo_orders() -> list[str]:
    """Load order numbers from Blue Lemo refund log."""
    try:
        with open(BLUE_LEMO_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        orders = []
        for line in lines:
            line = line.strip()
            if line.startswith("#"):
                orders.append(line.lstrip("#"))
        return orders
    except FileNotFoundError:
        return []

def lookup_order(order_number: str) -> dict | None:
    """Look up a Shopify order by display number."""
    resp = session.get(
        f"{REST_BASE}/orders.json",
        headers=HEADERS,
        params={"name": order_number, "status": "any", "limit": 5},
        timeout=30,
    )
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        name = str(o.get("name", "")).lstrip("#")
        if name == order_number:
            return o
    return None

def analyze_refunds(order: dict, refunds: list[dict]) -> list[dict]:
    """Analyze refunds for duplicates. Returns list of duplicate refund groups."""
    order_name = order.get("name", "?")

    # Group refunds by note keyword
    by_keyword: dict[str, list[dict]] = defaultdict(list)
    for r in refunds:
        note = r.get("note") or ""
        total = sum(
            abs(float(t.get("amount", 0)))
            for t in r.get("transactions", [])
            if t.get("kind") == "refund"
        )
        refund_info = {
            "refund_id": r.get("id"),
            "note": note,
            "created_at": r.get("created_at"),
            "amount": total,
            "line_items": [
                {
                    "line_item_id": li.get("line_item_id"),
                    "quantity": li.get("quantity"),
                }
                for li in r.get("refund_line_items", [])
            ],
        }

        # Classify by keyword
        matched = False
        for kw in REFUND_KEYWORDS:
            if kw.lower() in note.lower():
                by_keyword[kw].append(refund_info)
                matched = True
                break
        if not matched and note:
            by_keyword[note[:30]].append(refund_info)

    # Find duplicates (same keyword, multiple refunds)
    duplicates = []
    for keyword, group in by_keyword.items():
        if len(group) >= 2:
            duplicates.append(
                {
                    "order_name": order_name,
                    "order_id": order.get("id"),
                    "keyword": keyword,
                    "refund_count": len(group),
                    "refunds": group,
                    "total_refunded": sum(r["amount"] for r in group),
                    "overage": sum(r["amount"] for r in group[1:]),  # everything after first
                }
            )

    return duplicates

def main() -> None:
    print(f"\n{'=' * 60}")
    print("  Duplicate Refund Detector")
    print(f"{'=' * 60}\n")

    all_duplicates: list[dict] = []
    checked_order_ids: set[int] = set()

    # --- Check LFOLIVE / MARC orders (by ship tag) ---
    for ship_tag in ["_SHIP_2026-03-23", "_SHIP_2026-03-30"]:
        print(f"\nFetching orders with tag {ship_tag}...")
        orders = fetch_ship_tag_orders(ship_tag)
        print(f"  Found {len(orders)} orders")

        for i, order in enumerate(orders):
            order_id = order["id"]
            if order_id in checked_order_ids:
                continue
            checked_order_ids.add(order_id)

            refunds = get_refunds(order_id)
            if len(refunds) < 2:
                continue  # can't have duplicates with 0-1 refunds

            dupes = analyze_refunds(order, refunds)
            if dupes:
                all_duplicates.extend(dupes)
                for d in dupes:
                    print(
                        f"  ** DUPLICATE: {d['order_name']} — "
                        f"{d['refund_count']}x '{d['keyword']}' refunds, "
                        f"total ${d['total_refunded']:.2f}, "
                        f"overage ${d['overage']:.2f}"
                    )

            time.sleep(0.3)
            if (i + 1) % 20 == 0:
                print(f"  --- Checked {i + 1}/{len(orders)} ---")

    # --- Check Blue Lemo orders ---
    print("\nChecking Blue Lemo orders from log...")
    blue_lemo_orders = load_blue_lemo_orders()
    print(f"  {len(blue_lemo_orders)} orders in log")

    for i, num in enumerate(blue_lemo_orders):
        order = lookup_order(num)
        if not order:
            continue
        order_id = order["id"]
        if order_id in checked_order_ids:
            continue
        checked_order_ids.add(order_id)

        refunds = get_refunds(order_id)
        if len(refunds) < 2:
            continue

        dupes = analyze_refunds(order, refunds)
        if dupes:
            all_duplicates.extend(dupes)
            for d in dupes:
                print(
                    f"  ** DUPLICATE: {d['order_name']} — "
                    f"{d['refund_count']}x '{d['keyword']}' refunds, "
                    f"total ${d['total_refunded']:.2f}, "
                    f"overage ${d['overage']:.2f}"
                )

        time.sleep(0.5)
        if (i + 1) % 10 == 0:
            print(f"  --- Checked {i + 1}/{len(blue_lemo_orders)} ---")

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"{'=' * 60}")
    print(f"  Orders checked: {len(checked_order_ids)}")
    print(f"  Orders with duplicate refunds: {len(all_duplicates)}")

    if all_duplicates:
        total_overage = sum(d["overage"] for d in all_duplicates)
        print(f"  Total overage (duplicate amount): ${total_overage:.2f}\n")

        print(f"  {'Order':<12} {'Keyword':<20} {'Count':>6} {'Total':>10} {'Overage':>10}")
        print(f"  {'-' * 60}")
        for d in sorted(all_duplicates, key=lambda x: x["overage"], reverse=True):
            print(
                f"  {d['order_name']:<12} {d['keyword']:<20} "
                f"{d['refund_count']:>6} "
                f"${d['total_refunded']:>9.2f} "
                f"${d['overage']:>9.2f}"
            )
        print(f"  {'-' * 60}")
        print(f"  {'TOTAL':<12} {'':<20} {len(all_duplicates):>6} ${sum(d['total_refunded'] for d in all_duplicates):>9.2f} ${total_overage:>9.2f}")

        # Write detailed JSON report
        report_path = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\double_refund_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "checked": len(checked_order_ids),
                    "duplicates": all_duplicates,
                    "total_overage": total_overage,
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\n  Detailed report: {report_path}")
    else:
        print("  No duplicate refunds found!")

    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
