"""Fill blank Carrier (E) / Destination State (F) in UPDATE_Operational Issues
for rows that HAVE an order# — from the local fulfillments table (an order in
fulfillments = fulfilled, with tracking). No Shopify API.

Kurt rule (2026-07-26): "if there's an order number that's fulfilled, then it
needs a carrier and destination state."

Safety: keys writes to the row read in the SAME pass (fresh indices), only
writes BLANK cells, --dry-run default.

Run:  python scripts/audits/fill_carrier_state.py [--commit]
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

STATE_FULL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def carrier_canon(tc: str) -> str:
    u = (tc or "").upper()
    if u.startswith("FEDEX") or u.startswith("FED EX"):
        return "FedEx"
    if u.startswith("ONTRAC") or "LASER" in u:
        return "OnTrac"
    if u.startswith("VEHO"):
        return "veho"  # matches existing sheet vocabulary
    if u.startswith("UPS"):
        return "UPS"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    rows = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A1:J").execute().get("values", [])

    data, plan, unfulfilled = [], [], 0
    for i, r in enumerate(rows):
        if i == 0:
            continue
        while len(r) < 10:
            r.append("")
        digits = re.sub(r"\D", "", r[2])
        if not digits:
            continue
        need_carrier = not r[4].strip()
        need_state = not r[5].strip()
        if not (need_carrier or need_state):
            continue
        hit = con.execute(
            "SELECT tracking_company, dest_state FROM fulfillments WHERE order_number=? LIMIT 1",
            (int(digits),),
        ).fetchone()
        if not hit or not (hit[0] or "").strip():
            unfulfilled += 1  # not fulfilled yet (or pre-cache) — correctly left blank
            continue
        car, st2 = carrier_canon(hit[0]), (hit[1] or "").strip().upper()
        if need_carrier and car:
            data.append({"range": f"'{TAB}'!E{i+1}", "values": [[car]]})
            plan.append(f"E{i+1} <- {car} (#{digits})")
        if need_state and st2 in STATE_FULL:
            data.append({"range": f"'{TAB}'!F{i+1}", "values": [[STATE_FULL[st2]]]})
            plan.append(f"F{i+1} <- {STATE_FULL[st2]} (#{digits})")
    con.close()

    print(f"PLAN: {len(data)} cell fills | rows skipped as not-fulfilled/pre-cache: {unfulfilled}")
    for p in plan[:50]:
        print("  ", p)
    if len(plan) > 50:
        print(f"   ... +{len(plan)-50} more")
    if not args.commit:
        print("[dry-run] nothing written. Re-run with --commit.")
        return
    if data:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
    print(f"COMMITTED {len(data)} cells")


if __name__ == "__main__":
    main()
