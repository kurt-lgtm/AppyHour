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

try:  # cp1252 console guard — reconfigure, NOT a new TextIOWrapper: imported
    # modules (ingest.slack_reship.sync) also wrap stdout, and a GC'd wrapper
    # closes the shared buffer ("I/O operation on closed file")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (_ROOT, _ROOT / "AppyHourMCP" / "tools", _ROOT / "AppyHourMCP", _ROOT / "GelPackCalculator"):
    sys.path.insert(0, str(p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")  # AH_SLACK_BOT_TOKEN for the Triage feed, etc.

from appyhour_lib.credentials import get_shopify_auth  # noqa: E402
from appyhour_lib.notify import notify  # noqa: E402

SHEET_ID = "1JgyYknIxJ3-UJxJOX-y78rf8cPNhT0uPy5FUw2zO9wE"  # Reship Sheet
STATE_PATH = _ROOT.parent / "_outputs" / "cache" / "reship_report_state.json"
CREDS_FALLBACK = _ROOT / "shipping-perfomance-review-accd39ac4b78.json"
MATURITY_DAYS = 14  # cohort considered final for tail-CDF purposes
LATE_REPORT_DAYS = 16  # requested > Monday+16d -> flag (proxy for >14d post-delivery)
HIGH_VALUE = 150.0

_BASE, _HEADERS = get_shopify_auth()
_BASE = _BASE.replace(".myshopify.com.myshopify.com", ".myshopify.com")  # .env store var carries full domain


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


PIVOT_SHEET_ID = "1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU"


def _count(q: str) -> int:
    return gql('query($q:String!){ ordersCount(query:$q, limit:10000){ count } }',
               {"q": q})["ordersCount"]["count"]


def box_type_of(skus: list[str]) -> str:
    """Kurt 2026-07-09: AHB-MCUST-TRAY -> Medium Tray, AHB-LCUST-TRAY -> Large
    Tray (physical tray carton, vault Box Type Classification v2), else Regular."""
    up = [(s or "").upper() for s in skus]
    if any("LCUST-TRAY" in s for s in up):
        return "Large Tray"
    if any("MCUST-TRAY" in s for s in up):
        return "Medium Tray"
    return "Regular Box"


def enrich_original_boxtypes(state: dict, mondays: list[date]) -> None:
    """Fetch line-item SKUs of ORIGINAL orders (batched) -> rec['original_boxtype'].
    Cached in state; only missing ones are fetched."""
    tags = {f"_SHIP_{m.isoformat()}" for m in mondays}
    todo = sorted({rec["original"].lstrip("#") for rec in state.values()
                   if rec.get("original") and rec.get("original_cohort") in tags
                   and not rec.get("original_boxtype")})
    name_to_type = {}
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        q = " OR ".join(f"name:{n}" for n in batch)
        d = gql("""query($q:String!){ orders(first:20, query:$q){
                     edges{node{ name lineItems(first:50){edges{node{ sku }}} }}}}""", {"q": q})
        for e in d["orders"]["edges"]:
            skus = [le["node"]["sku"] for le in e["node"]["lineItems"]["edges"]]
            name_to_type[e["node"]["name"].lstrip("#")] = box_type_of(skus)
        time.sleep(0.2)
    for rec in state.values():
        n = (rec.get("original") or "").lstrip("#")
        if n and n in name_to_type:
            rec["original_boxtype"] = name_to_type[n]


BOX_TYPES = ["Regular Box", "Medium Tray", "Large Tray"]


def all_reships_rows(state: dict, mondays: list[date]) -> list[list]:
    """Hidden '_all' tab on the pivot sheet: EVERY reship attributed to the window
    cohorts (incl. ones outside the visible Raw Data membership), with a live
    exclude-lookup into Raw Data col L — feeds Product Mix's instant COUNTIFS."""
    tags = {f"_SHIP_{m.isoformat()}" for m in mondays}
    rows = [["Order", "Incoming week", "Original Box Type", "Excluded"]]
    for k in sorted(state):
        rec = state[k]
        if rec.get("original_cohort") in tags:
            rows.append([k, rec["original_cohort"],
                         rec.get("original_boxtype") or "Regular Box",
                         f"=IFERROR(VLOOKUP($A{len(rows)+1},'Raw Data'!$A:$L,12,FALSE),\"\")"])
    return rows


def tray_mix_rows(mondays: list[date], stamp: str) -> list[list]:
    """Kurt's layout: per cohort, per box type — cohort discrete (live Shopify,
    script-written) + reship discrete/% as LIVE COUNTIFS over the hidden '_all'
    tab, so an Exclude 'x' on Raw Data recomputes instantly (Kurt 2026-07-09)."""
    rows = [[f"REFRESHED {stamp}",
             "sizes = live Shopify (hourly); reship counts = live formulas over _all "
             "(instant, honor Exclude); reship type = ORIGINAL order's box type"],
            ["Cohort", "Cohort size",
             "Regular Box", "Regular Box Reship discrete", "Regular Box Reship %",
             "Medium Tray", "Medium Tray Reship discrete", "Medium Tray Reship %",
             "Large Tray", "Large Tray Reship discrete", "Large Tray Reship %"]]
    for i, mon in enumerate(sorted(mondays)):
        tag = f"_SHIP_{mon.isoformat()}"
        base_q = f"tag:'{tag}' -status:cancelled -tag:'Reship'"
        total = _count(base_q)
        med = _count(base_q + " sku:AHB-MCUST-TRAY*")
        lge = _count(base_q + " sku:AHB-LCUST-TRAY*")
        sizes = {"Regular Box": total - med - lge, "Medium Tray": med, "Large Tray": lge}
        r = i + 3  # data rows start at sheet row 3
        row = [tag, total]
        for bt, col in zip(BOX_TYPES, ("C", "F", "I")):
            cnt_col = chr(ord(col) + 1)
            row += [sizes[bt],
                    (f"=COUNTIFS('_all'!$B:$B,$A{r},'_all'!$C:$C,\"{bt}\","
                     f"'_all'!$D:$D,\"<>x\")"),
                    f"=IF({col}{r}>0,TEXT({cnt_col}{r}/{col}{r},\"0.00%\"),\"n/a\")"]
        rows.append(row)
    return rows


def triage_rows(state: dict, oldest: date, stamp: str, gclient) -> list[list]:
    """Requested-but-not-entered feed, SLACK ONLY (Kurt 2026-07-09 — the Gorgias
    'Reship req' tag is rule-81603 spam and is banned as a source, R1).
    Source: #reship-and-order-requests via the canonical ingest.slack_reship
    parser. Rows whose order already has an entered reship are dropped.
    User col F ('Decision') preserved across refreshes; never counted anywhere."""
    import datetime as _dt

    from ingest.slack_reship.parse import parse_record  # noqa: F401 (canonical parser)
    from ingest.slack_reship.sync import fetch_slack_live

    oldest_ts = _dt.datetime.combine(oldest, _dt.time()).timestamp()
    records = fetch_slack_live(oldest_ts, _dt.datetime.now().timestamp())

    originals = {(rec.get("original") or "").lstrip("#") for rec in state.values()}
    prev = {}
    try:
        for row in gclient.read_sheet(PIVOT_SHEET_ID, "'Triage'!A2:F1000") or []:
            row = row + [""] * (6 - len(row))
            if row[0]:
                prev[str(row[0])] = row[5]
    except Exception:
        pass
    rows = [[f"REFRESHED {stamp}",
             "Slack #reship-and-order-requests posts w/o an entered reship order — "
             "NOT counted anywhere. Col F is YOURS: reship / refund / no action",
             "", "", "", "Decision"],
            ["Key", "Posted", "Issue", "Order", "Gorgias", "Decision"]]
    for r in records:
        onum = str(r.order_number or "")
        if onum and onum in originals:
            continue  # already remediated by an entered reship
        key = str(r.gorgias_id or onum or (r.created_ts or ""))
        rows.append([key, (r.created_ts or "")[:16], r.issue or "",
                     f"#{onum}" if onum else "",
                     str(r.gorgias_id or ""), prev.get(key, "")])
    return rows


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


def find_requested(email: str, entered: str, floor_date: str = "") -> tuple[str, str]:
    """R4: earliest Gorgias ticket in [floor, entered]. floor defaults to
    entered-14d but callers pass the ORIGINAL's ship Monday — a complaint can't
    predate the shipment (without the floor, a signup/confirmation thread wins
    and poisons attribution — #158288, 2026-07-08)."""
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
    floor = max(filter(None, [(date.fromisoformat(entered) - timedelta(days=14)).isoformat(),
                              floor_date]))
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
        # order matters: attribute FIRST (bounded by entered date only), then
        # find the request ticket floored at the original's ship Monday
        if not rec.get("original_cohort"):
            if cust.get("id"):
                o_name, o_coh, o_total = find_original(
                    cust["id"], r["createdAt"], r["name"], rec["entered"])
                rec.update({"original": o_name, "original_cohort": o_coh, "original_total": o_total})
                time.sleep(0.15)
            else:
                rec.update({"original": "", "original_cohort": "NO-CUSTOMER", "original_total": 0.0})
        if not rec.get("requested"):
            coh = rec.get("original_cohort", "")
            floor = coh.replace("_SHIP_", "") if coh.startswith("_SHIP_") else ""
            requested, ticket = find_requested(cust.get("email", ""), rec["entered"], floor)
            rec["requested"], rec["ticket"] = requested, ticket
        state[key] = rec
    enrich_original_boxtypes(state, mondays)
    save_state(state)

    # user overrides from Raw Data cols J-M (script never writes into them
    # except to re-preserve; user edits survive refreshes and re-point pivots)
    gclient = None
    overrides: dict[str, dict] = {}
    if not dry_run:
        from google_integration import GoogleIntegration
        gclient = GoogleIntegration(str(CREDS_FALLBACK))
        try:
            raw = gclient.read_sheet(SHEET_ID, "'Raw Data'!A3:M10000") or []
        except Exception:
            raw = []
        for row in raw:
            row = row + [""] * (13 - len(row))
            if row[0]:
                overrides[row[0]] = {"issue": row[9], "incoming": row[10],
                                     "outgoing": row[11], "exclude": row[12].strip().lower() == "x"}
        # pivot sheet's Exclude col (L) counts too — Dan works there (Kurt 7/09)
        try:
            praw = gclient.read_sheet(PIVOT_SHEET_ID, "'Raw Data'!A2:L10000") or []
        except Exception:
            praw = []
        for row in praw:
            row = row + [""] * (12 - len(row))
            if row[0] and str(row[11]).strip().lower() == "x":
                overrides.setdefault(row[0], {"issue": "", "incoming": "", "outgoing": ""})["exclude"] = True

    # effective view: overrides applied, excluded rows dropped from ALL counts
    eff: dict[str, dict] = {}
    for k, rec in state.items():
        r = dict(rec)
        o = overrides.get(k)
        if o:
            if o["issue"]:
                r["issue"] = o["issue"]
            if o["incoming"]:
                r["original_cohort"] = o["incoming"]
            if o["outgoing"]:
                r["outbound"] = o["outgoing"]
            r["excluded"] = o["exclude"]
        eff[k] = r
    work = {k: r for k, r in eff.items() if not r.get("excluded")}

    cdf = tail_cdf(work, mondays)
    day_n = (today - this_mon).days

    def requests_by_day(mon: date, upto_offset: int | None = None) -> list[str]:
        """reship names attributed (R3) to cohort `mon`, by requested date (R4)."""
        tag = f"_SHIP_{mon.isoformat()}"
        names = []
        for name, rec in work.items():
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
        cohort_names = [n for n, rec in work.items() if rec.get("original_cohort") == tag]
        denom = denoms[mon]
        rows: list[list] = []
        rows.append([f"REFRESHED {stamp}", f"cohort {tag}", f"denominator {denom} orders "
                     f"(live Shopify, tag:'{tag}' -status:cancelled)", f"day {max(0,(today-mon).days)} since ship Monday"])
        rows.append([])
        rows.append(["ISSUE BREAKDOWN (unit = reship orders, R1/R6)"])
        rows.append(["Issue", "Count", "% of cohort"])
        for issue, cnt in Counter(work[n]["issue"] for n in cohort_names).most_common():
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
        entered_wk = [n for n, rec in work.items() if mon.isoformat() <= rec.get("entered", "") <= wk_end.isoformat()]
        rows.append(["SHOPIFY RECONCILIATION — reship orders ENTERED this calendar week "
                     "(what an admin eyeball counts; entry date ≠ request date, R4)"])
        rows.append(["Entered this week (deduped orders)", len(entered_wk)])
        for coh, cnt in Counter(work[n].get("original_cohort", "?") for n in entered_wk).most_common():
            rows.append([f"  remediating {coh}", cnt])
        rows.append([])
        rows.append(["DETAIL"])
        rows.append(["Reship", "Requested", "Entered", "Issue", "Original", "Outbound week",
                     "Ticket", "Status"])
        for n in sorted(cohort_names, key=lambda x: work[x].get("requested", "")):
            rec = work[n]
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
        n_now = sum(1 for rec in work.values() if rec.get("original_cohort") == tag)
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

    # Raw Data tab — source cols A-I script-owned; J-M USER-owned overrides
    # (preserved each refresh); N-P effective formulas. Pivots read N-P live.
    window_keys = sorted([n for n, rec in state.items()
                          if rec.get("entered", "") >= oldest.isoformat()],
                         key=lambda n: (state[n]["entered"], n))
    # ensure denominators for every incoming cohort seen (source OR override)
    for n in window_keys:
        coh = eff[n].get("original_cohort", "")
        if coh.startswith("_SHIP_"):
            mon_d = date.fromisoformat(coh.replace("_SHIP_", ""))
            if mon_d not in denoms:
                denoms[mon_d] = cohort_denominator(coh)

    rrows: list[list] = [
        [f"REFRESHED {stamp}", f"window: entered since {oldest}",
         "cols A-I refresh hourly (do not edit)", "cols J-M are YOURS (survive refresh)",
         "put x in Exclude to strike a row", "pivots update instantly"],
        ["Order", "Entered", "Requested", "Ticket", "Issue", "Incoming week",
         "Outgoing week", "Status", "Original",
         "Override Issue", "Override Incoming", "Override Outgoing", "Exclude",
         "Eff Issue", "Eff Incoming", "Eff Outgoing"],
    ]
    for i, n in enumerate(window_keys):
        rec = state[n]  # source values in A-I; overrides shown in J-M
        o = overrides.get(n, {})
        rnum = i + 3
        rrows.append([
            n, rec.get("entered", ""), rec.get("requested", ""), rec.get("ticket", ""),
            rec.get("issue", ""), rec.get("original_cohort", ""), rec.get("outbound", ""),
            rec.get("status", ""), rec.get("original", ""),
            o.get("issue", ""), o.get("incoming", ""), o.get("outgoing", ""),
            "x" if o.get("exclude") else "",
            f'=IF($J{rnum}<>"",$J{rnum},$E{rnum})',
            f'=IF($K{rnum}<>"",$K{rnum},$F{rnum})',
            f'=IF($L{rnum}<>"",$L{rnum},$G{rnum})',
        ])
    tabs["Raw Data"] = rrows

    # Pivots tab — live QUERY formulas over Raw Data effective cols (edit an
    # override or Exclude on Raw Data -> these change instantly, no refresh)
    rd = "'Raw Data'!$A$3:$P"
    def q(sel_col: str) -> str:
        return (f'=IFERROR(QUERY({rd}, "select {sel_col}, count(A) '
                f"where A<>'' and M<>'x' group by {sel_col} order by {sel_col} "
                f'label count(A) \'\'", 0), "no data")')
    N = None  # null cell -> untouched/empty, so QUERY spills aren't blocked
    prows: list[list] = [
        [f"REFRESHED {stamp}",
         f"live formulas over Raw Data (entered since {oldest}); overrides + Exclude apply instantly",
         N, "Grand Total (excl. excluded):",
         '=COUNTIFS(\'Raw Data\'!$A$3:$A,"<>",\'Raw Data\'!$M$3:$M,"<>x")'],
        [],
        ["Reship Created (entry date)", N, N, "Reship Requested (ticket date)", N, N,
         "Reship Outgoing ship week", N, N, "Reship Incoming ship week", N, "Rate", N,
         "Cohort size (excl. reships)", N],
        [q("B"), N, N, q("C"), N, N, q("P"), N, N, q("O"), N,
         '=ARRAYFORMULA(IF(J4:J="",,IFERROR(TEXT(K4:K/VLOOKUP(J4:J,$N$4:$O,2,FALSE),"0.00%"),"")))', N,
         N, N],
    ]
    for i, mon_d in enumerate(sorted(denoms)):
        row: list = [N] * 15
        row[13] = f"_SHIP_{mon_d.isoformat()}"
        row[14] = denoms[mon_d]
        if i + 4 == 4:  # denominator rows start at row 4
            prows[3][13] = row[13]
            prows[3][14] = row[14]
        else:
            prows.append(row)
    tabs["Pivots"] = prows

    # Flags tab (Dan-owned decisions)
    frows = [[f"REFRESHED {stamp}"], [],
             ["Reship", "Flag", "Detail"]]
    for n, rec in sorted(work.items()):
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

    existing = {s["properties"]["title"] for s in
                gclient._sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()["sheets"]}
    for name, rows in tabs.items():
        if name not in existing:
            gclient.add_sheet_tab(SHEET_ID, name)
        gclient._sheets.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID, range=f"'{name}'!A1:Z2000").execute()
        # None cells serialize to JSON null = untouched (stays empty after the
        # clear) — required so QUERY/ARRAYFORMULA spills on Pivots aren't blocked.
        # Data tabs write RAW so date strings stay text (USER_ENTERED turns them
        # into date serials that break QUERY group labels); formula cells need
        # USER_ENTERED, so Raw Data splits A-M (RAW) from N-P (formulas).
        if name == "Raw Data":
            gclient._sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range=f"'{name}'!A1",
                valueInputOption="RAW",
                body={"values": [r[:13] for r in rows]}).execute()
            gclient._sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range=f"'{name}'!N1",
                valueInputOption="USER_ENTERED",
                body={"values": [r[13:16] if len(r) > 13 else [None] for r in rows]}).execute()
        else:
            gclient._sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range=f"'{name}'!A1",
                valueInputOption="USER_ENTERED" if name == "Pivots" else "RAW",
                body={"values": rows}).execute()
        time.sleep(0.3)
    print(f"[reship-report] wrote {len(tabs)} tabs to {SHEET_ID} at {stamp}")

    # Dan's pivot sheet extras: Tray Mix (cohort composition) + Triage feed
    extra = {"_all": all_reships_rows(state, mondays),          # hidden feed, formulas
             "Product Mix": tray_mix_rows(mondays, stamp)}      # live COUNTIFS over _all
    try:
        extra["Triage"] = triage_rows(state, oldest, stamp, gclient)
    except Exception as e:
        print(f"[reship-report] triage build failed (non-fatal): {e}")
    p_meta = gclient._sheets.spreadsheets().get(spreadsheetId=PIVOT_SHEET_ID).execute()["sheets"]
    p_existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in p_meta}
    for name, rows in extra.items():
        if name not in p_existing:
            gclient.add_sheet_tab(PIVOT_SHEET_ID, name)
        gclient._sheets.spreadsheets().values().clear(
            spreadsheetId=PIVOT_SHEET_ID, range=f"'{name}'!A1:Z2000").execute()
        gclient._sheets.spreadsheets().values().update(
            spreadsheetId=PIVOT_SHEET_ID, range=f"'{name}'!A1",
            valueInputOption="RAW" if name == "Triage" else "USER_ENTERED",
            body={"values": rows}).execute()
        time.sleep(0.3)
    if "_all" in p_existing:  # keep the feed tab hidden
        gclient._sheets.spreadsheets().batchUpdate(spreadsheetId=PIVOT_SHEET_ID, body={
            "requests": [{"updateSheetProperties": {
                "properties": {"sheetId": p_existing["_all"], "hidden": True},
                "fields": "hidden"}}]}).execute()
    print(f"[reship-report] wrote {len(extra)} extra tabs to pivot sheet")

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
    import os
    for attempt in (1, 2):  # transient network flakes retry silently
        try:
            build(args.weeks_back, args.dry_run)
            return 0
        except Exception as e:
            if attempt == 1:
                print(f"[reship-report] attempt 1 failed ({type(e).__name__}), retrying in 60s")
                time.sleep(60)
                continue
            # errors go to EMAIL only — never the shared Slack channel Dan reads
            # (Kurt 2026-07-09); the webhook is reserved for breach alerts.
            os.environ.pop("AH_SLACK_WEBHOOK", None)
            notify(f"Reship report refresh FAILED (after retry): {type(e).__name__}: {e}",
                   level="critical")
            traceback.print_exc()
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
