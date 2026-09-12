"""Reserve floor is ONE operator setting, never a code constant (Kurt 2026-09-12, Plan: R-28).

🔴 The class under test: a hardcoded ``RESERVE_FLOOR = 20`` in the legacy checker while the
AdminApp planner runs on 30 — two floors, neither stated, and swap lists that disagree for a
reason nobody can see. The contract: settings key wins, CLI wins over settings, default 30
otherwise, and the SOURCE is always returned so the run can print it. All offline.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order_checks import check7  # noqa: E402


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "inventory_reorder_settings.json"
    import appyhour_lib.paths as paths
    monkeypatch.setattr(paths, "inventory_settings_path", lambda **kw: p)
    return p


def test_default_is_30_when_key_absent(settings_file):
    settings_file.write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert check7.reserve_floor() == (30, "default")


def test_settings_key_wins_over_default(settings_file):
    settings_file.write_text(json.dumps({"reserve_floor": 45}), encoding="utf-8")
    assert check7.reserve_floor() == (45, "settings")


def test_cli_override_wins_over_settings(settings_file):
    settings_file.write_text(json.dumps({"reserve_floor": 45}), encoding="utf-8")
    assert check7.reserve_floor(20) == (20, "cli")


def test_missing_settings_file_falls_to_default(tmp_path, monkeypatch):
    import appyhour_lib.paths as paths

    def boom(**kw):
        raise FileNotFoundError("nope")
    monkeypatch.setattr(paths, "inventory_settings_path", boom)
    assert check7.reserve_floor() == (30, "default")


def test_no_module_constant_survives():
    assert not hasattr(check7, "RESERVE_FLOOR")


def test_check7_cli_accepts_reserve_floor(capsys):
    with pytest.raises(SystemExit):
        check7.main(["--help"])
    assert "--reserve-floor" in capsys.readouterr().out
