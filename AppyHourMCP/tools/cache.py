"""
Per-resource TTL response cache for AppyHour MCP.

Ported from printing-press-library auto_refresh.go pattern (absorb 2026-05-30).
AppyHourMCP previously hit Shopify/Recharge/Gorgias live on every call. This
adds a SQLite-backed response cache tiered by resource volatility so high-
frequency workflows (cut order, LTF) stop re-fetching unchanged data.

Cache DB: %APPDATA%/AppyHour/mcp_cache.db (WAL mode, concurrent-safe).
Opt-out: set APPYHOUR_NO_CACHE=1.

SAFETY: orders capped at 10m TTL (locked orders shift, refunds land). Write-
adjacent reads should pass resource="orders-live" (no TTL entry -> live fetch).
Mutation tools MUST call bust("orders") after committing changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("appyhour_mcp.cache")

# ---------------------------------------------------------------------------
# TTL tier map — seconds. Tiered by how fast each resource changes.
# ---------------------------------------------------------------------------
CACHE_TTL: dict[str, int] = {
    "products": 3600,            # 1h — rarely changes
    "customers": 3600,           # 1h
    "inventory-items": 900,      # 15m
    "subscriptions": 900,        # 15m — Recharge active subs
    "orders": 600,               # 10m — locked orders shift, refunds land
    "fulfillment-orders": 600,   # 10m
    "gorgias-tickets": 300,      # 5m — CS data changes frequently
    "default": 1800,             # 30m fallback
}

# Resources with no TTL entry (e.g. "orders-live") fall through to a live
# fetch — get() returns None and put() is a no-op. This lets write-adjacent
# reads bypass the cache explicitly.


def _cache_db_path() -> Path:
    """Return %APPDATA%/AppyHour/mcp_cache.db, creating parent if missing."""
    try:
        from appyhour_lib.paths import appyhour_appdata
        return appyhour_appdata() / "mcp_cache.db"
    except ImportError:
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        appdir = base / "AppyHour"
        appdir.mkdir(parents=True, exist_ok=True)
        return appdir / "mcp_cache.db"


class CacheStore:
    """SQLite response cache. One row per (resource, query) key."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _cache_db_path()
        self._init_schema()
        self._sweep_expired()

    def _connect(self) -> sqlite3.Connection:
        # Connection-per-call: FastMCP runs tools in async context; a shared
        # connection across threads is unsafe. WAL allows concurrent readers.
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS response_cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    resource   TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource ON response_cache(resource)"
            )

    def _sweep_expired(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM response_cache WHERE expires_at < ?", (time.time(),)
                )
        except sqlite3.Error:
            logger.exception("cache sweep failed (non-fatal)")

    def get(self, key: str) -> Optional[dict]:
        """Return cached value, or None if missing/expired/opted-out."""
        if os.environ.get("APPYHOUR_NO_CACHE"):
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value, expires_at FROM response_cache WHERE key = ?",
                    (key,),
                ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at < time.time():
                return None
            return json.loads(value)
        except (sqlite3.Error, json.JSONDecodeError):
            logger.exception("cache get failed for key=%s (serving live)", key)
            return None

    def put(self, key: str, value: Any, resource: str) -> None:
        """Store value under key with TTL from CACHE_TTL[resource].

        Resources absent from CACHE_TTL (e.g. "orders-live") are NOT cached —
        this is the explicit bypass path for write-adjacent reads.
        """
        if os.environ.get("APPYHOUR_NO_CACHE"):
            return
        ttl = CACHE_TTL.get(resource)
        if ttl is None:
            return  # explicit bypass — resource not in tier map
        try:
            payload = json.dumps(value, default=str)
            expires_at = time.time() + ttl
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO response_cache (key, value, expires_at, resource)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        expires_at=excluded.expires_at,
                        resource=excluded.resource
                    """,
                    (key, payload, expires_at, resource),
                )
        except (sqlite3.Error, TypeError):
            logger.exception("cache put failed for key=%s (non-fatal)", key)

    def bust(self, resource: str) -> int:
        """Delete all cached entries for a resource. Returns rows deleted.

        Mutation tools MUST call this after committing changes (e.g. an order
        edit calls bust("orders")) so subsequent reads see fresh data.
        """
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM response_cache WHERE resource = ?", (resource,)
                )
                return cur.rowcount
        except sqlite3.Error:
            logger.exception("cache bust failed for resource=%s", resource)
            return 0


def cache_key(resource: str, **kwargs: Any) -> str:
    """Deterministic key from resource + sorted kwargs."""
    blob = resource + "|" + json.dumps(kwargs, sort_keys=True, default=str)
    return resource + ":" + hashlib.sha256(blob.encode()).hexdigest()[:16]


# Module-level lazy singleton.
_store: Optional[CacheStore] = None


def get_store() -> CacheStore:
    """Return the process-wide CacheStore, initializing on first use."""
    global _store
    if _store is None:
        _store = CacheStore()
    return _store
