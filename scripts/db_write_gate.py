"""Preflight gate that makes a Claude-initiated write to `shipping.db` safe — or refuses.

🔴 WHY (three corruptions: 2026-06-27, 07-01, 07-03):
`shipping.db` is WAL-mode and written by many independent processes. Corruption comes from two
processes checkpointing the WAL at once, which folds bad pages into the main file. The standing rule
has therefore been "Claude never writes shipping.db" — every write goes through Kurt's real terminal.

That rule is a blunt instrument for a real hazard. This gate replaces it with a CHECK: Claude may
write when it can PROVE nothing else is positioned to write at the same moment, and must refuse
otherwise. (Kurt 2026-08-20: "identify all the potential writer collisions. claude should be able to
write given no collisions.")

🔴 WHY THE EXISTING LOCK IS NOT ENOUGH — the thing that has to be checked from outside:
`appyhour_lib/db.py::connect()` already takes an advisory `<db>.writelock`. It did NOT prevent the
07-03 corruption, because an advisory lock only binds processes that ask for it. Measured
2026-08-20: **25 of 33 files that write shipping.db open it with raw `sqlite3.connect`** and never
touch the lockfile — among them `AppyHourMCP/tools/cache.py`, which runs inside the live MCP
servers. So "the lockfile is free" proves nothing on its own; the PROCESS check below is the part
that actually covers the 25.

Checks, all of which must pass:
  1. canonical DB resolves under DATA_ROOT (never the legacy %APPDATA% copy — split-brain class)
  2. `PRAGMA quick_check` is ok BEFORE we touch anything
  3. no live advisory writelock held by a running process
  4. no known lock-bypassing writer process is alive (MCP servers, Kori, sync_logon, invoice sync)
  5. no scheduled writer task fires within the blackout window
  6. `BEGIN IMMEDIATE` succeeds under a short busy_timeout — an empirical "nobody holds it"
  7. an integrity-gated backup exists before the first mutation
and afterwards:
  8. `PRAGMA quick_check` is ok AFTER, or we shout.

Usage:
    python db_write_gate.py --check            # verdict only, touches nothing
    from db_write_gate import guarded_write
    with guarded_write("acct backfill") as con: ...
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from appyhour_lib import db as ahdb  # noqa: E402
from appyhour_lib import paths  # noqa: E402

# 🔴 Processes whose LIVENESS must block a Claude write. Measured, not guessed — each is a file
# that contains write SQL against shipping.db. Re-derive with the scan in INVOICE_INGEST_RULES §11
# when this list ages.
#
# 🔴 Do NOT remove an entry just because it migrated to `appyhour_lib.db.connect()`. Holding the
# advisory lock is not the same as holding it CONTINUOUSLY: a migrated writer that takes the lock
# per checkpoint (daily_shipping_sync, 2026-08-31) is unlocked most of its ~180-minute run and will
# write again seconds from now, so `check_lockfile` would see "free" and wave us through. The
# PROCESS check is what covers that window — same reason it covers the raw-connect writers.
BYPASSING_WRITERS = (
    "AppyHourMCP/server.py",          # -> tools/cache.py, raw connect; usually 1-3 live
    "AppyHourMCP\\server.py",
    "sync_logon.py",                  # takes the lock, but its abandoned stage thread outlives it
    "sync_carrier_invoices.py",
    "daily_shipping_sync.py",         # migrated 2026-08-31: per-checkpoint lock, NOT continuous
    "gel_pack_webview.py",            # Kori -> kori/db_snapshots.py
    "gel_pack_shopify.py",
    "auto_import.py",
    "weather_sync_cron.py",
    "sync_shopify_orders.py",
)

# Scheduled tasks that write. A run starting mid-write is exactly the two-checkpoint race.
WRITER_TASKS = (
    "AppyHour Carrier Invoice Sync",
    "appyhour_daily_mon", "appyhour_daily_tue", "appyhour_daily_wed",
    "appyhour_daily_thu", "appyhour_daily_fri",
    "AppyHour Weekly Offsite Backup",
    "AppyHour Zone Floor Rebuild",
    "GorgiasUpdate",
)

BLACKOUT_MIN = int(os.environ.get("AH_GATE_BLACKOUT_MIN", "20"))


class GateRefused(RuntimeError):
    """Preflight said no. The DB was not touched."""


# ─────────────────────────────────────────────────────────────────────── individual checks

def _db_path() -> str:
    return str(paths.db_path())


def check_canonical(problems: list) -> str:
    p = _db_path()
    root = str(getattr(paths, "DATA_ROOT", r"C:\AppyHourData"))
    if os.path.exists(root) and not os.path.abspath(p).lower().startswith(os.path.abspath(root).lower()):
        problems.append(f"resolved DB {p} is NOT under {root} — legacy/split-brain path, refusing")
    if not os.path.exists(p):
        problems.append(f"DB not found at {p}")
    return p


def check_quick(path: str, problems: list, when: str) -> None:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"quick_check ({when}) failed to run: {type(e).__name__}: {e}")
        return
    if not row or str(row[0]).lower() != "ok":
        problems.append(f"quick_check ({when}) = {row[0] if row else 'no result'} — DB is NOT healthy")


def check_lockfile(path: str, problems: list) -> None:
    lockpath = path + ".writelock"
    holder = ahdb._read_lock(lockpath) if hasattr(ahdb, "_read_lock") else None
    if os.path.exists(lockpath) and holder:
        if not ahdb._lock_is_stale(lockpath, holder, 1800):
            problems.append(f"advisory writelock held: {ahdb._holder_msg(lockpath, holder)}")


def live_bypassing_writers() -> list:
    """The check that actually covers the 25 raw-connect writers."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.CommandLine } | "
             "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:                                        # noqa: BLE001
        return ["(could not enumerate processes — refusing rather than assuming quiet)"]
    me = str(os.getpid())
    found = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        pid, cmd = line.split("\t", 1)
        if pid.strip() == me:
            continue
        for needle in BYPASSING_WRITERS:
            if needle.lower() in cmd.lower():
                found.append(f"pid {pid.strip()}: {os.path.basename(needle)}")
                break
    return found


def imminent_tasks(minutes: int) -> list:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-ScheduledTask | ForEach-Object { $i=$_|Get-ScheduledTaskInfo; "
             "\"$($_.TaskName)`t$($i.NextRunTime)\" }"],
            capture_output=True, text=True, timeout=90).stdout
    except Exception:                                        # noqa: BLE001
        return ["(could not read scheduled tasks — refusing rather than assuming quiet)"]
    soon, horizon = [], datetime.now() + timedelta(minutes=minutes)
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, nxt = line.split("\t", 1)
        name, nxt = name.strip(), nxt.strip()
        if name not in WRITER_TASKS or not nxt:
            continue
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                when = datetime.strptime(nxt, fmt)
            except ValueError:
                continue
            if datetime.now() <= when <= horizon:
                soon.append(f"{name} fires {when:%Y-%m-%d %I:%M %p}")
            break
    return soon


def check_begin_immediate(path: str, problems: list) -> None:
    """Empirical proof nobody holds the write lock right now. Short timeout on purpose: this is a
    probe, not a wait — if someone has it, we want to abort, not queue behind them."""
    try:
        con = sqlite3.connect(path, timeout=3.0)
        try:
            con.execute("PRAGMA busy_timeout=3000")
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        problems.append(f"BEGIN IMMEDIATE refused ({e}) — another process holds the write lock")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"write-lock probe failed: {type(e).__name__}: {e}")


def backup(path: str) -> str:
    """Integrity-GATED backup — refuses to copy a DB that does not pass quick_check, so a bad image
    can never overwrite a good backup."""
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if str(src.execute("PRAGMA quick_check").fetchone()[0]).lower() != "ok":
            raise GateRefused("refusing to back up a DB that fails quick_check")
        dest_dir = os.path.join(os.path.dirname(path), "backups")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"shipping.pre-claude-write-{time.strftime('%Y%m%d-%H%M%S')}.db")
        out = sqlite3.connect(dest)
        try:
            src.backup(out)          # online backup API — safe against a live DB
        finally:
            out.close()
    finally:
        src.close()
    return dest


# ─────────────────────────────────────────────────────────────────────── the gate

def preflight(*, blackout_min: int = BLACKOUT_MIN, verbose: bool = True) -> tuple:
    problems: list = []
    path = check_canonical(problems)
    if not problems:
        check_quick(path, problems, "before")
        check_lockfile(path, problems)
        for w in live_bypassing_writers():
            problems.append(f"lock-BYPASSING writer alive — {w}")
        for t in imminent_tasks(blackout_min):
            problems.append(f"scheduled writer within {blackout_min}m — {t}")
        check_begin_immediate(path, problems)
    if verbose:
        print(f"db_write_gate: {path}")
        if problems:
            print(f"\n  REFUSED — {len(problems)} blocker(s):")
            for p in problems:
                print(f"    x {p}")
            print("\n  Nothing was touched. Either quiesce the writers above and re-run, or hand\n"
                  "  Kurt the command for his terminal.")
        else:
            print("\n  CLEAR — no writer collision detected. Safe for a gated write.")
    return (not problems), path, problems


@contextlib.contextmanager
def guarded_write(reason: str, *, blackout_min: int = BLACKOUT_MIN):
    """Yield a write connection ONLY when preflight is clean. Backs up first, verifies after.

    Raises GateRefused instead of writing when anything is off — a refusal is the correct outcome,
    never a warning we proceed past.
    """
    ok, path, problems = preflight(blackout_min=blackout_min, verbose=True)
    if not ok:
        raise GateRefused(f"write gate refused ({reason}): " + "; ".join(problems))
    snap = backup(path)
    print(f"  backup: {snap}")
    con = ahdb.connect(path)          # takes the advisory writelock too
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    after: list = []
    check_quick(path, after, "after")
    if after:
        raise GateRefused("🔴 POST-WRITE quick_check FAILED: " + "; ".join(after) +
                          f" — restore from {snap}")
    print("  post-write quick_check: ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="preflight only; touches nothing")
    ap.add_argument("--blackout-min", type=int, default=BLACKOUT_MIN)
    args = ap.parse_args()
    ok, _, _ = preflight(blackout_min=args.blackout_min)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
