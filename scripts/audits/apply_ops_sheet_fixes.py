"""Apply Kurt-approved fixes (2026-07-25) to UPDATE_Operational Issues.

Approved scope (Kurt: "Go for all"):
  1. Fill recovered order#s into BLANK col-C cells (from audit CSV proposed_order)
  2. Append `Arrived Warm` to Delayed-labeled rows whose customer text says warm
  3. Append the Slack-diagnosed issue on CORRECT_PER_SLACK rows (addendum CSV)
  4. Delete duplicate-gorgias-link rows (keep first occurrence)

Safety:
  * every write keys on gorgias_link (stable), NEVER stale row numbers
  * drift guards: order fill only into blank C; warm-append only if col H has
    Delayed and lacks Arrived Warm; correction only if class absent
  * --dry-run (default) prints the exact plan, writes nothing
  * dedup recomputed live at run time, deletes descending

Run:
  python scripts/audits/apply_ops_sheet_fixes.py            # plan only
  python scripts/audits/apply_ops_sheet_fixes.py --commit   # execute + verify
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appyhour_lib.credentials import get_google_credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

SID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
GID = 1341858722
TAB = "UPDATE_Operational Issues"
WARM = "Shipping::Damaged in transit::Arrived Warm"
AUDIT = ROOT.parent / "_outputs" / "reports" / "2026-07-25-ops-sheet-unfillable-audit.csv"
ADDENDUM = ROOT.parent / "_outputs" / "reports" / "2026-07-25-audit-slack-addendum.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write; default is dry-run")
    args = ap.parse_args()

    creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    rows = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A1:J").execute().get("values", [])
    link_row: dict[str, tuple[int, list]] = {}
    for i, r in enumerate(rows):
        if i == 0:
            continue
        while len(r) < 10:
            r.append("")
        if r[3].strip():
            link_row.setdefault(r[3].strip(), (i + 1, r))

    audit = list(csv.DictReader(open(AUDIT, encoding="utf-8-sig")))
    addendum = list(csv.DictReader(open(ADDENDUM, encoding="utf-8-sig")))
    audit_by_row = {a["sheet_row"]: a for a in audit}

    data, plan = [], []
    counts = {"order_fill": 0, "warm_append": 0, "slack_correct": 0, "skipped_drift": 0}

    # 1) order# fills — only into blank col C
    for a in audit:
        po = a["proposed_order"].strip()
        if not po or po == "MISSING":
            continue
        hit = link_row.get(a["gorgias_link"].strip())
        if hit and not hit[1][2].strip():
            val = po if po.startswith("#") else "#" + po
            data.append({"range": f"'{TAB}'!C{hit[0]}", "values": [[val]]})
            plan.append(f"C{hit[0]} <- {val} (order fill)")
            counts["order_fill"] += 1
        else:
            counts["skipped_drift"] += 1

    # 2) Delayed-but-warm — append Arrived Warm
    for a in audit:
        if not a["verdict"].startswith("SHEET_VS_TEXT_MISMATCH (sheet=Delayed, text=Warm"):
            continue
        hit = link_row.get(a["gorgias_link"].strip())
        if hit and "Delayed" in hit[1][7] and "Arrived Warm" not in hit[1][7]:
            newv = hit[1][7].strip() + ", " + WARM
            data.append({"range": f"'{TAB}'!H{hit[0]}", "values": [[newv]]})
            plan.append(f"H{hit[0]} += Arrived Warm (delayed-but-warm)")
            counts["warm_append"] += 1
        else:
            counts["skipped_drift"] += 1

    # 3) Slack corrections — append slack issue class if absent
    for x in addendum:
        if x["action"] != "CORRECT_PER_SLACK":
            continue
        a = audit_by_row.get(x["sheet_row"])
        hit = link_row.get(a["gorgias_link"].strip()) if a else None
        if not hit:
            counts["skipped_drift"] += 1
            continue
        cur = hit[1][7].strip()
        key = x["slack_issue"].split("::")[1][:6].lower()
        if key not in cur.lower():
            newv = (cur + ", " if cur else "") + x["slack_issue"]
            data.append({"range": f"'{TAB}'!H{hit[0]}", "values": [[newv]]})
            plan.append(f"H{hit[0]} += {x['slack_issue']} (slack correction)")
            counts["slack_correct"] += 1
        else:
            counts["skipped_drift"] += 1

    # 4) dedup plan — recomputed live
    bylink = defaultdict(list)
    for i, r in enumerate(rows):
        if i == 0:
            continue
        if len(r) > 3 and r[3].strip():
            bylink[r[3].strip()].append(i + 1)
    dels = sorted([idx for v in bylink.values() if len(v) > 1 for idx in v[1:]], reverse=True)

    print(f"PLAN: {counts} | cell writes: {len(data)} | dup rows to delete: {len(dels)} -> {dels}")
    for p in plan:
        print("  ", p)
    if not args.commit:
        print("[dry-run] nothing written. Re-run with --commit.")
        return

    if data:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
    pre = len(rows)
    if dels:
        reqs = [
            {"deleteDimension": {"range": {"sheetId": GID, "dimension": "ROWS", "startIndex": d - 1, "endIndex": d}}}
            for d in dels
        ]
        svc.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": reqs}).execute()
    post = len(svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A:A").execute().get("values", []))
    print(f"COMMITTED: {len(data)} cells written | rows {pre} -> {post} (deleted {pre - post})")


if __name__ == "__main__":
    main()
