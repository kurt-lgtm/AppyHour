"""Push the weekly reship report to a Google Sheet — ONE TAB PER WEEK.

Headless service-account (no interactive OAuth) so the scheduled task can run it.
First run CREATES the dedicated sheet + persists its id; thereafter it REUSES the
sheet and adds/refreshes the tab for that week. Re-running the same week overwrites
that week's tab only (idempotent) — other weeks' tabs are never touched.

Tab name = the ship-week Monday, e.g. "2026-06-29".
"""
from __future__ import annotations

import os
from datetime import datetime

import gspread

from appyhour_lib.credentials import get_google_credentials

SHARE_WITH = "kurt@elevatefoods.co"
_ID_CACHE = r"C:\Users\Work\Claude Projects\_outputs\cache\reship_sheet_id.txt"
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
           "https://www.googleapis.com/auth/drive"]

_HDR_BG = {"red": 0.12, "green": 0.23, "blue": 0.37}
_WHITE = {"red": 1, "green": 1, "blue": 1}


def _client(svc_json: str | None = None):
    """gspread client. An explicit svc_json path still wins; otherwise the
    canonical resolver (env inline JSON on App Platform, key file locally)."""
    if svc_json:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(svc_json, scopes=_SCOPES)
    else:
        creds = get_google_credentials(_SCOPES)
    return gspread.authorize(creds)


def _cached_id():
    try:
        return open(_ID_CACHE).read().strip() or None
    except OSError:
        return None


def _save_id(sid: str):
    os.makedirs(os.path.dirname(_ID_CACHE), exist_ok=True)
    with open(_ID_CACHE, "w") as f:
        f.write(sid)


def build_rows(week: str, denom: int, n_tickets: int, start_date: str, end_date: str,
               source: str, vendor_matrix: list[list], box_summary: list[list]) -> list[list]:
    """Assemble the tab's 2D rows: title + vendor×issue block + box-type block."""
    rows: list[list] = [
        [f"Weekly Reship Report — _SHIP_{week}"],
        [f"Tickets received {start_date}–{end_date}  |  denom {denom}  |  "
         f"{n_tickets} shipping tickets  |  source {source}  |  "
         f"generated {datetime.now():%Y-%m-%d %H:%M}"],
        ["Percent = count ÷ denominator (rate vs shipped volume), never share-of-issues."],
        [],
        ["CARRIER × ISSUE"],
    ]
    rows += vendor_matrix
    rows += [
        [],
        ["BOX TYPE OF RESHIPPED ORDERS  (MCUST=Medium Tray, LCUST=Large Tray, else Regular Box)"],
    ]
    rows += box_summary
    return rows


def _style(ss, ws, n_cols: int, header_rows: list[int]):
    def col(i):
        s = ""
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    last = col(max(n_cols, 1))
    # title bold
    ws.format(f"A1:{last}1", {"textFormat": {"bold": True, "fontSize": 12}})
    ws.format("A2:%s3" % last, {"textFormat": {"italic": True, "fontSize": 9}})
    # section-header rows: bold white on dark
    for r in header_rows:
        ws.format(f"A{r}:{last}{r}", {
            "textFormat": {"bold": True, "foregroundColor": _WHITE},
            "backgroundColor": _HDR_BG})


def push(week: str, rows: list[list], vendor_hdr_row: int, box_hdr_row: int,
         sheet_id: str | None = None, svc_json: str | None = None,
         share_with: str = SHARE_WITH) -> str:
    gc = _client(svc_json)
    sid = sheet_id or _cached_id()
    if sheet_id:                       # explicit id given -> persist it for next runs
        _save_id(sheet_id)
    if sid:
        ss = gc.open_by_key(sid)
    else:
        ss = gc.create("AppyHour — Weekly Reship Report (auto)")
        _save_id(ss.id)
        if share_with:
            ss.share(share_with, perm_type="user", role="writer", notify=False)
    title = week  # one tab per week, named by the Monday
    try:
        ws = ss.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=max(len(rows) + 5, 30),
                              cols=max((max(len(r) for r in rows) if rows else 8), 8))
    ws.update(rows, value_input_option="USER_ENTERED")
    n_cols = max(len(r) for r in rows) if rows else 8
    _style(ss, ws, n_cols, header_rows=[5, vendor_hdr_row, box_hdr_row])
    # newest week first: move this tab to the front
    try:
        ss.reorder_worksheets([ws] + [w for w in ss.worksheets() if w.id != ws.id])
    except Exception:
        pass
    # drop gspread's default empty Sheet1 if present
    try:
        ss.del_worksheet(ss.worksheet("Sheet1"))
    except Exception:
        pass
    return ss.url
