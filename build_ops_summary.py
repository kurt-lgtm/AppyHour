"""
Build Ops Summary tab with dynamic formulas in Google Sheets.

Part 1: Fix text dates in UPDATE_Operational Issues column A
Part 2: Create new "Ops Summary" tab with COUNTIFS formulas
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

import json
import re
from datetime import datetime, date

sys.path.insert(0, 'GelPackCalculator')
sys.path.insert(0, 'AppyHourMCP/tools')

from google_sheets import _get_client

SPREADSHEET_ID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
DATA_TAB = "UPDATE_Operational Issues"
SHIPMENTS_TAB = "Shipments"
OPS_TAB = "Ops Summary"

# Month name -> number
MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Year inference: May-Dec = 2025, Jan-Apr = 2026
def infer_year(month_num):
    return 2026 if month_num <= 4 else 2025

SHIPPING_ISSUES = [
    "Shipping::cannot be delivered",
    "Shipping::Damaged in transit::Arrived Warm",
    "Shipping::Damaged in transit::Broken/Leaking Ice Pack",
    "Shipping::Delayed in transit::4+ Days in Transit",
    "Shipping::Delayed in transit::3 Days in Transit",
    "Shipping::Change Address::Customer Updated, Not applied",
    "Shipping::Change Address::Requested, Cannot be applied",
    "Shipping::Lost in Transit/Misdelivered",
    "Shipping::Damaged in transit::Full box damaged",
    "Shipping::Damaged in transit::Damaged cheese",
    "Shipping::Damaged in transit::Spilled accompaniment",
    "Shipping::Damaged in transit::Damaged meat",
    "Shipping::Damaged in transit::Spilled accompaniment or Broken Crackers or Broken/Leaking Jar",
]

ORDER_ISSUES = [
    "Order::Missing item::All cheeses",
    "Order::Missing item::All Meats",
    "Order::Missing item::1+ cheeses",
    "Order::Missing item::1+ meats",
    "Order::Missing item::1+ accompaniment(s)",
    "Order::Missing item::Tasting guide",
    "Order::Missing tasting guide",
    "Order::Substitute complaint",
    "Order::Wrong Order",
    "Order::Wrong item(s)",
    "Order::Sent wrong box",
    "Order::Duplicate Order",
    "Order::Extra item",
    "Order::Missing item::Bonus Pairing",
    "Order::Quality Complaint::Cheese",
    "Order::Quality Complaint::Meat",
    "Order::Quality Complaint::Accompaniment",
    "Order::Spoiled Item::Cheese",
    "Order::Spoiled Item::Meat",
    "Order::Spoiled Item::Accompaniment",
]

RESOLUTIONS = [
    ("Full Reship", 65),
    ("Partial Reship", 35),
    ("Credit Next Box::Amount $6", 6),
    ("Credit Next Box::Amount $10", 10),
    ("Credit Next Box::Amount $15", 15),
    ("Credit Next Box::Amount $20", 20),
    ("Credit Next Box::Amount $30", 30),
    ("Credit Next Box::Amount $40+", 45),
    ("Refund Order::Amount $6", 6),
    ("Refund Order::Amount $10", 10),
    ("Refund Order::Amount $15", 15),
    ("Refund Order::Amount $20", 20),
    ("Refund Order::Amount $30", 30),
    ("Refund Order::Amount $40+", 45),
    ("Refund Order::Full Amount", 110),
    ("Refund Order::Specific Cheese(s)", 10),
    ("Refund Order::Specific Meat(s)", 10),
    ("Refund Order::Specific Accompaniment(s)", 6),
    ("Refund Order::Specific Items (Multiple types)", 20),
    ("Comp Item::Extra Cheese(s)", 5.5),
    ("Comp Item::Extra Meat(s)", 4),
    ("Comp Item::Extra Accompaniment(s)", 2.5),
    ("Comp Item + Credit/Refund::$10 off + extra cheese", 15.5),
    ("Comp Item + Credit/Refund::$15 off + extra cheese", 20.5),
    ("Comp Item + Credit/Refund::$20 off + extra cheese", 25.5),
    ("Comp Item + Credit/Refund::$10 off + extra meat", 14),
    ("Comp Item + Credit/Refund::$15 off + extra meat", 19),
    ("Comp Item + Credit/Refund::$20 off + extra meat", 24),
    ("Comp Item + Credit/Refund::$10 off + extra accompaniment", 12.5),
    ("Comp Item + Credit/Refund::$15 off + extra accompaniment", 17.5),
    ("Comp Item + Credit/Refund::$20 off + extra accompaniment", 22.5),
]


def col_letter(idx):
    """Convert 0-based column index to spreadsheet letter(s). 0=A, 26=AA."""
    result = ""
    n = idx
    while True:
        result = chr(65 + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


def main():
    client = _get_client()
    svc = client._sheets

    # ─── PART 1: Fix dates ────────────────────────────────────────────
    print("PART 1: Fixing dates in column A...")

    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DATA_TAB}'!A:A",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    col_a = result.get("values", [])
    print(f"  Read {len(col_a)} rows from column A")

    # Parse and convert dates (skip row 1 = header)
    date_values = []
    converted = 0
    skipped = 0
    for i, row in enumerate(col_a):
        if i == 0:
            # Keep header as-is
            date_values.append([row[0] if row else ""])
            continue
        if not row or not row[0]:
            date_values.append([""])
            continue

        raw = row[0].strip()
        # Try to parse "Month-Day" format
        m = re.match(r'^([A-Za-z]+)-(\d+)$', raw)
        if m:
            month_name = m.group(1).lower()
            day = int(m.group(2))
            month_num = MONTH_MAP.get(month_name)
            if month_num:
                year = infer_year(month_num)
                date_str = f"{month_num}/{day}/{year}"
                date_values.append([date_str])
                converted += 1
                continue

        # Already a date or unknown format — keep as-is
        date_values.append([raw])
        skipped += 1

    print(f"  Converted {converted} dates, kept {skipped} as-is")

    # Write back with USER_ENTERED so Google Sheets interprets as dates
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DATA_TAB}'!A1:A{len(date_values)}",
        valueInputOption="USER_ENTERED",
        body={"values": date_values},
    ).execute()
    print("  Wrote converted dates back to column A")

    # ─── PART 2: Build Ops Summary tab ─────────────────────────────────
    print("\nPART 2: Building Ops Summary tab...")

    # Get weeks from Shipments tab
    ship_result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHIPMENTS_TAB}'!A:F",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    ship_rows = ship_result.get("values", [])
    print(f"  Read {len(ship_rows)} rows from Shipments tab")

    # Parse week start dates from Shipments (column A, skip header)
    week_dates = []
    for row in ship_rows[1:]:
        if not row or not row[0]:
            continue
        raw = row[0].strip()
        try:
            dt = datetime.strptime(raw, "%m/%d/%Y").date()
            week_dates.append(dt)
        except ValueError:
            try:
                dt = datetime.strptime(raw, "%m/%d/%y").date()
                week_dates.append(dt)
            except ValueError:
                pass

    # Also scan data tab column A for weeks not in Shipments
    data_result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DATA_TAB}'!A2:A",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    data_dates_raw = data_result.get("values", [])

    from datetime import timedelta
    all_data_dates = set()
    for row in data_dates_raw:
        if not row or not row[0]:
            continue
        try:
            serial = float(row[0])
            # Google Sheets serial: days since Dec 30, 1899
            dt = date(1899, 12, 30) + timedelta(days=int(serial))
            all_data_dates.add(dt)
        except (ValueError, TypeError):
            pass

    # Find Monday of each data date
    data_weeks = set()
    for dt in all_data_dates:
        monday = dt - timedelta(days=dt.weekday())
        data_weeks.add(monday)

    # Merge with shipment weeks
    existing_set = set(week_dates)
    for w in data_weeks:
        if w not in existing_set:
            week_dates.append(w)

    week_dates = sorted(set(week_dates))
    print(f"  Found {len(week_dates)} weeks total")

    # ─── Create or clear the tab ──────────────────────────────────────
    # Get existing tabs
    spreadsheet = svc.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields="sheets.properties",
    ).execute()
    existing_tabs = {s["properties"]["title"]: s["properties"]["sheetId"]
                     for s in spreadsheet.get("sheets", [])}

    if OPS_TAB in existing_tabs:
        # Clear existing tab
        sheet_id = existing_tabs[OPS_TAB]
        svc.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{OPS_TAB}'!A:ZZ",
        ).execute()
        print(f"  Cleared existing '{OPS_TAB}' tab")
    else:
        # Create new tab
        resp = svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": OPS_TAB}}}]},
        ).execute()
        sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"  Created new '{OPS_TAB}' tab (sheetId={sheet_id})")

    # ─── Build the grid ───────────────────────────────────────────────
    num_weeks = len(week_dates)
    # Columns: A=Category, B=Issue, then 2 cols per week (# and %)
    total_cols = 2 + num_weeks * 2

    # Helper: column letter for week i, count column (0-based from C)
    def week_count_col(i):
        return col_letter(2 + i * 2)  # C, E, G, ...

    def week_pct_col(i):
        return col_letter(3 + i * 2)  # D, F, H, ...

    # Build rows
    rows = []

    # Row 1: Week of m/d (date values in count columns)
    row1 = ["", ""]
    for dt in week_dates:
        row1.append(f"=DATE({dt.year},{dt.month},{dt.day})")
        row1.append("")
    rows.append(row1)

    # Row 2: Date range text "m/d - m/d"
    row2 = ["", ""]
    for i, dt in enumerate(week_dates):
        c = week_count_col(i)
        # Start date from row 1, end date = start + 6
        row2.append(f'=TEXT({c}1,"m/d") & " - " & TEXT({c}1+6,"m/d")')
        row2.append("")
    rows.append(row2)

    # Row 3: Headers
    row3 = ["Category", "Issue"]
    for _ in week_dates:
        row3.append("#")
        row3.append("%")
    rows.append(row3)

    # Row 4: Total Orders
    row4 = ["", "Total Orders"]
    for i, dt in enumerate(week_dates):
        c = week_count_col(i)
        row4.append(f'=IFERROR(INDEX(\'{SHIPMENTS_TAB}\'!F:F,MATCH({c}$1,\'{SHIPMENTS_TAB}\'!$A:$A,0)),"")')
        row4.append("")
    rows.append(row4)

    current_row = 5  # 1-indexed (next row to write)

    # ─── SHIPPING ISSUES section ──────────────────────────────────────
    section_header = ["", "─── SHIPPING ISSUES ───"] + [""] * (num_weeks * 2)
    rows.append(section_header)
    shipping_header_row = current_row
    current_row += 1

    for issue in SHIPPING_ISSUES:
        row = ["Shipping", issue]
        for i in range(num_weeks):
            c = week_count_col(i)
            pc = week_pct_col(i)
            r = current_row  # 1-indexed row for this issue
            # Count formula: COUNTIFS date range + issue type in column H
            count_f = (
                f'=IF(COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$H:$H,$B{r})=0,"",'
                f'COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$H:$H,$B{r}))'
            )
            # Percent formula
            pct_f = f'=IFERROR(IF({c}{r}="","",{c}{r}/{c}$4),"")'
            row.append(count_f)
            row.append(pct_f)
        rows.append(row)
        current_row += 1

    # ─── ORDER ISSUES section ─────────────────────────────────────────
    section_header = ["", "─── ORDER ISSUES ───"] + [""] * (num_weeks * 2)
    rows.append(section_header)
    order_header_row = current_row
    current_row += 1

    for issue in ORDER_ISSUES:
        row = ["Order", issue]
        for i in range(num_weeks):
            c = week_count_col(i)
            r = current_row
            count_f = (
                f'=IF(COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$H:$H,$B{r})=0,"",'
                f'COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$H:$H,$B{r}))'
            )
            pct_f = f'=IFERROR(IF({c}{r}="","",{c}{r}/{c}$4),"")'
            row.append(count_f)
            row.append(pct_f)
        rows.append(row)
        current_row += 1

    # ─── RESOLUTIONS section ──────────────────────────────────────────
    section_header = ["", "─── RESOLUTIONS ───"] + [""] * (num_weeks * 2)
    rows.append(section_header)
    resolution_header_row = current_row
    current_row += 1

    resolution_start_row = current_row
    resolution_cost_map = {}  # row_num -> cost

    for res_name, cost in RESOLUTIONS:
        row = [f"${cost}", res_name]
        resolution_cost_map[current_row] = cost
        for i in range(num_weeks):
            c = week_count_col(i)
            r = current_row
            # Resolutions are in column I
            count_f = (
                f'=IF(COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$I:$I,$B{r})=0,"",'
                f'COUNTIFS(\'{DATA_TAB}\'!$A:$A,">="&{c}$1,'
                f'\'{DATA_TAB}\'!$A:$A,"<"&({c}$1+7),'
                f'\'{DATA_TAB}\'!$I:$I,$B{r}))'
            )
            pct_f = f'=IFERROR(IF({c}{r}="","",{c}{r}/{c}$4),"")'
            row.append(count_f)
            row.append(pct_f)
        rows.append(row)
        current_row += 1

    resolution_end_row = current_row - 1

    # ─── Total Reships % row ──────────────────────────────────────────
    rows.append([])  # blank row
    current_row += 1

    # Find the rows for Full Reship and Partial Reship
    full_reship_row = resolution_start_row  # First resolution
    partial_reship_row = resolution_start_row + 1  # Second resolution

    reship_row = ["", "Total Reships %"]
    for i in range(num_weeks):
        c = week_count_col(i)
        # Sum of Full + Partial reship counts / total orders
        reship_row.append(
            f'=IF({c}$4="","",IF({c}$4=0,"",'
            f'(IF({c}{full_reship_row}="",0,{c}{full_reship_row})'
            f'+IF({c}{partial_reship_row}="",0,{c}{partial_reship_row}))/{c}$4))'
        )
        reship_row.append("")
    rows.append(reship_row)
    total_reships_row = current_row
    current_row += 1

    # ─── COST SUMMARY section ─────────────────────────────────────────
    rows.append([])  # blank row
    current_row += 1

    section_header = ["", "─── COST SUMMARY ───"] + [""] * (num_weeks * 2)
    rows.append(section_header)
    cost_header_row = current_row
    current_row += 1

    # Total Cost row using SUMPRODUCT of resolution counts * costs
    total_cost_row_data = ["", "Total Cost"]
    for i in range(num_weeks):
        c = week_count_col(i)
        # Build SUMPRODUCT: for each resolution row, multiply count by cost
        # We need to handle blanks as 0
        parts = []
        for r_row in range(resolution_start_row, resolution_end_row + 1):
            cost = resolution_cost_map[r_row]
            parts.append(f'IF({c}{r_row}="",0,{c}{r_row})*{cost}')
        # Join with +
        formula = "=" + "+".join(parts)
        total_cost_row_data.append(formula)
        total_cost_row_data.append("")
    rows.append(total_cost_row_data)
    total_cost_row = current_row
    current_row += 1

    # Cost per Order
    cost_per_order_data = ["", "Cost per Order"]
    for i in range(num_weeks):
        c = week_count_col(i)
        cost_per_order_data.append(
            f'=IF({c}$4="","",IF({c}$4=0,"",{c}{total_cost_row}/{c}$4))'
        )
        cost_per_order_data.append("")
    rows.append(cost_per_order_data)
    current_row += 1

    # ─── Write all data ───────────────────────────────────────────────
    end_col = col_letter(total_cols - 1)
    write_range = f"'{OPS_TAB}'!A1:{end_col}{len(rows)}"
    print(f"  Writing {len(rows)} rows x {total_cols} cols to {write_range}")

    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=write_range,
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    print("  Data written successfully")

    # ─── Formatting (batchUpdate) ─────────────────────────────────────
    print("  Applying formatting...")

    requests_list = []

    # 1. Freeze columns A-B and rows 1-4
    requests_list.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": 4,
                    "frozenColumnCount": 2,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })

    # 2. Column widths: A=100, B=300, data cols=60
    requests_list.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 100},
            "fields": "pixelSize",
        }
    })
    requests_list.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 300},
            "fields": "pixelSize",
        }
    })
    requests_list.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": total_cols},
            "properties": {"pixelSize": 60},
            "fields": "pixelSize",
        }
    })

    # 3. Bold headers (rows 1-4)
    requests_list.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 4,
                       "startColumnIndex": 0, "endColumnIndex": total_cols},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    })

    # 4. Row 1 date format (m/d/yyyy) for date columns
    requests_list.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 2, "endColumnIndex": total_cols},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "\"Week of\" m/d"}}},
            "fields": "userEnteredFormat.numberFormat",
        }
    })

    # 5. % columns formatted as percentage (0.0%)
    for i in range(num_weeks):
        pct_col_idx = 3 + i * 2  # D, F, H, ...
        requests_list.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": len(rows),
                           "startColumnIndex": pct_col_idx, "endColumnIndex": pct_col_idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    # 6. Total Reships % row — format count cols as percentage too
    for i in range(num_weeks):
        cnt_col_idx = 2 + i * 2
        requests_list.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": total_reships_row - 1, "endRowIndex": total_reships_row,
                           "startColumnIndex": cnt_col_idx, "endColumnIndex": cnt_col_idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    # 7. Cost rows formatted as currency (Total Cost and Cost per Order)
    for cost_row in [total_cost_row, current_row - 1]:  # total_cost_row and cost_per_order row
        for i in range(num_weeks):
            cnt_col_idx = 2 + i * 2
            requests_list.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id,
                               "startRowIndex": cost_row - 1, "endRowIndex": cost_row,
                               "startColumnIndex": cnt_col_idx, "endColumnIndex": cnt_col_idx + 1},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

    # 8. Section headers: bold with light gray background
    # Rows: shipping_header_row, order_header_row, resolution_header_row, cost_header_row
    gray_bg = {"red": 0.9, "green": 0.9, "blue": 0.9}
    for sec_row in [shipping_header_row, order_header_row, resolution_header_row, cost_header_row]:
        requests_list.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": sec_row - 1, "endRowIndex": sec_row,
                           "startColumnIndex": 0, "endColumnIndex": total_cols},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": gray_bg,
                }},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor",
            }
        })

    # 9. Bold the Total Orders row and Total Reships row and cost rows
    for bold_row in [4, total_reships_row, total_cost_row, current_row - 1]:
        requests_list.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": bold_row - 1, "endRowIndex": bold_row,
                           "startColumnIndex": 0, "endColumnIndex": total_cols},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        })

    # Execute all formatting
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": requests_list},
    ).execute()
    print("  Formatting applied")

    # ─── Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"DONE!")
    print(f"  Part 1: Converted {converted} text dates to date values")
    print(f"  Part 2: Built '{OPS_TAB}' tab")
    print(f"    Rows: {len(rows)}")
    print(f"    Columns: {total_cols} (2 label + {num_weeks} weeks x 2)")
    print(f"    Weeks: {num_weeks} ({week_dates[0]} to {week_dates[-1]})")
    print(f"    Shipping issues: {len(SHIPPING_ISSUES)}")
    print(f"    Order issues: {len(ORDER_ISSUES)}")
    print(f"    Resolutions: {len(RESOLUTIONS)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
