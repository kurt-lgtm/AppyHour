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


CARRIER_LABEL = "CARRIER × ISSUE"
BOX_LABEL = "BOX TYPE OF RESHIPPED ORDERS  (MCUST=Medium Tray, LCUST=Large Tray, else Regular Box)"


def build_rows(week: str, denom: int, n_tickets: int, start_date: str, end_date: str,
               source: str, vendor_matrix: list[list], box_summary: list[list],
               note: str | None = None, denom_basis: str | None = None,
               as_of: str | None = None, reships_removed: int | None = None) -> list[list]:
    """Assemble the tab's 2D rows: title + vendor×issue block + box-type block.

    `note` adds one italic line under the subtitle. Its job is RESTATEMENT PROVENANCE: when a
    week is republished with different numbers, someone may already have read the old ones, so
    the tab has to say on its face that it changed and why. Same reason D39 rule 5 emits
    `VM_RESTATED` rather than silently moving a cell — a silent restatement is indistinguishable
    from the reader having misremembered.
    """
    rows: list[list] = [
        [f"Weekly Reship Report — _SHIP_{week}"],
        [f"Tickets received {start_date}–{end_date}  |  denom {denom}  |  "
         f"{n_tickets} shipping tickets  |  source {source}  |  "
         f"generated {datetime.now():%Y-%m-%d %H:%M}"],
    ]
    if denom_basis:
        # 🔴 The denominator's PROVENANCE lives beside the denominator, in the tab. A cohort
        # keeps accruing reship tags after publication, so a reship-excluded figure is only
        # true as of an instant; a cell carrying its basis and its as_of is re-derivable, and a
        # later recompute that differs reads as expected drift rather than as an error.
        stamp = f"Denominator basis: {denom_basis}"
        if reships_removed is not None:
            stamp += f" ({reships_removed} reship fulfillments removed)"
        if as_of:
            stamp += f"  |  as_of {as_of}"
        rows.append([stamp])
    if note:
        rows.append([note])
    rows += [
        ["Percent = count ÷ denominator (rate vs shipped volume), never share-of-issues."],
        [],
        [CARRIER_LABEL],
    ]
    rows += vendor_matrix
    rows += [
        [],
        [BOX_LABEL],
    ]
    rows += box_summary
    return rows


def section_header_rows(rows: list[list]) -> tuple[int, int, int]:
    """1-indexed (carrier label row, vendor header row, box header row).

    🔴 DERIVED, never hand-counted. These were three literals in `sync.main` with a comment
    counting the preamble by hand ("title(1) sub(2) note(3) blank(4)…"), so adding a single
    line to the preamble silently painted the dark header band across a row of DATA. Anything
    that changes `build_rows` must not have to remember to update arithmetic somewhere else.
    """
    def find(label):
        for i, r in enumerate(rows):
            if r and str(r[0]).startswith(label[:20]):
                return i + 1
        raise ValueError(f"section label not found in rows: {label!r}")
    carrier = find(CARRIER_LABEL)
    return carrier, carrier + 1, find(BOX_LABEL) + 1


def _col_letter(i: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _a1_block(first_row: int, last_row: int, n_cols: int) -> str | None:
    """Full-width A1 range for rows [first_row, last_row], or None if empty."""
    if last_row < first_row:
        return None
    return f"A{first_row}:{_col_letter(max(n_cols, 1))}{last_row}"


def _a1_cols_right_of(first_col: int, last_col: int, n_rows: int) -> str | None:
    """A1 range for the columns right of the payload across rows 1..n_rows, or None."""
    if last_col < first_col or n_rows < 1:
        return None
    return f"{_col_letter(first_col)}1:{_col_letter(last_col)}{n_rows}"


def _style(ss, ws, n_cols: int, header_rows: list[int]):
    last = _col_letter(max(n_cols, 1))
    # title bold
    ws.format(f"A1:{last}1", {"textFormat": {"bold": True, "fontSize": 12}})
    ws.format("A2:%s3" % last, {"textFormat": {"italic": True, "fontSize": 9}})
    # section-header rows: bold white on dark
    for r in header_rows:
        ws.format(f"A{r}:{last}{r}", {
            "textFormat": {"bold": True, "foregroundColor": _WHITE},
            "backgroundColor": _HDR_BG})


def push(week: str, rows: list[list], vendor_hdr_row: int | None = None,
         box_hdr_row: int | None = None,
         sheet_id: str | None = None, svc_json: str | None = None,
         share_with: str = SHARE_WITH) -> str:
    # header rows are DERIVED from the grid; the params remain only so an explicit override is
    # still possible, and are no longer computed by hand at the call site.
    carrier_label_row, derived_vendor, derived_box = section_header_rows(rows)
    vendor_hdr_row = vendor_hdr_row or derived_vendor
    box_hdr_row = box_hdr_row or derived_box
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
    n_cols = max(len(r) for r in rows) if rows else 8
    try:
        ws = ss.worksheet(title)
        # grow only — never shrink, `update` fails on a grid smaller than the payload
        if ws.row_count < len(rows) or ws.col_count < n_cols:
            ws.resize(rows=max(ws.row_count, len(rows) + 5),
                      cols=max(ws.col_count, n_cols))
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=max(len(rows) + 5, 30),
                              cols=max(n_cols, 8))
    # 🔴 WRITE FIRST, THEN CLEAR THE RESIDUE — never `clear()` before `update()`.
    # These are two separate API calls. Clearing first opens a window in which a refused
    # `update` (quota, 5xx, a dropped connection) leaves the week's tab EMPTY: the previous
    # good numbers are gone and nothing says so, on a re-run that was supposed to be
    # idempotent. Writing first means the worst case is a tab carrying the NEW numbers plus a
    # few stale trailing rows — visibly wrong instead of invisibly blank.
    ws.update(rows, value_input_option="USER_ENTERED")
    residue = [r for r in (_a1_block(len(rows) + 1, ws.row_count, n_cols),
                           _a1_cols_right_of(n_cols + 1, ws.col_count, len(rows)))
               if r]
    if residue:
        ws.batch_clear(residue)
    _style(ss, ws, n_cols, header_rows=sorted({carrier_label_row, vendor_hdr_row, box_hdr_row}))
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
