"""DATA_Weekly + Dashboard builder — replaces the drifted hardcoded-ref stack.

Constraints SSOT: AppyHourMCP/OPS_DASHBOARD_RULES.md (read BEFORE changing).
Single writer for tabs `DATA_Weekly` and `Dashboard`.

Reads:  UPDATE_Operational Issues (issues/resolutions, by receipt date col A)
        shipping.db fulfillments (denominators: _SHIP_<Mon> minus Reship tag)
        RESOLUTION_COSTS from ops_summary_builder (never forked)
Writes: DATA_Weekly (fixed schema, 12 weeks) + Dashboard (formulas over it)

Run:  python AppyHourMCP/tools/ops_dashboard_builder.py [--commit]
"""
from __future__ import annotations

import argparse
import io
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))          # tools/
sys.path.insert(0, str(HERE.parents[1]))      # AppyHourMCP/ (ops_summary_builder imports `utils` from here)
sys.path.insert(0, str(HERE.parents[2]))      # repo root

from googleapiclient.discovery import build  # noqa: E402
from appyhour_lib.credentials import get_google_credentials  # noqa: E402
from appyhour_lib.paths import db_path  # noqa: E402
from ops_summary_builder import RESOLUTION_COSTS  # noqa: E402

SID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
RAW_TAB = "UPDATE_Operational Issues"
DATA_TAB = "DATA_Weekly"
DASH_TAB = "Dashboard"
WEEKS = 12

# reships_requested comes from the RESHIP REPORT Raw Data (system of record,
# Shopify-verified) — NOT the Gorgias Resolution field, which counts intentions
# (denied/duplicate reships) and ran up to +63%% vs reality (2026-07-28 compare).
HEADERS = ["week_start", "orders_shipped", "issues_total", "warm", "delayed",
           "lost_misdeliv", "damaged", "undeliverable", "fulfillment_issues",
           "reships_requested", "credits", "refunds",
           "resolution_cost", "issue_rate", "reship_rate", "cost_per_order"]
RESHIP_SID = "1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU"
FULL_RESHIP_COST = 65.0  # cost model: every reship costed as full (partials not split in Raw Data)

# Per comma-separated component of col H, FIRST match wins (ordering matters:
# "Damaged in transit::Arrived Warm" contains "damaged" — warm must be checked
# first or every warm row double-counts as damaged; that bug shipped in the
# 2026-07-27 dry-run and was caught by the 46-damaged sanity check).
CLASS_PATTERNS = [
    ("warm", "arrived warm"),
    ("delayed", "delayed in transit"),
    ("lost_misdeliv", "lost in transit"),
    ("undeliverable", "cannot be"),
    ("damaged", "ice pack"),
    ("damaged", "damaged"),
    ("fulfillment_issues", "order::"),
]


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_date(s: str) -> date | None:
    m = re.match(r"(\d\d)/(\d\d)/(\d{4})", s or "")
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None


def resolution_bucket(res: str) -> str | None:
    r = (res or "").lower()
    if not r.strip():
        return None
    if "reship" in r:
        return None  # reships come from the reship report, not Gorgias resolutions
    if "credit" in r:
        return "credits"
    if "refund" in r:
        return "refunds"
    return None


def resolution_cost(res: str) -> float:
    res = (res or "").strip()
    if not res:
        return 0.0
    if res in RESOLUTION_COSTS:
        return RESOLUTION_COSTS[res]
    for k, v in RESOLUTION_COSTS.items():  # prefix match for annotated variants
        if res.startswith(k):
            return v
    return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    # Canonical resolver: inline JSON (App Platform) else the local key file.
    creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    raw = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{RAW_TAB}'!A2:J").execute().get("values", [])

    # Reship counts: reship-report Raw Data, deduped by order, bucketed by Requested week
    rrows = svc.spreadsheets().values().get(
        spreadsheetId=RESHIP_SID, range="'Raw Data'!A2:B"
    ).execute().get("values", [])
    reship_by_week: dict[str, set] = {}
    for rr in rrows:
        if len(rr) < 2 or not rr[1].strip():
            continue
        try:
            rd = date.fromisoformat(rr[1].strip())
        except ValueError:
            continue
        wk = monday(rd).isoformat()
        reship_by_week.setdefault(wk, set()).add(rr[0].strip() or f"anon{len(rrows)}")

    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    fresh = con.execute("SELECT MAX(fulfilled_at) FROM fulfillments").fetchone()[0] or ""
    stale = (datetime.now() - datetime.fromisoformat(fresh[:19])) > timedelta(days=3) if fresh else True

    this_mon = monday(date.today())
    week_starts = [this_mon - timedelta(weeks=k) for k in range(WEEKS - 1, -1, -1)]
    # Coverage floor: weeks fully before the raw tab's earliest receipt date have
    # no issue data — they must render BLANK, not zero (zero asserts a perfect
    # week; blank says "no data" — OPS_DASHBOARD_RULES no-fabrication).
    raw_dates = [d for d in (parse_date(r[0]) for r in raw if r) if d]
    coverage_start = min(raw_dates) if raw_dates else None
    out_rows = []
    for ws in week_starts:
        we = ws + timedelta(days=7)
        row = {h: 0 for h in HEADERS}
        row["week_start"] = ws.isoformat()
        res_seen: set = set()
        row["orders_shipped"] = con.execute(
            "SELECT COUNT(*) FROM fulfillments WHERE tags LIKE ? AND tags NOT LIKE '%Reship%'",
            (f"%_SHIP_{ws.isoformat()}%",),
        ).fetchone()[0]
        for r in raw:
            while len(r) < 10:
                r.append("")
            d = parse_date(r[0])
            if not d or not (ws <= d < we):
                continue
            issue = (r[7] or "").lower()
            if issue.strip():
                row["issues_total"] += 1
                seen = set()
                for part in issue.split(","):  # first matching class per component
                    part = part.strip()
                    for cls, pat in CLASS_PATTERNS:
                        if pat in part:
                            if cls not in seen:
                                row[cls] += 1
                                seen.add(cls)
                            break
            b = resolution_bucket(r[8])
            if b:
                # dedup by order#: one order with N tickets = ONE reship/credit
                # (metric definition 2026-07-09: full OR partial counts as 1 per
                # order). Rows with no order# can't dedup — counted per row.
                digits = re.sub(r"\D", "", r[2] or "")
                key = (b, digits) if digits else (b, f"row{id(r)}")
                if key not in res_seen:
                    res_seen.add(key)
                    row[b] += 1
                    row["resolution_cost"] += resolution_cost(r[8])
        row["reships_requested"] = len(reship_by_week.get(ws.isoformat(), set()))
        row["resolution_cost"] += row["reships_requested"] * FULL_RESHIP_COST
        shipped = row["orders_shipped"]
        if coverage_start and we <= coverage_start:
            # pre-coverage week: shipped count is real, everything else unknown
            for h in HEADERS[2:]:
                row[h] = ""
        else:
            row["issue_rate"] = round(row["issues_total"] / shipped, 4) if shipped else ""
            row["reship_rate"] = round(row["reships_requested"] / shipped, 4) if shipped else ""
            row["cost_per_order"] = round(row["resolution_cost"] / shipped, 4) if shipped else ""
            row["resolution_cost"] = round(row["resolution_cost"], 2)
        out_rows.append([row[h] for h in HEADERS])
    con.close()

    stamp = f"built {datetime.now():%Y-%m-%d %H:%M} · fulfillments max {fresh[:16]}"
    banner = ("STALE FEEDER — fulfillments >3d old, numbers withheld (OPS_DASHBOARD_RULES)" if stale else stamp)

    # Dashboard formula block — references DATA_Weekly fixed columns ONLY
    L = WEEKS + 1  # last data row in DATA_Weekly
    def col_range(c): return f"{DATA_TAB}!{c}2:{c}{L}"
    dash = [
        ["APPYHOUR DASHBOARD", "", banner],
        [],
        ["KPI", "wk-to-date", "prior wk", "12-wk sparkline"],
        ["Issue Rate", f"={DATA_TAB}!N{L}", f"={DATA_TAB}!N{L-1}", f"=SPARKLINE({col_range('N')})"],
        ["Reship Rate", f"={DATA_TAB}!O{L}", f"={DATA_TAB}!O{L-1}", f"=SPARKLINE({col_range('O')})"],
        ["Cost / Order", f"={DATA_TAB}!P{L}", f"={DATA_TAB}!P{L-1}", f"=SPARKLINE({col_range('P')})"],
        ["Resolution Cost $", f"={DATA_TAB}!M{L}", f"={DATA_TAB}!M{L-1}", f"=SPARKLINE({col_range('M')})"],
        ["Orders Shipped", f"={DATA_TAB}!B{L}", f"={DATA_TAB}!B{L-1}", f"=SPARKLINE({col_range('B')})"],
        [],
        ["ISSUES", "wk-to-date", "prior wk", "12-wk sparkline"],
        ["Arrived Warm", f"={DATA_TAB}!D{L}", f"={DATA_TAB}!D{L-1}", f"=SPARKLINE({col_range('D')})"],
        ["Delayed", f"={DATA_TAB}!E{L}", f"={DATA_TAB}!E{L-1}", f"=SPARKLINE({col_range('E')})"],
        ["Lost / Misdelivered", f"={DATA_TAB}!F{L}", f"={DATA_TAB}!F{L-1}", f"=SPARKLINE({col_range('F')})"],
        ["Damaged", f"={DATA_TAB}!G{L}", f"={DATA_TAB}!G{L-1}", f"=SPARKLINE({col_range('G')})"],
        ["Undeliverable", f"={DATA_TAB}!H{L}", f"={DATA_TAB}!H{L-1}", f"=SPARKLINE({col_range('H')})"],
        ["Fulfillment (Order::*)", f"={DATA_TAB}!I{L}", f"={DATA_TAB}!I{L-1}", f"=SPARKLINE({col_range('I')})"],
        [],
        ["RESOLUTIONS", "wk-to-date", "prior wk", "12-wk sparkline"],
        ["Reships (report)", f"={DATA_TAB}!J{L}", f"={DATA_TAB}!J{L-1}", f"=SPARKLINE({col_range('J')})"],
        ["Credits", f"={DATA_TAB}!K{L}", f"={DATA_TAB}!K{L-1}", f"=SPARKLINE({col_range('K')})"],
        ["Refunds", f"={DATA_TAB}!L{L}", f"={DATA_TAB}!L{L-1}", f"=SPARKLINE({col_range('L')})"],
    ]

    print(f"PLAN: DATA_Weekly {len(out_rows)} weeks | stale={stale}")
    for r in out_rows[-4:]:
        print("  ", r[:9], "…", r[13:])
    if not args.commit:
        print("[dry-run] nothing written.")
        return

    meta = svc.spreadsheets().get(spreadsheetId=SID).execute()
    titles = {s["properties"]["title"] for s in meta["sheets"]}
    if DATA_TAB not in titles:
        svc.spreadsheets().batchUpdate(spreadsheetId=SID, body={
            "requests": [{"addSheet": {"properties": {"title": DATA_TAB}}}]}).execute()
    svc.spreadsheets().values().clear(spreadsheetId=SID, range=f"'{DATA_TAB}'!A1:Z100").execute()
    svc.spreadsheets().values().update(
        spreadsheetId=SID, range=f"'{DATA_TAB}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [HEADERS] + ([] if stale else out_rows)},
    ).execute()
    svc.spreadsheets().values().clear(spreadsheetId=SID, range=f"'{DASH_TAB}'!A1:M45").execute()
    svc.spreadsheets().values().update(
        spreadsheetId=SID, range=f"'{DASH_TAB}'!A1",
        valueInputOption="USER_ENTERED", body={"values": dash},
    ).execute()
    # Number formats (re-applied every run so a rebuild can't lose them):
    # rows 4-5 rates -> percent, rows 6-7 dollars -> currency, row 8 -> integer.
    dash_gid = next(s["properties"]["sheetId"] for s in meta["sheets"]
                    if s["properties"]["title"] == DASH_TAB)
    def fmt(r1, r2, pattern, ftype):
        return {"repeatCell": {
            "range": {"sheetId": dash_gid, "startRowIndex": r1, "endRowIndex": r2,
                      "startColumnIndex": 1, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": ftype, "pattern": pattern}}},
            "fields": "userEnteredFormat.numberFormat"}}
    svc.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
        fmt(3, 5, "0.00%", "PERCENT"),
        fmt(5, 7, "$#,##0.00", "CURRENCY"),
        fmt(7, 8, "#,##0", "NUMBER"),
    ]}).execute()
    print("COMMITTED DATA_Weekly + Dashboard (+formats)")


if __name__ == "__main__":
    main()
