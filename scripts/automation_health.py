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
    db = APPDATA_AH / "shipping.db"
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


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    findings: list[str] = []
    try:
        check_beats(findings)
        check_sync_heartbeat(findings)
        check_schtasks(findings)
        check_shipping_db(findings)
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
        print("automation-health: all green (beats, ingest, schtasks, db)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
