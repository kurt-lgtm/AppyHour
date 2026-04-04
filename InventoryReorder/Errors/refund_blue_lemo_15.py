# /// script
# requires-python = ">=3.10"
# dependencies = ["openpyxl", "requests", "urllib3"]
# ///

"""Refund $15 to Blue Lemo orders.

Reads order numbers from 'Blue Lemo.csv.xlsx', looks up each in Shopify,
and issues a $15 partial refund (not tied to a line item).

Skips: #118062, #118105, #118102, #118075

Usage:
    python refund_blue_lemo_15.py              # dry-run
    python refund_blue_lemo_15.py --commit     # apply
"""

import json
import sys
import time

import openpyxl
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

COMMIT = "--commit" in sys.argv
REFUND_AMOUNT = 15.00
REFUND_NOTE = "Blue Lemo — $15 refund issued"

SKIP_ORDERS = {"118062", "118105", "118102", "118075"}

XLSX_PATH = r"C:\Users\Work\Claude Projects\AppyHour\Blue Lemo.csv.xlsx"

# Retry-capable session
session = requests.Session()
retries = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

def load_order_numbers():
    """Load order numbers from xlsx, skip header, last 2 rows, and exclusion list."""
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    # Skip header (row 0) and last 2 rows
    data_rows = rows[1:-2]
    orders = []
    for row in data_rows:
        raw = str(row[0] or "").strip().lstrip("#")
        if not raw or not raw.isdigit():
            continue
        if raw in SKIP_ORDERS:
            print(f"  SKIP #{raw} (exclusion list)")
            continue
        orders.append(raw)
    return orders

def lookup_order(order_number):
    """Look up a Shopify order by its display number."""
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

def get_transaction_id(order_id):
    """Get the original payment transaction ID for issuing a refund against."""
    resp = session.get(
        f"{REST_BASE}/orders/{order_id}/transactions.json",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    for txn in resp.json().get("transactions", []):
        if txn.get("kind") in ("sale", "capture") and txn.get("status") == "success":
            return txn["id"], txn.get("gateway", "")
    return None, None

def has_existing_refund(order_id, note_keyword="Blue Lemo"):
    """Check if this order already has a refund with the given note keyword."""
    resp = session.get(
        f"{REST_BASE}/orders/{order_id}/refunds.json",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    for r in resp.json().get("refunds", []):
        if note_keyword in (r.get("note") or ""):
            return True
    return False

def issue_refund(order_id, order_name, txn_id, gateway):
    """Issue a $15 partial refund (not line-item based)."""
    payload = {
        "refund": {
            "notify": False,
            "note": REFUND_NOTE,
            "shipping": {"amount": 0},
            "transactions": [
                {
                    "parent_id": txn_id,
                    "amount": str(REFUND_AMOUNT),
                    "kind": "refund",
                    "gateway": gateway,
                }
            ],
        }
    }

    resp = session.post(
        f"{REST_BASE}/orders/{order_id}/refunds.json",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        print(f"    FAILED {order_name}: HTTP {resp.status_code} — {resp.text[:300]}")
        return False

    result = resp.json().get("refund", {})
    if result.get("id"):
        print(f"    OK {order_name}: refunded ${REFUND_AMOUNT}")
        return True

    print(f"    FAILED {order_name}: unexpected response — {resp.text[:300]}")
    return False

def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'=' * 60}")
    print(f"  Blue Lemo $15 Refund [{mode}]")
    print(f"{'=' * 60}\n")

    print("Loading order numbers from xlsx...")
    order_numbers = load_order_numbers()
    print(f"  {len(order_numbers)} orders to process\n")

    print("Looking up orders in Shopify...")
    found = []
    not_found = []
    for i, num in enumerate(order_numbers):
        order = lookup_order(num)
        if order:
            found.append((num, order))
            print(f"  [{i + 1}/{len(order_numbers)}] #{num} — found (ID: {order['id']})")
        else:
            not_found.append(num)
            print(f"  [{i + 1}/{len(order_numbers)}] #{num} — NOT FOUND")
        time.sleep(0.5)

    print(f"\n  Found: {len(found)}, Not found: {len(not_found)}")
    if not_found:
        print(f"  Missing: {', '.join('#' + n for n in not_found)}")

    total_refund = len(found) * REFUND_AMOUNT
    print(f"  Total refund amount: ${total_refund:.2f}\n")

    if not COMMIT:
        print(f"DRY-RUN complete. Run with --commit to apply refunds.")
        return

    print("Issuing refunds...")
    success_count = 0
    fail_count = 0
    completed = []
    failed = []

    skipped_existing = 0
    for i, (num, order) in enumerate(found):
        order_id = order["id"]
        order_name = f"#{num}"

        if has_existing_refund(order_id):
            print(f"    SKIP {order_name}: already has Blue Lemo refund")
            skipped_existing += 1
            time.sleep(0.3)
            continue

        txn_id, gateway = get_transaction_id(order_id)
        if not txn_id:
            print(f"    FAILED {order_name}: no payment transaction found")
            fail_count += 1
            failed.append(num)
            continue

        time.sleep(0.3)

        if issue_refund(order_id, order_name, txn_id, gateway):
            success_count += 1
            completed.append(num)
        else:
            fail_count += 1
            failed.append(num)

        time.sleep(1.0)

        # Progress update every 10 orders
        if (i + 1) % 10 == 0:
            print(f"  --- Progress: {i + 1}/{len(found)} ({success_count} ok, {fail_count} failed) ---")

    print(f"\n{'=' * 60}")
    print(f"  Done: {success_count} refunded, {fail_count} failed, {skipped_existing} already had refund")
    print(f"  Total refunded: ${success_count * REFUND_AMOUNT:.2f}")
    if failed:
        print(f"  Failed orders: {', '.join('#' + n for n in failed)}")
    print(f"{'=' * 60}")

    # Write results log
    log_path = XLSX_PATH.replace(".csv.xlsx", "_refund_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Blue Lemo $15 Refund Log\n")
        f.write(f"{'=' * 40}\n")
        f.write(f"Completed ({success_count}):\n")
        for n in completed:
            f.write(f"  #{n}\n")
        if failed:
            f.write(f"\nFailed ({fail_count}):\n")
            for n in failed:
                f.write(f"  #{n}\n")
    print(f"\nLog written to: {log_path}")

if __name__ == "__main__":
    main()
