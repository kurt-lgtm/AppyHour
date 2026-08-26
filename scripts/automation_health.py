"""Automation-health checker (dead-man-switch) — HEARTBEAT_RULES.md is the constraints SSOT.

Daily, anomaly-first: silent when green, ONE consolidated Slack (appyhour_lib.notify) when red.
Alarms on ABSENCE (missing/stale heartbeats) — the signal the Slack-on-completion hooks
structurally can't produce. Also probes: sync_heartbeat.json age, AppyHour schtasks Last Result,
shipping.db integrity (READ-ONLY immutable — never a writer, rule 3).

Run:  PYTHONIOENCODING=utf-8 python scripts/automation_health.py [--verbose]
Exit: 0 green, 1 findings, 2 checker-broken (treat as red).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appyhour_lib.heartbeat import read_ledger, age_hours, beat  # noqa: E402
from appyhour_lib.notify import notify  # noqa: E402

APPDATA_AH = Path(os.environ.get("APPDATA", "")) / "AppyHour"

# Expectations live HERE, not in the ledger (rule 4): name -> max age in hours.
EXPECTED = {
    "offsite-backup": 8 * 24,
    "forecast-a-monitor": 8 * 24,
    "loop-scorecard": 8 * 24,
    "corrections-mining": 8 * 24,
    "automation-health": 2 * 24,
    "freshness-sweep": 8 * 24,
}
SYNC_HEARTBEAT_MAX_H = 48
SCHTASK_PREFIXES = ("appyhour_daily",)  # Windows tasks whose Last Result we audit


def check_beats(findings: list[str]) -> None:
    try:
        ledger = read_ledger()
    except Exception as e:
        findings.append(f"heartbeat LEDGER UNREADABLE ({type(e).__name__}: {e}) — treat as red")
        return
    for name, max_h in EXPECTED.items():
        ts = ledger.get(name)
        if not ts:
            if name == "automation-health" and not ledger:
                continue  # first ever run — self-beat lands below
            findings.append(f"heartbeat MISSING: {name} (expected every {max_h/24:.0f}d)")
        else:
            h = age_hours(ts)
            if h > max_h:
                findings.append(f"heartbeat STALE: {name} last {h/24:.1f}d ago (max {max_h/24:.0f}d)")


def check_sync_heartbeat(findings: list[str]) -> None:
    p = APPDATA_AH / "sync_heartbeat.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        findings.append(f"sync_heartbeat.json unreadable ({type(e).__name__}) — ingest state unknown")
        return
    newest = None
    for key, val in data.items():
        if key.endswith("_status"):
            continue
        try:
            ts = datetime.fromisoformat(str(val))
            newest = max(newest, ts) if newest else ts
        except ValueError:
            continue
    if newest is None:
        findings.append("sync_heartbeat.json has no parseable timestamps")
        return
    age_h = (datetime.now() - newest).total_seconds() / 3600
    if age_h > SYNC_HEARTBEAT_MAX_H:
        findings.append(f"ingest sync heartbeat stale: {age_h/24:.1f}d (max {SYNC_HEARTBEAT_MAX_H}h)")
    # status values may carry detail suffixes ("ok:new_invoices=6 ...") — prefix match, not equality
    bad = [k for k, v in data.items() if k.endswith("_status")
           and not str(v).lower().startswith(("ok", "success"))]
    if bad:
        findings.append(f"ingest legs not ok: {', '.join(bad)}")


def check_schtasks(findings: list[str]) -> None:
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/v"],
            capture_output=True, text=True, timeout=120,
        ).stdout
    except Exception as e:
        findings.append(f"schtasks query failed ({type(e).__name__}) — Windows task states unknown")
        return
    import csv
    import io
    for row in csv.DictReader(io.StringIO(out)):
        name = (row.get("TaskName") or "").lstrip("\\")
        if not name.lower().startswith(SCHTASK_PREFIXES):
            continue
        result = (row.get("Last Result") or "").strip()
        # 0 = ok; 267009 = currently running; 267011 = never yet run (fresh trigger)
        if result not in ("0", "267009", "267011", ""):
            findings.append(f"schtask {name}: Last Result {result} (last run {row.get('Last Run Time', '?')})")


def check_shipping_db(findings: list[str]) -> None:
    # 2026-07-22: probe the CANONICAL DB via the resolver — this probed the legacy
    # %APPDATA% path, which is archived (.orphan) since the split-brain cleanup, so the
    # old hardcode would false-alarm "unreadable" daily while canonical sat healthy.
    from appyhour_lib.paths import db_path as _db_path
    db = _db_path()
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            ok = c.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                findings.append(f"shipping.db quick_check: {ok}")
            n = c.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0]
            if n < 50000:
                findings.append(f"shipping.db fulfillments row count suspicious: {n}")
        finally:
            c.close()
    except Exception as e:
        findings.append(f"shipping.db unreadable read-only ({type(e).__name__}: {e})")


# Local sqlite REPLICAS of cloud-owned MySQL primaries (DATA_CANON ownership matrix).
# table -> (freshness column expr, max age days, why). 🔴 Why 4d and not the sweep's 14d
# (2026-08-26): the weekly Monday pull failed ONCE (transient DO MySQL 2003 timeout, 8/24) and
# shopify_orders sat 9 days stale behind a 14d gate — 1,513 of _SHIP_2026-08-24's orders missing
# locally, carrier-mix Pending denominator at 40.2%. The store takes orders daily and the pull now
# also runs on the weekday sync, so healthy age is ≤ ~3d (weekend); >4d = the pull chain is dead.
REPLICA_TABLE_CHECKS = [
    ("shopify_orders", "MAX(created_at)", 4,
     "cloud->local pull chain dead (feeds derive_failed_carriers/cohort denominators; 7/07 air burn)"),
    ("weather_history", "MAX(date)", 9,
     "cloud->local weather pull dead (weekly cadence; primary asserts its own freshness)"),
]
# Ingest STAMP written by daily_shipping_sync.run_cloud_replica_pull on each successful pull —
# METADATA (when the pull ran), deliberately distinct from the DATA-age checks above (an ingest
# timestamp is not an event date, and the 8/18-8/26 outage was unprovable without one). The two
# checks fail independently: a dead pull with a fresh-looking table trips the stamp; a pull that
# runs but moves nothing trips the data age.
REPLICA_STAMP = Path(r"C:\AppyHourData\replica_pull_stamp.json")
REPLICA_STAMP_MAX_D = 4


def check_replica_freshness(findings: list[str]) -> None:
    """Alarm when a local cloud-replica table stops moving (rule 11)."""
    from appyhour_lib.paths import db_path as _db_path
    try:
        c = sqlite3.connect(f"file:{_db_path()}?mode=ro&immutable=1", uri=True)
        try:
            for table, col, max_d, why in REPLICA_TABLE_CHECKS:
                newest = c.execute(f"SELECT {col} FROM {table}").fetchone()[0]
                if not newest:
                    findings.append(f"replica {table} EMPTY — {why}")
                    continue
                ts = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_d = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
                if age_d > max_d:
                    findings.append(
                        f"replica {table} stale: newest {newest} ({age_d:.1f}d, max {max_d}d) — {why}")
        finally:
            c.close()
    except Exception as e:
        findings.append(f"replica freshness unreadable ({type(e).__name__}: {e}) — pull state unknown")
    # stamp = ingest metadata; missing file is LOUD by design until the pull stage first deploys
    try:
        data = json.loads(REPLICA_STAMP.read_text(encoding="utf-8"))
        for table, meta in data.items():
            ts = datetime.fromisoformat(str(meta.get("pulled_at", "")).replace("Z", "+00:00"))
            age_d = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
            if age_d > REPLICA_STAMP_MAX_D:
                findings.append(f"replica pull stamp stale: {table} last pulled "
                                f"{meta.get('pulled_at')} ({age_d:.1f}d, max {REPLICA_STAMP_MAX_D}d)")
    except FileNotFoundError:
        findings.append("replica pull stamp missing (C:\\AppyHourData\\replica_pull_stamp.json) — "
                        "daily pull stage not yet deployed to C:\\AppyHourProd or never succeeded")
    except Exception as e:
        findings.append(f"replica pull stamp unreadable ({type(e).__name__}: {e})")


DEV_ROOT = Path(r"C:\Users\Work\Claude Projects\AppyHour")
PROD_ROOT = Path(r"C:\AppyHourProd\AppyHour")
PARITY_SKIP_DIRS = {".git", "__pycache__", "node_modules", "_archive", ".venv", "venv",
                    "dist", "build", "tests"}
# Only files that can route a DB write or resolve a live path — a doc/UI drift is noise.
PARITY_KEYWORDS = ("shipping.db", "db_path", "db_dir", "init_db", "DATA_ROOT",
                   "AppyHourData", "inventory_settings_path")
PARITY_MAX_LISTED = 6


def check_prod_parity(findings: list[str]) -> None:
    """Flag DB-relevant scripts where the DEV tree is newer than the PROD copy.

    🔴 Why this exists (2026-07-27): the scheduled tasks run from C:\\AppyHourProd,
    a SEPARATE copy of the dev tree. Four split-brain incidents in a row were
    "already fixed" in dev while prod ran the old file — 07-22's dir-keyed
    paths.db_path() and 07-24's sync_carrier_invoices guard both sat undeployed,
    so prod's guard called a stale resolver and silently wrote the legacy DB.
    NEGATIVE: half a fix is not a fix. Deploying a guard without its resolver is
    exactly how this recurred.

    Only reports dev-NEWER drift. Some prod files are legitimately newer (local
    hotfixes) — blanket-copying dev over prod would clobber them, so this never
    suggests a sweep, it names the files to review.
    """
    if not PROD_ROOT.exists() or not DEV_ROOT.exists():
        return  # not this machine's layout — nothing to compare
    stale: list[str] = []
    try:
        for dev_file in DEV_ROOT.rglob("*.py"):
            if PARITY_SKIP_DIRS & set(dev_file.relative_to(DEV_ROOT).parts):
                continue
            prod_file = PROD_ROOT / dev_file.relative_to(DEV_ROOT)
            if not prod_file.exists():
                continue
            dev_bytes = dev_file.read_bytes()
            if dev_bytes == prod_file.read_bytes():
                continue
            if dev_file.stat().st_mtime <= prod_file.stat().st_mtime:
                continue  # prod newer — a local hotfix, not a missed deploy
            text = dev_bytes.decode("utf-8", errors="replace")
            if any(k in text for k in PARITY_KEYWORDS):
                stale.append(str(dev_file.relative_to(DEV_ROOT)))
    except Exception as e:
        findings.append(f"prod parity check failed ({type(e).__name__}: {e}) — deploy state unknown")
        return
    if stale:
        stale.sort()
        shown = ", ".join(stale[:PARITY_MAX_LISTED])
        more = f" (+{len(stale) - PARITY_MAX_LISTED} more)" if len(stale) > PARITY_MAX_LISTED else ""
        findings.append(
            f"prod tree STALE vs dev on {len(stale)} DB-relevant file(s): {shown}{more} "
            "— C:\\AppyHourProd runs the scheduled tasks; an undeployed fix is not a fix"
        )


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    findings: list[str] = []
    try:
        check_beats(findings)
        check_sync_heartbeat(findings)
        check_schtasks(findings)
        check_shipping_db(findings)
        check_replica_freshness(findings)
        check_prod_parity(findings)
    except Exception as e:
        findings.append(f"CHECKER CRASHED mid-run ({type(e).__name__}: {e})")
        notify("🔴 automation-health checker crashed: " + findings[-1], level="error")
        return 2
    beat("automation-health")  # self-beat LAST (rule 7)
    if findings:
        msg = "🔴 automation-health: " + str(len(findings)) + " finding(s)\n• " + "\n• ".join(findings)
        print(msg)
        notify(msg, level="error")
        return 1
    if verbose:
        print("automation-health: all green (beats, ingest, schtasks, db, replicas, prod-parity)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
