"""Final audit closers (Kurt-approved 2026-07-27) for UPDATE_Operational Issues.

1. Apply the 35 Slack-resolved classifications from the thread trawl
   (2026-07-25-audit-thread-trawl.csv, proposed_issue != MISSING): append the
   Slack-diagnosed issue to col H when its class is absent. EXCLUDE rows are
   skipped (need human eyes, not an auto-write).
2. Mark permanently-unfillable rows (proposed_issue == MISSING in the trawl —
   no ticket text, no Slack post) with NO_SOURCE_SIGNAL in the Comment col (J)
   so the weekly enrich skips them (guard added to gorgias_sheets_sync 07-27).

Safety: keys on gorgias_link (row numbers shifted after the 51 deletions);
only appends/marks, never overwrites; --dry-run default.

Run:  python scripts/audits/apply_trawl_and_mark_nosignal.py [--commit]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appyhour_lib.credentials import get_google_credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

SID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
TAB = "UPDATE_Operational Issues"
REPORTS = ROOT.parent / "_outputs" / "reports"
TRAWL = REPORTS / "2026-07-25-audit-thread-trawl.csv"
AUDIT = REPORTS / "2026-07-25-ops-sheet-unfillable-audit.csv"
MARK = "NO_SOURCE_SIGNAL (audit 2026-07-25: no ticket text, no Slack post — do not re-enrich)"


def cls_key(issue: str) -> str:
    parts = (issue or "").split("::")
    return parts[1][:6].lower() if len(parts) > 1 else (issue or "").lower()[:6]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    # sheet_row (pre-shift) -> gorgias_link, from the audit CSV
    row_link = {a["sheet_row"]: a["gorgias_link"].strip()
                for a in csv.DictReader(open(AUDIT, encoding="utf-8-sig"))}
    trawl = list(csv.DictReader(open(TRAWL, encoding="utf-8-sig")))

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

    data, plan = [], []
    n_class = n_mark = n_skip = 0
    for t in trawl:
        link = row_link.get(t["sheet_row"], "")
        hit = link_row.get(link)
        if not hit:
            n_skip += 1
            continue
        rowno, r = hit
        pi = t["proposed_issue"].strip()
        if pi and pi not in ("MISSING", "EXCLUDE"):
            cur = r[7].strip()
            if cls_key(pi) not in cur.lower():
                newv = (cur + ", " if cur else "") + pi
                data.append({"range": f"'{TAB}'!H{rowno}", "values": [[newv]]})
                plan.append(f"H{rowno} += {pi} (slack thread trawl)")
                n_class += 1
        elif pi == "MISSING":
            cur = r[9].strip()
            if "NO_SOURCE_SIGNAL" not in cur:
                newv = (cur + " | " if cur else "") + MARK
                data.append({"range": f"'{TAB}'!J{rowno}", "values": [[newv]]})
                plan.append(f"J{rowno} <- NO_SOURCE_SIGNAL mark")
                n_mark += 1

    print(f"PLAN: classifications={n_class} marks={n_mark} unmatched-rows-skipped={n_skip} | cells={len(data)}")
    for p in plan[:30]:
        print("  ", p)
    if len(plan) > 30:
        print(f"   ... +{len(plan)-30} more")
    if not args.commit:
        print("[dry-run] nothing written.")
        return
    # batch in chunks of 100 ranges
    for i in range(0, len(data), 100):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data[i:i+100]}
        ).execute()
    print(f"COMMITTED {len(data)} cells")


if __name__ == "__main__":
    main()
