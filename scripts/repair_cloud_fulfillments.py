"""Repair the DigitalOcean `fulfillments` table so DO can be canon for it (DO_READ_CONTRACT B1-R).

🔴 DRY BY DEFAULT. Writing requires BOTH `--apply` and `--yes-write-production`.
   Without them this measures the gap, predicts every gate, and touches nothing.

🔴 THIS SCRIPT NEVER WRITES ANYTHING ITSELF.
   The write is performed by the CANONICAL loader `ShipRouting/server/etl_history.py`
   (`--snapshot-from-canonical` then `--load --tables fulfillments`). This script is the
   DRY-RUN, the GATE, the ROLLBACK MANIFEST and the VERIFICATION that loader does not have —
   `etl_history.py` has no dry-run flag and no confirmation gate, and `--load` IS the write.
   Do not reimplement the loader here. A hand-rolled row-by-row upsert would replace an atomic
   `RENAME TABLE` with a partial-failure mode, and would bypass the loader's regression,
   natural-key and post-publication gates.

WHY THIS EXISTS
===============
The cloud `fulfillments` writer is not merely flag-off — **there is no `fulfillments` timer in
`server/ingest_worker.REGISTRY` at all**. The cloud copy only ever moved via a manual
`etl_history --load`, and the last one was 2026-08-12. Measured 2026-08-27, read-only, both sides:

    local  118,904 rows   MAX(updated_at) 2026-08-27 16:17:42
    cloud  113,993 rows   MAX(updated_at) 2026-08-12 05:12:11
    LOCAL-ONLY 4,911      CLOUD-ONLY 0      (identical on tracking_number AND order_number)

Ship weeks `2026-08-17` (2,362) and `2026-08-24` (2,545) are missing from cloud ENTIRELY.

🔴 The hole is INVISIBLE to every guard that exists today. `histdb.FLOORS['fulfillments']` is
75,000 and cloud holds 113,993, so the row floor passes a table missing two whole ship weeks.
Only a per-ship-week assertion catches this — which is why `--verify` below checks per-week
parity and not a row count.

A restarted cloud writer fixes this table FORWARD only; it cannot backfill 4,911 rows it never
saw. This repair fills the HOLE. Both are required, and this one lands FIRST — see ORDERING.

WHY LOCAL IS A SAFE SOURCE (measured, not assumed)
==================================================
Repairing a canonical table from an unvetted source spreads dirt instead of data. Local was
checked for the exact defect classes that blocked `shipments` (B2/B2b), and is clean:

  * duplicates      0 duplicate `(order_number, tracking_number)` groups; 118,904 rows /
                    118,904 distinct tracking — the UNIQUE index enforces it. (Cloud: also 0.)
                    Contrast `shipments`, where cloud carries 25,795 duplicate rows.
  * date formats    ONE format per column. `updated_at` len 19 on all 118,904 rows;
                    `fulfilled_at` len 25 (ISO+offset) on all; `ship_date` len 10 on 118,827
                    and NULL on 77. No `YYYYMMDD`/`YYYY-MM-DD` split — the B2 defect is ABSENT.
  * NULL keys       0 NULL/blank `order_number`, 0 NULL/blank `tracking_number`.
  * key format      bare digits both sides, 0 rows carrying '#'. No normalization needed.
  * the 4,911       0 NULL/blank in ANY column; `tracking_company` ∈ {OnTrac, FedEx, UPS};
                    all `dest_state` 2-char; all `dest_zip` match ^\\d{5}(-\\d{4})?$;
                    `fulfilled_at` 2026-08-17 .. 2026-08-25 — consistent with the two weeks.
  * grain           19 orders carry 2 fulfillments, matching the DATA_CANON declaration exactly.

⚠️ Local carries the DATA_CANON `known_defect` (writer keys on Shopify REST's numeric
`order_number`, so `#164878A` and `#164878` collide). This repair does NOT fix it and MUST NOT be
recorded as having fixed it. It copies the defect forward deliberately — the ETL mirrors, it does
not clean (DATA_CANON gotcha: "Fix defects at the writer").

THE KEY, AND THE SURVIVOR RULE
==============================
`etl_history` publishes by FULL REFRESH into a staging table then an atomic `RENAME TABLE`. So
there is no per-row upsert key and no per-row survivor contest: **LOCAL WINS WHOLESALE.**

🔴 That is only safe because CLOUD-ONLY IS ZERO. A full refresh DELETES every cloud row absent
from local. This is the `shipments` lesson applied: there, keying on tracking alone would have
deleted 6,801 sole-copy rows and 25,788 hub values, because the two copies were NOT identical.
Here they are — but that is a MEASUREMENT WITH A SHELF LIFE, not a property. If the cloud writer
is restarted before this repair runs, cloud will hold rows local has never seen and a full
refresh will destroy them.

**Therefore `--apply` REFUSES unless cloud-only == 0, re-measured at write time.** Never trusted
from this docstring, never from a prior run. That check is the whole safety argument.

Evidence the wholesale survivor rule loses nothing (measured on all 113,993 shared rows):
  * `id` identical on 113,993 of 113,993 — the cloud copy is a verbatim mirror, ids included.
  * cloud NEWER than local on **0** rows. Local newer on 10,498 (`updated_at`).
  * only 2 columns differ at all: `updated_at` (10,498) and `tags` (49).
  * all 49 `tags` diffs are post-08-12 business events — local GAINED 22 refund tags
    (`Refund`, `Partial Refund - Missing Item`, ...) and DROPPED 38 `_HOLD` (holds released).
    Local is the correct value on every one; there is no case where cloud should win.

The natural key `etl_history` asserts for this table is `("order_number", "tracking_number")`
(`NATURAL_KEYS`), and its metrics are invariant across the copy (0 dup groups / 0 missing on both
sides), so gate (f) passes. `id` is deliberately NOT this table's identity.

WHAT THE GATES WILL DO (pre-flight predicts each; the loader enforces them)
==========================================================================
  regression      live 113,993 rows !> stage 118,904            -> PASS
  live-is-newer   live 2026-08-11T17:02:17-04:00 !>
                  stage 2026-08-25T17:01:02-04:00               -> PASS  (compares fulfilled_at)
  column contract 16 columns, identical names both sides        -> PASS
  natural key     0 dup groups / 0 missing, both sides          -> PASS
  post-publish    re-validated after RENAME; failure auto-reverts

🔴 `--require-cohort` is UNUSABLE for a fulfillments-only load: `_validate_cohorts` demands
`delivery_status` AND `fulfillments` in the SAME publication, and `delivery_status` is
cloud-owned and excluded. Do not pass it and assume a cohort was proven — this script's
`--verify` is the cohort proof instead.

ROLLBACK
========
Written BEFORE the write, to `_outputs/reports/repair_cloud_fulfillments_<ts>.json`:
  * the pre-write cloud fingerprint (rows, distinct keys, MAX(updated_at)/MAX(fulfilled_at),
    per-ship-week counts) — so "did this help" is answerable without this script;
  * the full local-only order list, so the intended delta is auditable;
  * after the write, the `etl_rollback_fulfillments_<token>` table name parsed from loader output.

`etl_history` renames the prior live table to `etl_rollback_fulfillments_<token>` in the same
atomic RENAME. Undo is:
    python ShipRouting/server/etl_history.py --rollback-token <token> --tables fulfillments
⚠️ There is NO retention/prune for `etl_rollback_*` — the old copy persists until dropped by
hand. That is the rollback guarantee; do not "tidy" it away before the exit condition is met.

USAGE
    python repair_cloud_fulfillments.py                    # dry run: gap + gates + sample diff
    python repair_cloud_fulfillments.py --verify           # post-repair proof only, read-only
    python repair_cloud_fulfillments.py --apply --yes-write-production
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WORKSPACE = Path(r"C:\Users\Work\Claude Projects")
AH = WORKSPACE / "AppyHour"
SR = WORKSPACE / "ShipRouting"
SCRIPTS = WORKSPACE / "_outputs" / "scripts"   # for pull_cloud_replicas.database_url()
REPORTS = WORKSPACE / "_outputs" / "reports"
for _p in (str(AH), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TABLE = "fulfillments"
# The 16 columns, identical on both sides (verified 2026-08-27). Order is the sqlite DDL order.
COLS = ["id", "order_number", "order_id", "order_date", "tags", "tracking_number",
        "tracking_company", "tracking_url", "fulfilled_at", "customer_name", "dest_city",
        "dest_state", "dest_zip", "updated_at", "ship_date", "ship_week"]
# etl_history TABLES["fulfillments"] — the column its regression gate compares.
FRESHNESS_COL = "fulfilled_at"
# etl_history NATURAL_KEYS["fulfillments"]
NATURAL_KEY = ("order_number", "tracking_number")


# ---------------------------------------------------------------- connections (READ-ONLY both)

def local_con():
    """Read-only sqlite. 🔴 `connect_ro` only — Claude never write-connects shipping.db
    (three WAL corruptions)."""
    from appyhour_lib.db import connect_ro
    return connect_ro()


def cloud_con():
    """Read-only-by-discipline pymysql. Every statement this module issues is a SELECT."""
    import pymysql
    import pull_cloud_replicas as pcr
    url = pcr.database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL missing: not in env and no %APPDATA%\\AppyHour\\database_url.txt. "
            "🔴 The file must be created from a REAL terminal — Claude/MSIX writes to %APPDATA% "
            "land in a sandbox shadow.")
    m = re.match(r"mysql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)", url)
    if not m:
        raise RuntimeError("DATABASE_URL unparseable")
    u, p, h, port, db = m.groups()
    return pymysql.connect(host=h, port=int(port), user=u, password=p, database=db,
                           ssl={"ssl": {}})


# ---------------------------------------------------------------- measurement

def _control_join(lc, cc) -> tuple[bool, str]:
    """🔴 A ZERO IS A CLAIM. Prove a KNOWN-PRESENT key survives the join before trusting any
    zero this script prints.

    Positive control: five old local rows must be FOUND in cloud by tracking_number.
    Negative control: the same order under a '#'-prefixed key must return 0 — proving the
    comparison is format-sensitive and a silent normalization is not papering over the join.
    (`#132940` vs `132940` has produced confident zeros in this operation three times.)
    """
    rows = lc.execute(
        f"SELECT tracking_number, order_number FROM {TABLE} "
        f"WHERE updated_at < '2026-08-01' AND tracking_number IS NOT NULL "
        f"ORDER BY id LIMIT 5").fetchall()
    if not rows:
        return False, "no pre-August local rows available to use as a control"
    cur = cc.cursor()
    for tr, _on in rows:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE tracking_number=%s", (tr,))
        if cur.fetchone()[0] != 1:
            return False, f"positive control FAILED: known-present tracking {tr!r} not in cloud"
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE order_number=%s", ("#" + str(rows[0][1]),))
    if cur.fetchone()[0] != 0:
        return False, "negative control FAILED: a '#'-prefixed key matched — key formats differ"
    return True, f"{len(rows)}/5 positive + negative control passed"


def measure(lc, cc) -> dict:
    """Everything the gate and the report need, in one pass."""
    cur = cc.cursor()

    ltrk = {r[0] for r in lc.execute(f"SELECT tracking_number FROM {TABLE}")}
    cur.execute(f"SELECT tracking_number FROM {TABLE}")
    ctrk = {r[0] for r in cur.fetchall()}

    lord = {str(r[0]) for r in lc.execute(f"SELECT order_number FROM {TABLE}")}
    cur.execute(f"SELECT order_number FROM {TABLE}")
    cord = {str(r[0]) for r in cur.fetchall()}

    lcnt = lc.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    lupd = lc.execute(f"SELECT MAX(updated_at) FROM {TABLE}").fetchone()[0]
    lful = lc.execute(f"SELECT MAX({FRESHNESS_COL}) FROM {TABLE}").fetchone()[0]
    cur.execute(f"SELECT COUNT(*), MAX(updated_at), MAX({FRESHNESS_COL}) FROM {TABLE}")
    ccnt, cupd, cful = cur.fetchone()

    lwk = dict(lc.execute(
        f"SELECT COALESCE(ship_week,'(null)'), COUNT(*) FROM {TABLE} GROUP BY 1"))
    cur.execute(f"SELECT COALESCE(ship_week,'(null)'), COUNT(*) FROM {TABLE} GROUP BY 1")
    cwk = {r[0]: r[1] for r in cur.fetchall()}

    ldup = lc.execute(
        f"SELECT COUNT(*) FROM (SELECT {NATURAL_KEY[0]},{NATURAL_KEY[1]} FROM {TABLE} "
        f"GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM (SELECT {NATURAL_KEY[0]},{NATURAL_KEY[1]} FROM {TABLE} "
                f"GROUP BY 1,2 HAVING COUNT(*)>1) t")
    cdup = cur.fetchone()[0]

    only_local = ltrk - ctrk
    only_cloud = ctrk - ltrk
    lonly_orders = sorted(lord - cord)

    # profile the local-only rows by ship_week (the shape of the hole)
    hole = collections.Counter()
    for tr, wk in lc.execute(f"SELECT tracking_number, ship_week FROM {TABLE}"):
        if tr in only_local:
            hole[wk or "(null)"] += 1

    return {
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "local": {"rows": lcnt, "max_updated_at": lupd, f"max_{FRESHNESS_COL}": lful,
                  "distinct_tracking": len(ltrk), "distinct_order": len(lord),
                  "natural_key_dup_groups": ldup, "by_ship_week": lwk},
        "cloud": {"rows": ccnt, "max_updated_at": cupd, f"max_{FRESHNESS_COL}": cful,
                  "distinct_tracking": len(ctrk), "distinct_order": len(cord),
                  "natural_key_dup_groups": cdup, "by_ship_week": cwk},
        "only_local_tracking": len(only_local),
        "only_cloud_tracking": len(only_cloud),
        "only_cloud_sample": sorted(only_cloud)[:20],
        "only_local_orders": lonly_orders,
        "hole_by_ship_week": dict(sorted(hole.items())),
    }


def _iso(v):
    try:
        return datetime.datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def gates(m: dict) -> list[tuple[str, bool, str]]:
    """Predict every gate. `ok=False` on ANY row means --apply must refuse."""
    lc_, cc_ = m["local"], m["cloud"]
    out = []

    # 🔴 THE gate. A full refresh deletes every cloud row absent from local.
    n = m["only_cloud_tracking"]
    out.append(("cloud-only rows == 0 (a full refresh would DELETE them)", n == 0,
                f"cloud-only={n}" + ("" if n == 0 else f"  sample={m['only_cloud_sample'][:5]}")))

    out.append(("there is actually a hole to fill", m["only_local_tracking"] > 0,
                f"local-only={m['only_local_tracking']}"))

    out.append((f"etl_history regression: live rows !> stage",
                cc_["rows"] <= lc_["rows"], f"live={cc_['rows']} stage={lc_['rows']}"))

    a, b = _iso(cc_[f"max_{FRESHNESS_COL}"]), _iso(lc_[f"max_{FRESHNESS_COL}"])
    if a is None or b is None:
        out.append((f"etl_history _live_is_newer ({FRESHNESS_COL})", False,
                    "🔴 unparseable timestamp — the loader's gate FAILS CLOSED here"))
    else:
        out.append((f"etl_history _live_is_newer: live !> stage ({FRESHNESS_COL})", not (a > b),
                    f"live={a.isoformat()} stage={b.isoformat()}"))

    out.append(("natural-key metrics invariant (0 dups both sides)",
                lc_["natural_key_dup_groups"] == 0 and cc_["natural_key_dup_groups"] == 0,
                f"local={lc_['natural_key_dup_groups']} cloud={cc_['natural_key_dup_groups']}"))

    out.append(("local is a strict superset by order_number",
                len(m["only_local_orders"]) == lc_["distinct_order"] - cc_["distinct_order"],
                f"local-only orders={len(m['only_local_orders'])}"))
    return out


def per_week_table(m: dict) -> str:
    lw, cw = m["local"]["by_ship_week"], m["cloud"]["by_ship_week"]
    lines = [f"{'ship_week':<16}{'local':>9}{'cloud':>9}{'delta':>9}"]
    for w in sorted(set(lw) | set(cw), key=str):
        a, b = lw.get(w, 0), cw.get(w, 0)
        if a != b or str(w) >= "2026-07-20":
            lines.append(f"{str(w):<16}{a:>9}{b:>9}{a - b:>9}"
                         + ("   🔴 MISSING FROM CLOUD" if b == 0 and a else ""))
    return "\n".join(lines)


def sample_diff(lc, m: dict, n: int = 10) -> str:
    """The exact rows that would be added — a count alone is not a diff."""
    orders = m["only_local_orders"][:n]
    if not orders:
        return "  (none)"
    q = ",".join("?" * len(orders))
    rows = lc.execute(
        f"SELECT order_number, ship_week, tracking_company, tracking_number, dest_state, "
        f"{FRESHNESS_COL} FROM {TABLE} WHERE order_number IN ({q}) ORDER BY order_number",
        orders).fetchall()
    return "\n".join(
        f"  + order={r[0]:<8} wk={r[1]:<12} {r[2]:<7} {r[3]:<20} {r[4]:<3} {r[5]}" for r in rows)


# ---------------------------------------------------------------- verification

def verify(lc, cc) -> tuple[bool, str]:
    """🔴 PROOF IS THE TABLE READING DIFFERENTLY, not the script reporting success.

    Three independent checks — a row count alone cannot see a missing ship week (the 75,000
    histdb floor passes a table missing two of them).
    """
    m = measure(lc, cc)
    lines, ok = [], True

    n = m["only_local_tracking"]
    good = n == 0
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] cloud-only-missing (local-only rows) = {n}"
                 f"  (target 0)")

    n2 = m["only_cloud_tracking"]
    good = n2 == 0
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] no rows were DESTROYED: cloud-only = {n2}"
                 f"  (target 0 — a full refresh must not have invented rows)")

    lw, cw = m["local"]["by_ship_week"], m["cloud"]["by_ship_week"]
    bad = [w for w in set(lw) | set(cw) if lw.get(w, 0) != cw.get(w, 0)]
    good = not bad
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] per-ship-week parity across "
                 f"{len(set(lw) | set(cw))} weeks"
                 + ("" if good else f"  MISMATCHED: {sorted(map(str, bad))}"))

    # Spot-check named orders from the two repaired weeks, read back FROM DO.
    cur = cc.cursor()
    for wk in ("2026-08-17", "2026-08-24"):
        spot = lc.execute(
            f"SELECT order_number, tracking_number, tracking_company FROM {TABLE} "
            f"WHERE ship_week=? ORDER BY order_number LIMIT 3", (wk,)).fetchall()
        for on, tr, co in spot:
            cur.execute(f"SELECT tracking_number, tracking_company FROM {TABLE} "
                        f"WHERE order_number=%s", (on,))
            got = cur.fetchall()
            hit = any(g[0] == tr and g[1] == co for g in got)
            ok &= hit
            lines.append(f"  [{'PASS' if hit else 'FAIL'}] wk{wk} order {on} readable from DO "
                         f"with tracking {tr} / {co}")

    lines.append(f"\n  cloud now: {m['cloud']['rows']} rows, "
                 f"MAX(updated_at) {m['cloud']['max_updated_at']}")
    return ok, "\n".join(lines)


# ---------------------------------------------------------------- the write (delegated)

def run_repair(manifest_path: Path) -> tuple[int, str | None]:
    """Shell out to the CANONICAL loader. Returns (returncode, rollback_token).

    🔴 Run this from Kurt's REAL terminal. The snapshot step read-connects the canonical
    shipping.db via `connect_ro`, which is safe, but the MySQL credential resolution needs the
    real %APPDATA% and the cloud write needs Kurt's explicit go.
    """
    py = sys.executable
    etl = SR / "server" / "etl_history.py"
    snap = REPORTS / f"fulfillments_repair_snapshot_{datetime.datetime.now():%Y%m%d%H%M%S}.db"

    print(f"\n[1/2] snapshot canonical sqlite -> {snap}")
    r1 = subprocess.run([py, str(etl), "--snapshot-from-canonical", "--out", str(snap)],
                        cwd=str(SR), capture_output=True, text=True)
    print(r1.stdout or "", (r1.stderr or "")[:2000])
    if r1.returncode != 0:
        return r1.returncode, None

    print(f"\n[2/2] publish {TABLE} to MySQL (staging -> atomic RENAME)")
    r2 = subprocess.run([py, str(etl), "--snapshot", str(snap), "--load", "--tables", TABLE],
                        cwd=str(SR), capture_output=True, text=True)
    out = (r1.stdout or "") + (r2.stdout or "") + (r2.stderr or "")
    print(r2.stdout or "", (r2.stderr or "")[:4000])

    token = None
    mt = re.search(r"token[=\s]+(\d{14}[0-9a-f]{6})", out)
    if mt:
        token = mt.group(1)
    try:
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
        man["loader_returncode"] = r2.returncode
        man["rollback_token"] = token
        man["rollback_table"] = f"etl_rollback_{TABLE}_{token}" if token else None
        man["rollback_command"] = (
            f'python "{etl}" --rollback-token {token} --tables {TABLE}' if token else
            "🔴 TOKEN NOT PARSED — find it with: SHOW TABLES LIKE 'etl_rollback_fulfillments_%'")
        man["snapshot_path"] = str(snap)
        man["loader_output"] = out[-8000:]
        manifest_path.write_text(json.dumps(man, indent=2, default=str), encoding="utf-8")
    except OSError as e:
        print(f"⚠️ could not update manifest: {e}")
    return r2.returncode, token


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="perform the repair (also needs --yes-write-production)")
    ap.add_argument("--yes-write-production", action="store_true",
                    help="second gate: acknowledges this mutates the DO MySQL primary")
    ap.add_argument("--verify", action="store_true",
                    help="read-only post-repair proof; makes no changes")
    ap.add_argument("--json-out", default=None, help="write the full measurement to this path")
    args = ap.parse_args()

    if args.apply and not args.yes_write_production:
        print("REFUSED: --apply also requires --yes-write-production. Nothing written.")
        return 2

    lc, cc = local_con(), cloud_con()
    try:
        ctrl_ok, ctrl_msg = _control_join(lc, cc)
        print(f"control join: {ctrl_msg}")
        if not ctrl_ok:
            print("🔴 REFUSED: the join control failed, so every zero below is untrustworthy.")
            return 2

        if args.verify:
            ok, report = verify(lc, cc)
            print("\n=== VERIFICATION (read-only) ===")
            print(report)
            print("\nVERDICT:", "REPAIRED ✅" if ok else "🔴 NOT REPAIRED")
            return 0 if ok else 1

        m = measure(lc, cc)
        print(f"\n=== GAP, measured {m['measured_at']} ===")
        print(f"  local  {m['local']['rows']:>7} rows   MAX(updated_at) {m['local']['max_updated_at']}")
        print(f"  cloud  {m['cloud']['rows']:>7} rows   MAX(updated_at) {m['cloud']['max_updated_at']}")
        print(f"  LOCAL-ONLY {m['only_local_tracking']}    CLOUD-ONLY {m['only_cloud_tracking']}")
        print(f"\n=== PER SHIP WEEK ===\n{per_week_table(m)}")
        print(f"\n=== THE HOLE, by ship_week ===\n  {m['hole_by_ship_week']}")
        print(f"\n=== SAMPLE OF ROWS THAT WOULD BE ADDED (first 10 of "
              f"{len(m['only_local_orders'])}) ===\n{sample_diff(lc, m)}")

        print("\n=== GATES ===")
        g = gates(m)
        for name, ok_, detail in g:
            print(f"  [{'PASS' if ok_ else 'REFUSE'}] {name}\n           {detail}")
        all_ok = all(x[1] for x in g)

        if args.json_out:
            Path(args.json_out).write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
            print(f"\nmeasurement written to {args.json_out}")

        if not (args.apply and args.yes_write_production):
            print(f"\nDRY RUN — nothing written. {m['only_local_tracking']} row(s) would be "
                  f"added to the DO primary via a full refresh.")
            print("To apply: --apply --yes-write-production   (🔴 needs Kurt's explicit go; the "
                  "cloud write is Routing Coordinator's surface)")
            return 0 if all_ok else 1

        if not all_ok:
            print("\n🔴 REFUSED: a gate above did not pass. Nothing written.")
            return 2

        REPORTS.mkdir(parents=True, exist_ok=True)
        manifest = REPORTS / f"repair_cloud_fulfillments_{datetime.datetime.now():%Y%m%d%H%M%S}.json"
        manifest.write_text(json.dumps(
            {"pre_write_measurement": m, "gates": [[a, b, c] for a, b, c in g]},
            indent=2, default=str), encoding="utf-8")
        print(f"\nrollback manifest (pre-write state) -> {manifest}")

        rc, token = run_repair(manifest)
        if rc != 0:
            print(f"\n🔴 loader exited {rc}. etl_history rolls its own publication back on "
                  f"failure; re-run this script with --verify to see the actual table state.")
            return rc
        print(f"\nrollback token: {token or '🔴 NOT PARSED — see manifest'}")

        # PROOF comes from re-reading DO, on fresh connections.
        cc.close()
        cc = cloud_con()
        ok, report = verify(lc, cc)
        print("\n=== VERIFICATION (re-read from DO) ===")
        print(report)
        print("\nVERDICT:", "REPAIRED ✅" if ok else "🔴 NOT REPAIRED — consider rollback")
        return 0 if ok else 1
    finally:
        try:
            lc.close()
        finally:
            cc.close()


if __name__ == "__main__":
    raise SystemExit(main())
