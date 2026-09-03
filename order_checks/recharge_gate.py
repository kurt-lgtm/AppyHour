"""Check 7's customization gate: did a HUMAN change this order's contents?

  python -m order_checks.recharge_gate build <events.csv>
  python -m order_checks.recharge_gate stats

An order is excluded from the repeat analysis when a human changed its contents -- the
customer or our own staff. Getting this wrong in either direction ruins the check:

🔴 Too strict and it empties. Dan's CONTEXT.md: the automated post-charge box build fires
   on most rotating-selection boxes within seconds of the bill as an api-origin event, so
   treating every api change as a customization "would drop roughly a third of the run."
🔴 Too loose and every repeat looks deliberate.

An event qualifies only if it BOTH touches contents AND is human-sourced.

  contents-touching : verb swapped / bundle_selection*, or the change text names
                      box_contents, sku, external_variant_id, variant_title, product_title
  human-sourced     : Customer / Store Admin / Recharge Admin
  api, conditional  : "[API] ..." counts as human ONLY when a human-sourced event lands on
                      the same customer within HUMAN_WINDOW -- a portal edit executes as an
                      api call, and a portal payment reveals the customer was logged in.

🔴 Joins on the RECHARGE customer id via customer_map, never email: an email lookup for
the #176919 customer returned ZERO rows against 115 events found by id.

Known floor: this export starts 2026-05-01, so a customization older than that is
invisible and the order reads as un-customized.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

DB = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history.db"
HUMAN_WINDOW = 300          # seconds; Dan uses 5 minutes

HUMAN_SOURCES = ("customer", "store admin", "recharge admin")
AUTOMATED = ("recharge charge processing", "recharge customer sync")
CONTENT_KEYS = ("box_contents", "sku", "external_variant_id", "variant_title",
                "product_title", "bundle_selection")
CONTENT_VERBS = ("swapped",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rc_events (
    event_id     TEXT PRIMARY KEY,
    customer_id  TEXT,
    object_class TEXT,
    verb         TEXT,
    source       TEXT,
    kind         TEXT,        -- human | api | automated
    touches      INTEGER,     -- contents-touching
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_ev_cust ON rc_events(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_ev_touch ON rc_events(touches, customer_id);
"""


def api_event_to_row(e: dict) -> dict:
    """Normalise a Recharge /events API event to the CSV export's row shape.

    🔴 The API and the CSV export disagree on TWO fields, and both crashed or blinded the
    customize gate on 2026-09-03 before this existed:
      * `source` is a STRING in the CSV ("CUSTOMER", "[API] support@…", "Recharge Admin")
        and a DICT in the API ({origin, user_type, api_token_name, account_email, …}).
        classify() on the dict raised `'dict' object has no attribute 'strip'`.
      * `changes` (CSV) is `updated_attributes` (API): a list of {attribute, previous_value,
        new_value}. The API has NO `changes` key at all, so touches_contents() saw "" and
        9,532 of 11,168 touch events in the probe window became invisible -- the customize
        half was 85% blind through the API while the login half was 100% covered.
    Map the dict to the CSV vocabulary classify() already knows, and serialise
    updated_attributes so the CONTENT_KEYS substring test works unchanged.
    """
    import json as _json
    src = e.get("source")
    if isinstance(src, dict):
        ut, origin = (src.get("user_type") or ""), (src.get("origin") or "")
        if src.get("api_token_name") or origin == "api":
            src = f"[API] {src.get('api_token_name') or ''}".strip()
        elif ut == "customer":
            src = "CUSTOMER"
        elif ut in ("recharge_admin", "store_admin"):
            src = "Recharge Admin" if ut == "recharge_admin" else "Store Admin"
        elif "recharge_process" in (ut, origin):
            src = "Recharge Charge Processing"
        else:
            src = ""                                   # classify() -> automated
    ua = e.get("updated_attributes")
    changes = e.get("changes") or (_json.dumps(ua) if ua else "")
    return {"event_id": str(e.get("id")), "customer_id": str(e.get("customer_id")),
            "object_class": e.get("object_type"), "verb": e.get("verb") or "",
            "source": src or "", "changes": changes,
            "description": e.get("description") or "",
            "created_at": (e.get("created_at") or "").replace("T", " ")[:19]}


def classify(source) -> str:
    if isinstance(source, dict):                       # API shape -- normalise first
        source = api_event_to_row({"source": source})["source"]
    s = (source or "").strip().lower()
    if s.startswith("[api]") or s == "api":
        return "api"
    if any(h == s or s.startswith(h) for h in HUMAN_SOURCES):
        return "human"
    if any(a in s for a in AUTOMATED):
        return "automated"
    return "automated"


def touches_contents(verb: str, changes: str, description: str) -> bool:
    v = (verb or "").lower()
    if any(k in v for k in CONTENT_VERBS) or "bundle_selection" in v:
        return True
    blob = f"{changes or ''} {description or ''}".lower()
    return any(k in blob for k in CONTENT_KEYS)


def build(path, db=DB):
    csv.field_size_limit(10 ** 9)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM rc_events")
    rows, n = [], 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            n += 1
            rows.append((r["event_id"], r["customer_id"], r["object_class"], r["verb"],
                         r["source"], classify(r["source"]),
                         1 if touches_contents(r["verb"], r.get("changes"),
                                               r.get("description")) else 0,
                         r["created_at"]))
            if len(rows) >= 100000:
                con.executemany("INSERT OR REPLACE INTO rc_events VALUES (?,?,?,?,?,?,?,?)", rows)
                con.commit()
                rows = []
                print(f"  {n:,} events...", flush=True)
    if rows:
        con.executemany("INSERT OR REPLACE INTO rc_events VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    stats(con)
    con.close()


def stats(con=None):
    close = con is None
    con = con or sqlite3.connect(DB)
    q = lambda s: con.execute(s).fetchone()[0]        # noqa: E731
    print(f"  events            {q('SELECT COUNT(*) FROM rc_events'):>10,}")
    print(f"  contents-touching {q('SELECT COUNT(*) FROM rc_events WHERE touches=1'):>10,}")
    for k in ("human", "api", "automated"):
        print(f"    {k:<16}{q(f'SELECT COUNT(*) FROM rc_events WHERE touches=1 AND kind={chr(39)}{k}{chr(39)}'):>10,}")
    print(f"  date range        {q('SELECT MIN(created_at) FROM rc_events')} .. "
          f"{q('SELECT MAX(created_at) FROM rc_events')}")
    if close:
        con.close()


def norm_ts(iso):
    """Recharge event timestamps -> the shape rc_events actually stores.

    🔴 `rc_events.created_at` is `2026-05-01 00:00:17` (space, no zone). A Shopify order
    `createdAt` is `2026-07-15T04:04:11Z`. Comparing them as TEXT is not a near-miss --
    space is 0x20 and 'T' is 0x54, so every event on the SAME CALENDAR DAY as the
    threshold sorts BELOW it and reads as absent. That silently blinded the login half of
    the swap guardrail to same-day logins: 1,208 orders read as protected on
    RMFG_20260828 where the true figure is 1,247. Normalise before any comparison.
    """
    return (iso or "").replace("T", " ").rstrip("Z").strip()


def customized(con, recharge_customer_id, since_iso=None):
    """True when a HUMAN changed this customer's contents (optionally since a date).

    An api-origin contents change counts only when a human-sourced event on the same
    customer lands within HUMAN_WINDOW seconds of it.
    """
    if not recharge_customer_id:
        return False, ""
    args = [str(recharge_customer_id)]
    extra = ""
    if since_iso:
        extra = " AND created_at >= ?"
        args.append(norm_ts(since_iso))
    hits = con.execute(
        f"""SELECT created_at, verb, source, kind FROM rc_events
            WHERE customer_id = ? AND touches = 1{extra} ORDER BY created_at""", args).fetchall()
    if not hits:
        return False, ""
    for created, verb, source, kind in hits:
        if kind == "human":
            return True, f"{created} {verb} [{source}]"
    # api-origin: human activity nearby makes it human (a portal edit runs as an api call)
    for created, verb, source, kind in hits:
        if kind != "api":
            continue
        near = con.execute(
            """SELECT created_at, source FROM rc_events
               WHERE customer_id = ? AND kind = 'human'
                 AND ABS(STRFTIME('%s', created_at) - STRFTIME('%s', ?)) <= ?
               LIMIT 1""",
            (str(recharge_customer_id), created, HUMAN_WINDOW)).fetchone()
        if near:
            return True, f"{created} {verb} [api, human {near[1]} at {near[0]}]"
    return False, ""


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "build":
        p = sys.argv[2] if len(sys.argv) > 2 else None
        if not p or not os.path.exists(p):
            raise SystemExit("usage: python -m order_checks.recharge_gate build <events.csv>")
        build(p)
    else:
        stats()
