"""
Fix remaining gaps in UPDATE_Operational Issues tab.

Tasks:
1. Rows missing order # but have Gorgias link → fetch ticket from Gorgias API to find order #
2. Rows missing carrier/state but have order # → look up Shopify
3. Rows missing FC tag → default to RMFG
4. Write updates back using targeted cell updates
"""

import sys
import time
import re

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "GelPackCalculator")
sys.path.insert(0, "AppyHourMCP/tools")

from google_sheets import _get_client
from gorgias_sheets_sync import (
    _load_settings,
    _gorgias_auth,
    _shopify_order_by_name,
    _extract_carrier_from_shopify,
    _extract_state_from_shopify,
    _extract_fc_from_shopify_tags,
    _extract_order_from_text,
    FIELD_ISSUE_TYPE,
    FIELD_RESOLUTION,
    SPREADSHEET_ID,
    TAB_NAME,
)
from google_integration import GoogleIntegration
import requests
import os
from pathlib import Path

# ── Setup ──────────────────────────────────────────────────────────────────────

settings = _load_settings()
creds_path = settings.get("google_credentials_path", "")
if not creds_path or not os.path.exists(creds_path):
    creds_path = str(Path("shipping-perfomance-review-accd39ac4b78.json").resolve())
gclient = GoogleIntegration(creds_path)
svc = gclient._sheets

auth, base_url = _gorgias_auth()

# Column indices (0-based): A=0 Date, B=1 ContactReason, C=2 Order#, D=3 GorgiasLink,
# E=4 Carrier, F=5 State, G=6 FCTag, H=7 IssueType, I=8 Resolution, J=9 Comment
COL_ORDER    = 2   # C
COL_LINK     = 3   # D
COL_CARRIER  = 4   # E
COL_STATE    = 5   # F
COL_FC       = 6   # G
COL_ISSUE    = 7   # H
COL_RES      = 8   # I

SHEET_RANGE = f"'{TAB_NAME}'!A:J"

# ── Read all rows ──────────────────────────────────────────────────────────────

print("Reading sheet...")
all_rows = gclient.read_sheet(SPREADSHEET_ID, SHEET_RANGE)
print(f"  Total rows (including header): {len(all_rows)}")

# ── Helper: extract ticket ID from Gorgias link ────────────────────────────────

def _ticket_id_from_link(link: str) -> str | None:
    """Extract ticket ID from https://appyhour.gorgias.com/app/views/VIEWID/TICKETID"""
    m = re.search(r"/views/\d+/(\d+)", link)
    return m.group(1) if m else None


def _fetch_gorgias_ticket(ticket_id: str) -> dict | None:
    """Fetch a single Gorgias ticket by ID."""
    try:
        resp = requests.get(
            f"{base_url}/tickets/{ticket_id}",
            auth=auth,
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"    [warn] Gorgias ticket fetch failed for {ticket_id}: {e}")
    return None


def _fetch_ticket_messages(ticket_id: str) -> list[dict]:
    """Fetch messages for a Gorgias ticket."""
    try:
        resp = requests.get(
            f"{base_url}/tickets/{ticket_id}/messages",
            auth=auth,
            params={"limit": 10, "order_by": "created_datetime:asc"},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
    except Exception as e:
        print(f"    [warn] Gorgias messages fetch failed for {ticket_id}: {e}")
    return []


def _order_from_ticket(ticket: dict) -> str:
    """Try to extract order number from ticket subject and messages."""
    order = _extract_order_from_text(ticket.get("subject", ""))
    if order:
        return order
    # Try messages
    messages = _fetch_ticket_messages(str(ticket["id"]))
    time.sleep(0.3)
    for m in messages:
        body = m.get("body_text", "") or ""
        order = _extract_order_from_text(body[:2000])
        if order:
            return order
    return ""


# ── Column letter helper ───────────────────────────────────────────────────────

def col_letter(col_idx: int) -> str:
    """Convert 0-based column index to letter (0→A, 1→B, etc.)."""
    return chr(ord("A") + col_idx)


# ── Scan rows and build update list ───────────────────────────────────────────

updates = []  # list of {"range": "...", "values": [[val]]}

stats = {
    "order_filled": 0,
    "order_failed": 0,
    "carrier_filled": 0,
    "state_filled": 0,
    "fc_filled": 0,
    "issue_skipped": 0,
    "res_skipped": 0,
}

# Rows with missing order # that need Gorgias lookup (dedup by ticket ID)
seen_ticket_ids: set[str] = set()

rows_data = all_rows[1:]  # skip header

print(f"\nScanning {len(rows_data)} data rows for gaps...")

for i, row in enumerate(rows_data):
    sheet_row = i + 2  # 1-based, +1 for header

    # Pad to at least 9 cols
    while len(row) < 9:
        row.append("")

    order_num   = row[COL_ORDER].strip()
    gorgias_link = row[COL_LINK].strip()
    carrier     = row[COL_CARRIER].strip()
    state       = row[COL_STATE].strip()
    fc_tag      = row[COL_FC].strip()
    issue_type  = row[COL_ISSUE].strip() if len(row) > COL_ISSUE else ""
    resolution  = row[COL_RES].strip() if len(row) > COL_RES else ""

    changed = False  # track if we need any Shopify call

    # ── Task 1: Missing order # but have Gorgias link ─────────────────────────
    if not order_num and gorgias_link:
        ticket_id = _ticket_id_from_link(gorgias_link)
        if ticket_id and ticket_id not in seen_ticket_ids:
            seen_ticket_ids.add(ticket_id)
            print(f"  Row {sheet_row}: missing order #, ticket {ticket_id} — fetching from Gorgias...")
            ticket = _fetch_gorgias_ticket(ticket_id)
            time.sleep(0.3)
            if ticket:
                found = _order_from_ticket(ticket)
                if found:
                    order_num = found
                    updates.append({
                        "range": f"'{TAB_NAME}'!{col_letter(COL_ORDER)}{sheet_row}",
                        "values": [[order_num]],
                    })
                    print(f"    → found order {order_num}")
                    stats["order_filled"] += 1
                    changed = True
                else:
                    print(f"    → no order # found in ticket")
                    stats["order_failed"] += 1
            else:
                print(f"    → ticket fetch returned None")
                stats["order_failed"] += 1

    # ── Task 2: Missing carrier or state but have order # ─────────────────────
    needs_carrier = not carrier
    needs_state   = not state
    needs_fc      = not fc_tag

    if order_num and (needs_carrier or needs_state or needs_fc):
        print(f"  Row {sheet_row} ({order_num}): missing"
              + (" carrier" if needs_carrier else "")
              + (" state" if needs_state else "")
              + (" fc" if needs_fc else "")
              + " — looking up Shopify...")
        shopify_order = _shopify_order_by_name(order_num)
        time.sleep(0.3)

        if shopify_order:
            if needs_carrier:
                new_carrier = _extract_carrier_from_shopify(shopify_order)
                if new_carrier:
                    carrier = new_carrier
                    updates.append({
                        "range": f"'{TAB_NAME}'!{col_letter(COL_CARRIER)}{sheet_row}",
                        "values": [[carrier]],
                    })
                    print(f"    → carrier: {carrier}")
                    stats["carrier_filled"] += 1

            if needs_state:
                new_state = _extract_state_from_shopify(shopify_order)
                if new_state:
                    state = new_state
                    updates.append({
                        "range": f"'{TAB_NAME}'!{col_letter(COL_STATE)}{sheet_row}",
                        "values": [[state]],
                    })
                    print(f"    → state: {state}")
                    stats["state_filled"] += 1

            if needs_fc:
                new_fc = _extract_fc_from_shopify_tags(shopify_order)
                if new_fc:
                    fc_tag = new_fc
                    updates.append({
                        "range": f"'{TAB_NAME}'!{col_letter(COL_FC)}{sheet_row}",
                        "values": [[fc_tag]],
                    })
                    print(f"    → fc: {fc_tag}")
                    stats["fc_filled"] += 1
        else:
            print(f"    → Shopify order not found")

    # ── Task 3: Missing FC tag (safety net, default RMFG) ─────────────────────
    if not fc_tag:
        fc_tag = "RMFG"
        updates.append({
            "range": f"'{TAB_NAME}'!{col_letter(COL_FC)}{sheet_row}",
            "values": [["RMFG"]],
        })
        print(f"  Row {sheet_row}: defaulting fc_tag → RMFG")
        stats["fc_filled"] += 1

    if (i + 1) % 5 == 0:
        print(f"  ... processed {i + 1}/{len(rows_data)} rows, {len(updates)} updates queued")

print(f"\nDone scanning. {len(updates)} cell updates to write.")

# ── Write updates ──────────────────────────────────────────────────────────────

if not updates:
    print("Nothing to update — sheet is already complete.")
else:
    print("\nWriting updates to Google Sheets...")
    batch_body = {
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": u["range"], "values": u["values"]} for u in updates],
    }
    result = (
        svc.spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body=batch_body,
        )
        .execute()
    )
    total_updated = result.get("totalUpdatedCells", "?")
    print(f"  API confirmed {total_updated} cells updated across {len(updates)} ranges.")

# ── Summary ────────────────────────────────────────────────────────────────────

print("\n── Summary ──────────────────────────────────────────")
print(f"  Order # filled:    {stats['order_filled']}")
print(f"  Order # not found: {stats['order_failed']}")
print(f"  Carrier filled:    {stats['carrier_filled']}")
print(f"  State filled:      {stats['state_filled']}")
print(f"  FC tag filled:     {stats['fc_filled']}")
print(f"  Total cells written: {len(updates)}")
print("─────────────────────────────────────────────────────")
