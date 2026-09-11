"""THE cloud-MySQL credential resolver and connection builder. One fact, one owner.

🔴 WHY THIS EXISTS (Kurt 2026-09-11, standing: "dedupe scripts and make sure they're modular").
Five implementations of the same two facts were live on 2026-09-11, and they did NOT agree:

    appyhour_lib/cloud_reads._database_url()     env -> %APPDATA% file          (its own docstring
                                                 says "same resolution as freshness_sweep" — an
                                                 ACKNOWLEDGED duplicate nobody collapsed)
    _outputs/scripts/freshness_sweep._database_url()      env -> %APPDATA% file
    _outputs/scripts/pull_cloud_replicas.database_url()   env -> %APPDATA% file
    _outputs/scripts/normalize_ship_date.conn()           env -> file, parse, connect
    ShipRouting/server/manual_ingest._mysql()             env ONLY — 🔴 NO FILE FALLBACK

🔴 That last one is the reason this is a correctness fix and not tidiness. `_mysql()` resolves
`DATABASE_URL` from the environment alone, so it works on the cloud (where App Platform injects
the env var) and RAISES on Kurt's machine in exactly the situation the other four handle — the
ACL'd `%APPDATA%\\AppyHour\\database_url.txt` that Kurt creates from a real terminal. Two callers,
same intent, different answers depending on which helper the author happened to reach for.

## Scope, deliberately narrow

This owns TWO facts and nothing else: **where the URL comes from** and **how to open a connection
from it**. It is not a query layer, not a mirror, not an allowlist.

- Reporting reads with a cloud/local fallback and a table allowlist stay in
  `appyhour_lib.cloud_reads` (`connect_reporting`) — that module has a contract of its own
  (`ShippingReports/DO_READ_CONTRACT.md`) and should call `database_url()` here rather than
  re-resolving.
- 🔴 `ShipRouting/server/*` is NOT converted. The repos are splitting, and importing an AppyHour
  module from the cloud image would create exactly the cross-repo dependency that split removes —
  shared facts cross through the DO database, never a sibling repo's file. `manual_ingest._mysql()`
  stays as the cloud-side builder; the divergence above is documented there instead.

## Modularity rules this file follows (and every caller should)

- **No filesystem or network work at import time.** Reading the credential file happens inside
  `database_url()`, never at module scope — the bug class behind the `C:\\AppyHourData` mkdir that
  fired on every cloud build.
- `pymysql` is imported inside `connect()`, so a caller that only wants URL resolution (a test, a
  dry-run, a doc generator) does not need the driver installed.
"""
import os
import re
from pathlib import Path

__all__ = ["database_url", "parse_url", "connect", "CREDENTIAL_FILE_HINT"]

CREDENTIAL_FILE_HINT = r"%APPDATA%\AppyHour\database_url.txt"

_URL_RE = re.compile(r"mysql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)")


def database_url() -> str | None:
    """`DATABASE_URL` from the environment, else Kurt's ACL'd credential file. None if neither.

    🔴 The file must be created from a REAL terminal. Claude runs as an MSIX-packaged app, so a
    `%APPDATA%` write from a Claude session lands in a package-private shadow that a scheduled
    task cannot see — the file would appear to exist here and be absent to the job that needs it.

    Returns None rather than raising: a caller that can degrade to local (reporting) needs to
    distinguish "no credential" from "credential is wrong", and an exception collapses both.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    try:
        return (Path(os.environ.get("APPDATA", "")) / "AppyHour"
                / "database_url.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def parse_url(url: str) -> dict:
    """`mysql://user:pw@host:port/db` -> connect kwargs. Raises on an unparseable URL.

    Separate from `connect()` so the parse is testable without a driver or a live server."""
    m = _URL_RE.match(url or "")
    if not m:
        raise ValueError("DATABASE_URL unparseable — expected "
                         "mysql://user:password@host:port/database")
    user, password, host, port, db = m.groups()
    return {"host": host, "port": int(port), "user": user, "password": password,
            "database": db, "ssl": {"ssl": {}}}


def connect(*, autocommit: bool = False, url: str | None = None):
    """Open a cloud MySQL connection. Raises with an ACTIONABLE message when unconfigured.

    `autocommit=False` by default: every write path in this codebase is expected to commit
    explicitly, so a caller that forgets rolls back rather than half-applying."""
    url = url or database_url()
    if not url:
        raise RuntimeError(
            f"No cloud DB credential. Set DATABASE_URL, or create {CREDENTIAL_FILE_HINT} "
            f"from a REAL terminal (an MSIX-sandboxed write is invisible to scheduled tasks).")
    import pymysql          # inside the function: URL resolution must not need the driver
    return pymysql.connect(autocommit=autocommit, **parse_url(url))
