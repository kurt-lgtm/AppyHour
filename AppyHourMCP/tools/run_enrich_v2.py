"""Enrichment v2 with progress, customer-email Gorgias search."""
import sys
import os
import time
import json
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "GelPackCalculator"))

from gorgias_sheets_sync import (
    _load_settings, _gorgias_auth, _shopify_order_by_name,
    _search_gorgias_by_order, _extract_carrier_from_shopify,
    _extract_state_from_shopify, _extract_fc_from_shopify_tags,
    _extract_gorgias_link, _extract_state_from_tags, _extract_fc_tag,
    _matches_valid_prefix, VALID_ISSUE_PREFIXES, EXCLUDED_RESOLUTIONS,
    FIELD_ISSUE_TYPE, FIELD_RESOLUTION, SPREADSHEET_ID, TAB_NAME,
)
from google_integration import GoogleIntegration


def main():
    settings = _load_settings()
    auth, base_url = _gorgias_auth()
    subdomain = settings.get("gorgias_subdomain", "appyhour")
    creds = settings.get("google_credentials_path", "")
    if not creds or not os.path.exists(creds):
        creds = str(Path(__file__).resolve().parent.parent.parent
                     / "shipping-perfomance-review-accd39ac4b78.json")
    gclient = GoogleIntegration(creds)

    all_rows = gclient.read_sheet(SPREADSHEET_ID, f"'{TAB_NAME}'!A:J")
    print(f"Read {len(all_rows)} rows", flush=True)

    enriched = []
    updates = []
    errors = 0
    processed = 0

    for row_idx, row in enumerate(all_rows):
        if row_idx == 0:
            continue
        while len(row) < 10:
            row.append("")

        order_num = row[2].strip()
        if not order_num:
            continue

        missing = []
        if not row[3].strip(): missing.append("gorgias_link")
        if not row[4].strip(): missing.append("carrier")
        if not row[5].strip(): missing.append("state")
        if not row[6].strip(): missing.append("fc_tag")
        if not row[7].strip(): missing.append("issue_type")
        if not row[8].strip(): missing.append("resolution")
        if not missing:
            continue

        processed += 1
        try:
            shopify_order = _shopify_order_by_name(order_num)
            time.sleep(0.3)

            customer_email = ""
            if shopify_order:
                customer_email = (
                    shopify_order.get("email", "")
                    or shopify_order.get("customer", {}).get("email", "")
                )

            ticket = None
            if any(f in missing for f in ("gorgias_link", "issue_type", "resolution", "state", "fc_tag")):
                ticket = _search_gorgias_by_order(
                    order_num, auth, base_url, customer_email=customer_email,
                )
                time.sleep(0.3)

            new_vals = list(row[:10])
            filled = []

            if not new_vals[3].strip() and ticket:
                new_vals[3] = f"https://{subdomain}.gorgias.com/app/views/238613/{ticket['id']}"
                filled.append("gorgias_link")
            if not new_vals[4].strip() and shopify_order:
                c = _extract_carrier_from_shopify(shopify_order)
                if c:
                    new_vals[4] = c
                    filled.append("carrier")
            if not new_vals[5].strip():
                if shopify_order:
                    s = _extract_state_from_shopify(shopify_order)
                    if s:
                        new_vals[5] = s
                        filled.append("state")
                if not new_vals[5].strip() and ticket:
                    s = _extract_state_from_tags(ticket)
                    if s:
                        new_vals[5] = s
                        filled.append("state")
            if not new_vals[6].strip():
                if shopify_order:
                    fc = _extract_fc_from_shopify_tags(shopify_order)
                    if fc:
                        new_vals[6] = fc
                        filled.append("fc_tag")
                if not new_vals[6].strip() and ticket:
                    fc = _extract_fc_tag(ticket)
                    if fc:
                        new_vals[6] = fc
                        filled.append("fc_tag")
            if not new_vals[7].strip() and ticket:
                cf = ticket.get("custom_fields", {})
                it = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")
                if it and _matches_valid_prefix(it, VALID_ISSUE_PREFIXES):
                    new_vals[7] = it
                    filled.append("issue_type")
            if not new_vals[8].strip() and ticket:
                cf = ticket.get("custom_fields", {})
                res = cf.get(FIELD_RESOLUTION, {}).get("value", "")
                if res and res not in EXCLUDED_RESOLUTIONS:
                    new_vals[8] = res
                    filled.append("resolution")

            if filled:
                sheet_row = row_idx + 1
                enriched.append({"row": sheet_row, "order": order_num, "filled": filled})
                updates.append({
                    "range": f"'{TAB_NAME}'!A{sheet_row}:J{sheet_row}",
                    "values": [new_vals],
                })

        except Exception as e:
            errors += 1
            print(f"  Error row {row_idx+1} ({order_num}): {e}", flush=True)
            if errors >= 20:
                print("Too many errors, stopping", flush=True)
                break
            time.sleep(2)

        if processed % 25 == 0:
            print(f"  {processed} processed, {len(enriched)} enriched, {errors} errors", flush=True)

    print(f"\nDone: {processed} processed, {len(enriched)} enriched, {errors} errors", flush=True)

    if updates:
        sheets_svc = gclient._sheets
        for i in range(0, len(updates), 100):
            batch = updates[i:i+100]
            sheets_svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "USER_ENTERED", "data": batch},
            ).execute()
            print(f"  Written batch {i//100+1} ({len(batch)} rows)", flush=True)

    for e in enriched[:10]:
        print(f"  Row {e['row']}: {e['order']} -> {e['filled']}")
    if len(enriched) > 10:
        print(f"  ... +{len(enriched)-10} more")
    print("COMPLETE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
