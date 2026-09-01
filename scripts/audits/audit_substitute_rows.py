"""Sweep all Substitute-complaint rows: rotation-duplicate vs true substitution.

Kurt 2026-08-20 (ticket 288642988): rotation served duplicate trays across
orders — mislabeled Substitute complaint by the keyword inference
("change my selections" etc). New class added: Order::Rotation::Duplicate curation.

For every ops-sheet row whose Issue Type contains 'Substitute', fetch the
Gorgias ticket, scan subject + customer text:
  ROTATION_DUPE  — same/duplicate/repeat tray|curation|box, "again", "every order",
                   "already had/received/tried", "change my selections"
  TRUE_SUBSTITUTE — "substitut*", "replaced X", "instead of", "out of stock"
  NO_SIGNAL       — neither (image-only / unrelated)
Output CSV of proposed relabels; --commit appends the rotation class to col H
(replacing the Substitute label on ROTATION_DUPE rows only). Read-only otherwise.

Run: python scripts/audits/audit_substitute_rows.py [--commit]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AppyHourMCP"))
sys.path.insert(0, str(ROOT / "AppyHourMCP" / "tools"))

from appyhour_lib.credentials import get_google_credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from tools.gorgias_sheets_sync import _gorgias_auth, _gorgias_get  # noqa: E402

SID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
TAB = "UPDATE_Operational Issues"
ROTATION_CLASS = "Order::Rotation::Duplicate curation"
OUT = ROOT.parent / "_outputs" / "reports" / "2026-08-20-substitute-rows-audit.csv"

ROT = re.compile(r"same (tray|trays|curation|box|items|selections)|duplicate|repeat(ed)? (tray|box|curation|item)"
                 r"|again last|every order|already (had|received|tried|got)|change my selections?"
                 r"|different selections?|try something different|same .{0,20}(again|every)", re.I)
SUB = re.compile(r"substitut|replaced .{0,30}with|instead of|swap(ped)? .{0,20}for|out of stock", re.I)


def customer_text(t: dict) -> str:
    parts = [t.get("subject") or ""]
    for m in (t.get("messages") or [])[:5]:
        if not m.get("from_agent", True):
            parts.append(m.get("body_text") or m.get("stripped_text") or "")
    return "\n".join(parts)[:2000]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    rows = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A1:J").execute().get("values", [])
    targets = []
    for i, r in enumerate(rows):
        if i == 0:
            continue
        while len(r) < 10:
            r.append("")
        if "substitute" in r[7].lower():
            m = re.search(r"/(\d+)\s*$", r[3].strip())
            if m:
                targets.append((i + 1, r[3].strip(), int(m.group(1)), r[7]))
    print(f"substitute rows: {len(targets)}")

    auth, base = _gorgias_auth()
    out = []
    for rowno, link, gid, cur_issue in targets:
        verdict, ev = "NO_SIGNAL", ""
        try:
            resp = _gorgias_get(f"{base}/tickets/{gid}", auth=auth)
            if resp.status_code == 404:
                verdict, ev = "TICKET_GONE", "404"
            elif resp.status_code == 200:
                txt = customer_text(resp.json())
                rot, sub = ROT.search(txt), SUB.search(txt)
                if rot and not sub:
                    verdict, ev = "ROTATION_DUPE", rot.group(0)[:40]
                elif sub and not rot:
                    verdict, ev = "TRUE_SUBSTITUTE", sub.group(0)[:40]
                elif rot and sub:
                    verdict, ev = "AMBIGUOUS_BOTH", f"{rot.group(0)[:20]} / {sub.group(0)[:20]}"
                else:
                    ev = txt.replace("\n", " ")[:60]
            else:
                verdict, ev = "FETCH_ERR", str(resp.status_code)
        except Exception as e:
            verdict, ev = "FETCH_ERR", type(e).__name__
        out.append({"row": rowno, "gid": gid, "current": cur_issue[:60], "verdict": verdict, "evidence": ev})
        time.sleep(0.25)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print("verdicts:", dict(Counter(x["verdict"] for x in out).most_common()))
    print("wrote", OUT)

    if args.commit:
        data = []
        for x in out:
            if x["verdict"] != "ROTATION_DUPE":
                continue
            cur = svc.spreadsheets().values().get(
                spreadsheetId=SID, range=f"'{TAB}'!H{x['row']}").execute().get("values", [[""]])[0][0]
            newv = re.sub(r"Order::Substitute complaint", ROTATION_CLASS, cur)
            if ROTATION_CLASS not in cur:
                data.append({"range": f"'{TAB}'!H{x['row']}", "values": [[newv]]})
        for j in range(0, len(data), 100):
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data[j:j+100]}).execute()
        print(f"COMMITTED {len(data)} relabels -> {ROTATION_CLASS}")


if __name__ == "__main__":
    main()
