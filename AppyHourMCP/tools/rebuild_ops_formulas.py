"""One-shot script to build formula-based Ops Summary + Shipments tab.

Supports date-based FC cutoff:
- Before RMFG_ONLY_FROM: 3 FCs (GRIPCA, RMFG, COG) + SUM per week
- From RMFG_ONLY_FROM onward: RMFG only + SUM per week
"""

import sys
import json
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "GelPackCalculator"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from google_integration import GoogleIntegration
from ops_summary_builder import (
    _load_shipment_volumes, _week_start, _load_settings,
    ISSUE_TYPES, RESOLUTION_TYPES, RESOLUTION_COSTS, FCS,
    SPREADSHEET_ID,
)

OPS = "Ops Summary Report "
DATA = "'UPDATE_Operational Issues'"

# FC era cutoffs
GRIPCA_RETIRED = date(2026, 1, 5)   # No more GRIPCA from Jan 2026
COG_RETIRED = date(2026, 2, 2)      # No more COG from Feb 2026

# FC lists by era
ERA_ALL = ["GRIPCA", "RMFG", "COG"]   # Pre-2026
ERA_JAN = ["RMFG", "COG"]             # Jan 2026 (GRIPCA retired)
ERA_FEB = ["RMFG"]                    # Feb 2026+ (COG retired)

# Shipments tab column for each FC
SHIP_COL_MAP = {"GRIPCA": "C", "RMFG": "D", "COG": "E"}


def col_letter(n):
    result = ""
    while True:
        result = chr(65 + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


def fcs_for_week(week):
    """Return the FC list for a given week start date."""
    d = week.date() if isinstance(week, datetime) else week
    if d >= COG_RETIRED:
        return ERA_FEB
    if d >= GRIPCA_RETIRED:
        return ERA_JAN
    return ERA_ALL


def main():
    settings = _load_settings()
    creds = settings.get("google_credentials_path", "")
    if not creds or not __import__("os").path.exists(creds):
        creds = str(Path(__file__).resolve().parent.parent.parent
                     / "shipping-perfomance-review-accd39ac4b78.json")
    client = GoogleIntegration(creds)
    sheets_svc = client._sheets
    SID = SPREADSHEET_ID

    # ── 1. Create Shipments tab ──────────────────────────────────────
    volumes = _load_shipment_volumes()

    meta = sheets_svc.spreadsheets().get(spreadsheetId=SID, fields="sheets.properties").execute()
    tab_exists = any(s["properties"]["title"] == "Shipments" for s in meta["sheets"])
    if not tab_exists:
        sheets_svc.spreadsheets().batchUpdate(spreadsheetId=SID, body={
            "requests": [{"addSheet": {"properties": {"title": "Shipments"}}}]
        }).execute()
    else:
        sheets_svc.spreadsheets().values().clear(spreadsheetId=SID, range="'Shipments'!A1:Z200").execute()

    ship_headers = ["Week Start", "Week End", "GRIPCA", "RMFG", "COG", "Total"]
    ship_rows = []
    for week in sorted(volumes.keys()):
        end = week + timedelta(days=6)
        vol = volumes[week]
        rn = len(ship_rows) + 2
        ship_rows.append([
            week.strftime("%m/%d/%Y"),
            end.strftime("%m/%d/%Y"),
            int(vol.get("GRIPCA", 0)) or "",
            int(vol.get("RMFG", 0)) or "",
            int(vol.get("COG", 0)) or "",
            f"=SUM(C{rn}:E{rn})",
        ])

    client.write_sheet(SID, "Shipments", ship_headers, ship_rows)
    print(f"Shipments tab: {len(ship_rows)} weeks")

    # ── 2. Determine weeks ───────────────────────────────────────────
    weeks = sorted(volumes.keys())
    raw = client.read_sheet(SID, f"{DATA}!A:A")
    for r in raw[1:]:
        if r and r[0]:
            try:
                dt = datetime.strptime(r[0].strip(), "%m/%d/%Y")
                w = _week_start(dt)
                if w not in weeks:
                    weeks.append(w)
            except ValueError:
                pass
    weeks = sorted(set(weeks))
    num_weeks = len(weeks)
    print(f"Weeks: {num_weeks}")

    # ── 3. Build formula-based Ops Summary ────────────────────────────
    sheets_svc.spreadsheets().values().clear(
        spreadsheetId=SID, range=f"'{OPS}'!A1:ZZ100"
    ).execute()

    all_rows = []

    # Precompute column offsets per week (cumulative, starting at col index 3 = D)
    week_col_start = []  # col index where each week starts
    week_fcs = []        # FC list for each week
    col_pos = 3          # start after A/B/C
    for week in weeks:
        wfcs = fcs_for_week(week)
        week_col_start.append(col_pos)
        week_fcs.append(wfcs)
        col_pos += len(wfcs) + 1  # FCs + SUM

    total_cols = col_pos

    # Row 1: Shipment counts from Shipments tab
    row1 = ["Issue vs. Resolution", "Category", "Issue"]
    row2 = ["", "", ""]
    row3 = ["", "", ""]

    for wi, week in enumerate(weeks):
        end = week + timedelta(days=6)
        wfcs = week_fcs[wi]
        base = week_col_start[wi]

        for fi, fc in enumerate(wfcs):
            c = col_letter(base + fi)
            ship_col = SHIP_COL_MAP[fc]
            row1.append(
                f'=IFERROR(INDEX(Shipments!{ship_col}:{ship_col},'
                f'MATCH({c}$2,Shipments!$A:$A,0)),"")'
            )
            row3.append(fc)

        # SUM col
        fc_cols = [col_letter(base + fi) for fi in range(len(wfcs))]
        sum_formula = "+".join(f"{c}1" for c in fc_cols)
        row1.append(f"={sum_formula}")
        row3.append("SUM")

        # Date row: pad to match stride
        stride = len(wfcs) + 1
        date_cells = [week.strftime("%m/%d/%Y"), "to ", end.strftime("%m/%d/%Y")]
        date_cells.extend([""] * (stride - len(date_cells)))
        row2.extend(date_cells)

    all_rows.extend([row1, row2, row3])

    # Issue rows with COUNTIFS
    for itype in ISSUE_TYPES:
        category = "Shipping" if itype.startswith("Shipping") else "Order"
        row = ["Issue", category, itype.strip()]
        rn = len(all_rows) + 1
        for wi, week in enumerate(weeks):
            wfcs = week_fcs[wi]
            base = week_col_start[wi]
            nw = week + timedelta(days=7)
            date_end = f"DATE({nw.year},{nw.month},{nw.day})"
            for fi, fc in enumerate(wfcs):
                c = col_letter(base + fi)
                formula = (
                    f"=COUNTIFS({DATA}!$A:$A,\">=\"&{c}$2,"
                    f"{DATA}!$A:$A,\"<\"&{date_end},"
                    f"{DATA}!$G:$G,{c}$3,"
                    f"{DATA}!$H:$H,$C{rn})"
                )
                row.append(formula)
            fc_cols = [col_letter(base + fi) for fi in range(len(wfcs))]
            sum_formula = "+".join(f"{c}{rn}" for c in fc_cols)
            row.append(f"={sum_formula}")
        all_rows.append(row)

    # Resolution rows with COUNTIFS
    res_start_row = len(all_rows) + 1  # 1-indexed
    for rtype in RESOLUTION_TYPES:
        cost = RESOLUTION_COSTS.get(rtype, 0)
        row = ["Resolution", cost if cost else "", rtype]
        rn = len(all_rows) + 1
        for wi, week in enumerate(weeks):
            wfcs = week_fcs[wi]
            base = week_col_start[wi]
            nw = week + timedelta(days=7)
            date_end = f"DATE({nw.year},{nw.month},{nw.day})"
            for fi, fc in enumerate(wfcs):
                c = col_letter(base + fi)
                formula = (
                    f"=COUNTIFS({DATA}!$A:$A,\">=\"&{c}$2,"
                    f"{DATA}!$A:$A,\"<\"&{date_end},"
                    f"{DATA}!$G:$G,{c}$3,"
                    f"{DATA}!$I:$I,$C{rn})"
                )
                row.append(formula)
            fc_cols = [col_letter(base + fi) for fi in range(len(wfcs))]
            sum_formula = "+".join(f"{c}{rn}" for c in fc_cols)
            row.append(f"={sum_formula}")
        all_rows.append(row)
    res_end_row = len(all_rows)  # 1-indexed, last resolution row

    # Reship percent row
    fr_rn = res_start_row  # Full Reship row
    pr_rn = res_start_row + 1  # Partial Reship row
    reship_row = ["Resolution", "", "Total Reships percent"]
    rn = len(all_rows) + 1
    for wi, week in enumerate(weeks):
        wfcs = week_fcs[wi]
        base = week_col_start[wi]
        for fi, fc in enumerate(wfcs):
            c = col_letter(base + fi)
            reship_row.append(f"=IFERROR(({c}{fr_rn}+{c}{pr_rn})/{c}1,0)")
        sc = col_letter(base + len(wfcs))  # SUM col
        reship_row.append(f"=IFERROR(({sc}{fr_rn}+{sc}{pr_rn})/{sc}1,0)")
    all_rows.append(reship_row)

    # Blank + Cost section
    blank = [""] * total_cols
    cost_header = ["", "", ""]
    cost_fc = ["", "", ""]
    cost_total_row = ["", "", "Total Cost"]
    cost_per_order_row = ["", "", "Cost per Order"]

    cost_total_rn = len(all_rows) + 5  # +blank +blank +header +fc +this

    for wi, week in enumerate(weeks):
        wfcs = week_fcs[wi]
        base = week_col_start[wi]
        end = week + timedelta(days=6)
        stride = len(wfcs) + 1

        date_cells = [week.strftime("%m/%d/%Y"), "to ", end.strftime("%m/%d/%Y")]
        date_cells.extend([""] * (stride - len(date_cells)))
        cost_header.extend(date_cells)

        fc_labels = list(wfcs) + ["SUM"]
        cost_fc.extend(fc_labels)

        for fi, fc in enumerate(wfcs):
            c = col_letter(base + fi)
            formula = f"=SUMPRODUCT({c}{res_start_row}:{c}{res_end_row},$B{res_start_row}:$B{res_end_row})"
            cost_total_row.append(formula)
        fc_cols = [col_letter(base + fi) for fi in range(len(wfcs))]
        sum_formula = "+".join(f"{c}{cost_total_rn}" for c in fc_cols)
        cost_total_row.append(f"={sum_formula}")

        for fi, fc in enumerate(wfcs):
            c = col_letter(base + fi)
            cost_per_order_row.append(f"=IFERROR({c}{cost_total_rn}/{c}1,0)")
        sc = col_letter(base + len(wfcs))
        cost_per_order_row.append(f"=IFERROR({sc}{cost_total_rn}/{sc}1,0)")

    all_rows.extend([blank, blank, cost_header, cost_fc, cost_total_row, cost_per_order_row])

    # Write
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=SID,
        range=f"'{OPS}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": all_rows},
    ).execute()

    print(f"Ops Summary: {len(all_rows)} rows x {total_cols} cols with formulas")
    print(f"  3-FC weeks: {sum(1 for w in weeks if fcs_for_week(w) == ERA_ALL)}")
    print(f"  2-FC weeks (RMFG+COG): {sum(1 for w in weeks if fcs_for_week(w) == ERA_JAN)}")
    print(f"  RMFG-only weeks: {sum(1 for w in weeks if fcs_for_week(w) == ERA_FEB)}")

    # ── 4. Format ─────────────────────────────────────────────────────
    from ops_summary_builder import _format_ops_summary
    _format_ops_summary(sheets_svc, num_weeks, len(all_rows))
    print("Formatting applied.")

    print("Done. Cost of Issues tab should be rebuilt after formulas compute.")


if __name__ == "__main__":
    main()
