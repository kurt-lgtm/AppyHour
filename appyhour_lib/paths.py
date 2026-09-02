"""Canonical filesystem paths for the AppyHour data layer.

Single source of truth — every script that touches `shipping.db` (Kori,
ShippingReports, skill query.py, sync_logon, sync_all_carriers, parsers,
backfills) MUST import from here. No hardcoded paths anywhere else.

Why this module exists
======================
Prior to 2026-05-14, ~15 scripts hardcoded `shipping.db` with subtly
different prefixes — `PROJECT_DIR/shipping.db` (the 0-byte stub),
absolute Windows paths to the canonical, ad-hoc `os.path.join(...)`
constructions. The drift caused at least one wrong-DB crash this session
(`download_fedex_imap.py:125`) plus stale-data confusion across other
agent sessions.

Canonical DB location
=====================
`C:\\AppyHourData\\shipping.db` (as of 2026-07-08) — the Kori-owned DB
containing all 4 carriers + delivery_status (Parcel Panel) + feedback
(Gorgias) + kori_snapshots + weather_history. Moved OUT of
`%APPDATA%\\AppyHour` because MSIX virtualizes AppData: packaged
(Claude/MCP) processes saw a copy-on-write shadow that a 7/07 app update
deleted, causing the 7/08 false-MISSING incident (REBUILD-WITH-AI.md
§5.1). The legacy `%APPDATA%` path remains a TRANSITION fallback only —
never symlink it to the new location (sqlite sidecars follow the opened
name; two names = two wals = corruption).
`ShippingReports/output/shipments.db` and `GelPackCalculator/shipping.db`
(0-byte stub) are deprecated since 2026-05-14.

Override
========
Set `APPYHOUR_DB_PATH` env var to point at a different file (useful for
testing, scratch analysis, or future multi-machine setups). The override
is honored only if the file exists; otherwise we fall back to canonical.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

__all__ = ["db_path", "db_dir", "appyhour_appdata", "data_root", "invoices_dir",
           "inventory_settings_path", "gel_calc_settings_path", "settings_path",
           "assert_canonical_db", "NonCanonicalDBPath",
           "GEL_CALC_SETTINGS_NAME", "INVENTORY_SETTINGS_NAME"]

# Canonical data root — deliberately OUTSIDE %APPDATA% (MSIX-virtualized; see module docstring).
DATA_ROOT = Path(r"C:\AppyHourData")

# The one file name that is a SHARED image across ~30 processes. A scratch DB by any
# other name is nobody's second name for shipping.db, so the guard below ignores it.
SHARED_DB_NAME = "shipping.db"


class NonCanonicalDBPath(RuntimeError):
    """A writer tried to open ``shipping.db`` under a name that is not the canonical one.

    Subclasses :class:`RuntimeError` on purpose — the guard this was promoted from
    (``sync_logon._resolve_db_guarded``) raised ``RuntimeError``, and its callers'
    ``except RuntimeError`` clauses must keep working.
    """


# One CRITICAL notify per offending path per process — a refusal aborts the caller, but a
# retry loop must not turn one bug into a notification storm.
_NONCANONICAL_NOTIFIED: set[str] = set()


def _notify_critical(msg: str) -> None:
    try:
        from .notify import notify
        notify(msg, level="critical")
    except Exception:  # noqa: BLE001 — a broken notifier must not mask the refusal
        print(f"[CRITICAL] {msg}")


def _unattended() -> bool:
    """True when this process has nobody watching stdout, so a refusal must page.

    Set ``AH_UNATTENDED=1`` in a scheduled task / cron entry point. Deliberately NOT
    inferred from ``sys.stdin.isatty()``: sync_logon tees stdout to a log file, so an
    isatty probe would call every interactive pytest run unattended and vice versa.
    """
    return os.environ.get("AH_UNATTENDED", "").strip().lower() in ("1", "true", "yes")


def assert_canonical_db(path: str | Path, *, caller: str = "", notify: bool | None = None) -> Path:
    """HARD-REFUSE a ``shipping.db`` that resolves outside the canonical data root.

    🔴 WHY THIS IS A REFUSAL AND NOT A WARNING (measured 2026-08-31, HEARTBEAT_RULES
    rule 15). The three corruptions (6/27, 7/01, 7/03) were not caused by concurrent
    writers — a controlled 4-writer experiment on one path ran ~2,900 transactions clean.
    They were caused by **two NAMES for one file**: SQLite keeps its WAL locks in the
    ``-shm`` created beside whichever name was opened, so two names are two lock
    namespaces, the writers cannot see each other, and each checkpoints its own WAL into
    the one shared image. The same experiment with an NTFS hardlink — same bytes, two
    names — corrupted 5/5 with ``database disk image is malformed``, verbatim the string
    in ``notify_fallback.log`` on 7/01.

    NEGATIVE — do NOT oversell this. Canonicalizing onto ``db_path()`` would **not** have
    prevented 7/03: on that date the canonical path *was* ``%APPDATA%\\AppyHour\\shipping.db``,
    the MSIX-virtualized one, and MSIX splits packaged from unpackaged writers at the SAME
    name string. What actually fixed it was the 7/08 move off the VFS to ``C:\\AppyHourData``
    (zero corruptions in the 8 weeks since, same writer set). This guard prevents a
    REGRESSION back onto a virtualized, relative, or repo-local second name. It is not the
    missing 7/03 fix and must never be reported as one.

    NEGATIVE — the advisory ``<db>.writelock`` cannot stand in for this. The lock is keyed
    on ``str(target) + ".writelock"``, i.e. per-NAME: measured, two ``connect()`` calls on
    two names for one file BOTH acquired a lock (the same-name control was correctly
    refused). A second name is invisible to it by construction.

    Refuses when the target's file name is ``shipping.db`` and it is not under
    :data:`DATA_ROOT`. Deliberately narrow escape hatches, in order:

    * the legacy ``%APPDATA%\\AppyHour\\shipping.db`` is refused with **no** escape hatch —
      it is the specific virtualized second name that produced 7/22's 9-day split-brain;
    * an explicit ``APPYHOUR_DB_PATH`` / ``AH_DB_OVERRIDE`` naming exactly this file is
      honored (the documented scratch/restore override) and printed, so it is never silent;
    * a path under the OS temp dir is allowed — tests and scratch experiments live there,
      and no real writer targets ``%TEMP%``;
    * a machine with no ``C:\\AppyHourData`` at all is pre-migration; the guard stands down.

    🔴 A REFUSAL DOES NOT SLACK BY DEFAULT (2026-08-31, same day, after it did). ``raise`` is
    the loud part. When this was promoted out of ``sync_logon`` it kept that function's
    ``notify(level="critical")``, and one test run posted **7 CRITICALs to #kurt-ops in 90
    seconds**. In ``sync_logon`` the page was right — nobody is watching a logon task's
    stdout, so an unexpected refusal there is a real outage. As a LIBRARY default it is
    wrong: every pytest run, every CI pass and every developer typo pages Kurt, and a guard
    that cries wolf gets muted, which is worse than no guard. So ``notify`` defaults to
    "only when ``AH_UNATTENDED=1``"; interactive and test paths raise and print, nothing
    more. Pass ``notify=True`` explicitly from a scheduled/unattended entry point.

    Args:
        path: the DB path a writer is about to open.
        caller: short name used in the error text (e.g. ``"sync_logon"``).
        notify: ``True`` → page on refusal; ``False`` → never; ``None`` (default) → page
            only when :func:`_unattended`. Deduped to one page per offending path per
            process so a retry loop cannot storm.

    Returns:
        The path, unchanged, when it is safe to open.

    Raises:
        NonCanonicalDBPath: the target is a second name for the shared image. The message
            names the offending path, the canonical path, and how to fix the invocation —
            a break here is meant to be self-servicing.
    """
    p = Path(path)
    who = f"{caller}: " if caller else ""
    if p.name.lower() != SHARED_DB_NAME:
        return p                       # not the shared image; nobody's second name
    try:
        resolved = Path(os.path.abspath(str(p)))
    except OSError:
        resolved = p
    legacy = appyhour_appdata() / SHARED_DB_NAME
    is_legacy = str(resolved).lower() == str(legacy).lower()

    if not is_legacy:
        if not DATA_ROOT.exists():
            return p                   # pre-migration machine; legacy IS canonical there
        if resolved.parent == DATA_ROOT or DATA_ROOT in resolved.parents:
            return p                   # canonical
        for env_name in ("APPYHOUR_DB_PATH", "AH_DB_OVERRIDE"):
            override = os.environ.get(env_name, "").strip()
            if override and os.path.abspath(override).lower() == str(resolved).lower():
                print(f"[warn] {who}opening NON-CANONICAL {resolved} because {env_name} "
                      "names it explicitly. Never point this at a live writer.")
                return p
        try:
            tmp = Path(os.path.abspath(tempfile.gettempdir()))
            if tmp == resolved.parent or tmp in resolved.parents:
                return p               # tests / scratch copies
        except OSError:
            pass

    canonical = DATA_ROOT / SHARED_DB_NAME
    reason = ("the LEGACY MSIX-virtualized Roaming path — the second name behind the "
              "2026-07-22 9-day split-brain; it has no override"
              if is_legacy else
              f"not under the canonical data root {DATA_ROOT}")
    msg = (
        f"{who}REFUSED to open {resolved} — {reason}. "
        f"shipping.db is a SHARED image; a second name gives it a second WAL lock "
        f"namespace and that is what corrupted it on 2026-06-27 / 07-01 / 07-03. "
        f"Canonical: {canonical}. "
        f"FIX THE INVOCATION: pass no path (or appyhour_lib.paths.db_path() / db_dir()) "
        f"instead of a hardcoded %APPDATA%, a relative directory, or the repo-local copy; "
        f"run the script from anywhere — it resolves the canonical path itself. "
        f"For a deliberate scratch copy, set APPYHOUR_DB_PATH to that file "
        f"(the legacy Roaming path is never accepted)."
    )
    should_page = _unattended() if notify is None else bool(notify)
    key = str(resolved).lower()
    if should_page and key not in _NONCANONICAL_NOTIFIED:
        _NONCANONICAL_NOTIFIED.add(key)
        _notify_critical(msg)
    raise NonCanonicalDBPath(msg)


def appyhour_appdata() -> Path:
    """Return %APPDATA%\\AppyHour as a Path. Creates if missing.

    Settings JSONs and other 3-app shared config live here. The shipping.db
    does NOT (as of 2026-07-08) — use db_path().
    """
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    p = base / "AppyHour"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Shared settings JSONs ───────────────────────────────────────────────────
#
# 🔴 THE SPLIT THIS SOLVES (measured 2026-08-31). These two files lived at
# `%APPDATA%\AppyHour\<name>.json`, which MSIX virtualizes. A packaged process
# (Claude Code, the MCP servers) reads and writes a package-private overlay; an
# unpackaged process (Kori, the real-context scheduled tasks) reads and writes the
# real profile. Same path string, TWO FILES, two independent write histories — and
# which one you get depends only on how the process was launched.
#
# It was not "one file that is sometimes stale". Both sides carried edits the other
# never had: the real profile's 8/09 write dropped 6 zip routing overrides and
# retuned 3 more, while the overlay separately grew `routing_levers` + 9 policy keys
# on 8/22 from a PRE-8/09 base. `gel_calc_shopify_settings.json` feeds the
# ShipRouting build's zip overrides, so a routing build produced 142 overrides in one
# context and 137 in the other, silently, for three weeks.
#
# Merged onto `C:\AppyHourData` on 2026-08-31 (real profile = base, sandbox-only keys
# unioned on top, real wins conflicts). Audit trail: `<name>.MERGE-AUDIT.md` beside
# each file; pre-merge copies in `C:\AppyHourData\forensics\config\unify-20260831`.
#
# NEGATIVE — do NOT "fix" a stale read by pointing a caller back at %APPDATA%. That
# is the bug. Read through these helpers; the legacy fallback below exists to make an
# unmigrated caller ANNOUNCE itself for one cycle, not to keep the old path alive.

GEL_CALC_SETTINGS_NAME = "gel_calc_shopify_settings.json"
INVENTORY_SETTINGS_NAME = "inventory_reorder_settings.json"

# One warning per (file, path) per process — a caller in a loop must not spam stderr.
_LEGACY_SETTINGS_WARNED: set[str] = set()


def _warn_legacy_settings(name: str, path: Path) -> None:
    key = f"{name}|{path}".lower()
    if key in _LEGACY_SETTINGS_WARNED:
        return
    _LEGACY_SETTINGS_WARNED.add(key)
    print(
        f"paths: LEGACY-SETTINGS FALLBACK fired for {name} -> {path}. The canonical copy "
        f"({DATA_ROOT / name}) is missing, so this process is reading the MSIX-virtualized "
        f"%APPDATA% path, whose contents depend on whether the reader is packaged. "
        f"Migrate this caller to appyhour_lib.paths and restore the canonical file.",
        file=sys.stderr,
    )


def settings_path(name: str, *, for_write: bool = False, extra_fallbacks=()) -> Path:
    """Resolve a shared settings JSON to its canonical home under ``C:\\AppyHourData``.

    Reads fall back to the legacy ``%APPDATA%\\AppyHour`` copy for one deprecation
    cycle, printing loudly to stderr when they do (silent fallback is what let the
    split run for three weeks unnoticed).

    🔴 WRITES NEVER FALL BACK. ``for_write=True`` always returns the canonical path,
    creating the directory if needed, even when the file does not exist yet. A writer
    that fell back would re-create the divergence this migration just removed.

    Args:
        name: bare file name, e.g. :data:`GEL_CALC_SETTINGS_NAME`.
        for_write: return the canonical path unconditionally (no fallback, no
            existence requirement).
        extra_fallbacks: additional read-only candidates tried after the legacy
            %APPDATA% copy (used for the repo-local InventoryReorder copies).

    Returns:
        The path to open.

    Raises:
        FileNotFoundError: read mode, and no candidate exists — names every path tried
            so a caller cannot half-open a missing settings file.
    """
    canonical = DATA_ROOT / name
    if for_write:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        return canonical
    if canonical.exists():
        return canonical
    legacy = appyhour_appdata() / name
    if legacy.exists():
        _warn_legacy_settings(name, legacy)
        return legacy
    for extra in extra_fallbacks:
        if Path(extra).exists():
            _warn_legacy_settings(name, Path(extra))
            return Path(extra)
    tried = [canonical, legacy, *(Path(e) for e in extra_fallbacks)]
    raise FileNotFoundError(
        f"{name} not found. Tried: " + "; ".join(str(p) for p in tried)
    )


def gel_calc_settings_path(*, for_write: bool = False) -> Path:
    """Return ``gel_calc_shopify_settings.json`` — Kori's settings store.

    Carries the zip routing overrides the ShipRouting build reads, the hub policies,
    ``routing_levers``, and live API keys. See the section header above for why this
    must not be resolved from ``%APPDATA%`` by hand.
    """
    return settings_path(GEL_CALC_SETTINGS_NAME, for_write=for_write)


def inventory_settings_path(*, for_write: bool = False) -> Path:
    """Return the live ``inventory_reorder_settings.json`` (IMAP + API creds).

    🔴 2026-07-27: the three carrier IMAP downloaders each hardcoded
    ``<tree>/../InventoryReorder/dist/inventory_reorder_settings.json``. That
    subdir exists in the DEV tree only — in ``C:\\AppyHourProd`` it does not, so
    every logon run failed FedEx+OnTrac+Veho with FileNotFoundError while
    ``sync_logon`` still stamped ``carriers: ok``. Silent carrier-invoice lag,
    same class as the 2026-06-24 FedEx-only gap.

    Resolution order (2026-08-31 — canonical first; see the section header):
      1. C:\\AppyHourData\\inventory_reorder_settings.json   (the live master)
      2. %APPDATA%\\AppyHour\\inventory_reorder_settings.json (legacy, warns loudly)
      3. <repo>/InventoryReorder/inventory_reorder_settings.json
      4. <repo>/InventoryReorder/dist/inventory_reorder_settings.json

    Raises FileNotFoundError naming every path tried — never returns a path that
    does not exist, so a caller can't half-open a missing settings file.
    """
    repo = Path(__file__).resolve().parent.parent
    return settings_path(
        INVENTORY_SETTINGS_NAME,
        for_write=for_write,
        extra_fallbacks=(
            repo / "InventoryReorder" / INVENTORY_SETTINGS_NAME,
            repo / "InventoryReorder" / "dist" / INVENTORY_SETTINGS_NAME,
        ),
    )


def data_root() -> Path:
    """Return the canonical data root (C:\\AppyHourData). Does NOT create it —
    the migration/restore step owns creation; a missing root means the legacy
    location is still live."""
    return DATA_ROOT


def db_dir() -> Path:
    """Return the directory containing shipping.db (backups/ + writelock live beside it)."""
    return db_path().parent


def db_path() -> Path:
    """Return the canonical shipping.db path.

    Resolution order (REBUILD-WITH-AI.md §5.1):
      1. APPYHOUR_DB_PATH env override, if that file exists
      2. C:\\AppyHourData\\shipping.db, if the canonical ROOT DIR exists (canonical)
      3. legacy %APPDATA%\\AppyHour\\shipping.db (pre-migration machine only)

    🔴 2026-07-22: step 2 keys on the canonical DIRECTORY, not the file. It used to test
    ``canonical.exists()`` (the file). On the logon task, ``DB = db_path()`` is evaluated
    ONCE at import; at login the canonical file wasn't yet visible (drive/path race), so
    db_path() fell back to legacy Roaming and sync_logon wrote ``%APPDATA%`` for 9 days
    (canonical ``fulfillments`` frozen 07-13 while Roaming advanced to 07-22 — a split-brain
    the healthcheck kept flagging). Once a machine is migrated, ``C:\\AppyHourData`` persists,
    so keying on the dir makes a WRITER resolve canonical even if the file is momentarily
    absent (WAL rename, restore, first-write). The %APPDATA% fallback now fires ONLY on a
    genuinely pre-migration box that has no ``C:\\AppyHourData`` at all.
    """
    override = os.environ.get("APPYHOUR_DB_PATH", "").strip()
    if override:
        p = Path(override)
        if p.exists():
            return p
    if DATA_ROOT.exists():
        return DATA_ROOT / "shipping.db"
    return appyhour_appdata() / "shipping.db"


def invoices_dir() -> Path:
    """Carrier-invoice landing directory (where IMAP pullers + manual drops save).

    Currently still at `GelPackCalculator/Invoices` for backwards-compat with
    OnTrac/Veho IMAP scripts. Migrate to %APPDATA%/AppyHour/Invoices in a
    later pass once all sources are agnostic.
    """
    # PROJECT_DIR isn't stable across repos — anchor on a marker file instead.
    # Walk up from this module looking for the AppyHour repo root.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "GelPackCalculator" / "Invoices"
        if candidate.is_dir():
            return candidate
    # Fallback: APPDATA-relative
    p = appyhour_appdata() / "Invoices"
    p.mkdir(parents=True, exist_ok=True)
    return p
