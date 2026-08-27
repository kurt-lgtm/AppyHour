"""Reporting-side cloud reads — DO-backed `delivery_status`, everything else LOCAL.

🔴 CONSTRAINTS SSOT = ``AppyHour/ShippingReports/DO_READ_CONTRACT.md``. Read it before
changing anything here. This module is the consumer-side implementation of that contract's
§4.1 recommendation (the ``ShipRouting/lib/histdb.py`` mirror path) narrowed to the ONE table
the contract's §1 verdict table clears for migration.

Why this module exists (negatives first)
========================================

**The failure it prevents #1 — a partial mirror silently deleting tables.**
``histdb.resolve()`` sets ``APPYHOUR_DB_PATH`` to the mirror it just built. The mirror
contains ONLY the tables the caller requested. So ``resolve(tables=["delivery_status"])``
repoints the canonical DB path at a file with no ``fulfillments``, no ``shipments``, no
``shopify_orders`` — and every consumer reading those through ``paths.db_path()`` dies with
"no such table", or worse, a consumer that catches the error reports a zero. We therefore call
``histdb.materialize()`` DIRECTLY and **never** ``resolve()``, and we never write
``APPYHOUR_DB_PATH``.

**The failure it prevents #2 — dragging a table whose cloud copy is BEHIND local.**
``histdb.TABLES`` carries six tables. Two of them must not move (DO_READ_CONTRACT §1, B1):

* ``fulfillments`` — cloud writer DEAD since 2026-08-12; there is no ``fulfillments`` timer in
  the ingest worker's REGISTRY at all. **LOCAL is the strict superset by 4,911 orders and
  cloud-only is ZERO**; ship weeks 08-17 and 08-24 are missing from cloud entirely. Migrating
  it LOSES TWO SHIP WEEKS.
* ``shipments`` — not stale but CORRUPT: ``ship_date`` holds two formats (8,940 ``YYYYMMDD``
  vs 109,377 ``YYYY-MM-DD``), so ``MAX()``/``BETWEEN`` on it are wrong, plus 25,795 duplicate
  rows pending a cloud-side dedupe. Blocked on B2/B2b.

``_CLOUD_OK`` below is an ALLOWLIST, and requesting anything outside it raises. A future
caller that types ``fulfillments`` gets a loud error naming the contract, not a quiet
regression. **Widening ``_CLOUD_OK`` requires updating DO_READ_CONTRACT §1 in the SAME
commit** — the cloud writer has to be alive first.

**The failure it prevents #3 — "stale" printed when we mean "unreachable".**
``histdb.materialize()`` raises with no fall-through, so an unreachable DO currently kills
whatever depends on it. A reporting script must degrade to local, LOUDLY, naming which store
answered — and a monitor must be able to tell UNREACHABLE from STALE, because they send a
human to two different places. :class:`CloudReadStatus` carries that distinction as a field,
never folded into a single "not fresh" boolean. Absent is safe; silently-substituted is not.

How the swap works
==================
No SQL is rewritten. We open the canonical local ``shipping.db`` read-only, ``ATTACH`` the
cloud mirror read-only as ``cloud``, then create a **TEMP VIEW** named ``delivery_status``.
SQLite resolves an unqualified name against the ``temp`` schema FIRST, so every existing
``FROM delivery_status`` reads cloud while ``fulfillments`` / ``shipments`` / ``shopify_orders``
still resolve to ``main`` (local). ``main.delivery_status`` remains reachable by explicit
qualification for parity diffs. Both connections are ``mode=ro``: this module can never write
either store (three WAL corruptions — ``shipping.db`` is never write-connected).

Staleness is additive: cloud writer interval (1h) + mirror TTL (``HISTORY_DB_TTL_MIN``,
default 60m) ≈ 2h before ParcelPanel's own lag. 🔴 Raising the TTL invalidates the §3.2
freshness tolerances — re-derive them, don't just bump it.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from .db import connect_ro

__all__ = ["CLOUD_TABLES", "CloudReadStatus", "connect_reporting", "cloud_enabled"]

# 🔴 ALLOWLIST — the ONLY tables cleared to read from DO (DO_READ_CONTRACT §1).
# fulfillments: dead cloud writer, local is the superset (B1).  shipments: two ship_date
# formats + 25,795 dupes (B2/B2b).  feedback: no cloud writer at all.
_CLOUD_OK = ("delivery_status",)
CLOUD_TABLES = _CLOUD_OK

_SHIPROUTING = Path(r"C:\Users\Work\Claude Projects\ShipRouting")

# Failure classes. 🔴 UNREACHABLE must never be reported as STALE — a stale table means the
# ingest is behind (go look at the writer); unreachable means the network/credential is down
# (go look at the link). Collapsing them sends someone to debug an ingest that is fine.
REASON_OK = "ok"
REASON_DISABLED = "disabled"          # flag off — local by design, not a degrade
REASON_UNREACHABLE = "unreachable"    # no credential, DNS/TCP/auth failure, MySQL down
REASON_THIN = "thin"                  # mirror refused: row floor / cohort requirement unmet
REASON_ERROR = "error"                # anything else, named


@dataclass(frozen=True)
class CloudReadStatus:
    """Which store answered, and why — for the caller to PRINT, never to swallow."""

    cloud: bool                 # True = the cloud mirror is attached and shadowing
    reason: str                 # one of the REASON_* constants
    tables: tuple[str, ...]     # tables actually served from cloud
    detail: str = ""            # exception text / credential path, human-facing
    mirror_path: str | None = None
    rows: int | None = None
    max_synced_at: str | None = None

    @property
    def degraded(self) -> bool:
        """True when cloud was WANTED but local answered. Flag-off is not a degrade."""
        return not self.cloud and self.reason != REASON_DISABLED

    def banner(self) -> str:
        if self.cloud:
            return (f"[cloud-read] delivery_status <- DO CLOUD mirror "
                    f"({self.rows:,} rows, newest synced_at {self.max_synced_at}) | "
                    f"fulfillments/shipments/shopify_orders <- LOCAL shipping.db")
        if self.reason == REASON_DISABLED:
            return "[cloud-read] disabled (REPORTING_CLOUD_DB unset) — ALL tables <- LOCAL shipping.db"
        return (f"🔴 [cloud-read] DEGRADED TO LOCAL — cloud delivery_status is {self.reason.upper()}: "
                f"{self.detail} | answering from LOCAL shipping.db, which is behind the cloud copy. "
                f"Delivery/late-rate numbers for the CURRENT ship week are UNTRUSTED "
                f"(measured 6.2% local vs 91.3% cloud on _SHIP_2026-08-24).")


def cloud_enabled() -> bool:
    """``REPORTING_CLOUD_DB=1`` enables; explicit ``0`` disables.

    Unset falls back to ``ROUTING_HISTORY_DB`` so the reporting surface follows the routing
    flip by default, while an explicit value lets either side move alone. Unset+unset = every
    existing local path, byte-identical.
    """
    own = os.environ.get("REPORTING_CLOUD_DB", "").strip()
    if own:
        return own == "1"
    return os.environ.get("ROUTING_HISTORY_DB", "").strip() == "1"


def _database_url() -> str | None:
    """env ``DATABASE_URL``, else Kurt's ACL'd ``%APPDATA%`` file (same resolution as
    ``freshness_sweep._database_url``). 🔴 Claude/MSIX ``%APPDATA%`` writes land in a sandbox
    shadow a scheduled task cannot see — the file must be created from a REAL terminal."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    f = Path(os.environ.get("APPDATA", "")) / "AppyHour" / "database_url.txt"
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _mirror_dir() -> str:
    """🔴 ``histdb._mirror_path`` defaults to ``/tmp``, which on Windows resolves to the
    CURRENT DRIVE ROOT. Pin ``FLOW_CACHE_DIR`` or the mirror lands in ``C:\\``."""
    cur = os.environ.get("FLOW_CACHE_DIR", "").strip()
    if cur:
        return cur
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "AppyHour" / "flow_cache"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _classify(exc: Exception) -> tuple[str, str]:
    """(reason, detail). Distinguishes UNREACHABLE from a THIN mirror from anything else."""
    name = type(exc).__name__
    text = str(exc)
    if "refusing a thinned mirror" in text or "< floor" in text or "exact cohort" in text:
        return REASON_THIN, f"{name}: {text[:200]}"
    # pymysql raises OperationalError/InterfaceError for DNS, TCP, TLS, and auth failures;
    # a missing/unparseable DATABASE_URL surfaces as the RuntimeError histdb raises.
    if name in ("OperationalError", "InterfaceError", "socket.gaierror", "gaierror",
                "TimeoutError", "ConnectionError", "OSError", "ImportError", "ModuleNotFoundError"):
        return REASON_UNREACHABLE, f"{name}: {text[:200]}"
    if "DATABASE_URL" in text:
        return REASON_UNREACHABLE, f"{name}: {text[:200]}"
    return REASON_ERROR, f"{name}: {text[:200]}"


def _materialize(tables: tuple[str, ...]) -> str:
    """Build/reuse the cloud mirror for exactly ``tables``. Raises on any failure."""
    url = _database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL missing: not in env and no %APPDATA%\\AppyHour\\database_url.txt "
            "(must be created from a REAL terminal — MSIX writes land in a sandbox shadow)")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("ROUTING_HISTORY_DB", "1")   # histdb.enabled() gate; materialize() ignores it
    os.environ["FLOW_CACHE_DIR"] = _mirror_dir()
    if str(_SHIPROUTING) not in sys.path:
        sys.path.insert(0, str(_SHIPROUTING))
    from lib import histdb  # noqa: PLC0415 — optional dep, only on the cloud path
    # 🔴 materialize(), NEVER resolve(): resolve() sets APPYHOUR_DB_PATH to this partial
    # mirror and would strand every consumer of fulfillments/shipments/shopify_orders.
    return histdb.materialize(tables=list(tables))


def connect_reporting(tables=("delivery_status",), *, quiet: bool = False,
                      local_path=None) -> tuple[sqlite3.Connection, CloudReadStatus]:
    """Return ``(read-only connection, status)`` with ``tables`` served from DO when possible.

    The connection is always usable: on ANY cloud failure it is the plain local read-only
    connection and ``status.degraded`` is True with a named reason. Callers MUST surface
    ``status.banner()`` — a silent fallback is the exact class this module exists to prevent.

    Raises:
        ValueError: a requested table is not cleared for cloud reads (DO_READ_CONTRACT §1).
    """
    tables = tuple(dict.fromkeys(tables))
    bad = [t for t in tables if t not in _CLOUD_OK]
    if bad:
        raise ValueError(
            f"cloud read requested for {bad!r}, which DO_READ_CONTRACT §1 does NOT clear. "
            f"Cleared: {list(_CLOUD_OK)}. 'fulfillments' has a DEAD cloud writer and local is "
            f"the superset by 4,911 orders (B1); 'shipments' holds two ship_date formats and "
            f"25,795 duplicate rows (B2/B2b). Fix the cloud writer and update the contract "
            f"FIRST — do not widen this allowlist to make a caller work.")

    con = connect_ro(local_path)

    if not cloud_enabled():
        st = CloudReadStatus(cloud=False, reason=REASON_DISABLED, tables=())
        if not quiet:
            print(st.banner(), file=sys.stderr, flush=True)
        return con, st

    try:
        path = _materialize(tables)
        con.execute("ATTACH DATABASE ? AS cloud", ("file:" + Path(path).as_posix() + "?mode=ro",))
        for t in tables:
            # TEMP VIEW shadows the local table for UNQUALIFIED references only; main.<t>
            # stays reachable for parity diffs. Interpolation is safe here and cannot be
            # parameterized: `t` is an identifier, and it is checked against the `_CLOUD_OK`
            # allowlist above before we get here — an unlisted name raised already.
            con.execute(f'CREATE TEMP VIEW "{t}" AS SELECT * FROM cloud."{t}"')  # noqa: S608
        rows = con.execute("SELECT COUNT(*) FROM cloud.delivery_status").fetchone()[0]
        mx = con.execute("SELECT MAX(synced_at) FROM cloud.delivery_status").fetchone()[0]
        st = CloudReadStatus(cloud=True, reason=REASON_OK, tables=tables, mirror_path=path,
                             rows=rows, max_synced_at=mx)
    except Exception as exc:   # noqa: BLE001 — a cloud failure must degrade, never kill a report
        reason, detail = _classify(exc)
        # Drop any half-applied attach/view so the connection is a clean LOCAL reader.
        for stmt in (*(f'DROP VIEW IF EXISTS temp."{t}"' for t in tables), "DETACH DATABASE cloud"):
            with contextlib.suppress(sqlite3.Error):
                con.execute(stmt)
        st = CloudReadStatus(cloud=False, reason=reason, tables=(), detail=detail)

    if not quiet:
        print(st.banner(), file=sys.stderr, flush=True)
    return con, st
