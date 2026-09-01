"""Automation-health checker (dead-man-switch) — HEARTBEAT_RULES.md is the constraints SSOT.

Daily, anomaly-first: silent when green, ONE consolidated Slack (appyhour_lib.notify) when red.
Alarms on ABSENCE (missing/stale heartbeats) — the signal the Slack-on-completion hooks
structurally can't produce. Also probes: sync_heartbeat.json age, AppyHour schtasks Last Result,
shipping.db integrity (READ-ONLY immutable — never a writer, rule 3).

Every check above grades ONE automation. `check_task_set` grades the SET — same-slot collisions,
two owners on one script, two writers on one heartbeat key (which defeats this very checker), and
expectations/beats with no counterpart. See its block comment for what it deliberately lets
through and why.

Run:  python scripts/automation_health.py [--verbose]   (bootstrap.init handles UTF-8 stdio)
Exit: 0 green, 1 findings, 2 checker-broken (treat as red).
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appyhour_lib.bootstrap import init  # noqa: E402
from appyhour_lib.heartbeat import read_ledger, age_hours, beat  # noqa: E402
from appyhour_lib.notify import notify  # noqa: E402

APPDATA_AH = Path(os.environ.get("APPDATA", "")) / "AppyHour"

# Expectations live HERE, not in the ledger (rule 4): name -> max age in hours.
EXPECTED = {
    "offsite-backup": 8 * 24,
    "forecast-a-monitor": 8 * 24,
    "loop-scorecard": 8 * 24,
    "corrections-mining": 8 * 24,
    # 🔴 4d, not 2d (2026-08-31): the owning routine `automation-health-daily` is cron
    # `15 12 * * 1-5` — WEEKDAYS ONLY. Friday 12:16 -> Monday 12:16 is 72h, so a 48h limit graded
    # Friday's healthy run stale every single Monday, forever, and 08-31 fed that false alarm into
    # the rule-12 dispatcher. 4d = the Fri->Mon gap plus one missed weekday, same shape and reason
    # as pytest-shiprouting below. Chosen over a weekday-aware limit deliberately: "is the beat
    # older than N hours" is one comparison anyone can verify, while "what was the previous
    # SCHEDULED run day" needs a calendar the checker does not have (holidays, a disabled task, a
    # cron change) — an unprovable threshold in a false-alarm fix is the bug again.
    "automation-health": 4 * 24,
    "freshness-sweep": 8 * 24,
    # weekday-only via catch-up-missed-tasks.sh; 4d allows the Fri->Mon gap + one missed day
    "pytest-shiprouting": 4 * 24,
    # --- WEEKLY business-tracking posters, wired 2026-08-31 so they could go EXCEPTION-ONLY ---
    # 🔴 10d, NEVER 7d (rule 4). These fire once a week at a fixed noon-ish slot, and the machine
    # routinely sleeps through a slot: the catch-up run lands hours-to-days late, so the legal gap
    # between two healthy beats is >7d on any week with a catch-up. A 7d limit would grade a
    # perfectly healthy late run stale — the same structural false alarm that `automation-health`
    # itself carried at 2d until this morning, and the reason a weekday-aware limit is refused
    # here too (the checker has no calendar; one subtraction anyone can verify beats a threshold
    # nobody can). 10d still fails a genuinely SKIPPED week (next healthy beat lands at ~14d).
    # Each of the four routines below now posts to Slack ONLY on an exception, so absence of the
    # beat is the sole remaining evidence that the routine still runs. If one is ever unscheduled,
    # DELETE its row here in the same change (rule 4).
    "warm-cohort-report": 10 * 24,   # _outputs/scripts/warm_cohort_report.py (Mon ~14:10)
    "shipping-cost-sheet": 10 * 24,  # _outputs/scripts/shipping_cost_report.py --push (Mon ~13:09)
    "vendor-matrix": 10 * 24,        # ingest/slack_reship/sync.py --report (Tue ~12:00)
    # `slack-reship` is weekly-reship-report's PRE-EXISTING beat (ingest/slack_reship/weekly_task.py,
    # Tue ~12:00) — promoted into EXPECTED rather than minting a second key for one routine.
    # ⚠️ freshness_sweep.py D3 also checks this name on its own 8d constant; that block is now
    # redundant with the rule-13 beat-or-fail loop and is the sweep owner's to retire (it also
    # still hand-rolls the deprecated %APPDATA% ledger path, which rule 3 bans).
    "slack-reship": 10 * 24,
}
SYNC_HEARTBEAT_MAX_H = 48

# --- Windows scheduled tasks ------------------------------------------------------------
# 🔴 Why this is a REGISTRY and not a prefix (2026-08-31): the audit filtered on
# `startswith("appyhour_daily")`, so `AppyHour\GorgiasUpdate` — which had been returning
# 0x8007042B (ERROR_PROCESS_ABORTED) since 08-26 — was structurally invisible for five days.
# Eight AppyHour tasks sat outside the filter. NEGATIVES that shaped this:
#   - **Do NOT just widen the prefix.** That arms findings for eight tasks nobody triaged, and
#     an expectation you cannot justify trains alarm-deafness — the exact failure rule 4 bans.
#     Every row below was triaged individually (schedule read from the task XML, last success
#     read from its own log where it keeps one) before it was added.
#   - **A max age must clear the schedule's longest legal gap (rule 4).** Weekly → 10d, NEVER
#     7d: this machine sleeps through fixed-time slots and the catch-up run legally lands >7d
#     after the last one. Daily → 4d (weekend + one missed fire), the same number and reasoning
#     as REPLICA_STAMP_MAX_D.
#   - **An UNREGISTERED in-scope task is itself a finding.** A task absent from BOTH dicts is
#     reported, because "nobody added it to the registry" is precisely how GorgiasUpdate went
#     five days unwatched. Silence about a task we do not know about is the original blind spot.
#   - **A DISABLED expected task is a finding.** Last Result stays 0 forever on a task that can
#     no longer fire — a green reading from a task that is structurally dead.
SCHTASK_SCOPE = "appyhour"  # every task whose name starts with this is in the audit's remit

# name (lowercased, leading "\" stripped, as schtasks reports it) -> max last-run age in DAYS,
# or None = audit Last Result only (no cadence to be stale against). The `| None` is load-bearing
# and annotated so a checker cannot infer `dict[str, int]` and call the None a type error: the
# sole consumer guards with `if max_age_d is not None` before any comparison, because None here
# means "this task has no cadence", NOT "no result yet" — a sentinel compared against a threshold
# would grade the logon task stale on every run.
SCHTASK_EXPECTED: dict[str, int | None] = {
    # -- weekly, one fire per week each (was the only audited family, via the old prefix) --
    "appyhour_daily_tue": 10,   # daily_shipping_sync, Tue 12:00
    "appyhour_daily_wed": 10,   # daily_shipping_sync, Wed 12:00
    "appyhour_daily_thu": 10,   # daily_shipping_sync, Thu 12:00
    "appyhour_daily_fri": 10,   # daily_shipping_sync, Fri 12:00
    # -- newly audited 2026-08-31, each triaged before being added --
    # Gorgias sync + enrich of UPDATE_Operational Issues. Weekly Wed 09:00, runs
    # C:\AppyHourProd\...\gorgias_update.bat. Last clean exit 08-12 (08-26 did all its work
    # then died between python's exit and the .bat's exit-code echo).
    "appyhour\\gorgiasupdate": 10,
    # Carrier invoice ingest (run_carrier_sync.bat). DAILY 16:00, last success 08-31 16:00.
    "appyhour carrier invoice sync": 4,
    # backup_offsite.py. Weekly Sun 02:00, WakeToRun=true, last success 08-30 02:00. Also
    # covered by the `offsite-backup` beat — the beat catches ABSENCE, this catches a non-zero
    # exit on a run that happened; they fail independently (rule 13's shape).
    "appyhour weekly offsite backup": 10,
    # REMOVED 2026-09-01: `AppyHour Zone Floor Rebuild` was DELETED (Kurt, elevated shell) after
    # investigation showed it had never once succeeded — Last Result 2 every Sunday since
    # 06-19, because its action pointed at `rebuild_zone_floor.py` in C:\AppyHourProd\ShipRouting
    # where that file has never existed. The FEATURE was retired 2026-06-25: tail-insurance ice
    # (P95) via `_ice_eff`/`lane_p95` replaced the precomputed zone_floor.json
    # (ROUTING_RULES.md:2290-2294; TOOL_REGISTRY.md:121 "Do not port"). Nothing reads the cache
    # and the cache file does not exist, so ten weeks of no rebuild cost nothing.
    # 🔴 The row outlived the task by a day and became an ORPHAN-REGISTRATION: check_schtasks
    # iterates the rows `schtasks /query` RETURNS, so a deleted task goes SILENT rather than red —
    # an expectation that can neither pass nor fail. Rule 4: delete the row with the task.
    # melt_efficiency_calibrator.bat. Weekly Mon 09:15, last success 08-31 09:15.
    "appyhour\\meltefficiencycalibrator": 10,
    # postmortem_runner.py. Weekly Mon 09:00, WakeToRun=true, last success 08-31 09:02.
    "appyhour\\postmortemrunner": 10,
    # safety_factor_sweep.bat. Weekly Mon 09:30, last success 08-31 09:30.
    "appyhour\\safetyfactorsweep": 10,
    # sync_logon.py on a noon trigger. DAILY, last success 08-31 12:05.
    "appyhour_sync_daily_noon": 4,
    # shipping_db_healthcheck.py. DAILY 12:10, last success 08-31 12:10.
    "appyhour-db-healthcheck": 4,
    # vf_archive_refresh.bat. Weekly Tue 11:00, last success 08-31 10:59 (catch-up).
    "appyhour-vf-archive-refresh": 10,
    # sync_logon.py. Trigger is "at logon", NOT a clock — so Last Run Time tracks the last
    # BOOT, and any age gate here measures how long Kurt has gone without rebooting, a number
    # with no health meaning. Deliberately None: Last Result is audited, the staleness gate is
    # refused. The work this task does is covered for freshness by sync_heartbeat.json age
    # (check_sync_heartbeat) instead.
    "appyhour_sync_on_logon": None,
}

# In scope (name starts with SCHTASK_SCOPE) but deliberately NOT audited at all, reason
# recorded. Empty today — every in-scope task is registered above. A name lands here only with
# a reason a reader can check; "we never looked at it" is not one (that was the bug).
SCHTASK_EXCLUDED: dict[str, str] = {}

# Last Result values that are not failures: 0 = ok, 267009 = currently running,
# 267011 = never yet run (fresh trigger), "" = column absent.
SCHTASK_OK_RESULTS = ("0", "267009", "267011", "")
# schtasks /v prints this for a task that has never run.
SCHTASK_NEVER_RUN = ("11/30/1999 12:00:00 AM", "N/A", "")


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


_SCHTASKS_CSV: str | None = None


def _schtasks_csv() -> str:
    """`schtasks /query /fo csv /v`, queried ONCE per process and memoised.

    Two checks read it now (check_schtasks and check_task_set) and the query costs seconds on a
    machine with ~35 tasks. Memoised rather than passed around so neither check can silently run
    against a DIFFERENT snapshot of the task list than the other — a set-level check that
    disagreed with the per-task check about which tasks exist would produce findings nobody
    could reproduce. Raises; both callers catch and report the blindness.

    ⚠️ Process-lifetime cache: a test that injects a different task list MUST set
    `automation_health._SCHTASKS_CSV = None` first, or it silently grades the previous test's
    fixture. (Measured 2026-08-31: adding this memo turned 9 passing schtask tests red, all of
    them re-running test #1's CSV.)
    """
    global _SCHTASKS_CSV
    if _SCHTASKS_CSV is None:
        _SCHTASKS_CSV = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/v"],
            capture_output=True, text=True, timeout=120,
        ).stdout
    return _SCHTASKS_CSV


def check_schtasks(findings: list[str]) -> None:
    try:
        out = _schtasks_csv()
    except Exception as e:
        findings.append(f"schtasks query failed ({type(e).__name__}) — Windows task states unknown")
        return
    import csv
    import io
    unparseable: list[str] = []
    seen: set[str] = set()
    for row in csv.DictReader(io.StringIO(out)):
        name = (row.get("TaskName") or "").lstrip("\\")
        key = name.lower()
        if not key.startswith(SCHTASK_SCOPE) or key in seen:
            continue  # schtasks /v emits one row PER TRIGGER — audit each task once
        seen.add(key)
        if key in SCHTASK_EXCLUDED:
            continue
        if key not in SCHTASK_EXPECTED:
            findings.append(
                f"schtask '{name}': UNREGISTERED — not in SCHTASK_EXPECTED or SCHTASK_EXCLUDED "
                "in scripts/automation_health.py, so nothing audits it. Triage it and add a row "
                "(HEARTBEAT_RULES rule 4)")
            continue

        # One finding per task: report() dedupes a repeated key within a run, but a single
        # line keeps the Slack message and the dispatched handoff readable.
        problems: list[str] = []
        last_run = (row.get("Last Run Time") or "").strip()

        if (row.get("Status") or "").strip().lower() == "disabled":
            problems.append("DISABLED — it can no longer fire, and a disabled task's Last "
                            "Result stays green forever")

        result = (row.get("Last Result") or "").strip()
        if result not in SCHTASK_OK_RESULTS:
            problems.append(f"Last Result {result}{_win32_hint(result)} (last run {last_run or '?'})")

        max_age_d = SCHTASK_EXPECTED[key]
        if max_age_d is not None and last_run not in SCHTASK_NEVER_RUN:
            ts = _parse_schtask_time(last_run)
            if ts is None:
                unparseable.append(name)
            else:
                age_d = (datetime.now() - ts).total_seconds() / 86400
                if age_d > max_age_d:
                    problems.append(
                        f"has not run for {age_d:.1f}d (max {max_age_d}d) — last run {last_run}")
        if problems:
            findings.append(f"schtask '{name}': " + "; ".join(problems))

    if unparseable:
        # Aggregated on purpose: a locale change breaks EVERY row at once, and one finding per
        # task would bury the real ones. Loud, because an unreadable last-run time means the
        # staleness half of this check is blind — not that it passed (rule 1).
        findings.append(
            f"schtask last-run time unparseable for {len(unparseable)} task(s) "
            f"({', '.join(sorted(unparseable)[:6])}) — staleness gate BLIND for them")


def _win32_hint(result: str) -> str:
    """Decode the common HRESULT-shaped Last Result values into words.

    🔴 A bare `-2147023829` gets misread. It is 0x8007042B = HRESULT_FROM_WIN32(1067)
    ERROR_PROCESS_ABORTED — "the process terminated unexpectedly", i.e. the task's process
    was KILLED. It is NOT 0xC0000005 (an access violation), which is -1073741819 and means a
    native crash. Those two point at completely different investigations, and the finding is
    read by someone who will not stop to convert signed decimal to hex.
    """
    names = {
        1: "ERROR_INVALID_FUNCTION — the action ran and exited 1; read the task's own log",
        2: "ERROR_FILE_NOT_FOUND — the action's exe or script path is wrong",
        3: "ERROR_PATH_NOT_FOUND — the action's working directory is wrong",
        5: "ERROR_ACCESS_DENIED",
        1067: "ERROR_PROCESS_ABORTED — the process was killed, not a Python crash",
        267014: "task terminated by the user or by Task Scheduler",
    }
    try:
        n = int(result)
    except ValueError:
        return ""
    if 0 < n <= 0xFFFF:  # a plain exit code / win32 error, not an HRESULT
        return f": {names[n]}" if n in names else ""
    if n >= 0:
        return ""
    u = n + (1 << 32)
    if (u >> 16) == 0x8007:  # HRESULT_FROM_WIN32
        w = u & 0xFFFF
        return f" [0x{u:08X} = win32 {w}" + (f": {names[w]}" if w in names else "") + "]"
    if u == 0xC0000005:
        return " [0xC0000005 = STATUS_ACCESS_VIOLATION — a native crash]"
    return f" [0x{u:08X}]"


def _parse_schtask_time(s: str):
    """schtasks /v prints Last Run Time in the machine's short date/time format."""
    from datetime import datetime as _dt
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S",
                "%d/%m/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.strptime(s, fmt)
        except ValueError:
            continue
    return None


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
            "— C:\\AppyHourProd runs the scheduled tasks; an undeployed fix is not a fix. "
            "Review: python scripts/deploy_prod.py (dry-run); deploy is Kurt's --apply call"
        )


# --- SET-LEVEL check (2026-08-31) -------------------------------------------------------
# 🔴 WHY A SET-LEVEL CHECK EXISTS AT ALL. Every check above this line grades ONE automation at a
# time: is this beat fresh, is this schtask's Last Result 0, is this replica moving. A whole class
# of failure is invisible to that method because it is not a property of any member — it exists
# only in the SET. Two were found BY HAND on 2026-08-31, both after the per-item checks reported
# green:
#   (a) `weekly-shipping-vendor-matrix` and `weekly-reship-report` are pinned to the SAME cron
#       slot (`0 12 * * 2`), and both publish weekly reship numbers to Google Sheets off the same
#       Slack window. Nothing about either task, read alone, is wrong.
#   (b) the freshness sweep had TWO owners — a Windows schtask `\AppyHour\FreshnessSweep` (Mon
#       12:00) and the Claude routine `freshness-sweep` (Mon 12:30) — running the SAME script and
#       therefore writing the SAME heartbeat key. 🔴 That one is the worst of the class: a beat
#       from EITHER owner made the OTHER look alive, so `check_beats` above could not have
#       detected either owner dying. The duplication defeated the detector. (Resolved that day:
#       the schtask was deleted, the routine is sole owner — so it is now reconstructed as a
#       FIXTURE in tests, not expected live.)
# Both were CROSS-SYSTEM (a Windows schtask paired with a Claude routine), which is exactly where
# a single-system audit cannot look.
#
# 🔴 NEGATIVES that shaped this — a check that fires on benign overlap is worse than no check
# (rule 4), and every one of these was a tempting design that would have done exactly that:
#   - **Do NOT infer a task's write surface by grepping its SKILL.md prose.** Measured: the
#     prewarm routines' SKILL.md contain the literal strings "no sheet writes" and "no shipping.db
#     writes" — a marker scan reads those as sheet+db writers and collides them with everything at
#     noon. Negations, warnings and don't-do-this lists are the MAJORITY of what a good SKILL.md
#     says about a resource.
#   - **Do NOT infer it from the target SCRIPT source either.** Same measurement: `backup_offsite.py`
#     mentions `carrier_tnt_cache.json` three times (in comments, as a file it SKIPS), and the
#     slack_reship modules that demonstrably write Google Sheets contain no sheet marker at all
#     because they push through a helper. Inference is wrong in BOTH directions here.
#     → surfaces are a hand-triaged REGISTRY (TASK_SURFACES), same shape and same reason as
#       SCHTASK_EXPECTED above. A pair collides only when BOTH members are registered and share a
#       surface. An unregistered task is never half of a collision finding.
#   - **Do NOT treat "same cron minute" as "same instant", and do NOT treat jitter as a fix.**
#     Claude routines carry a stored `jitterSeconds` (0..~600). The Tuesday pair above is
#     separated TODAY by 9m07s of incidental jitter (19s vs 566s) — but jitter is a value nobody
#     chose, re-rolled whenever a task is edited, so it MASKS the collision rather than resolving
#     it. The comparison is therefore CRON SLOT (dow, hour, minute); the effective offsets and the
#     current gap are printed IN the finding so the reader can judge the urgency themselves.
#   - **Do NOT count the heartbeat ledger as a shared write surface.** Every scheduled thing beats;
#     including it would collide everything with everything. Writes are atomic-replace anyway.
#   - **Do NOT flag same-script tasks whose ARGUMENTS differ.** `appyhour_daily_{tue,wed,thu,fri}`
#     all run `daily_shipping_sync.py`, on four different days, with four different `--day` values.
#     That is one job scheduled four times, not four owners.
#
# INPUT PROBLEM (read before "why a snapshot file"): Claude routines are CLOUD-side objects. Only
# SKILL.md is on disk; `cronExpression` / `enabled` / `jitterSeconds` live server-side and are
# reachable only through the `scheduled-tasks` MCP tool, which a plain Python checker cannot call.
# So the routine half comes from a SNAPSHOT that an agent refreshes. A stale snapshot would arm
# FALSE pairs (a routine disabled since capture still counted as live), so past the soft limit this
# check does NOT guess: it drops to Windows-only and says so loudly (rule 1 — blind is not green).
CLAUDE_TASKS_SNAPSHOT = Path(
    r"C:\Users\Work\Claude Projects\_outputs\cache\claude_scheduled_tasks.json")
CLAUDE_TASKS_SNAPSHOT_MAX_D = 7
WORKSPACE_ROOT = Path(r"C:\Users\Work\Claude Projects")

# Shared-resource labels, hand-triaged per task. A label means "this task WRITES this resource".
# Absent from this dict = surface UNKNOWN = never reported as half of a collision. Adding a row is
# a triage step: read the task's command, read what the script actually writes, then write the row.
# 🔴 Populating this by pattern-matching names or grepping prose re-introduces the exact failure
# the block comment above measures.
TASK_SURFACES: dict[str, frozenset[str]] = {
    # -- Claude routines --
    # `-m ingest.slack_reship.sync --report --history-sheet`: writes the reship HISTORY sheet and
    # DMs Kurt, off the Mon-Sun Slack #reship-and-order-requests window.
    "weekly-shipping-vendor-matrix": frozenset({"gsheets", "slack-reship-window"}),
    # `-m ingest.slack_reship.weekly_task`: overwrites THIS week's tab in the reship Google Sheet,
    # off the same Slack window and the same fulfillments denominator. Same package as above.
    "weekly-reship-report": frozenset({"gsheets", "slack-reship-window"}),
    # friday_forecast_refresh.py = build -> fetch_cohort_forecast -> build -> upload_sheet.py: it
    # PUBLISHES over the live routing sheet and re-sizes the cohort's ice.
    "friday-forecast-refresh": frozenset({"gsheets", "routing-cohort"}),
    # prewarm_carrier_tnt.py: sole write is the ShipEngine quote cache. Verified in the script, not
    # in the prose (the prose says "no sheet writes", which is why prose is not evidence here).
    "prewarm-carrier-tnt-thursday": frozenset({"carrier-tnt-cache"}),
    "prewarm-carrier-tnt-friday-delta": frozenset({"carrier-tnt-cache"}),
    "prewarm-carrier-tnt-hourly-fill": frozenset({"carrier-tnt-cache"}),
    # shipping_cost_report.py --push: rewrites the CEO shipping-cost sheet.
    "shipping-cost-sheet": frozenset({"gsheets"}),
    # backup_offsite.py: uploads the rescue set to Drive. Same surface as the Sunday schtask below
    # — which is the point (see DUAL-OWNER below).
    "appyhour-offsite-backup": frozenset({"offsite-backup"}),
    # Read-only by charter (both SKILL.mds forbid writes; both scripts open the DB mode=ro). An
    # EMPTY surface set is a triaged answer, not a missing row — it means "collides with nothing".
    "automation-health-daily": frozenset(),
    "freshness-sweep": frozenset(),
    "warm-cohort-report": frozenset(),       # reports + Slack; no sheet push
    "evo-transfer-monday-reminder": frozenset(),   # one Slack DM
    "vault-bm25-refresh": frozenset(),       # rebuilds a local vault index only
    # -- Windows schtasks (keys as check_schtasks normalises them: lowercased, leading \ stripped) --
    "appyhour_daily_tue": frozenset({"shipping-db-write"}),
    "appyhour_daily_wed": frozenset({"shipping-db-write"}),
    "appyhour_daily_thu": frozenset({"shipping-db-write"}),
    "appyhour_daily_fri": frozenset({"shipping-db-write"}),
    "appyhour_sync_daily_noon": frozenset({"shipping-db-write"}),
    "appyhour_sync_on_logon": frozenset({"shipping-db-write"}),
    "appyhour carrier invoice sync": frozenset({"shipping-db-write"}),
    "appyhour weekly offsite backup": frozenset({"offsite-backup"}),
    "appyhour-db-healthcheck": frozenset(),        # mode=ro integrity probe
    "appyhour-vf-archive-refresh": frozenset(),    # copies vF xlsx into an archive dir
}

# Pairs that DO run the same target on purpose, with the reason. Keyed by the two task names,
# sorted. A row here suppresses the DUAL-OWNER finding for that pair ONLY — never the DUAL-BEAT
# finding, because a shared heartbeat key is dangerous even when the duplication is deliberate.
ALLOWED_DUAL_OWNERS: dict[tuple[str, str], str] = {
    ("appyhour_sync_daily_noon", "appyhour_sync_on_logon"): (
        "deliberate catch-up pair: the logon trigger exists BECAUSE the fixed-time daily one is "
        "missed whenever the machine is asleep at its slot (the dead-job rule). They serialise on "
        "the shipping.db advisory write lock rather than racing, and sync_logon.py is idempotent. "
        "Neither writes a heartbeat key, so this does not blind any dead-man switch."),
}

def _norm_target(raw: str) -> str | None:
    """Collapse every spelling of one script to ONE id.

    dev tree vs C:\\AppyHourProd, `/c/users/...` (git-bash) vs `C:\\Users\\...`, forward vs
    backslash, quoted vs bare all name the same file — and a duplicate-owner check that compares
    raw command strings sees two different scripts and reports nothing. That is precisely how the
    FreshnessSweep pair (schtask on the prod path, routine on the dev path) stayed invisible.
    """
    s = (raw or "").strip().strip('"\'').replace("\\", "/").lower()
    while s.endswith('"') or s.endswith("'"):
        s = s[:-1]
    if not s.endswith((".py", ".bat", ".sh")):
        return None
    if s.startswith("/c/"):           # git-bash spelling of C:\
        s = "c:/" + s[3:]
    for prefix, repl in (("c:/appyhourprod/", ""),
                         ("c:/users/work/claude projects/", ""),
                         ("c:/users/work/", "~/")):
        if s.startswith(prefix):
            s = repl + s[len(prefix):]
            break
    return s.lstrip("/")


def _dev_path(target_id: str) -> Path | None:
    """Resolve a normalised id back to a readable file in the DEV tree.

    Reading the DEV copy of a target a PROD-tree task runs is deliberate: C:\\AppyHourProd may not
    exist on a given machine, and the two copies are the same script by definition of _norm_target.
    Prod/dev DRIFT is check_prod_parity's job, not this one's."""
    if not target_id:
        return None
    p = (Path(r"C:\Users\Work") / target_id[2:]) if target_id.startswith("~/") \
        else (WORKSPACE_ROOT / target_id)
    return p if p.exists() else None


def _beats_in_file(path: Path, _depth: int = 0) -> set[str]:
    """Heartbeat keys a script writes: `beat("name")` in EXECUTABLE code only.

    🔴 Comments and docstrings are stripped first, and this is not fussiness. `freshness_sweep.py`
    carries the line `#  ... while `beat("slack-reship")` wrote canonical` — a naive regex reads
    that comment as freshness-sweep writing weekly-reship-report's key and files a DUAL-BEAT
    finding on two tasks that share nothing. One false finding of that shape teaches the reader to
    skim the next one.
    """
    keys: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    if path.suffix.lower() in (".bat", ".sh"):
        # one hop: a .bat wrapper's real payload is the .py it launches
        if _depth == 0:
            import re as _re
            for m in _re.finditer(r"[^\s\"']+\.py", text):
                sub = _dev_path(_norm_target(m.group(0)) or "")
                if sub:
                    keys |= _beats_in_file(sub, _depth + 1)
        return keys
    # 🔴 TOKENISED, not regexed. A line-based regex with hand-rolled comment/docstring stripping
    # was written first and was wrong on its FIRST real run: it read the literal `beat("name")`
    # out of this very function's own docstring and filed an UNWATCHED-BEAT finding for a
    # heartbeat key called "name". Prose about beats is everywhere in this codebase (a comment in
    # freshness_sweep.py quotes `beat("slack-reship")`, another task's key), so "is this token a
    # string in executable code" has to be answered by the tokeniser, not by guessing quote state.
    import io as _io
    import token as _tok
    import tokenize as _tokenize
    try:
        toks = [t for t in _tokenize.generate_tokens(_io.StringIO(text).readline)
                if t.type not in (_tok.COMMENT, _tok.NL, _tok.NEWLINE, _tok.INDENT,
                                  _tok.DEDENT)]
    except (SyntaxError, _tokenize.TokenError, IndentationError, ValueError):
        return keys  # unparseable: report NOTHING rather than guess (a wrong key is worse)
    for i, t in enumerate(toks[:-3]):
        if t.type != _tok.NAME or t.string not in ("beat", "_beat"):
            continue
        if i and toks[i - 1].type == _tok.NAME and toks[i - 1].string == "def":
            continue  # the definition in heartbeat.py, not a call
        if toks[i + 1].string != "(" or toks[i + 2].type != _tok.STRING:
            continue  # beat(name) with a variable is unresolvable here; skip, never guess
        try:
            val = ast.literal_eval(toks[i + 2].string)
        except (ValueError, SyntaxError):
            continue
        if isinstance(val, str) and val:
            keys.add(val)
    return keys


def _targets_from_text(text: str) -> set[tuple[str, str]]:
    """(normalised target, normalised argv-tail) for every INVOCATION in a command blob.

    🔴 Only lines that actually invoke python count. A SKILL.md names many scripts in prose — the
    one it runs, the ones it must NOT run, the ones a comment cites as provenance — and treating a
    mention as an invocation would collide two routines that merely reference the same file.
    """
    import re as _re
    found: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if "python" not in line.lower():
            continue
        cwd = ""
        m = _re.search(r"\bcd\s+[\"']?([A-Za-z]:[\\/][^\"'&;|]+)", line)
        if m:
            cwd = (_norm_target(m.group(1).rstrip("/\\ ") + "/x.py") or "")[:-4]
        # 🔴 Find the EXTENSION first, then walk BACK to the last drive-letter/`/c/` marker. A
        # token regex (`[^\s"']+\.py`) looks correct and silently misses every real invocation on
        # this machine, because the canonical paths contain a space: "C:/Users/Work/Claude
        # Projects/AppyHour/scripts/backup_offsite.py" tokenises to "C:/Users/Work/Claude", which
        # has no .py and is dropped. schtasks also mangles its own quoting, so quote-aware parsing
        # is not enough either. The two DUAL-OWNER findings this check exists to make both live on
        # such paths — a token regex reports a clean set-level run over a set it never read.
        for m in _re.finditer(r"\.(?:py|bat|sh)(?![A-Za-z0-9])", line):
            seg = line[:m.end()]
            starts = [s.start() for s in _re.finditer(r"[A-Za-z]:[\\/]|/c/", seg)]
            if not starts:
                continue
            raw = seg[starts[-1]:]
            if _re.search(r"\.exe[\s\"'`]", raw):
                # The walk-back ran through the interpreter, i.e. the SCRIPT was written relative
                # ("python.exe rebuild_zone_floor.py"). Its real location depends on the task's
                # working directory, which is not in this text — so skip it rather than mint an
                # id like "~/anaconda3/python.exe rebuild_zone_floor.py" that could false-match
                # another task's equally unresolvable relative script. Same rule as `-m` with no
                # `cd`: unresolvable is skipped, never guessed.
                continue
            tid = _norm_target(raw)
            if tid:
                found.add((tid, _args_tail(line, m.end())))
        for m in _re.finditer(r"-m\s+([A-Za-z_][\w.]*)", line):
            if not cwd:
                continue  # `-m pkg.mod` is unresolvable without the cwd; skip rather than guess
            found.add((f"{cwd}{m.group(1).replace('.', '/')}.py", _args_tail(line, m.end())))
    return found


def _args_tail(line: str, start: int) -> str:
    """Everything after the invoked target, normalised — this is what tells four `--day` variants
    of ONE job apart from four owners of one script."""
    import re as _re
    # Cut at the first backtick: these commands are quoted inside markdown fences/spans, so the
    # backtick is where the command stops and the SKILL.md's prose about it begins. Without the
    # cut, one routine's args read "` from working dir `C:\\...`" — prose in an identity field.
    tail = line[start:].split("`")[0].strip().strip('"').strip()
    tail = _re.sub(r"\s+", " ", tail.replace('"', "").replace("'", ""))
    return tail.lower()


def _cron_field(field: str, lo: int, hi: int) -> set[int]:
    vals: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(part)
        vals.update(range(a, b + 1, step))
    return vals


def _cron_slots(expr: str) -> set[tuple[int, int, int]] | None:
    """`M H DOM MON DOW` -> {(dow, hour, minute)}. None = a shape this expander does not model.

    Returning None (not an empty set) on day-of-month / month restrictions is deliberate: an
    unmodelled schedule must make the task INELIGIBLE for a collision finding, never make it look
    like it never fires. Nothing in the current set uses those fields."""
    parts = expr.split()
    if len(parts) != 5:
        return None
    minute, hour, dom, mon, dow = parts
    if dom != "*" or mon != "*":
        return None
    try:
        minutes, hours = _cron_field(minute, 0, 59), _cron_field(hour, 0, 23)
        dows = {0 if d == 7 else d for d in _cron_field(dow, 0, 6)}
    except (ValueError, TypeError):
        return None
    return {(d, h, m) for d in dows for h in hours for m in minutes}


_WIN_DOW = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}


def _win_slots(sched_type: str, start: str, days: str) -> set[tuple[int, int, int]] | None:
    """Windows trigger -> the same (dow, hour, minute) space. None = not a clock trigger."""
    t = _parse_schtask_time("1/1/2000 " + start.strip()) if start.strip() else None
    if t is None:
        for fmt in ("%I:%M:%S %p", "%H:%M:%S"):
            try:
                from datetime import datetime as _dt
                t = _dt.strptime(start.strip(), fmt)
                break
            except ValueError:
                continue
    if t is None:
        return None  # "At logon time" / "N/A" — no clock slot, so no same-slot comparison
    st = (sched_type or "").strip().lower()
    if st.startswith("daily"):
        dows = set(range(7))
    elif st.startswith("weekly"):
        dows = {_WIN_DOW[d.strip().upper()[:3]] for d in days.split(",")
                if d.strip().upper()[:3] in _WIN_DOW}
        if not dows:
            return None
    else:
        return None
    return {(d, t.hour, t.minute) for d in dows}


def _pair_hash(*names: str) -> str:
    """Short stable discriminator appended to every pair key.

    🔴 finding_key()'s slug turns EVERY separator into '-', so a bare `a__b` key lets the pair
    (x, y-z) and the pair (x-y, z) collapse onto one dispatch key — the same class of collapse
    that let three space-named schtasks share one key until it was fixed this morning. The hash of
    the sorted member tuple cannot collapse, and it is stable across runs (no ages, no counts)."""
    # blake2b, not sha1 — this is an identity digest, not a security primitive, and a flagged
    # hash in a monitoring file invites someone to "fix" it later, which would silently re-key
    # every open finding streak. 🔴 Changing this function's output IS a streak reset for every
    # pair currently counting toward dispatch.
    import hashlib
    return hashlib.blake2b("\x00".join(sorted(names)).encode("utf-8"), digest_size=4).hexdigest()


def _load_claude_routines(snapshot: Path) -> tuple[list[dict], str | None]:
    """Routine records from the snapshot, or ([], reason) when it cannot be trusted."""
    try:
        blob = json.loads(snapshot.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], (f"snapshot missing at {snapshot}")
    except Exception as e:
        return [], f"snapshot unreadable ({type(e).__name__}: {e})"
    try:
        cap = datetime.fromisoformat(str(blob.get("captured_at", "")).replace("Z", "+00:00"))
        if cap.tzinfo is None:
            cap = cap.replace(tzinfo=timezone.utc)
    except ValueError:
        return [], "snapshot has no parseable captured_at"
    age_d = (datetime.now(timezone.utc) - cap).total_seconds() / 86400
    if age_d > CLAUDE_TASKS_SNAPSHOT_MAX_D:
        return [], (f"snapshot {age_d:.1f}d old (max {CLAUDE_TASKS_SNAPSHOT_MAX_D}d)")
    out: list[dict] = []
    for t in blob.get("tasks", []):
        name = t.get("taskId") or ""
        cron = t.get("cronExpression")
        slots = _cron_slots(cron) if cron else None   # one-time tasks have fireAt, not cron
        text = ""
        with contextlib.suppress(OSError):
            # a routine whose SKILL.md is unreadable contributes no targets and no beats — it is
            # still counted for SAME-SLOT, which needs only its cron and its registered surface
            text = Path(t.get("path", "")).read_text(encoding="utf-8", errors="replace")
        out.append({"name": name, "system": "routine", "enabled": bool(t.get("enabled")),
                    "slots": slots, "jitter": int(t.get("jitterSeconds") or 0),
                    "targets": _targets_from_text(text)})
    return out, None


def _inscope_schtask_names() -> set[str]:
    """Every in-scope task the machine actually has, BEFORE SCHTASK_EXCLUDED is applied.

    Needed unfiltered: an EXCLUDED row for a task that still exists is fine, an EXCLUDED row for a
    task that is gone is dead registry text — and filtering first would report the former as the
    latter."""
    import csv
    import io
    return {n for n in ((row.get("TaskName") or "").lstrip("\\").lower()
                        for row in csv.DictReader(io.StringIO(_schtasks_csv())))
            if n.startswith(SCHTASK_SCOPE)}


def _load_schtask_records() -> list[dict]:
    import csv
    import io
    by_name: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(_schtasks_csv())):
        name = (row.get("TaskName") or "").lstrip("\\")
        key = name.lower()
        if not key.startswith(SCHTASK_SCOPE) or key in SCHTASK_EXCLUDED:
            continue
        rec = by_name.setdefault(key, {
            "name": name, "system": "schtask", "slots": set(), "jitter": 0,
            "enabled": (row.get("Status") or "").strip().lower() != "disabled",
            "targets": _targets_from_text("python " + (row.get("Task To Run") or "")),
        })
        # schtasks /v emits one row PER TRIGGER: union every trigger's slots, and let a single
        # unmodellable trigger (logon) mark the whole task ineligible rather than half-modelled.
        slots = _win_slots(row.get("Schedule Type") or "", row.get("Start Time") or "",
                           row.get("Days") or "")
        if rec["slots"] is not None:
            if slots is None:
                rec["slots"] = None
            else:
                rec["slots"] |= slots
    return list(by_name.values())


def check_task_set(findings: list[str], snapshot: Path | None = None) -> None:
    """Failures that exist only ACROSS automations — see the block comment above."""
    snapshot = snapshot or CLAUDE_TASKS_SNAPSHOT
    routines, blind = _load_claude_routines(snapshot)
    if blind:
        findings.append(
            f"task-set SNAPSHOT BLIND: {blind} — Claude routines are cloud-side objects with no "
            "local schedule file, so the cross-system half of the set check is running "
            "WINDOWS-ONLY. Refresh: call mcp__scheduled-tasks__list_scheduled_tasks and write its "
            f"list verbatim to {snapshot} under a `tasks` key with a fresh `captured_at`. Not "
            "green — blind (rule 1).")
    try:
        schtasks_live = _inscope_schtask_names()
        tasks = routines + _load_schtask_records()
    except Exception as e:
        findings.append(f"task-set schtask enumeration failed ({type(e).__name__}: {e}) — "
                        "set-level check BLIND for Windows tasks")
        return
    live = [t for t in tasks if t["enabled"]]
    for t in live:                       # beats are derived, not declared: read what runs
        keys: set[str] = set()
        for tid, _args in t["targets"]:
            p = _dev_path(tid)
            if p:
                keys |= _beats_in_file(p)
        t["beats"] = keys

    # (1) SAME-SLOT: identical (dow, hour, minute) AND a shared, registered write surface.
    slot_map: dict[tuple[int, int, int], list[dict]] = {}
    for t in live:
        for slot in (t["slots"] or ()):
            slot_map.setdefault(slot, []).append(t)
    reported: set[tuple[str, str]] = set()
    for slot, group in sorted(slot_map.items()):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pair = tuple(sorted((a["name"], b["name"])))
                if pair in reported:
                    continue
                sa = TASK_SURFACES.get(a["name"].lower())
                sb = TASK_SURFACES.get(b["name"].lower())
                if sa is None or sb is None:
                    continue     # unregistered surface — never half of a collision finding
                shared = sa & sb
                if not shared:
                    continue
                reported.add(pair)
                dow, hh, mm = slot
                ea, eb = mm * 60 + a["jitter"], mm * 60 + b["jitter"]
                gap = abs(ea - eb)
                findings.append(
                    f"task-set SAME-SLOT: '{a['name']}' and '{b['name']}' both fire "
                    f"{['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][dow]} {hh:02d}:{mm:02d} and "
                    f"share write surface(s) {sorted(shared)}. Effective offsets after stored "
                    f"jitter: +{a['jitter']}s vs +{b['jitter']}s (gap {gap // 60}m{gap % 60:02d}s) "
                    "— jitter is an incidental value re-rolled on any edit, so it masks this "
                    "rather than resolving it. Fix by moving one cron minute, not by trusting the "
                    f"gap [{_pair_hash(*pair)}]")

    # (2) DUAL-OWNER: two live tasks that ARE the same job — same script, same arguments, and the
    # script is each task's WHOLE job.
    # 🔴 The sole-target condition is not a nicety; without it the first real run reported the
    # three `prewarm-carrier-tnt-*` routines as "3 owners" of `build_prewarm_universe.py`. They
    # are three different jobs (Thursday bulk / Friday delta / paced weekday fill, on different
    # days, feeding different `--max` budgets) that happen to share a pipeline STEP. A shared step
    # is a library call; a duplicated owner is a task whose entire content is that one script —
    # which is exactly the shape of both real instances (backup_offsite.py; and the FreshnessSweep
    # schtask-vs-routine pair). Flagging shared steps is benign-overlap noise (rule 4).
    owners: dict[tuple[str, str], list[str]] = {}
    for t in live:
        if len(t["targets"]) != 1:
            continue
        for tgt in t["targets"]:
            owners.setdefault(tgt, []).append(t["name"])
    for (tid, args), names in sorted(owners.items()):
        uniq = sorted(set(names))
        if len(uniq) < 2:
            continue
        allowed = ALLOWED_DUAL_OWNERS.get(tuple(sorted(n.lower() for n in uniq))[:2])
        if allowed and len(uniq) == 2:
            continue
        findings.append(
            f"task-set DUAL-OWNER: {len(uniq)} live tasks run the same target '{tid}"
            f"{(' ' + args) if args else ''}': {uniq}. Two owners means two runs of one job and "
            "no single place to disable it; if either also writes a heartbeat, see DUAL-BEAT. "
            "Justified? Add the pair to ALLOWED_DUAL_OWNERS with the reason "
            f"(scripts/automation_health.py) [{_pair_hash(*uniq)}]")

    # (3) DUAL-BEAT — the dangerous one: two live writers of ONE heartbeat key.
    beat_owners: dict[str, list[str]] = {}
    for t in live:
        for k in t.get("beats", ()):
            beat_owners.setdefault(k, []).append(t["name"])
    for key, names in sorted(beat_owners.items()):
        uniq = sorted(set(names))
        if len(uniq) < 2:
            continue
        findings.append(
            f"task-set DUAL-BEAT: heartbeat '{key}' is written by {len(uniq)} live tasks {uniq} "
            "— 🔴 a beat from EITHER makes the OTHER look alive, so check_beats CANNOT detect "
            "either one dying. This defeats the dead-man switch for that key; it is not merely "
            "redundant scheduling. Fix: one owner beats, or split into two keys with two EXPECTED "
            f"rows [{_pair_hash(*uniq)}]")

    # (4) Expectations and beats that have no counterpart, BOTH directions.
    written_anywhere: dict[str, list[str]] = {}
    for base in (WORKSPACE_ROOT / "_outputs" / "scripts", WORKSPACE_ROOT / "AppyHour",
                 WORKSPACE_ROOT / "ShipRouting"):
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if {"_archive", "archive", ".git", "__pycache__", "tests"} & set(py.parts):
                continue
            for k in _beats_in_file(py):
                written_anywhere.setdefault(k, []).append(py.name)
    # 🔴 A registry row whose task no longer exists is SILENT, not green. check_schtasks iterates
    # the rows schtasks RETURNS, so a task that is deleted simply stops being visited — its
    # SCHTASK_EXPECTED row keeps standing as a documented expectation that nothing can ever
    # satisfy or violate. Observed live 2026-08-31: `appyhour zone floor rebuild` was registered
    # (deliberately, as a known-broken job awaiting Kurt's call) and the task was then deleted
    # mid-day; the row survived and nothing said so. Exact set comparison, no inference — both
    # sides are authoritative — and it is skipped entirely when the query returned nothing in
    # scope, so a failed/empty query cannot mass-report every row as orphaned.
    if schtasks_live:
        for key in sorted(set(SCHTASK_EXPECTED) | set(SCHTASK_EXCLUDED)):
            if key not in schtasks_live:
                where = "SCHTASK_EXPECTED" if key in SCHTASK_EXPECTED else "SCHTASK_EXCLUDED"
                findings.append(
                    f"task-set ORPHAN-REGISTRATION: {where}['{key}'] names a Windows task that no "
                    "longer exists, so check_schtasks never visits it and the row can neither "
                    "pass nor fail. Delete the row (rule 4) — or restore the task if the deletion "
                    "was not intended")
    for name in sorted(EXPECTED):
        if name not in written_anywhere:
            findings.append(
                f"task-set ORPHAN-EXPECTATION: EXPECTED['{name}'] is graded every run but NO "
                "script in the tree calls beat() with that key — the expectation can only ever go "
                "stale, which is a guaranteed future false alarm. Either the writer was renamed/"
                "retired (delete the EXPECTED row, rule 4) or the beat was never wired (rule 16)")
    # The other direction, kept DELIBERATELY NARROW: only a key a live task's own target writes.
    # 🔴 NOT "every routine with no heartbeat" — that would arm ~16 findings for routines nobody
    # triaged, which is the widen-the-prefix mistake this file already carries a scar from.
    for key, names in sorted(beat_owners.items()):
        if key not in EXPECTED:
            findings.append(
                f"task-set UNWATCHED-BEAT: live task(s) {sorted(set(names))} write heartbeat "
                f"'{key}' but no EXPECTED row grades it — the beat is written and read by nobody, "
                "so that task's silence is invisible. Add an EXPECTED row (max age must clear the "
                "schedule's longest legal gap) or stop writing the beat")


# --- findings -> dispatch loop (2026-08-29, HEARTBEAT_RULES rule 12) --------------------
# Evidence (_outputs/reports/harness-efficiency-review-2026-08-29.md, "The one systemic
# finding"): this checker re-reported identical findings daily for a month (ingest
# heartbeat 7d->28d stale, prod tree 9->20 undeployed files) — alerts fired, nobody owned
# the fix. A finding that repeats 3 consecutive runs now files a durable
# _coordination/handoffs.jsonl row to "Kurt triage" (surfaced by the SessionStart inbox
# hook), deduped while a prior row for the same key is open. ADDITIVE ONLY: findings,
# Slack message, and exit codes are byte-identical with or without the dispatcher; a
# broken dispatcher prints LOUDLY and never fails the checker (rule-2 family).
_COORD_DIR = Path(r"C:\Users\Work\Claude Projects\_coordination")  # absolute on purpose:
# the scheduled copy runs from C:\AppyHourProd, but _coordination lives only in Claude Projects
_DISPATCH_REF = r"C:\Users\Work\Claude Projects\AppyHour\HEARTBEAT_RULES.md"


def finding_key(text: str) -> str:
    """Stable dispatch key for a finding line. Variable parts (ages, counts, error
    detail) must NOT reach the key, or every run mints a "new" finding and the
    consecutive counter never hits 3. Keyed per entity (per heartbeat name, per
    replica table, per schtask) so distinct rots dispatch separately."""
    import re
    m = re.match(r"heartbeat (MISSING|STALE): (\S+)", text)
    if m:
        return f"heartbeat-{m.group(1).lower()}-{m.group(2)}"
    if text.startswith("ingest sync heartbeat stale"):
        return "ingest-heartbeat-stale"
    if text.startswith("ingest legs not ok"):
        return "ingest-legs-not-ok"
    # 🔴 Quoted form FIRST. Three audited task names contain spaces ("AppyHour Carrier Invoice
    # Sync", "AppyHour Weekly Offsite Backup", "AppyHour Zone Floor Rebuild"), and the bare
    # `(\S+)` pattern below captures only "AppyHour" — collapsing all three onto ONE key, so
    # two of them could never dispatch while the third held the streak. check_schtasks emits
    # the quoted form; the bare form is kept for any older/handwritten finding text.
    m = re.match(r"schtask '([^']+)':", text)
    if m:
        return "schtask-" + m.group(1)
    m = re.match(r"schtask (\S+):", text)
    if m:
        return "schtask-" + m.group(1)
    if text.startswith("shipping.db"):
        return "shipping-db"
    if text.startswith("replica pull stamp"):
        return "replica-pull-stamp"
    m = re.match(r"replica (\S+) (stale|EMPTY)", text)
    if m:
        return "replica-" + m.group(1)
    if text.startswith("prod tree STALE"):
        return "prod-tree-drift"
    # 🔴 Set-level findings are keyed by the PAIR/SET, not by the class. Every pair line ends in
    # `[<8-hex>]`, a hash of the sorted member names (see _pair_hash) — the member names are the
    # only stable identity a pair has, and slugging them directly lets distinct pairs collapse
    # onto one key. Anything that collapses here silently caps three findings at one streak.
    m = re.match(r"task-set (SAME-SLOT|DUAL-OWNER|DUAL-BEAT).*\[([0-9a-f]{8})\]\s*$", text,
                 re.DOTALL)
    if m:
        return f"taskset-{m.group(1).lower()}-{m.group(2)}"
    # 🔴 Anchor on the FIELD, never on "the first quoted thing". A `.*?'([^']+)'` was written here
    # first and keyed UNWATCHED-BEAT by the TASK name (the finding names the task list before the
    # heartbeat), so one task writing two unwatched beats collapsed to one key and one task
    # renamed minted a fresh streak. Both of these findings are about the KEY; key them by it.
    m = re.match(r"task-set ORPHAN-REGISTRATION: SCHTASK_\w+\['([^']+)'\]", text)
    if m:
        return "taskset-orphan-registration-" + m.group(1)
    m = re.match(r"task-set ORPHAN-EXPECTATION: EXPECTED\['([^']+)'\]", text)
    if m:
        return "taskset-orphan-expectation-" + m.group(1)
    m = re.match(r"task-set UNWATCHED-BEAT:.*?write heartbeat '([^']+)'", text, re.DOTALL)
    if m:
        return "taskset-unwatched-beat-" + m.group(1)
    if text.startswith("task-set SNAPSHOT BLIND"):
        return "taskset-snapshot-blind"
    if text.startswith("task-set schtask enumeration failed"):
        return "taskset-schtask-enum-failed"
    # fallback: slug the leading words before any '(' — error-type detail varies per run
    words = re.split(r"[^A-Za-z]+", text.split("(")[0])
    return "-".join(w for w in words if w)[:60].lower().strip("-") or "unclassified-finding"


def dispatch_findings(findings: list[str]) -> None:
    """Additive repeat-finding dispatcher. Called every completed run — a green run
    finalizes with no keys, resetting every streak (consecutive means consecutive)."""
    try:
        sys.path.insert(0, str(_COORD_DIR))
        import finding_dispatch
        finding_dispatch.SOURCE = "automation-health"
        seen: list[str] = []
        try:
            for text in findings:
                key = finding_key(text)
                seen.append(key)
                print(f"dispatch[{key}]: {finding_dispatch.report(key, text, ref=_DISPATCH_REF)}")
        finally:
            # 🔴 finalize() runs even if report() dies mid-loop (2026-08-31). Skipping it FREEZES
            # every streak: a finding that a fix has already cleared keeps its old count, and the
            # next real appearance walks it to 3 and files a handoff for a bug that no longer
            # exists — the false-alarm class this dispatcher exists to stop, one level up.
            # Finalizing on the partial `seen` under-counts (streaks reset early), which is the
            # safe direction: a real finding just re-counts next run.
            finding_dispatch.finalize(seen)
    except Exception as e:  # never fail the checker over its dispatcher — but never silently
        print(f"finding-dispatch unavailable ({type(e).__name__}: {e}) — "
              "repeat findings NOT tracked this run")


def main(argv: list[str]) -> int:
    init()  # UTF-8 stdio (the 🔴 findings line crashed cp1252 without PYTHONIOENCODING) + .env for notify
    verbose = "--verbose" in argv
    findings: list[str] = []
    try:
        check_beats(findings)
        check_sync_heartbeat(findings)
        check_schtasks(findings)
        check_task_set(findings)  # set-level: collisions/dual-owners no per-task check can see
        check_shipping_db(findings)
        check_replica_freshness(findings)
        check_prod_parity(findings)
    except Exception as e:
        findings.append(f"CHECKER CRASHED mid-run ({type(e).__name__}: {e})")
        notify("🔴 automation-health checker crashed: " + findings[-1], level="error")
        return 2
    beat("automation-health")  # self-beat LAST (rule 7)
    dispatch_findings(findings)  # rule 12: repeat-findings -> handoff row; additive, isolated
    if findings:
        msg = "🔴 automation-health: " + str(len(findings)) + " finding(s)\n• " + "\n• ".join(findings)
        print(msg)
        notify(msg, level="error")
        return 1
    if verbose:
        print("automation-health: all green (beats, ingest, schtasks, task-set, db, replicas, "
              "prod-parity)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
