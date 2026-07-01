"""Weekly shipping vendor x issue matrix from Slack #reship-and-order-requests.

CANONICAL weekly tool. Deterministic: same week + same denom -> same table.
Encodes the two GATE rules from ~/.knowledge/ops/Weekly Shipping Issue Report.md:
  1. WINDOW = ticket RECEIPT date (fixed Mon-Sun), never ship cohort.
  2. PERCENT = count / DENOMINATOR (rate vs shipped volume), never share-of-issues.

Counts come from Slack (the real categorization layer) joined to fulfillments for
carrier — NOT feedback.issue_type (Gorgias Contact Reason is empty on ~all
shipping tickets; verified 2026-06-26).

Two fetch paths:
  * LIVE (primary): direct Slack Web API via urllib, Bearer $AH_SLACK_BOT_TOKEN
    (needs scope channels:history + bot in the channel). No third-party deps.
  * DUMP (fallback, no token): --dump-file <concise slack_read_channel blob>.

Usage:
    python -m ingest.slack_reship.sync --week 2026-06-22 --denom 2419 --report
    python -m ingest.slack_reship.sync --week 2026-06-22 --denom 2419 \
        --dump-file fixtures/running_week_2026-06-22.txt --report
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import io
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ingest.slack_reship.parse import (  # noqa: E402
    ReshipRecord, parse_concise_blob, parse_detailed_blob, parse_messages,
)

try:
    from appyhour_lib.paths import db_path
except Exception:
    def db_path() -> Path:  # type: ignore
        return Path(os.environ["APPDATA"]) / "AppyHour" / "shipping.db"

CHANNEL_ID = "C095UVCKCBB"  # #reship-and-order-requests
REPORT_TZ = "America/Chicago"
VENDOR_ORDER = ["Veho", "FedEx", "OnTrac", "UPS"]
ISSUE_ORDER = ["Delayed", "Warm", "Lost", "Undeliverable", "Damaged"]
# map canonical taxonomy -> short matrix column
ISSUE_SHORT = {
    "Shipping::Delayed in transit": "Delayed",
    "Shipping::Damaged in transit::Arrived Warm": "Warm",
    "Shipping::Damaged in transit::Arrived Warm (melted)": "Warm",
    "Shipping::Damaged in transit::Broken/Leaking Ice Pack": "Damaged",
    "Shipping::Damaged in transit::Box damaged": "Damaged",
    "Shipping::Lost in Transit/Misdelivered::Lost": "Lost",
    "Shipping::Lost in Transit/Misdelivered::Misdelivered": "Lost",
    "Shipping::Cannot be delivered": "Undeliverable",
}

CARRIER_CANON = """CASE
  WHEN UPPER(tracking_company) LIKE 'FEDEX%' OR UPPER(tracking_company) LIKE 'FED EX%' THEN 'FedEx'
  WHEN UPPER(tracking_company) LIKE 'ONTRAC%' THEN 'OnTrac'
  WHEN UPPER(tracking_company) LIKE 'VEHO%'   THEN 'Veho'
  WHEN UPPER(tracking_company) LIKE 'UPS%'    THEN 'UPS'
  WHEN UPPER(tracking_company) LIKE '%LASER%' THEN 'OnTrac'
  WHEN tracking_company IS NULL OR tracking_company='' THEN 'unknown'
  ELSE tracking_company END"""


# ---------- window math (GATE rule 1) ----------------------------------------
def week_window(monday: str) -> tuple[float, float, str, str]:
    """Monday 'YYYY-MM-DD' -> (oldest_epoch, latest_epoch, start_date, end_date)
    covering Mon 00:00:00 .. next Mon 00:00:00 in the report tz."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(REPORT_TZ)
    start = datetime.strptime(monday, "%Y-%m-%d").replace(tzinfo=tz)
    end = start + timedelta(days=7)
    return (start.timestamp(), end.timestamp(),
            start.strftime("%Y-%m-%d"), (end - timedelta(days=1)).strftime("%Y-%m-%d"))


# ---------- fetch paths ------------------------------------------------------
def fetch_slack_live(oldest: float, latest: float) -> list[ReshipRecord]:
    token = os.environ.get("AH_SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "AH_SLACK_BOT_TOKEN not set. Create a Slack app with scope "
            "'channels:history', invite it to #reship-and-order-requests, export the "
            "xoxb token as AH_SLACK_BOT_TOKEN — or use --dump-file for the no-token path."
        )
    msgs: list[dict] = []
    cursor = ""
    while True:
        q = {"channel": CHANNEL_ID, "oldest": f"{oldest:.6f}",
             "latest": f"{latest:.6f}", "limit": "200"}
        if cursor:
            q["cursor"] = cursor
        req = urllib.request.Request(
            "https://slack.com/api/conversations.history?" + urllib.parse.urlencode(q),
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not data.get("ok"):
            raise SystemExit(f"Slack API error: {data.get('error')}")
        msgs.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return parse_messages(msgs)


def fetch_dump(path: Path) -> list[ReshipRecord]:
    """Parse a saved slack_read_channel dump — auto-detects detailed vs concise.
    The detailed format (with `Message TS:`) is preferred: real epoch receipt time."""
    blob = path.read_text(encoding="utf-8")
    if "Message TS:" in blob:
        return parse_detailed_blob(blob)
    return parse_concise_blob(blob)


# ---------- enrich + window filter -------------------------------------------
def enrich_and_filter(records: list[ReshipRecord], start_date: str, end_date: str,
                      db: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    seen_gid: set[int] = set()
    for r in records:
        if r.team != "shipping":
            continue
        # GATE rule 1: keep iff receipt date in [start, end]
        d = (r.created_ts or "")[:10]
        if not (start_date <= d <= end_date):
            continue
        # dedup: same ticket can be posted to the channel twice
        if r.gorgias_id is not None:
            if r.gorgias_id in seen_gid:
                continue
            seen_gid.add(r.gorgias_id)
        carrier = "unknown"
        if r.order_number is not None:
            row = con.execute(
                f"SELECT {CARRIER_CANON} FROM fulfillments WHERE order_number=? LIMIT 1",
                (r.order_number,),
            ).fetchone()
            carrier = row[0] if row else "unjoined"
        out.append(dict(order=r.order_number, gid=r.gorgias_id, carrier=carrier,
                        col=ISSUE_SHORT.get(r.issue, r.issue), date=d))
    con.close()
    return out


# ---------- deterministic matrix (GATE rule 2: % of denom) -------------------
def build_matrix(rows: list[dict], denom: int, issues: list[str]) -> str:
    grid = defaultdict(lambda: defaultdict(int))
    for x in rows:
        grid[x["carrier"]][x["col"]] += 1
    vendors = VENDOR_ORDER + sorted(
        c for c in grid if c not in VENDOR_ORDER
    )
    vendors = [v for v in vendors if grid[v]]
    pct = lambda n: f"{100*n/denom:.2f}%" if denom else "—"

    head = "| Vendor | " + " | ".join(issues) + " | **Total** | **% denom** |"
    sep = "|" + "---|" * (len(issues) + 3)
    lines = [head, sep]
    col_tot = defaultdict(int)
    grand = 0
    for v in vendors:
        cells = [str(grid[v].get(i, 0)) for i in issues]
        rt = sum(grid[v].get(i, 0) for i in issues)
        for i in issues:
            col_tot[i] += grid[v].get(i, 0)
        grand += rt
        lines.append(f"| {v} | " + " | ".join(cells) + f" | **{rt}** | {pct(rt)} |")
    tot_cells = " | ".join(f"**{col_tot[i]}**" for i in issues)
    lines.append(f"| **Total** | {tot_cells} | **{grand}** | **{pct(grand)}** |")
    pct_cells = " | ".join(f"{pct(col_tot[i])}" for i in issues)
    lines.append(f"| **% denom** | {pct_cells} | | |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True, help="ship-week Monday YYYY-MM-DD")
    ap.add_argument("--denom", type=int, required=True, help="cohort total (ASK Kurt)")
    ap.add_argument("--issues", default="Delayed,Warm,Lost,Undeliverable,Damaged")
    ap.add_argument("--dump-file", help="no-token fallback: concise slack blob")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    oldest, latest, start_date, end_date = week_window(args.week)
    issues = [i.strip() for i in args.issues.split(",") if i.strip()]
    recs = (fetch_dump(Path(args.dump_file)) if args.dump_file
            else fetch_slack_live(oldest, latest))
    rows = enrich_and_filter(recs, start_date, end_date, db_path())

    src = "DUMP" if args.dump_file else "LIVE Slack API"
    print(f"# Weekly Shipping — _SHIP_{args.week} · denom {args.denom} "
          f"· tickets received {start_date}–{end_date} · source {src}")
    print(f"_{len(rows)} shipping tickets in window (carrier-joined)._\n")
    if args.report:
        print(build_matrix(rows, args.denom, issues))


if __name__ == "__main__":
    main()
