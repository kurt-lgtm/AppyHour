"""Make col-C Order # cells clickable Shopify-admin links in UPDATE_Operational Issues.

Kurt 2026-07-27: "my order raw data should also be a clickable link."

For every row whose col C holds a plain #NNNNNN, look up the Shopify order_id in
the local fulfillments cache and rewrite the cell as
  =HYPERLINK("https://admin.shopify.com/store/504ac4/orders/<order_id>", "#NNNNNN")
Display text unchanged, so nothing downstream that reads values breaks
(sheets values.get returns the display string, and our own scripts regex the
digits out regardless).

Skips: rows already containing a formula/link, order#s not in the local cache
(no guessing — they linkify on a later run once the ingest has them).
--dry-run default.
"""
from __future__ import annotations

import argparse
import io
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appyhour_lib.credentials import get_google_credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from appyhour_lib.paths import db_path  # noqa: E402

SID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"
TAB = "UPDATE_Operational Issues"
STORE = "504ac4"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    # FORMULA render so existing =HYPERLINK cells are visible and skipped
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{TAB}'!C1:C", valueRenderOption="FORMULA"
    ).execute().get("values", [])

    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    data, n_skip_formula, n_nocache = [], 0, 0
    for i, r in enumerate(rows):
        if i == 0 or not r or not str(r[0]).strip():
            continue
        cell = str(r[0]).strip()
        if cell.startswith("="):
            n_skip_formula += 1
            continue
        digits = re.sub(r"\D", "", cell)
        if not digits:
            continue
        hit = con.execute(
            "SELECT order_id FROM fulfillments WHERE order_number=? LIMIT 1", (int(digits),)
        ).fetchone()
        if not hit or not hit[0]:
            n_nocache += 1
            continue
        formula = f'=HYPERLINK("https://admin.shopify.com/store/{STORE}/orders/{hit[0]}", "#{digits}")'
        data.append({"range": f"'{TAB}'!C{i+1}", "values": [[formula]]})
    con.close()

    print(f"PLAN: linkify {len(data)} cells | already-linked {n_skip_formula} | not-in-cache {n_nocache}")
    if not args.commit:
        print("[dry-run] nothing written.")
        return
    for j in range(0, len(data), 200):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data[j:j+200]}
        ).execute()
    print(f"COMMITTED {len(data)} cells")


if __name__ == "__main__":
    main()
