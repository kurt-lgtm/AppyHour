# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Detect duplicate refunds — writes results to detect_output.txt.

Instead of paginating all orders, checks only orders from known refund logs
and a targeted search for LFOLIVE/MARC refunds.
"""
import json
import time
from collections import defaultdict

import requests

OUTPUT_FILE = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\detect_output.txt"
log_lines = []

def log(msg: str) -> None:
    print(msg, flush=True)
    log_lines.append(msg)

def save_log() -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Plain session — no auto-retry (Shopify sends Retry-After: 4.0 which urllib3 chokes on)
session = requests.Session()

BLUE_LEMO_LOG = r"C:\Users\Work\Claude Projects\AppyHour\Blue Lemo_refund_log.txt"
REFUND_KEYWORDS = ["AC-MARC", "AC-LFOLIVE", "LFOLIVE", "Blue Lemo"]

def api_get(url: str, **kwargs) -> requests.Response:
    """GET with manual rate-limit handling."""
    for attempt in range(5):
        resp = session.get(url, headers=HEADERS, timeout=30, **kwargs)
        if resp.status_code == 429:
            wait = 5
            try:
                wait = max(float(resp.headers.get("Retry-After", "5")), 2)
            except (ValueError, TypeError):
                pass
            log(f"    Rate limited, waiting {wait:.0f}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise Exception("Rate limited 5 times in a row")

def api_post(url: str, **kwargs) -> requests.Response:
    """POST with manual rate-limit handling."""
    for attempt in range(5):
        resp = session.post(url, headers=HEADERS, timeout=30, **kwargs)
        if resp.status_code == 429:
            wait = 5
            try:
                wait = max(float(resp.headers.get("Retry-After", "5")), 2)
            except (ValueError, TypeError):
                pass
            log(f"    Rate limited, waiting {wait:.0f}s...")
            time.sleep(wait)
            continue
        return resp
    raise Exception("Rate limited 5 times in a row")

def get_refunds(order_id: int) -> list:
    resp = api_get(f"{REST_BASE}/orders/{order_id}/refunds.json")
    return resp.json().get("refunds", [])

def lookup_order(order_number: str) -> dict | None:
    resp = api_get(
        f"{REST_BASE}/orders.json",
        params={"name": order_number, "status": "any", "limit": 5},
    )
    for o in resp.json().get("orders", []):
        if str(o.get("name", "")).lstrip("#") == order_number:
            return o
    return None

def search_orders_with_refunds(created_at_min: str, created_at_max: str) -> list:
    """Fetch orders in a date range that have refunds, using financial_status filter."""
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "any",
        "financial_status": "partially_refunded,refunded",
        "created_at_min": created_at_min,
        "created_at_max": created_at_max,
        "limit": 250,
        "fields": "id,name,tags",
    }
    page = 0
    while url:
        page += 1
        log(f"  Fetching page {page}...")
        resp = api_get(url, params=params if page == 1 else None)
        batch = resp.json().get("orders", [])
        orders.extend(batch)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.5)
    return orders

def analyze_refunds(order: dict, refunds: list) -> list:
    order_name = order.get("name", "?")
    by_keyword = defaultdict(list)
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
        }
        matched = False
        for kw in REFUND_KEYWORDS:
            if kw.lower() in note.lower():
                by_keyword[kw].append(refund_info)
                matched = True
                break
        if not matched and note:
            by_keyword[note[:30]].append(refund_info)

    duplicates = []
    for keyword, group in by_keyword.items():
        if len(group) >= 2:
            duplicates.append({
                "order_name": order_name,
                "order_id": order.get("id"),
                "keyword": keyword,
                "refund_count": len(group),
                "refunds": group,
                "total_refunded": sum(r["amount"] for r in group),
                "overage": sum(r["amount"] for r in group[1:]),
            })
    return duplicates

def main() -> None:
    try:
        log("=" * 60)
        log("  Duplicate Refund Detector")
        log("=" * 60)

        all_duplicates = []
        checked = set()

        # Strategy: fetch all partially_refunded/refunded orders from March 2026
        # This is much faster than paginating all orders by tag
        log("\nFetching refunded orders from March 2026...")
        orders = search_orders_with_refunds("2026-03-01T00:00:00", "2026-04-03T00:00:00")
        log(f"  Found {len(orders)} refunded orders")

        for i, order in enumerate(orders):
            oid = order["id"]
            if oid in checked:
                continue
            checked.add(oid)

            try:
                refunds = get_refunds(oid)
            except Exception as e:
                log(f"  ERROR fetching refunds for {order.get('name')}: {e}")
                continue

            if len(refunds) < 2:
                continue

            dupes = analyze_refunds(order, refunds)
            for d in dupes:
                all_duplicates.append(d)
                log(
                    f"  ** DUPLICATE: {d['order_name']} — "
                    f"{d['refund_count']}x '{d['keyword']}', "
                    f"overage ${d['overage']:.2f}"
                )

            time.sleep(0.3)
            if (i + 1) % 25 == 0:
                log(f"  --- Checked {i + 1}/{len(orders)} ---")

        # Also check Blue Lemo orders explicitly
        log("\nChecking Blue Lemo orders from log...")
        try:
            with open(BLUE_LEMO_LOG) as f:
                bl_orders = [l.strip().lstrip("#") for l in f if l.strip().startswith("#")]
        except FileNotFoundError:
            bl_orders = []
        log(f"  {len(bl_orders)} orders in log")

        for i, num in enumerate(bl_orders):
            try:
                order = lookup_order(num)
            except Exception as e:
                log(f"  ERROR looking up #{num}: {e}")
                continue
            if not order:
                continue
            oid = order["id"]
            if oid in checked:
                continue
            checked.add(oid)
            try:
                refunds = get_refunds(oid)
            except Exception as e:
                log(f"  ERROR fetching refunds for #{num}: {e}")
                continue
            if len(refunds) < 2:
                continue
            dupes = analyze_refunds(order, refunds)
            for d in dupes:
                all_duplicates.append(d)
                log(
                    f"  ** DUPLICATE: {d['order_name']} — "
                    f"{d['refund_count']}x '{d['keyword']}', "
                    f"overage ${d['overage']:.2f}"
                )
            time.sleep(0.5)
            if (i + 1) % 10 == 0:
                log(f"  --- Checked {i + 1}/{len(bl_orders)} ---")

        # Summary
        log(f"\n{'=' * 60}")
        log("  RESULTS")
        log(f"{'=' * 60}")
        log(f"  Orders checked: {len(checked)}")
        log(f"  Orders with duplicate refunds: {len(all_duplicates)}")

        if all_duplicates:
            total_overage = sum(d["overage"] for d in all_duplicates)
            log(f"  Total overage: ${total_overage:.2f}")
            log("")
            log(f"  {'Order':<12} {'Keyword':<20} {'Count':>6} {'Total':>10} {'Overage':>10}")
            log(f"  {'-' * 60}")
            for d in sorted(all_duplicates, key=lambda x: x["overage"], reverse=True):
                log(
                    f"  {d['order_name']:<12} {d['keyword']:<20} "
                    f"{d['refund_count']:>6} "
                    f"${d['total_refunded']:>9.2f} "
                    f"${d['overage']:>9.2f}"
                )
            log(f"  {'-' * 60}")
            log(f"  TOTAL overage: ${total_overage:.2f}")

            report_path = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\double_refund_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"checked": len(checked), "duplicates": all_duplicates, "total_overage": total_overage},
                    f, indent=2, default=str,
                )
            log(f"\n  JSON report: {report_path}")
        else:
            log("  No duplicate refunds found!")

        log("=" * 60)

    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        save_log()

if __name__ == "__main__":
    main()
