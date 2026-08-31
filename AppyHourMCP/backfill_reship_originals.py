"""One-shot backfill: scan UPDATE_Operational Issues for rows whose Order #
column holds a RESHIP order, and swap it to the ORIGINAL damaged order.

Rationale: carrier attribution must anchor on the order that failed, not
the replacement carrier. The sync now writes original-first, but older
rows still carry reship numbers.

Algorithm per row:
  1. Skip if Order # blank, or it ends in a known-reship marker
  2. Shopify lookup by order name. If tags do not contain "reship", skip.
  3. Find the customer email, call _shopify_order_by_email to get the
     most recent non-reship fulfilled order.
  4. If the original differs from current, write the swap back to the sheet.
  5. Log the swap.

Safe: only touches rows where Shopify confirms the current value is a reship
and an original exists. Supports --dry-run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools._gorgias_internal import _load_settings  # noqa: E402
from tools.gorgias_sheets_sync import (  # noqa: E402
    SPREADSHEET_ID,
    TAB_NAME,
    _extract_carrier_from_shopify,
    _extract_fc_from_shopify_tags,
    _extract_state_from_shopify,
    _shopify_order_by_email,
    _shopify_order_by_name,
)


def backfill(dry_run: bool = False, start_row: int = 2, max_rows: int = 0) -> dict:
    from google_integration import GoogleIntegration

    settings = _load_settings()
    creds_path = settings.get("google_credentials_path", "")
    # No settings path -> GoogleIntegration resolves via appyhour_lib.credentials
    # (GOOGLE_SVC_ACCOUNT_JSON_CONTENT on App Platform, key file locally).
    gclient = GoogleIntegration(creds_path or None)

    rows = gclient.read_sheet(SPREADSHEET_ID, f"'{TAB_NAME}'!A:J")
    if not rows:
        return {"error": "No rows"}
    header = rows[0]
    data_rows = rows[1:]

    # Collect swaps
    swaps = []
    scanned = 0
    skipped_blank = 0
    skipped_not_reship = 0
    skipped_no_original = 0
    shopify_not_found = 0

    total = len(data_rows)
    limit = total if max_rows == 0 else min(total, max_rows)

    for idx, row in enumerate(data_rows):
        sheet_row = idx + 2  # 1-indexed, header = row 1
        if sheet_row < start_row:
            continue
        if len(swaps) + scanned >= limit:
            break
        while len(row) < 10:
            row.append("")
        order = (row[2] or "").strip()
        if not order:
            skipped_blank += 1
            continue
        scanned += 1

        if scanned % 25 == 0:
            print(
                f"[backfill] scanned {scanned}, swaps={len(swaps)}",
                flush=True,
            )

        # Shopify lookup
        shopify = _shopify_order_by_name(order)
        time.sleep(0.25)  # rate limit
        if not shopify:
            shopify_not_found += 1
            continue
        tags = (shopify.get("tags", "") or "").lower()
        if "reship" not in tags:
            skipped_not_reship += 1
            continue

        email = shopify.get("email", "") or (shopify.get("customer") or {}).get("email", "")
        if not email:
            skipped_no_original += 1
            continue

        original = _shopify_order_by_email(email)
        time.sleep(0.25)
        if not original or not original.get("name"):
            skipped_no_original += 1
            continue

        orig_name = original["name"]
        if orig_name.lower() == order.lower():
            skipped_not_reship += 1  # can happen if original shares name
            continue

        # Also refresh carrier/state/FC from the ORIGINAL order's Shopify
        # data — without this, the sheet would still show the reship's
        # carrier (wrong attribution).
        orig_carrier = _extract_carrier_from_shopify(original)
        orig_state = _extract_state_from_shopify(original)
        orig_fc = _extract_fc_from_shopify_tags(original)

        swap = {
            "sheet_row": sheet_row,
            "from": order,
            "to": orig_name,
            "email": email,
            "gorgias_link": row[3],
            "new_carrier": orig_carrier,
            "new_state": orig_state,
            "new_fc": orig_fc,
            "old_carrier": row[4],
            "old_state": row[5],
            "old_fc": row[6],
        }
        swaps.append(swap)

    # Apply swaps — update cols C (Order #), E (Carrier), F (State), G (FC)
    # in a single range write per row. Only overwrite C/E/F/G columns;
    # leave D (Gorgias Link), H (Issue Type), I (Resolution) untouched.
    applied = 0
    if swaps and not dry_run:
        for s in swaps:
            # Write C (Order #) alone first — reliable
            gclient._sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{TAB_NAME}'!C{s['sheet_row']}",
                valueInputOption="USER_ENTERED",
                body={"values": [[s["to"]]]},
            ).execute()
            # Write E:G (Carrier, State, FC) as a single 3-cell row
            gclient._sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{TAB_NAME}'!E{s['sheet_row']}:G{s['sheet_row']}",
                valueInputOption="USER_ENTERED",
                body={
                    "values": [[
                        s["new_carrier"] or s["old_carrier"],
                        s["new_state"] or s["old_state"],
                        s["new_fc"] or s["old_fc"],
                    ]]
                },
            ).execute()
            applied += 1
            if applied % 25 == 0:
                print(f"[backfill] applied {applied}/{len(swaps)}", flush=True)
            time.sleep(0.1)

    return {
        "total_sheet_rows": total,
        "scanned": scanned,
        "swaps_found": len(swaps),
        "applied": applied,
        "skipped_blank": skipped_blank,
        "skipped_not_reship": skipped_not_reship,
        "skipped_no_original": skipped_no_original,
        "shopify_not_found": shopify_not_found,
        "dry_run": dry_run,
        "sample_swaps": swaps[:20],
        "started_at": datetime.now().isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="backfill-reship-originals",
        description="Swap reship order #s to original damaged order #s in the sheet.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Preview swaps only")
    ap.add_argument(
        "--start-row",
        type=int,
        default=2,
        help="Sheet row to start at (default 2, skip header)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Cap scanned rows (0 = all). Useful for progressive rollout.",
    )
    args = ap.parse_args()

    print(
        f"[backfill] dry_run={args.dry_run} start_row={args.start_row} "
        f"max_rows={args.max_rows or 'all'}",
        flush=True,
    )
    result = backfill(
        dry_run=args.dry_run,
        start_row=args.start_row,
        max_rows=args.max_rows,
    )
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
