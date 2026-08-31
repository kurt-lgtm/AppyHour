"""Durable HISTORY for the weekly carrier×issue vendor matrix — ledger + one pivot tab.

Constraints SSOT: AppyHour/ShippingReports/RESHIP_REPORT_RULES.md **D39**. Read it first.

WHY THIS EXISTS (2026-08-31): the `weekly-shipping-vendor-matrix` routine's Slack DM was the
ONLY record of any week's matrix. Slack scrollback is not a queryable history — nothing to trend,
and the record is gone the day retention bites. The DM still posts every week (it is a REPORT Kurt
reads, not a monitor — it is deliberately NOT exception-only); this module adds the durable copy
next to it.

SHAPE — one tab, ship weeks as COLUMNS, repainted whole from a ledger. Deliberately the same shape
as its neighbour `Carrier Mix` (D35c) in the same spreadsheet:
  * the LEDGER (`_outputs/reports/vendor_matrix_ledger.json`) is the MEMORY; the tab is a VIEW.
    Do NOT add per-cell write-once to the tab — that duplicates the ledger's job in a second store
    and the two will disagree.
  * weeks as columns is what makes the thing trendable at a glance. A tab-per-week would add ~52
    tabs/yr to a spreadsheet that already carries 23, and would force cross-tab reading to answer
    "is FedEx delayed getting worse".

🔴 NEVER a rate over a zero denominator. `denom == 0` means the cohort tag is wrong, and this
module REFUSES to touch either the ledger or the sheet — a bogus 0-denom percentage is worse than
no number (same gate the DM has carried since 2026-08-28).
🔴 Additive only: this module owns exactly ONE tab and refuses to overwrite a tab it did not
paint (`_foreign_tab`). It never reads, writes or reorders any other tab — `_exc_state` in
particular is the Exceptions sweep's durable state and clobbering it loses every open box.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHEET_ID = "1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU"   # "Running Reship Report"
SHEET_TAB = "Vendor Matrix"
SHEET_TITLE = "Vendor Matrix — carrier×issue by ship week, weeks as columns (D39)"
SHEET_CREDS = Path(__file__).resolve().parents[2] / "shipping-perfomance-review-accd39ac4b78.json"
LEDGER = Path(r"C:\Users\Work\Claude Projects\_outputs\reports\vendor_matrix_ledger.json")
ET = ZoneInfo("America/New_York")   # 🔴 every human-read timestamp is Eastern; UTC stays in code.

DENOM_ROW = "Denominator (cohort shipped)"
TICKETS_ROW = "Tickets in window"
WINDOW_ROW = "Ticket window (receipt date)"
GRAND_ROW = "All vendors — Total"


class VendorMatrixError(RuntimeError):
    """Loud failure — never degrade to a partial or fabricated table."""


def pct(n: int, denom: int) -> str:
    """🔴 Only ever called with a proven-positive denom (see `upsert`); the guard is belt-and-braces."""
    if not denom:
        raise VendorMatrixError("VM_ZERO_DENOM: refusing to render a rate over a zero denominator")
    return f"{100.0 * n / denom:.2f}%"


# ── Ledger ───────────────────────────────────────────────────────────────────
def load_ledger(path: Path = LEDGER) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"_schema": 1, "weeks": {}}


def save_ledger(led: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(led, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def upsert(led: dict, week: str, denom: int, n_tickets: int, start: str, end: str,
           source: str, issues: list[str], counts: dict[str, dict[str, int]],
           dropped: int = 0) -> list[str]:
    """Record one week. Returns run notes (each one lands in the tab, visible, never silent).

    🔴 A prior week CAN legitimately move — tickets get posted to #reship late, and a ship week is
    multi-leg so its denominator grows when the Tuesday Dallas leg lands. So this is an upsert, not
    a freeze. What is NOT allowed is a SILENT restatement: any change to a week already on record
    is named in the tab's notes and appended to that week's log.
    """
    if denom <= 0:
        raise VendorMatrixError(
            f"VM_ZERO_DENOM: _SHIP_{week} counted {denom} shipped boxes — the cohort tag looks "
            "wrong. Refusing to write the ledger or the sheet; report the bad tag instead.")
    notes: list[str] = []
    now = datetime.now(ET).isoformat(timespec="seconds")
    prev = led["weeks"].get(week)
    entry = {"denom": denom, "tickets": n_tickets, "window": [start, end], "source": source,
             "issues": list(issues), "counts": counts, "updated_at": now,
             "log": (prev or {}).get("log", [])}
    if prev:
        moved = [k for k in ("denom", "tickets", "counts")
                 if prev.get(k) != entry[k]]
        if moved:
            notes.append(f"VM_RESTATED: {week} changed on re-run — {', '.join(moved)} "
                         f"(denom {prev.get('denom')}→{denom}, tickets {prev.get('tickets')}"
                         f"→{n_tickets}); a later leg or a late-posted ticket, not a fix")
            entry["log"] = (entry["log"] + [{"at": now, "restated": moved,
                                             "from": {"denom": prev.get("denom"),
                                                      "tickets": prev.get("tickets")}}])[-20:]
    if dropped:
        notes.append(f"VM_UNCLASSIFIED: {week} had {dropped} ticket(s) whose issue is outside "
                     f"{issues} — counted in neither the tab nor the DM matrix")
    led["weeks"][week] = entry
    return notes


# ── Rendering ────────────────────────────────────────────────────────────────
def _vendors(led: dict, order: list[str]) -> list[str]:
    seen = {v for w in led["weeks"].values() for v in w["counts"]}
    return [v for v in order if v in seen] + sorted(v for v in seen if v not in order)


def _issues(led: dict, order: list[str]) -> list[str]:
    seen = {i for w in led["weeks"].values() for i in w["issues"]}
    return [i for i in order if i in seen] + sorted(i for i in seen if i not in order)


def grid(led: dict, vendor_order: list[str], issue_order: list[str]) -> list[list[str]]:
    """The pivot as a plain 2-D grid of cell STRINGS — header first, label in column 0.

    🔴 The ONE place ledger state becomes cell text, so the tab and any future renderer cannot
    drift into showing two different tables (the D35c lesson).
    🔴 A `0` here is a MEASURED zero (that week ran, that vendor had no such ticket). `—` means the
    week has no ledger entry for that cell at all. Blank ≠ zero; never coerce one into the other.
    """
    weeks = sorted(led["weeks"])
    if not weeks:
        raise VendorMatrixError("VM_EMPTY_LEDGER: nothing to paint")
    vendors, issues = _vendors(led, vendor_order), _issues(led, issue_order)
    W = [led["weeks"][w] for w in weeks]
    rows = [["Row"] + weeks,
            [DENOM_ROW] + [str(e["denom"]) for e in W],
            [TICKETS_ROW] + [str(e["tickets"]) for e in W],
            [WINDOW_ROW] + [f"{e['window'][0]}–{e['window'][1]}" for e in W],
            [""]]
    for v in vendors:
        for i in issues:
            rows.append([f"{v} · {i}"] + [_cell(e, v, i) for e in W])
        rows.append([f"{v} — Total"] + [_vendor_total(e, v) for e in W])
        rows.append([""])
    # per-issue % denom — the bottom row of the DM matrix, kept verbatim because Kurt reads it.
    for i in issues:
        rows.append([f"% denom · {i}"] + [_issue_total(e, i) for e in W])
    rows.append([GRAND_ROW] + [_grand_total(e) for e in W])
    return rows


def _cell(e: dict, vendor: str, issue: str) -> str:
    if issue not in e["issues"]:
        return "—"                                  # issue not tracked that week
    return str(e["counts"].get(vendor, {}).get(issue, 0))


def _vendor_total(e: dict, vendor: str) -> str:
    n = sum(e["counts"].get(vendor, {}).get(i, 0) for i in e["issues"])
    return f"{n} ({pct(n, e['denom'])})"


def _issue_total(e: dict, issue: str) -> str:
    if issue not in e["issues"]:
        return "—"
    n = sum(c.get(issue, 0) for c in e["counts"].values())
    return f"{n} ({pct(n, e['denom'])})"


def _grand_total(e: dict) -> str:
    n = sum(c.get(i, 0) for c in e["counts"].values() for i in e["issues"])
    return f"{n} ({pct(n, e['denom'])})"


# ── Sheet view ───────────────────────────────────────────────────────────────
def _foreign_tab(a1_value, tab_is_empty: bool) -> bool:
    """True when an existing `Vendor Matrix` tab is NOT ours to repaint.

    Ours = A1 carries SHEET_TITLE, or the tab is completely empty (a crash between the clear and
    the batch leaves exactly that, and must stay repaintable rather than becoming a wall).
    Anything else belongs to somebody — 🔴 REFUSE. Pure, so --self-test exercises it offline.
    """
    if tab_is_empty:
        return False
    return str(a1_value or "").strip() != SHEET_TITLE


def write_sheet(led: dict, vendor_order: list[str], issue_order: list[str],
                notes: list[str], sheet_id: str = SHEET_ID, tab: str = SHEET_TAB) -> int:
    """Repaint the tab from `grid()`. Full repaint; the ET stamp is a SEPARATE final write, so a
    missing stamp row means the paint died partway and must be rerun.

    🔴 valueInputOption=RAW everywhere: `0.55%` must land as literal text, not be coerced to a
    number by Sheets, and `—` must stay `—`.
    """
    from google.oauth2.service_account import Credentials  # type: ignore[reportMissingImports]  # noqa: PLC0415
    from googleapiclient.discovery import build  # type: ignore[reportMissingImports]  # noqa: PLC0415

    if not SHEET_CREDS.exists():
        raise VendorMatrixError(f"VM_SHEET_NO_CREDS: {SHEET_CREDS} not found")
    creds = Credentials.from_service_account_file(
        str(SHEET_CREDS), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id,
                                  fields="properties.title,sheets.properties.title").execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    print(f"  sheet: {meta['properties']['title']!r} ({sheet_id}) · {len(tabs)} tabs")
    vals = svc.spreadsheets().values()
    if tab not in tabs:
        svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
            {"addSheet": {"properties": {"title": tab}}}]}).execute()
        print(f"  created tab {tab!r}")
    else:
        got = vals.get(spreadsheetId=sheet_id, range=f"'{tab}'!A1:B2",
                       valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
        if _foreign_tab(got[0][0] if got and got[0] else "", not got):
            raise VendorMatrixError(
                f"VM_SHEET_FOREIGN_TAB: a tab named {tab!r} exists and its A1 is not this tool's "
                "marker — refusing to overwrite a tab this tool did not paint.")

    g = grid(led, vendor_order, issue_order)
    block = [[SHEET_TITLE], [""]] + g + [[""]] + [[
        "Rules SSOT: AppyHour/ShippingReports/RESHIP_REPORT_RULES.md D39 · the ledger "
        "(_outputs/reports/vendor_matrix_ledger.json) is the MEMORY, this tab is a VIEW repainted "
        "whole each run · counts come from Slack #reship-and-order-requests joined to fulfillments "
        "for carrier, NEVER feedback.issue_type · % denom = count ÷ shipped cohort, never "
        "share-of-issues · '0' is a measured zero, '—' means not tracked that week · if the 'Last "
        "refreshed' row below the notes is missing, the paint is INCOMPLETE — rerun."],
        ["Provenance: weeks 2026-06-22 … 2026-08-17 were RE-DERIVED on 2026-08-31 from the "
         "archived Slack fixtures in AppyHour/ingest/slack_reship/fixtures/ by the same tool — "
         "they are not transcriptions of the DM sent at the time, so a cell may differ from that "
         "week's DM if a fulfillments carrier join has changed since. Weeks from 2026-08-24 "
         "onward are written by the routine's own run."]] \
        + [[n] for n in ([f"- {n}" for n in notes] or ["- run notes: none"])] + [[""]]
    vals.clear(spreadsheetId=sheet_id, range=f"'{tab}'").execute()
    vals.update(spreadsheetId=sheet_id, range=f"'{tab}'!A1", valueInputOption="RAW",
                body={"values": block}).execute()
    back = vals.get(spreadsheetId=sheet_id, range=f"'{tab}'!A3:Z3",
                    valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[]])[0]
    if [str(v) for v in back[:len(g[0])]] != g[0]:
        raise VendorMatrixError(
            f"VM_SHEET_READBACK: header row read back {back!r}, expected {g[0]!r} — stamp NOT "
            "written; the tab is marked incomplete by its absence.")
    stamp_row = len(block) + 1
    vals.update(spreadsheetId=sheet_id, range=f"'{tab}'!A{stamp_row}", valueInputOption="RAW",
                body={"values": [[f"Last refreshed: {datetime.now(ET):%Y-%m-%d %I:%M %p} ET "
                                  "— paint complete"]]}).execute()
    print(f"  tab {tab!r} repainted: {len(g)} table rows, {len(notes)} note(s), "
          f"stamp at A{stamp_row}")
    return stamp_row


# ── Self-test (offline; no network, no creds) ────────────────────────────────
def self_test() -> int:
    led = {"_schema": 1, "weeks": {}}
    issues = ["Delayed", "Warm"]
    notes = upsert(led, "2026-08-17", 2366, 3, "2026-08-17", "2026-08-23", "DUMP", issues,
                   {"FedEx": {"Delayed": 2, "Warm": 0}, "OnTrac": {"Delayed": 0, "Warm": 1}})
    assert notes == [], notes
    g = grid(led, ["Veho", "FedEx", "OnTrac", "UPS"], ["Delayed", "Warm", "Lost"])
    assert g[0] == ["Row", "2026-08-17"], g[0]
    assert ["FedEx · Delayed", "2"] in g and ["OnTrac · Warm", "1"] in g, g
    assert ["FedEx — Total", f"2 ({pct(2, 2366)})"] in g, g
    assert ["% denom · Delayed", f"2 ({pct(2, 2366)})"] in g, g
    assert [GRAND_ROW, f"3 ({pct(3, 2366)})"] in g, g
    assert not any(r[0].startswith("Veho") or r[0].startswith("UPS") for r in g), "absent vendors"
    assert not any(r[0].endswith("· Lost") for r in g), "untracked issue must not appear"
    # restatement is named, never silent
    notes = upsert(led, "2026-08-17", 2400, 4, "2026-08-17", "2026-08-23", "DUMP", issues,
                   {"FedEx": {"Delayed": 3, "Warm": 0}, "OnTrac": {"Delayed": 0, "Warm": 1}})
    assert notes and notes[0].startswith("VM_RESTATED"), notes
    assert led["weeks"]["2026-08-17"]["log"], "restatement must be logged"
    # zero denom refuses, both halves
    for bad in (0, -1):
        try:
            upsert(led, "2026-08-24", bad, 1, "a", "b", "DUMP", issues, {})
        except VendorMatrixError as e:
            assert "VM_ZERO_DENOM" in str(e)
        else:
            raise AssertionError("zero denom must refuse")
    assert "2026-08-24" not in led["weeks"], "a refused week must not land in the ledger"
    # foreign-tab guard
    assert _foreign_tab("", True) is False
    assert _foreign_tab(SHEET_TITLE, False) is False
    assert _foreign_tab("Exceptions sweep state", False) is True
    print("self-test: OK (10 assertions)")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit("usage: python -m ingest.slack_reship.matrix_history --self-test\n"
                     "(the weekly write runs via `sync.py --report --history-sheet`)")
