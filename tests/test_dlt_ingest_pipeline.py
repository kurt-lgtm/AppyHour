"""Import-clean + no-op-without-flag tests for the isolated dlt pipeline.

MUST NOT hit the Shopify API or touch shipping.db. We only import, inspect config,
and confirm the no-op path. The API pull (--run) is NEVER exercised here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_DLT_DIR = _REPO_ROOT / "dlt_ingest"
if str(_DLT_DIR) not in sys.path:
    sys.path.insert(0, str(_DLT_DIR))

import shopify_orders_pipeline as pipe  # noqa: E402


def test_imports_clean_and_targets_own_db():
    # Isolated db lives inside dlt_ingest/, and is NOT shipping.db.
    assert pipe.DLT_DB_PATH.name == "dlt_ingest.db"
    assert pipe.DLT_DB_PATH.parent.name == "dlt_ingest"
    assert "shipping.db" not in str(pipe.DLT_DB_PATH).lower()


def test_no_flag_is_noop(capsys):
    # Running without --run must not build/run the pipeline or hit the API.
    rc = pipe.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no-op" in out.lower()
    assert "--run" in out


def test_run_is_flag_gated(monkeypatch):
    # Prove main() only calls run() when --run is present. We stub run() so the API
    # is never actually contacted.
    called = {"n": 0}

    def _fake_run():
        called["n"] += 1

    monkeypatch.setattr(pipe, "run", _fake_run)

    assert pipe.main([]) == 0
    assert called["n"] == 0  # no flag -> run() not called

    assert pipe.main(["--run"]) == 0
    assert called["n"] == 1  # flag -> run() called exactly once
