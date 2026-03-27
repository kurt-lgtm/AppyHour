"""Enrich RC upcoming class 2/3/4B CSV with subscription IDs from Recharge API."""
import csv
import json
import os
import sys
import time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
INPUT = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\rc-upcoming-class234b-2026-03-12.csv"
OUTPUT = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\rc-upcoming-class234b-2026-03-12.csv"

with open(SETTINGS) as f:
    settings = json.load(f)

RC_TOKEN = settings.get("recharge_api_token", "")
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Accept": "application/json",
    "X-Recharge-Version": "2021-11",
}

import requests


def fetch_charge(charge_id):
    """Fetch a single charge from Recharge."""
    for attempt in range(3):
        try:
            resp = requests.get(
                f"https://api.rechargeapps.com/charges/{charge_id}",
                headers=RC_HEADERS, timeout=30,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                time.sleep(retry_after)
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("charge")
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"  FAILED charge {charge_id}: {e}")
                return None
    return None


def main():
    with open(INPUT, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Enriching {len(rows)} rows...")
    enriched = 0
    failed = 0

    for i, row in enumerate(rows):
        charge_id = row.get("charge_id", "").strip()
        if not charge_id:
            continue

        # Skip if already has data
        if row.get("rc_subscription_id", "").strip():
            continue

        charge = fetch_charge(charge_id)
        if not charge:
            failed += 1
            continue

        # Extract subscription ID from line items
        sub_ids = set()
        for item in charge.get("line_items", []):
            sub_id = item.get("subscription_id")
            if sub_id:
                sub_ids.add(str(sub_id))

        row["rc_customer_id"] = str(charge.get("customer_id", ""))
        row["rc_subscription_id"] = ",".join(sorted(sub_ids))
        row["email"] = charge.get("email", "")

        enriched += 1

        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(rows)} ({enriched} enriched, {failed} failed)")

        time.sleep(0.5)  # rate limit

    print(f"\nDone. Enriched: {enriched}, Failed: {failed}")

    # Write output
    fieldnames = list(rows[0].keys())
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written to: {OUTPUT}")


if __name__ == "__main__":
    main()
