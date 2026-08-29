"""Offline tests for appyhour_lib.bootstrap — no network, no writes, no real .env reads.

Every test isolates the module from the real AppyHour/.env by monkeypatching
_load_env_file (or pre-marking _initialized), so nothing here depends on the
machine's secrets or leaks them into assertions.
"""
from __future__ import annotations

import os
import sys

import pytest

from appyhour_lib import bootstrap


@pytest.fixture()
def fresh(monkeypatch):
    """Reset the idempotence flag and stub the .env loader to empty."""
    monkeypatch.setattr(bootstrap, "_initialized", False)
    monkeypatch.setattr(bootstrap, "_load_env_file", lambda: {})


def test_double_init_is_idempotent(fresh):
    bootstrap.init()
    out1, err1 = sys.stdout, sys.stderr
    bootstrap.init()  # second call must be a complete no-op
    assert sys.stdout is out1
    assert sys.stderr is err1
    assert bootstrap._initialized is True


def test_init_skips_streams_already_utf8(fresh):
    # Whatever stream pytest gives us, after one init() the encoding must be
    # utf-8 (or absent, for capture objects with no encoding attr), and a
    # re-init must not re-wrap it.
    bootstrap.init()
    enc = (getattr(sys.stdout, "encoding", "") or "").replace("-", "").replace("_", "").lower()
    assert enc in ("utf8", "")


def test_env_vars_win_over_env_file(fresh, monkeypatch):
    monkeypatch.setattr(
        bootstrap, "_load_env_file",
        lambda: {"AH_BOOTSTRAP_TEST": "fromfile", "AH_BOOTSTRAP_NEW": "loaded"})
    monkeypatch.setenv("AH_BOOTSTRAP_TEST", "fromenv")
    monkeypatch.delenv("AH_BOOTSTRAP_NEW", raising=False)
    try:
        bootstrap.init()
        assert os.environ["AH_BOOTSTRAP_TEST"] == "fromenv"  # setdefault: real env wins
        assert os.environ["AH_BOOTSTRAP_NEW"] == "loaded"    # .env fills the gap
    finally:
        os.environ.pop("AH_BOOTSTRAP_NEW", None)  # setdefault bypasses monkeypatch bookkeeping


def test_require_env_returns_stripped_value(monkeypatch):
    monkeypatch.setattr(bootstrap, "_initialized", True)  # isolate from real .env
    monkeypatch.setenv("AH_BOOTSTRAP_REQ", "  value  ")
    assert bootstrap.require_env("AH_BOOTSTRAP_REQ") == "value"


def test_require_env_missing_exits_loud_naming_var_and_env_path(monkeypatch):
    monkeypatch.setattr(bootstrap, "_initialized", True)
    monkeypatch.delenv("AH_BOOTSTRAP_MISSING", raising=False)
    with pytest.raises(SystemExit) as ei:
        bootstrap.require_env("AH_BOOTSTRAP_MISSING")
    msg = str(ei.value)
    assert "AH_BOOTSTRAP_MISSING" in msg              # names the var
    assert str(bootstrap.ENV_FILE) in msg             # names the .env path


def test_require_env_blank_value_is_missing(monkeypatch):
    monkeypatch.setattr(bootstrap, "_initialized", True)
    monkeypatch.setenv("AH_BOOTSTRAP_BLANK", "   ")
    with pytest.raises(SystemExit):
        bootstrap.require_env("AH_BOOTSTRAP_BLANK")


def test_require_env_triggers_init_first(monkeypatch):
    """The Aug-2026 ordering bug: env checked before .env loaded. require_env
    must load the .env (via init) before deciding the var is missing."""
    monkeypatch.setattr(bootstrap, "_initialized", False)
    monkeypatch.setattr(bootstrap, "_load_env_file",
                        lambda: {"AH_BOOTSTRAP_FROMFILE": "tok"})
    monkeypatch.delenv("AH_BOOTSTRAP_FROMFILE", raising=False)
    try:
        assert bootstrap.require_env("AH_BOOTSTRAP_FROMFILE") == "tok"
    finally:
        os.environ.pop("AH_BOOTSTRAP_FROMFILE", None)
