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
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# 🔴 UTF-8 stdio + canonical .env, via the ONE bootstrap every other scheduled entry point uses
# (2026-08-31). Two bugs fixed at once by deleting the hand-rolled line that stood here:
#   (a) ENV: this module reads AH_SLACK_BOT_TOKEN straight off os.environ, and nothing loaded
#       AppyHour/.env on the `python -m ingest.slack_reship.sync` path — only weekly_task.py called
#       init(). A scheduled shell has no such var, so the LIVE Slack path reported "token not set"
#       while the token sat in .env the whole time (the 2026-08-11/18/22/28 class, which
#       bootstrap.init already fixed everywhere else). Real env vars still win over .env.
#   (b) STDIO: `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` is the bare-wrapper
#       anti-pattern bootstrap.py names — the discarded wrapper can be GC'd and close the shared
#       buffer ("I/O operation on closed file"). init() reconfigures in place and keeps a GC guard.
# init() is idempotent, so weekly_task.py calling it first costs nothing.
from appyhour_lib.bootstrap import init as _bootstrap_init  # noqa: E402

_bootstrap_init()
from ingest.slack_reship.parse import (  # noqa: E402
    ReshipRecord, parse_concise_blob, parse_detailed_blob, parse_messages,
)
# box-type display order (kept local so importing sync doesn't trigger Shopify
# auth — boxtype.py authenticates at import; only pull it in lazily when needed).
BOX_ORDER = ["Regular Box", "Medium Tray", "Large Tray"]

try:
    from appyhour_lib.paths import db_path
except Exception:
    def db_path() -> Path:  # type: ignore
        return Path(os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.isdir(r"C:\AppyHourData") else str(Path(os.environ["APPDATA"]) / "AppyHour" / "shipping.db")))  # dir-keyed 2026-07-22 (login-race split-brain guard)

# Readers MUST use connect_ro (mode=ro) — never raw sqlite3.connect on the live
# DB (MSIX+WAL corruption guard, appyhour_lib/CLAUDE.md).
from appyhour_lib.db import connect_ro  # noqa: E402
from appyhour_lib.heartbeat import beat  # noqa: E402  — dead-man-switch (HEARTBEAT_RULES)

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
    con = connect_ro(db)
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


# ---------- auto denominator (cohort shipped volume) -------------------------
def auto_denom(week: str, db: Path) -> int:
    """denom = COUNT(fulfillments tagged _SHIP_<week>) — the shipped cohort size.
    Read-only. Replaces the old manual --denom arg so the weekly task self-serves."""
    con = connect_ro(db)
    try:
        (n,) = con.execute(
            "SELECT COUNT(*) FROM fulfillments WHERE tags LIKE ?",
            (f"%_SHIP_{week}%",),
        ).fetchone()
    finally:
        con.close()
    return int(n)


# denominator floor as a FRACTION of the trailing median, not a magic constant. Measured
# 2026-09-01 over ten weeks 06-29..08-31: 2026 … 2554, median ~2364. The two published
# failures were denom 0 and denom 2 — three orders of magnitude clear of this bound, so it
# separates "the cohort never landed" from "a light week" without ever needing a tuned number.
MIN_DENOM_FRACTION_OF_MEDIAN = 0.5
TRAILING_WEEKS_FOR_FLOOR = 8


class NotPublishable(RuntimeError):
    """Base: this week must not be written to the sheet. Catch THIS to mean 'refused'."""


class ImplausibleDenominator(NotPublishable):
    """Refusal to publish a rate whose denominator cannot be a real completed ship week."""


class IncompleteWeek(NotPublishable):
    """Refusal to publish a Mon–Sun window that has not closed yet."""


def assert_week_complete(week: str, today: date | None = None) -> None:
    """🔴 THE DENOMINATOR GATE ONLY GUARDS THE DENOMINATOR — this guards the NUMERATOR.

    Found 2026-09-01 while dry-running the backfill: week `2026-08-31` passed
    `assert_denom_publishable` cleanly (denom 2471, a full real cohort) and would have
    published **0 tickets / 0.00%** — because that week began the day before and its Mon–Sun
    ticket window still had ~1.5 of 7 days in it. That is the same defect as the denom-0 tabs
    wearing the opposite face: there, a truncated denominator under a real numerator; here, a
    real denominator under a truncated numerator. Both render as a rate nobody can tell is
    partial, and a denominator check cannot see it — the denominator looks perfect.

    So: a week is publishable only once its Sunday has passed. Cheap, total, and it makes the
    `--week` argument safe in a human's hands, not just on the routine's own path.
    """
    _, _, _, end_date = week_window(week)
    d = today or date.today()
    if end_date >= d.isoformat():
        raise IncompleteWeek(
            f"week _SHIP_{week} is still open — its Mon–Sun ticket window runs through "
            f"{end_date} and today is {d.isoformat()}. Publishing now would put a truncated "
            "ticket count over a full-cohort denominator and render it as a rate. Wait until "
            "the week has closed (the routine reports the last COMPLETE week for this reason).")


def trailing_denoms(week: str, db: Path, n: int = TRAILING_WEEKS_FOR_FLOOR) -> list[int]:
    """auto_denom for the `n` Mondays BEFORE `week`, in chronological order (zeros kept)."""
    monday = datetime.strptime(week, "%Y-%m-%d")
    return [auto_denom((monday - timedelta(days=7 * k)).strftime("%Y-%m-%d"), db)
            for k in range(n, 0, -1)]


def assert_denom_publishable(week: str, denom: int, db: Path) -> None:
    """🔴 A ZERO IS A CLAIM — prove it before writing it to a sheet people read.

    Published evidence this exists (measured 2026-09-01 off the live tabs): the `2026-07-20`
    tab went out with **denom 0** and the `2026-08-10` tab with **denom 2**, against true
    cohort sizes of 2082 and 2365. Both were written by an on-schedule Tuesday run, both
    rendered every `% denom` cell meaningless (`—`, or a rate over 2), and NOTHING objected —
    the tab is the deliverable, so a garbage tab is a silently wrong answer, not a failure.

    Two failure shapes hide behind the same `0`, and they need different words:
      * the LIKE join itself matched nothing (tag format moved, table empty/not synced) — the
        control probe below fails too, and no week would pass;
      * the join is sound and THIS cohort is absent (wrong Monday, or the week has not
        shipped yet — the defect that produced both bad tabs).
    Raises `ImplausibleDenominator` either way: refusing to publish is the loud failure.
    """
    con = connect_ro(db)
    try:
        (control,) = con.execute(
            "SELECT COUNT(*) FROM fulfillments WHERE tags LIKE '%_SHIP_%'").fetchone()
    finally:
        con.close()
    if not control:
        raise ImplausibleDenominator(
            f"denom {denom} for _SHIP_{week} is UNPROVEN: the control probe "
            "(fulfillments with ANY _SHIP_ tag) also returned 0, so the join matched nothing "
            "for every week — fulfillments is empty, unsynced, or the tag format changed. "
            "Refusing to publish a rate over it.")
    if denom <= 0:
        raise ImplausibleDenominator(
            f"denom 0 for _SHIP_{week} while the control probe found {control} tagged "
            "fulfillments — the join works, this cohort is simply absent. Wrong Monday, or the "
            "week has not shipped yet. Refusing to publish a 0-denominator rate.")
    prior = [d for d in trailing_denoms(week, db) if d > 0]
    if len(prior) >= 3:
        prior.sort()
        median = prior[len(prior) // 2]
        floor = int(median * MIN_DENOM_FRACTION_OF_MEDIAN)
        if denom < floor:
            raise ImplausibleDenominator(
                f"denom {denom} for _SHIP_{week} is below the floor {floor} "
                f"({MIN_DENOM_FRACTION_OF_MEDIAN:.0%} of the trailing median {median} over "
                f"{len(prior)} weeks) — a partially-ingested or in-progress cohort, not a "
                "completed ship week. Refusing to publish rates against it.")


# ---------- box-type enrichment (Shopify line-item SKUs) ---------------------
def enrich_box_types(rows: list[dict]) -> None:
    """Mutate rows in place, adding row['box'] (Regular Box / Medium Tray /
    Large Tray / unknown). Isolated import so the no-Shopify markdown path still
    works if creds are absent."""
    onums = [r["order"] for r in rows if r.get("order") is not None]
    mapping: dict[int, str] = {}
    if onums:
        try:
            from ingest.slack_reship.boxtype import box_types_for
            mapping = box_types_for(onums)
        except Exception as e:  # creds missing / API down — don't kill the report
            print(f"# WARN box-type lookup failed: {e}", file=sys.stderr)
    for r in rows:
        r["box"] = mapping.get(r.get("order"), "unknown")


def box_summary_grid(rows: list[dict], denom: int) -> tuple[list[list], int]:
    """Box-type breakdown of reshipped orders (list-of-lists for the sheet).
    Returns (grid, header_row_offset)."""
    counts = defaultdict(int)
    for r in rows:
        counts[r.get("box", "unknown")] += 1
    pct = lambda n: f"{100*n/denom:.2f}%" if denom else "—"
    grid = [["Box type", "Reshipped", "% denom"]]
    order = BOX_ORDER + [k for k in counts if k not in BOX_ORDER]
    tot = 0
    for b in order:
        if b not in counts:
            continue
        grid.append([b, counts[b], pct(counts[b])])
        tot += counts[b]
    grid.append(["Total", tot, pct(tot)])
    return grid, 0


# ---------- deterministic matrix (GATE rule 2: % of denom) -------------------
def counts_by_vendor(rows: list[dict], issues: list[str]) -> dict[str, dict[str, int]]:
    """THE aggregation — vendor -> issue -> count, in display order.

    🔴 Every consumer (the markdown DM matrix, the sheet grid, the history ledger) reads THIS.
    A second consumer that re-tallies `rows` its own way is how the DM and the durable tab drift
    into reporting two different numbers for the same week (the D35c lesson, already paid for on
    the neighbouring `Carrier Mix` tab).
    """
    grid: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for x in rows:
        grid[x["carrier"]][x["col"]] += 1
    vendors = VENDOR_ORDER + sorted(c for c in grid if c not in VENDOR_ORDER)
    return {v: {i: grid[v].get(i, 0) for i in issues} for v in vendors if grid[v]}


def unclassified_count(rows: list[dict], issues: list[str]) -> int:
    """Tickets whose issue falls outside `issues` — dropped by the matrix, so say so out loud."""
    return sum(1 for x in rows if x["col"] not in issues)


def matrix_grid(rows: list[dict], denom: int, issues: list[str]) -> list[list]:
    """Same numbers as build_matrix() but as a 2D list for the Google Sheet."""
    counts = counts_by_vendor(rows, issues)
    pct = lambda n: f"{100*n/denom:.2f}%" if denom else "—"
    out = [["Vendor"] + issues + ["Total", "% denom"]]
    col_tot = defaultdict(int)
    grand = 0
    for v, c in counts.items():
        rt = sum(c[i] for i in issues)
        for i in issues:
            col_tot[i] += c[i]
        grand += rt
        out.append([v] + [c[i] for i in issues] + [rt, pct(rt)])
    out.append(["Total"] + [col_tot[i] for i in issues] + [grand, pct(grand)])
    out.append(["% denom"] + [pct(col_tot[i]) for i in issues] + ["", ""])
    return out


def build_matrix(rows: list[dict], denom: int, issues: list[str]) -> str:
    counts = counts_by_vendor(rows, issues)
    pct = lambda n: f"{100*n/denom:.2f}%" if denom else "—"

    head = "| Vendor | " + " | ".join(issues) + " | **Total** | **% denom** |"
    sep = "|" + "---|" * (len(issues) + 3)
    lines = [head, sep]
    col_tot = defaultdict(int)
    grand = 0
    for v, c in counts.items():
        cells = [str(c[i]) for i in issues]
        rt = sum(c[i] for i in issues)
        for i in issues:
            col_tot[i] += c[i]
        grand += rt
        lines.append(f"| {v} | " + " | ".join(cells) + f" | **{rt}** | {pct(rt)} |")
    tot_cells = " | ".join(f"**{col_tot[i]}**" for i in issues)
    lines.append(f"| **Total** | {tot_cells} | **{grand}** | **{pct(grand)}** |")
    pct_cells = " | ".join(f"{pct(col_tot[i])}" for i in issues)
    lines.append(f"| **% denom** | {pct_cells} | | |")
    return "\n".join(lines)


def main() -> str | None:
    """Returns the pushed sheet URL on the `--push` path, else None.

    🔴 The return value is the CALLER'S PROOF OF PUBLICATION. `weekly_task.py` beats
    `slack-reship` only when this returns a URL, so the heartbeat cannot mark a run healthy
    that produced no tab (HEARTBEAT_RULES: a beat on a failed run is worse than no beat).
    Do not make this return early on the push path without also returning the URL.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True, help="ship-week Monday YYYY-MM-DD")
    ap.add_argument("--denom", type=int, default=None,
                    help="cohort total; omit to auto-count fulfillments _SHIP_<week>")
    ap.add_argument("--issues", default="Delayed,Warm,Lost,Undeliverable,Damaged")
    ap.add_argument("--dump-file", help="no-token fallback: concise slack blob")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--box-types", action="store_true",
                    help="enrich each order with box type via Shopify line items")
    ap.add_argument("--push", action="store_true",
                    help="write/refresh this week's tab in the reship Google Sheet")
    ap.add_argument("--sheet-id", default=None, help="override cached sheet id")
    ap.add_argument("--history-sheet", action="store_true",
                    help="record this week in the vendor-matrix ledger and repaint the "
                         "'Vendor Matrix' history tab on the Running Reship sheet (D39)")
    args = ap.parse_args()

    oldest, latest, start_date, end_date = week_window(args.week)
    issues = [i.strip() for i in args.issues.split(",") if i.strip()]
    recs = (fetch_dump(Path(args.dump_file)) if args.dump_file
            else fetch_slack_live(oldest, latest))
    rows = enrich_and_filter(recs, start_date, end_date, db_path())
    denom = args.denom if args.denom is not None else auto_denom(args.week, db_path())

    # 🔴 Gate BEFORE any Shopify call or sheet write. Scoped to the publishing path on purpose:
    # `--report` alone prints to stdout where a human/agent reads the number in context, while
    # `--push` writes a durable tab nobody re-checks. `--history-sheet` keeps its own
    # VM_ZERO_DENOM refusal in matrix_history (D39 rule 3) — this does not duplicate it.
    if args.push:
        assert_week_complete(args.week)          # numerator: is the window closed?
        assert_denom_publishable(args.week, denom, db_path())   # denominator: is it real?

    # box types needed if the flag is set OR we're pushing to the sheet
    if args.box_types or args.push:
        enrich_box_types(rows)

    src = "DUMP" if args.dump_file else "LIVE Slack API"
    print(f"# Weekly Shipping — _SHIP_{args.week} · denom {denom} "
          f"· tickets received {start_date}–{end_date} · source {src}")
    print(f"_{len(rows)} shipping tickets in window (carrier-joined)._\n")
    if args.report:
        print(build_matrix(rows, denom, issues))
    if args.box_types or args.push:
        box_grid, _ = box_summary_grid(rows, denom)
        print("\n## Box type of reshipped orders")
        for r in box_grid:
            print("| " + " | ".join(str(c) for c in r) + " |")

    pushed_url: str | None = None
    if args.push:
        from ingest.slack_reship.sheet_push import build_rows, push
        vmatrix = matrix_grid(rows, denom, issues)
        bgrid, _ = box_summary_grid(rows, denom)
        sheet_rows = build_rows(args.week, denom, len(rows), start_date, end_date,
                                src, vmatrix, bgrid)
        # header rows (1-indexed) for styling: vendor block hdr, box block hdr
        vendor_hdr = 6                 # title(1) sub(2) note(3) blank(4) "CARRIER"(5) -> matrix hdr(6)
        box_hdr = 8 + len(vmatrix)     # matrix rows 6..5+N, blank, "BOX TYPE" label, then box hdr
        pushed_url = push(args.week, sheet_rows, vendor_hdr, box_hdr, sheet_id=args.sheet_id)
        print(f"\nPUSHED: {pushed_url}")

    if args.history_sheet:
        # Durable HISTORY for the DM (D39). The DM still posts every week — this is the record it
        # leaves behind, not a replacement for it. Same `counts_by_vendor` the matrix above used,
        # so the tab cannot disagree with what was DM'd.
        from ingest.slack_reship import matrix_history as mh
        led = mh.load_ledger()
        notes = mh.upsert(led, args.week, denom, len(rows), start_date, end_date, src, issues,
                          counts_by_vendor(rows, issues), unclassified_count(rows, issues))
        mh.write_sheet(led, VENDOR_ORDER, ISSUE_ORDER, notes)
        mh.save_ledger(led)   # ledger last: a failed paint must not record a week as published
        print(f"\nHISTORY: {mh.SHEET_TAB!r} tab updated for {args.week} "
              f"(ledger {mh.LEDGER})")

    # Dead-man-switch beat for `weekly-shipping-vendor-matrix` (HEARTBEAT_RULES; added 2026-08-31
    # so that routine could go exception-only — a run that never happens sends no Slack, and
    # absence of this beat is then the ONLY signal left).
    # 🔴 NOT keyed on --report alone. `weekly-reship-report` runs THIS SAME main() via
    # weekly_task.py with `--report --push`, and that routine already owns the separate
    # `slack-reship` beat. Beating one key from both callers would let either routine's death hide
    # behind the other's success — two routines, two keys, and `--push` is what tells them apart.
    # 🔴 `--history-sheet` (D39) is deliberately NOT part of this condition: it is an addition to
    # the vendor-matrix routine's own invocation, so the beat must still fire on the shape that
    # routine actually runs (`--report --history-sheet`, no `--push`). Verified live 2026-08-31.
    if args.report and not args.push:
        beat("vendor-matrix")
    return pushed_url


if __name__ == "__main__":
    main()
