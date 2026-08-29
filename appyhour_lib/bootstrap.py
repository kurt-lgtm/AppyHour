"""Entry-point bootstrap for scheduled/CLI runs — UTF-8 stdio + canonical .env.

Why this exists (negatives first — every one of these burned us):
  * cp1252 stdout: Windows consoles/schtasks default sys.stdout to cp1252; the
    first emoji/em-dash print raises UnicodeEncodeError. Re-derived ad-hoc in 8+
    sessions; for apply-tools the crash lands MID-MUTATION. init() fixes stdio
    once, idempotently — stop writing per-script wraps.
  * env-check-before-.env ordering: scheduled runs checked os.environ BEFORE
    anything loaded AppyHour/.env, so AH_SLACK_BOT_TOKEN looked "missing" and
    broke runs 4x (2026-08-11/18/22/28) while the token sat in .env the whole
    time. init() loads the canonical .env into os.environ FIRST; real env vars
    always win (setdefault, matching credentials.py doctrine).
  * Silent defaults: require_env() fails LOUD — SystemExit naming the var and
    the .env path — never a silent empty-string fallback (core rule).

Usage — first lines of every scheduled/CLI main():
    from appyhour_lib.bootstrap import init, require_env
    init()
    token = require_env("AH_SLACK_BOT_TOKEN")

Stdlib only (appyhour_lib layer rule). The .env path + parser are REUSED from
appyhour_lib.notify — the existing single source — never duplicated here.

Stream discipline: prefer stream.reconfigure(); NEVER a bare new TextIOWrapper
over a shared buffer — a GC'd wrapper closes the buffer ("I/O operation on
closed file"; see ShippingReports/reship_report_refresh.py header). The
TextIOWrapper fallback runs only when reconfigure is unavailable, and the
replaced stream is kept referenced so it can never be GC'd.
"""
from __future__ import annotations

import io
import os
import sys

# Single source for the canonical .env path + parser (appyhour_lib/notify.py).
from appyhour_lib.notify import _ENV_FILE as ENV_FILE
from appyhour_lib.notify import _load_env_file

_initialized = False
_replaced_streams: list[object] = []  # GC guard for the TextIOWrapper fallback


def _ensure_utf8(name: str) -> None:
    """Force sys.<name> to UTF-8 errors=replace; skip if already UTF-8."""
    stream = getattr(sys, name, None)
    if stream is None:
        return
    enc = (getattr(stream, "encoding", "") or "").replace("-", "").replace("_", "").lower()
    if enc == "utf8":
        return  # already UTF-8 (PYTHONIOENCODING, log-file redirect, prior wrap)
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
        return
    except (AttributeError, ValueError, io.UnsupportedOperation):
        pass
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return  # exotic stream with no buffer — better unwrapped than broken
    _replaced_streams.append(stream)  # keep old wrapper alive: GC would close the shared buffer
    setattr(sys, name, io.TextIOWrapper(
        buffer, encoding="utf-8", errors="replace", line_buffering=True))


def init() -> None:
    """Idempotent: UTF-8 sys.stdout/sys.stderr, then canonical .env -> os.environ.

    Call at the top of main() in every scheduled/CLI entry point, BEFORE any
    os.environ check and before anything prints non-ASCII.
    """
    global _initialized
    if _initialized:
        return
    _ensure_utf8("stdout")
    _ensure_utf8("stderr")
    for key, value in _load_env_file().items():
        os.environ.setdefault(key, value)  # real env vars WIN over .env
    _initialized = True


def require_env(name: str) -> str:
    """Return the stripped value of an env var, or exit LOUD.

    Calls init() first so the canonical .env is loaded before the check — the
    exact ordering bug that broke scheduled runs 4x in Aug 2026 cannot recur
    through this path. Missing/blank -> SystemExit naming the var AND the .env
    path (never a silent default).
    """
    init()
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"FATAL: required env var {name} is not set and not found in {ENV_FILE}. "
            f"Set it in the environment or add a {name}=... line to that file.")
    return value
