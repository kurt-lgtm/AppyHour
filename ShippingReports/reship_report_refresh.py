"""Daily reship tracking report — refreshes the Reship Sheet.

🔴 Constraints SSOT: ShippingReports/RESHIP_REPORT_RULES.md — read BEFORE changing
anything here. Key rules enforced in code:
  R1  never count Gorgias tags; unit = deduped Shopify reship ORDERS
  R3  attribute to the ORIGINAL order's cohort (customer's prior _SHIP_ order)
  R4  requested date (ticket) != entered date (order created); WoW uses requested
  R5  order sweeps exclude cancelled (status:open OR -status:cancelled)
  R7  denominator = LIVE Shopify tag count excl. cancelled, timestamped
  R8  WoW = same day-offset + projected final via tail CDF from mature cohorts
  R11 provenance rows on every tab; UNKNOWN never estimated

Usage:
  python reship_report_refresh.py [--weeks-back 3] [--dry-run]

Scheduled daily ~12:15 local (CLI, never MCP). Fails LOUD: notify(critical) + exit 1.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

if sys.stdout and hasattr(sys.stdout, "buffer"):  # cp1252 console guard
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (_ROOT, _ROOT / "AppyHourMCP" / "tools", _ROOT / "AppyHourMCP", _ROOT / "GelPackCalculator"):
    sys.path.insert(0, str(p))

from appyhour_lib.credentials import get_shopify_auth  # noqa: E402
from appyhour_lib.notify import notify  # noqa: E402

SHEET_ID = "1JgyYknIxJ3-UJxJOX-y78rf8cPNhT0uPy5FUw2zO9wE"  # Reship Sheet
STATE_PATH = _ROOT.parent / "_outputs" / "cache" / "reship_report_state.json"
CREDS_FALLBACK = _ROOT / "shipping-perfomance-review-accd39ac4b78.json"
MATURITY_DAYS = 14  # cohort considered final for tail-CDF purposes
LATE_REPORT_DAYS = 16  # requested > Monday+16d -> flag (proxy for >14d post-delivery)
HIGH_VALUE = 150.0

_BASE, _HEADERS = get_shopify_auth()


def gql(query: str, variables: dict | None = None) -> dict:
    for _ in range(6):
        r = requests.post(f"{_BASE}/graphql.json", headers=_HEADERS,
                          json={"query": query, "variables": variables or {}}, timeout=30)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        d = r.json()
        if "errors" in d:
            if any("THROTTLED" in str(e) for e in d["errors"]):
                time.sleep(2)
                continue
            raise RuntimeError(str(d["errors"])[:500])
        return d["data"]
    raise RuntimeError("Shopify GraphQL: throttled out of retries")


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def last_ship_tag(tags: list[str]) -> str:
    ships = sorted(t for t in tags if t.startswith("_SHIP_"))
    return ships[-1] if ships else ""


def cohort_denominator(tag: str) -> int:
    """R7: live Shopify count of the cohort tag; cancelled AND reship orders
    excluded (Dan 2026-07-09: reships must not inflate the denominator)."""
    d = gql('query($q:String!){ ordersCount(query:$q, limit:10000){ count } }',
            {"q": f"tag:'{tag}' -status:cancelled -tag:'Reship'"})
    return d["ordersCount"]["count"]


def sweep_reships(since: date) -> list[dict]:
    """R1/R5/R6: deduped reship ORDERS created since `since`, cancelled excluded."""
    out, cursor = [], None
    q = f"tag:'Reship' -status:cancelled created_at:>='{since.isoformat()}T00:00:00-05:00'"
    while True:
        d = gql("""query($q:String!,$c:String){ orders(first:50, after:$c, query:$q){
                     pageInfo{hasNextPage endCursor}
                     edges{node{ id name createdAt tags displayFulfillmentStatus
                                 totalPriceSet{shopMoney{amount}}
                                 customer{ id email numberOfOrders } }}}}""",
                {"q": q, "c": cursor})
        o = d["orders"]
        out += [e["node"] for e in o["edges"]]
        if not o["pageInfo"]["hasNextPage"]:
            return out
        cursor = o["pageInfo"]["endCursor"]


def find_original(customer_gid: str, before_iso: str, self_name: str,
                  complaint_date: str) -> tuple[str, str, float]:
    """R3: most recent prior non-Reship _SHIP_-tagged order whose ship Monday
    PRECEDES the complaint. Guard: a subscriber's NEXT box is created days before
    it ships — without the ship-Monday bound, an old issue misattributes forward
    to the not-yet-shipped cohort (found 2026-07-08, inflated 06-29 counts)."""
    cid = customer_gid.rsplit("/", 1)[-1]
    bound = complaint_date or before_iso[:10]
    d = gql("""query($q:String!){ orders(first:15, query:$q, sortKey:CREATED_AT, reverse:true){
                 edges{node{ name createdAt tags totalPriceSet{shopMoney{amount}} }}}}""",
            {"q": f"customer_id:{cid}"})
    for e in d["orders"]["edges"]:
        n = e["node"]
        if n["name"] == self_name or "Reship" in n["tags"] or n["createdAt"] >= before_iso:
            continue
        tag = last_ship_tag(n["tags"])
        if not tag:
            continue
        ship_mon = tag.replace("_SHIP_", "")
        if ship_mon >= bound:  # box can't have failed before it shipped
            continue
        return n["name"], tag, float(n["totalPriceSet"]["shopMoney"]["amount"])
    return "", "PRIOR-NO-SHIP-TAG", 0.0


def find_requested(email: str, entered: str) -> tuple[str, str]:
    """R4: earliest Gorgias ticket within 14d before entry. -> (date, ticket_id)."""
    if not email:
        return "", ""
    from gorgias_sheets_sync import _gorgias_auth, _gorgias_get
    auth, gbase = _gorgias_auth()
    g = _gorgias_get(f"{gbase}/customers", auth=auth, params={"email": email})
    custs = g.json().get("data", []) if g.ok else []
    if not custs:
        return "", ""
    g = _gorgias_get(f"{gbase}/tickets", auth=auth,
                     params={"customer_id": custs[0]["id"], "limit": 30,
                             "order_by": "created_datetime:desc"})
    floor = (date.fromisoformat(entered) - timedelta(days=14)).isoformat()
    best, best_id = "", ""
    for t in (g.json().get("data", []) if g.ok else []):
        tc = (t.get("created_datetime") or "")[:10]
        if floor <= tc <= entered:
            best, best_id = tc, str(t["id"])  # desc list -> last in-range = earliest
    return best, best_id


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def tail_cdf(state: dict, mondays: list[date]) -> dict[int, float]:
    """R8: fraction of a mature cohort's requests received by day-offset N
    (offset = requested - cohort Monday). Only cohorts >= MATURITY_DAYS old."""
    offsets = []
    today = date.today()
    for rec in state.values():
        coh, req = rec.get("original_cohort", ""), rec.get("requested", "")
        if not (coh.startswith("_SHIP_") and req):
            continue
        cmon = date.fromisoformat(coh.replace("_SHIP_", ""))
        if (today - cmon).days < MATURITY_DAYS:
            continue
        off = (date.fromisoformat(req) - cmon).days
        if off < 0:  # request predates ship = misattribution; exclude from curve
            continue
        offsets.append(off)
    if len(offsets) < 10:
        return {}
    total = len(offsets)
    return {n: sum(1 for o in offsets if o <= n) / total for n in range(0, MATURITY_DAYS + 1)}


def issue_of(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("Reship - "):
            return t.replace("Reship - ", "")
    return "unspecified"


def build(weeks_back: int, dry_run: bool) -> None:
    today = date.today()
    this_mon = monday_of(today)
    mondays = [this_mon - timedelta(weeks=i) for i in range(weeks_back + 1)]
    oldest = mondays[-1]
    stamp = datetime.now().isoformat(timespec="seconds")

    state = load_state()

    # denominators (R7)
    denoms = {m: cohort_denominator(f"_SHIP_{m.isoformat()}") for m in mondays}

    # sweep + enrich (R1/R3/R4/R5)
    reships = sweep_reships(oldest)
    for r in reships:
        key = r["name"]
        rec = state.get(key, {})
        rec.update({
            "entered": r["createdAt"][:10],
            "issue": issue_of(r["tags"]),
            "outbound": last_ship_tag(r["tags"]),
            "total": r["totalPriceSet"]["shopMoney"]["amount"],
            "status": r["displayFulfillmentStatus"],
        })
        cust = r.get("customer") or {}
        rec["lifetime_orders"] = cust.get("numberOfOrders", "")
        if not rec.get("requested"):
            requested, ticket = find_requested(cust.get("email", ""), rec["entered"])
            rec["requested"], rec["ticket"] = requested, ticket
        if not rec.get("original_cohort"):
            if cust.get("id"):
                o_name, o_coh, o_total = find_original(
                    cust["id"], r["createdAt"], r["name"],
                    rec.get("requested") or rec["entered"])
                rec.update({"original": o_name, "original_cohort": o_coh, "original_total": o_total})
                time.sleep(0.15)
            else:
                rec.update({"original": "", "original_cohort": "NO-CUSTOMER", "original_total": 0.0})
        state[key] = rec
    save_state(state)

    cdf = tail_cdf(state, mondays)
    day_n = (today - this_mon).days

    def requests_by_day(mon: date, upto_offset: int | None = None) -> list[str]:
        """reship names attributed (R3) to cohort `mon`, by requested date (R4)."""
        tag = f"_SHIP_{mon.isoformat()}"
        names = []
        for name, rec in state.items():
            if rec.get("original_cohort") != tag or not rec.get("requested"):
                continue
            off = (date.fromisoformat(rec["requested"]) - mon).days
            if off < 0:
                continue  # request predates ship = misattribution, keep out of rates
            if upto_offset is None or off <= upto_offset:
                names.append(name)
        return names

    tabs: dict[str, list[list]] = {}

    # per-week tabs
    for mon in mondays:
        tag = f"_SHIP_{mon.isoformat()}"
        cohort_names = [n for n, rec in state.items() if rec.get("original_cohort") == tag]
        denom = denoms[mon]
        rows: list[list] = []
        rows.append([f"REFRESHED {stamp}", f"cohort {tag}", f"denominator {denom} orders "
                     f"(live Shopify, tag:'{tag}' -status:cancelled)", f"day {max(0,(today-mon).days)} since ship Monday"])
        rows.append([])
        rows.append(["ISSUE BREAKDOWN (unit = reship orders, R1/R6)"])
        rows.append(["Issue", "Count", "% of cohort"])
        for issue, cnt in Counter(state[n]["issue"] for n in cohort_names).most_common():
            rows.append([issue, cnt, f"{cnt/denom:.2%}" if denom else "n/a"])
        rows.append(["TOTAL", len(cohort_names), f"{len(cohort_names)/denom:.2%}" if denom else "n/a"])
        rows.append([])
        # WoW panel (only meaningful on the current week's tab, shown on all)
        prev = mon - timedelta(weeks=1)
        off = min((today - mon).days, MATURITY_DAYS)
        this_n = len(requests_by_day(mon, off))
        prev_n = len(requests_by_day(prev, off))
        rows.append([f"SAME-DAY-OFFSET COMPARISON (day {off}, requested-date basis, R8)"])
        rows.append(["Cohort", f"requests by day {off}", "denominator", "rate"])
        rows.append([tag, this_n, denom, f"{this_n/denom:.2%}" if denom else "n/a"])
        prev_denom = denoms.get(prev, 0)
        rows.append([f"_SHIP_{prev.isoformat()}", prev_n, prev_denom or "n/a",
                     f"{prev_n/prev_denom:.2%}" if prev_denom else "n/a"])
        if cdf.get(off):
            rows.append(["Projected final (to-date / tail CDF)", round(this_n / cdf[off], 1),
                         f"CDF({off})={cdf[off]:.0%} from mature cohorts"])
        else:
            rows.append(["Projected final", "n/a (insufficient mature history)"])
        rows.append(["NOTE: counts lag CS entry — a request exists only once its reship "
                     "order is entered in Shopify. Fresh not-yet-entered requests live in "
                     "Gorgias/Slack triage."])
        rows.append([])
        # Reconciliation panel: entered this calendar week (R4)
        wk_end = mon + timedelta(days=6)
        entered_wk = [n for n, rec in state.items() if mon.isoformat() <= rec.get("entered", "") <= wk_end.isoformat()]
        rows.append(["SHOPIFY RECONCILIATION — reship orders ENTERED this calendar week "
                     "(what an admin eyeball counts; entry date ≠ request date, R4)"])
        rows.append(["Entered this week (deduped orders)", len(entered_wk)])
        for coh, cnt in Counter(state[n].get("original_cohort", "?") for n in entered_wk).most_common():
            rows.append([f"  remediating {coh}", cnt])
        rows.append([])
        rows.append(["DETAIL"])
        rows.append(["Reship", "Requested", "Entered", "Issue", "Original", "Outbound week",
                     "Ticket", "Status"])
        for n in sorted(cohort_names, key=lambda x: state[x].get("requested", "")):
            rec = state[n]
            rows.append([n, rec.get("requested") or "UNKNOWN", rec["entered"], rec["issue"],
                         rec.get("original", ""), rec.get("outbound", ""),
                         rec.get("ticket", ""), rec.get("status", "")])
        tabs[f"RS {tag}"] = rows

    # Summary tab
    srows = [[f"REFRESHED {stamp}", "unit = deduped reship orders; attribution = original cohort; "
              "rates vs live Shopify denominators"], [],
             ["Cohort", "Cohort size", "Reships to date", "Rate", "Day", "Projected final",
              "Projected rate", "Maturity"]]
    for mon in sorted(mondays):
        tag = f"_SHIP_{mon.isoformat()}"
        # headline = ALL attributed reships (incl. UNKNOWN requested) — must match Pivots
        n_now = sum(1 for rec in state.values() if rec.get("original_cohort") == tag)
        denom = denoms[mon]
        off = (today - mon).days
        mature = off >= MATURITY_DAYS
        # projection scales the dated subset, then adds the undated remainder
        dated = len(requests_by_day(mon))
        undated = n_now - dated
        proj = n_now if mature else (round(dated / cdf[min(off, MATURITY_DAYS)] + undated, 1)
                                     if cdf.get(min(off, MATURITY_DAYS)) else "n/a")
        srows.append([tag, denom, n_now, f"{n_now/denom:.2%}" if denom else "n/a",
                      off, proj,
                      (f"{proj/denom:.2%}" if denom and isinstance(proj, (int, float)) else "n/a"),
                      "FINAL" if mature else f"maturing (day {off})"])
    tabs["Summary"] = srows

    # Pivots tab — Dan's 4 views (2026-07-09), window = swept weeks
    def pivot(counter: Counter, label: str) -> list[list]:
        blk = [[label, "Count"]]
        for k in sorted(counter, key=lambda x: (x == "(blank)", x)):
            blk.append([k, counter[k]])
        blk.append(["Grand Total", sum(counter.values())])
        blk.append([])
        return blk

    window_recs = {n: rec for n, rec in state.items()
                   if rec.get("entered", "") >= oldest.isoformat()}
    prows: list[list] = [[f"REFRESHED {stamp}",
                          f"window: reship orders entered since {oldest} (deduped, R6)"], []]
    prows += pivot(Counter(r["entered"] for r in window_recs.values()),
                   "Reship Created (Shopify entry date)")
    prows += pivot(Counter(r.get("requested") or "(blank)" for r in window_recs.values()),
                   "Reship Requested (Slack/Gorgias ticket date)")
    prows += pivot(Counter(r.get("outbound") or "(blank)" for r in window_recs.values()),
                   "Reship Outgoing ship week")
    prows.append(["Reship Incoming ship week (original order's cohort)", "Count",
                  "Cohort size (excl. reships)", "Reship rate"])
    incoming = Counter(r.get("original_cohort") or "(blank)" for r in window_recs.values())
    for coh in sorted(incoming, key=lambda x: (not x.startswith("_SHIP_"), x)):
        cnt = incoming[coh]
        if coh.startswith("_SHIP_"):
            mon_d = date.fromisoformat(coh.replace("_SHIP_", ""))
            denom = denoms.get(mon_d) or cohort_denominator(coh)
            denoms[mon_d] = denom
            prows.append([coh, cnt, denom, f"{cnt/denom:.2%}" if denom else "n/a"])
        else:
            prows.append([coh, cnt, "", ""])
    prows.append(["Grand Total", sum(incoming.values())])
    tabs["Pivots"] = prows

    # Flags tab (Dan-owned decisions)
    frows = [[f"REFRESHED {stamp}"], [],
             ["Reship", "Flag", "Detail"]]
    for n, rec in sorted(state.items()):
        coh = rec.get("original_cohort", "")
        if not coh.startswith("_SHIP_"):
            continue
        cmon = date.fromisoformat(coh.replace("_SHIP_", ""))
        if cmon < oldest:
            continue
        if float(rec.get("original_total") or 0) > HIGH_VALUE:
            frows.append([n, ">$150 original — Dan-managed", f"original {rec.get('original')} ${rec.get('original_total')}"])
        if rec.get("requested") and (date.fromisoformat(rec["requested"]) - cmon).days > LATE_REPORT_DAYS:
            frows.append([n, "late report (>14d post-delivery proxy)", f"requested {rec['requested']} vs ship {cmon}"])
        lo = rec.get("lifetime_orders")
        if isinstance(lo, int) and lo < 3:
            frows.append([n, "<3 lifetime boxes — check sub status", f"lifetime orders: {lo}"])
        if not rec.get("requested"):
            frows.append([n, "UNKNOWN — no ticket found, needs manual check", f"entered {rec.get('entered')}"])
    tabs["Flags"] = frows

    if dry_run:
        for name, rows in tabs.items():
            print(f"\n===== {name} =====")
            for r in rows[:40]:
                print(r)
        return

    from google_integration import GoogleIntegration
    gclient = GoogleIntegration(str(CREDS_FALLBACK))
    existing = {s["properties"]["title"] for s in
                gclient._sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()["sheets"]}
    for name, rows in tabs.items():
        if name not in existing:
            gclient.add_sheet_tab(SHEET_ID, name)
        gclient._sheets.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID, range=f"'{name}'!A1:Z2000").execute()
        width = max(len(r) for r in rows)
        gclient._sheets.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"'{name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [r + [""] * (width - len(r)) for r in rows]}).execute()
        time.sleep(0.3)
    print(f"[reship-report] wrote {len(tabs)} tabs to {SHEET_ID} at {stamp}")

    # breach alert: current cohort worse than last at same day-offset
    off = min(day_n, MATURITY_DAYS)
    cur = len(requests_by_day(this_mon, off))
    prv = len(requests_by_day(this_mon - timedelta(weeks=1), off))
    if prv and cur > prv:
        notify(f"Reship report: _SHIP_{this_mon} at {cur} requests by day {off} vs {prv} "
               f"last week same day — tracking WORSE. Sheet: docs.google.com/spreadsheets/d/{SHEET_ID}",
               level="warning")


def main() -> int:
    ap = argparse.ArgumentParser(prog="reship-report-refresh")
    ap.add_argument("--weeks-back", type=int, default=2)  # window starts _SHIP_2026-06-22-style (Kurt 7/09)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        build(args.weeks_back, args.dry_run)
        return 0
    except Exception as e:
        notify(f"Reship report refresh FAILED: {type(e).__name__}: {e}", level="critical")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
