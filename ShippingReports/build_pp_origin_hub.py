"""Build the `pp_origin_hub` derived table: PP-native origin hub, from events we ALREADY have.

🔴 CONSTRAINTS SSOT: `AppyHour/PP_ORIGIN_HUB_RULES.md` — read it before changing this script.
Pure logic lives in `AppyHour/appyhour_lib/pp_origin.py`; this file is the I/O shell only.

WHAT IT IS. One row per order per ship leg, carrying a COMPLETE lane — canonical carrier,
scan-derived origin hub, assigned hub from our own routing tag, destination zip5, pickup, delivery,
calendar transit — derived entirely from `pp_webhook_events.payload_json`. It answers "which hub did
this box actually leave from" without an invoice join and without `fulfillments`, and it fills the
gap left by `delivery_status.origin_hub`, which is NULL everywhere by design (STATUS_INGEST_RULES
rule 20 forbids writing it from a routing tag — that compares the tag to itself and reads a fake
100%). A SCAN is not a tag, so it is a genuine second source.

🔴 READ-ONLY on cloud MySQL. The only statement issued there is SELECT. `pp_webhook_events` is
owned by the flow-api webhook route (DATA_CANON_RULES) and this script must never write it, ALTER
it, or create anything beside it. The connection is opened with autocommit off and nothing is ever
committed.

🔴 ZERO ParcelPanel API calls, permanently. Every field comes from payloads DigitalOcean already
ingested. Adding a PP fetch here would re-create the exact quota drain the webhook was built to
delete (STATUS_INGEST_RULES rule 26: no consumer keeps its own carrier call "just in case").

🔴 WRITE TARGET is the LOCAL sqlite `shipping.db`, through `appyhour_lib.db.connect()` ONLY — never
a raw `sqlite3.connect()` on the canonical file (that is how it corrupted three times in one week).
`--apply` is required to write; the default is a dry run that prints the full report and touches
nothing.

🔴 WHY IT LIVES HERE, not in `_outputs/scripts/` beside `ingest_middle_mile.py` (the other live
shipping.db table builder): `_outputs/` is NOT a git repository. A builder there is single-copy and
unversioned, and this one is the only thing that can rebuild `pp_origin_hub`
([[single-copy-sources-of-truth]]). It sits in `ShippingReports/` because its constraints doc, its
consumer (`carrier_mix_pivot.py`) and its rules SSOT (RESHIP_REPORT_RULES D35) are all here.

Usage:
  python build_pp_origin_hub.py                 # DRY RUN — report only, no writes anywhere
  python build_pp_origin_hub.py --apply         # write/refresh the local sqlite table
  python build_pp_origin_hub.py --since-days 30 # narrow the source window (default: all events)
  python build_pp_origin_hub.py --json <path>   # also dump the report as JSON
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_AH = r"C:/Users/Work/Claude Projects/AppyHour"
if _AH not in sys.path:
    sys.path.insert(0, _AH)
from appyhour_lib.db import connect  # noqa: E402
# WAL + busy_timeout + single-writer lock — NEVER raw sqlite3.connect on shipping.db.
from appyhour_lib import pp_origin  # noqa: E402

_SR_LIB = r"C:/Users/Work/Claude Projects/ShipRouting/lib"
if _SR_LIB not in sys.path:
    sys.path.insert(0, _SR_LIB)
import canon  # noqa: E402
# 🔴 The carrier canonicaliser (LaserShip → OnTrac) and the ONLY correct routing-tag reader.
# Imported, never copied — ShipRouting is not ours to edit and its grammar is not ours to restate.

TABLE = "pp_origin_hub"

COLUMNS = [
    "order_number", "tracking", "carrier", "carrier_raw", "pp_status",
    "origin_hub", "hub_source", "origin_scan_city", "origin_scan_state", "origin_scan_zip",
    "origin_label_zip", "first_physical_checkpoint_at",
    "assigned_hub", "assigned_tag", "hub_agree",
    "dest_zip5", "pickup_at", "delivered_at", "transit_days",
    "source_event_id", "source_received_at", "derived_at",
]


# ── cloud MySQL, READ-ONLY ────────────────────────────────────────────────────────────────────────
def _database_url():
    """Same credential resolution as `pull_cloud_replicas.database_url()` — env, else the ACL'd
    file Kurt creates in the REAL %APPDATA% (never written from here)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    f = Path(os.environ.get("APPDATA", "")) / "AppyHour" / "database_url.txt"
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _fetch_events(since_days=None):
    """Newest landed event per order_number → [(id, order_number, received_at, payload_json)].

    🔴 The NEWEST event per order is deliberate: PP resends the FULL checkpoint history on every
    notification, so the latest payload is a superset of the earlier ones. Taking `MIN(id)` would
    read a payload written before the box was ever scanned.

    🔴 `state='landed'` only. Quarantined rows are kept as evidence and must NEVER be derived from
    (pp_webhook.land: "quarantined rows NEVER derive"). Rows with no order_number never derive
    either — FedEx reuses tracking numbers, so a tracking-keyed join silently attaches a stale
    shipment to a live box (STATUS_INGEST_RULES rule 1).
    """
    import pymysql
    url = _database_url()
    if not url:
        raise SystemExit("no DATABASE_URL (env or %APPDATA%/AppyHour/database_url.txt)")
    m = re.match(r"mysql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)", url)
    if not m:
        raise SystemExit("DATABASE_URL unparseable")
    u, p, h, port, db = m.groups()
    con = pymysql.connect(host=h, port=int(port), user=u, password=p, database=db,
                          ssl={"ssl": {}}, autocommit=False)
    try:
        cur = con.cursor()
        where = "state='landed' AND order_number IS NOT NULL AND order_number <> ''"
        args = []
        if since_days:
            where += " AND received_at >= (NOW() - INTERVAL %s DAY)"
            args.append(int(since_days))
        cur.execute(f"SELECT MAX(id) FROM pp_webhook_events WHERE {where} GROUP BY order_number",
                    args)
        ids = [r[0] for r in cur.fetchall()]
        rows = []
        for i in range(0, len(ids), 300):
            chunk = ids[i:i + 300]
            cur.execute(
                "SELECT id, order_number, received_at, payload_json FROM pp_webhook_events "
                "WHERE id IN (%s)" % ",".join(["%s"] * len(chunk)), chunk)
            rows.extend(cur.fetchall())
        return rows
    finally:
        con.close()          # never committed — this connection only ever ran SELECT


# ── local sqlite ──────────────────────────────────────────────────────────────────────────────────
def _ensure_table(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          order_number   TEXT NOT NULL,   -- bare, no '#' (join-zero class)
          tracking       TEXT,
          carrier        TEXT,            -- CANONICAL (canon.normalize_carrier); LaserShip -> OnTrac
          carrier_raw    TEXT,            -- what PP actually said, kept for audit
          pp_status      TEXT,
          origin_hub     TEXT,            -- scan-derived hub, or 'MISSING' — never a guess
          hub_source     TEXT,            -- how origin_hub was resolved; see pp_origin.HUB_SOURCES
          origin_scan_city  TEXT,
          origin_scan_state TEXT,
          origin_scan_zip   TEXT,         -- TEXT, leading zeros are real
          origin_label_zip  TEXT,         -- FedEx shipper-declared origin zip, RAW/unmapped
          first_physical_checkpoint_at TEXT,
          assigned_hub   TEXT,            -- from OUR routing tag (canon.parse_routing_tag)
          assigned_tag   TEXT,
          hub_agree      INTEGER,         -- 1 / 0 / NULL when either side is unknown
          dest_zip5      TEXT,            -- TEXT, truncated from ZIP+4
          pickup_at      TEXT,            -- ET ISO
          delivered_at   TEXT,            -- ET ISO
          transit_days   INTEGER,         -- CALENDAR days between ET dates; never PP transit_time
          source_event_id     INTEGER,
          source_received_at  TEXT,
          derived_at     TEXT NOT NULL,
          PRIMARY KEY (order_number)
        )""")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_pp_origin_hub ON {TABLE}(origin_hub)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_pp_origin_pickup ON {TABLE}(pickup_at)")


# ── report ────────────────────────────────────────────────────────────────────────────────────────
def _report(rows, control):
    """Every rate here is stated with its denominator and its control. A bare percentage is a claim."""
    n = len(rows)
    out = {"orders": n, "generated_at": datetime.now(pp_origin.ET).isoformat()}

    scanned = sum(1 for r in rows if r["first_physical_checkpoint_at"])
    tagged = sum(1 for r in rows if r["assigned_hub"])
    mapped = sum(1 for r in rows if r["origin_hub"] != pp_origin.MISSING)
    out["first_physical_scan"] = [scanned, n]
    out["assigned_hub_tag"] = [tagged, n]
    out["origin_hub_mapped"] = [mapped, n]
    out["hub_source"] = dict(Counter(r["hub_source"] for r in rows).most_common())
    out["carrier"] = dict(Counter(r["carrier"] for r in rows).most_common())
    out["carrier_raw"] = dict(Counter(r["carrier_raw"] for r in rows).most_common())
    out["control_first_scan_equals_pp_pickup"] = control

    # 🔴 TWO disagreement rates, never one blended number.
    #   tier-1 rows are mapped from ShipRouting/lib/hubs.py zips — INDEPENDENT of our tags.
    #   tier-2 rows were clustered FROM the tags, so their agreement is partly circular and is
    #   reported separately ([[count-only-independent-checks]]).
    for label, src in (("independent_tier1", "scan_authority_zip"),
                       ("derived_tier2", "scan_derived_facility")):
        sub = [r for r in rows if r["hub_source"] == src and r["hub_agree"] is not None]
        dis = [r for r in sub if not r["hub_agree"]]
        out[f"disagreement_{label}"] = {
            "comparable": len(sub), "disagree": len(dis),
            "rate": (len(dis) / len(sub)) if sub else None,
            "examples": [{"order_number": r["order_number"], "carrier": r["carrier"],
                          "scan": f"{r['origin_scan_city']}, {r['origin_scan_state']} "
                                  f"{r['origin_scan_zip'] or ''}".strip(),
                          "scan_hub": r["origin_hub"], "assigned_hub": r["assigned_hub"],
                          "assigned_tag": r["assigned_tag"]}
                         for r in dis[:25]],
        }
    comparable = [r for r in rows if r["hub_agree"] is not None]
    dis_all = [r for r in comparable if not r["hub_agree"]]
    out["disagreement_overall"] = {
        "comparable": len(comparable), "disagree": len(dis_all),
        "rate": (len(dis_all) / len(comparable)) if comparable else None}

    # MISSING, broken out by the facility that produced it — this is Kurt's decision list.
    miss = defaultdict(lambda: {"n": 0, "carriers": Counter(), "assigned_hubs": Counter(),
                                "label_zips": Counter()})
    for r in rows:
        if r["origin_hub"] != pp_origin.MISSING:
            continue
        key = (f"{r['origin_scan_city']}, {r['origin_scan_state']} {r['origin_scan_zip'] or ''}"
               .strip() if r["first_physical_checkpoint_at"] else "(no physical scan)")
        d = miss[key]
        d["n"] += 1
        d["carriers"][r["carrier"]] += 1
        d["assigned_hubs"][r["assigned_hub"]] += 1
        if r["origin_label_zip"]:
            d["label_zips"][r["origin_label_zip"]] += 1
    out["missing_facilities"] = sorted(
        ({"facility": k, "orders": v["n"], "carriers": dict(v["carriers"]),
          "assigned_hubs": dict(v["assigned_hubs"].most_common(5)),
          "label_origin_zips": dict(v["label_zips"])} for k, v in miss.items()),
        key=lambda d: -d["orders"])

    # The unmapped FedEx label-origin zips, for the authority-gap decision.
    lz = defaultdict(Counter)
    for r in rows:
        if r["origin_label_zip"]:
            lz[r["origin_label_zip"]][r["assigned_hub"]] += 1
    out["label_origin_zip_x_assigned_hub"] = {
        z: dict(c.most_common()) for z, c in sorted(lz.items(), key=lambda kv: -sum(kv[1].values()))}
    return out


def _print_report(rep):
    def pct(pair):
        a, b = pair
        return f"{a}/{b} = {a / b:.1%}" if b else f"{a}/0"

    print(f"\n=== pp_origin_hub — {rep['orders']} orders ===")
    print(f"  first physical scan found : {pct(rep['first_physical_scan'])}")
    print(f"  assigned-hub routing tag  : {pct(rep['assigned_hub_tag'])}")
    print(f"  origin_hub MAPPED         : {pct(rep['origin_hub_mapped'])}")
    c = rep["control_first_scan_equals_pp_pickup"]
    print(f"  CONTROL first-scan time == PP pickup_date : {pct(c)}"
          f"   <- known-present control; a low value invalidates the extraction")
    print(f"  hub_source : {rep['hub_source']}")
    print(f"  carrier (canonical) : {rep['carrier']}    raw: {rep['carrier_raw']}")

    for label in ("independent_tier1", "derived_tier2"):
        d = rep[f"disagreement_{label}"]
        r = "n/a" if d["rate"] is None else f"{d['rate']:.2%}"
        print(f"\n  TAG-vs-SCAN disagreement [{label}] : {d['disagree']}/{d['comparable']} = {r}")
        for e in d["examples"][:10]:
            print(f"     #{e['order_number']} {e['carrier']:7s} scan={e['scan']:26s} "
                  f"-> {e['scan_hub']:12s} tag={e['assigned_hub']} ({e['assigned_tag']})")
    d = rep["disagreement_overall"]
    r = "n/a" if d["rate"] is None else f"{d['rate']:.2%}"
    print(f"\n  TAG-vs-SCAN disagreement [ALL mapped] : {d['disagree']}/{d['comparable']} = {r}")

    print("\n  MISSING facilities (origin_hub='MISSING') — Kurt's decision list:")
    for m in rep["missing_facilities"]:
        print(f"     {m['orders']:5d}  {m['facility']:32s} {m['carriers']} "
              f"tags={m['assigned_hubs']} labelzip={m['label_origin_zips']}")

    print("\n  FedEx label-origin zip x assigned hub (RAW, unmapped — authority gap):")
    for z, c2 in rep["label_origin_zip_x_assigned_hub"].items():
        print(f"     zip {z}: {c2}")


# ── main ──────────────────────────────────────────────────────────────────────────────────────────
def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="WRITE the local sqlite table (default is a dry run that writes nothing)")
    ap.add_argument("--since-days", type=int, default=None,
                    help="only derive from events received in the last N days")
    ap.add_argument("--json", default=None, help="also write the report as JSON to this path")
    a = ap.parse_args()

    events = _fetch_events(a.since_days)
    print(f"[pp-origin] {len(events)} landed events (newest per order) read from cloud MySQL "
          f"(READ-ONLY)")
    if not events:
        # 🔴 A zero is a claim. An empty pull must never be allowed to blank a populated table.
        print("FLAG pp_origin_hub: source returned 0 events — local table left untouched")
        return 1

    now = datetime.now(pp_origin.ET).isoformat()
    rows, control = [], [0, 0]
    for eid, onum, received_at, payload_json in events:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        r = pp_origin.derive_origin(payload, canon=canon)
        # The raw row's order_number is the authority (pp_webhook.parse already stripped the '#').
        r["order_number"] = str(onum).strip().lstrip("#")
        r["source_event_id"] = eid
        r["source_received_at"] = str(received_at) if received_at else None
        r["derived_at"] = now
        rows.append(r)
        # Known-present control: PP's own pickup_date must equal the first physical scan we picked.
        # This is what proves the extraction found the RIGHT checkpoint, not merely A checkpoint.
        pk = payload.get("pickup_date")
        if pk and r["first_physical_checkpoint_at"]:
            control[1] += 1
            if str(pk)[:16] == str(r["first_physical_checkpoint_at"])[:16]:
                control[0] += 1

    rep = _report(rows, control)
    _print_report(rep)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
        print(f"\n[pp-origin] report -> {a.json}")

    if not a.apply:
        print("\n[pp-origin] DRY RUN — nothing written. Re-run with --apply to write "
              f"{TABLE} into shipping.db.")
        return 0

    con = connect()
    try:
        _ensure_table(con)
        # Full refresh inside ONE implicit transaction: DELETE + inserts commit or roll back
        # together, exactly like pull_cloud_replicas. The empty-pull refusal above is what makes
        # the DELETE safe.
        con.execute(f"DELETE FROM {TABLE}")
        con.executemany(
            f"INSERT OR REPLACE INTO {TABLE} ({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' * len(COLUMNS))})",
            [tuple(None if r.get(c) is None else
                   (int(r[c]) if c == "hub_agree" else r[c]) for c in COLUMNS) for r in rows])
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(f"\nok {TABLE}: {len(rows)} rows -> local shipping.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
