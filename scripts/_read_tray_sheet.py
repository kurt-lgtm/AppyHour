"""Read the shared tray-recipe Google Sheet (tab gid=461556308) to find speck
weight per board. Read-only dump of the tab."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from cut_order_server.app.creds import get_google_credentials_path  # noqa: E402

from google.oauth2.service_account import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

SHEET_ID = "1MHyIyWhDO8P1Y66dbmZXNXp9TWPyycUBP9O60h1G0Rs"
GID = 461556308

creds = Credentials.from_service_account_file(
    get_google_credentials_path(),
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
svc = build("sheets", "v4", credentials=creds)

meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
tab = None
for s in meta["sheets"]:
    p = s["properties"]
    if p["sheetId"] == GID:
        tab = p["title"]
print("ALL TABS:", [s["properties"]["title"] for s in meta["sheets"]])
print("TARGET TAB:", tab)

titles = [s["properties"]["title"] for s in meta["sheets"]]
res = svc.spreadsheets().values().batchGet(
    spreadsheetId=SHEET_ID, ranges=[f"'{t}'" for t in titles]
).execute()
for t, vr in zip(titles, res.get("valueRanges", [])):
    vals = vr.get("values", [])
    hits = [(i, row) for i, row in enumerate(vals)
            if any("speck" in str(c).lower() or "aab" in str(c).lower()
                   or ("oz" in str(c).lower() and any("speck" in str(x).lower() for x in row))
                   for c in row)]
    if hits:
        print(f"\n===== {t} ({len(vals)} rows) — speck/AAB hits =====")
        for i, row in hits[:25]:
            print(i, row)
# also dump RAWINVCONVERSIONS + FORECAST_TRAY headers fully
for t in ("RAWINVCONVERSIONS", "FORECAST_TRAY"):
    vr = next((v for tt, v in zip(titles, res["valueRanges"]) if tt == t), None)
    if vr:
        vals = vr.get("values", [])
        print(f"\n##### {t} (first 30 rows) #####")
        for i, row in enumerate(vals[:30]):
            print(i, row)
