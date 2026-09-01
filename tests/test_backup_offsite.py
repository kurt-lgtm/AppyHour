import zipfile
from datetime import date

import pytest

from scripts.backup_offsite import (
    _self_check_and_log,
    prune_weekly_snapshots,
    verify_uploads,
    zip_knowledge,
)


def test_prune_weekly_snapshots_removes_only_older_than_28_days(tmp_path):
    old = tmp_path / "shipping.weekly-2026-05-01.db"
    keep_boundary = tmp_path / "shipping.weekly-2026-05-15.db"
    keep_new = tmp_path / "shipping.weekly-2026-06-01.db"
    unrelated = tmp_path / "shipping.snapshot-2026-05-01.db"
    for path in (old, keep_boundary, keep_new, unrelated):
        path.write_text("x", encoding="utf-8")

    assert prune_weekly_snapshots(tmp_path, today=date(2026, 6, 12)) == 1
    assert not old.exists()
    assert keep_boundary.exists()
    assert keep_new.exists()
    assert unrelated.exists()


def test_zip_knowledge_bundles_roots_preserving_dir_names(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".knowledge" / "ops").mkdir(parents=True)
    (home / ".knowledge" / "ops" / "note.md").write_text("vault", encoding="utf-8")
    (home / ".claude" / "skills" / "s").mkdir(parents=True)
    (home / ".claude" / "skills" / "s" / "SKILL.md").write_text("skill", encoding="utf-8")
    (home / ".knowledge" / "__pycache__").mkdir()
    (home / ".knowledge" / "__pycache__" / "x.pyc").write_text("junk", encoding="utf-8")

    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))

    dst = tmp_path / "out" / "knowledge.zip"
    count = zip_knowledge(dst)

    assert count == 2  # __pycache__ excluded
    names = set(zipfile.ZipFile(dst).namelist())
    assert ".knowledge/ops/note.md" in names
    assert ".claude/skills/s/SKILL.md" in names


# --- offsite HONESTY: a backup that never left the machine must not report OK ---


def _artifact(tmp_path, name="a.zip", body="payload"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_verify_uploads_passes_when_drive_size_matches(tmp_path):
    p = _artifact(tmp_path)
    res = {"id": "abc", "size": str(p.stat().st_size)}
    assert verify_uploads([(p, res)]) == []


def test_verify_uploads_flags_missing_drive_id(tmp_path):
    p = _artifact(tmp_path)
    problems = verify_uploads([(p, {})])
    assert len(problems) == 1 and "did NOT land offsite" in problems[0]


def test_verify_uploads_flags_partial_upload(tmp_path):
    p = _artifact(tmp_path)
    problems = verify_uploads([(p, {"id": "abc", "size": "1"})])
    assert len(problems) == 1 and "partial upload" in problems[0]


def test_verify_uploads_flags_zero_byte_artifact(tmp_path):
    p = _artifact(tmp_path, body="")
    problems = verify_uploads([(p, {"id": "abc", "size": "0"})])
    assert len(problems) == 1 and "0 bytes" in problems[0]


def test_verify_uploads_ignores_skipped_no_upload_mode(tmp_path):
    p = _artifact(tmp_path)
    assert verify_uploads([(p, {"skipped": True})]) == []


def test_self_check_hard_fails_on_unverified_upload(tmp_path, monkeypatch):
    """The specific failure being fixed: local zips all fine, nothing on Drive."""
    monkeypatch.setattr("scripts.backup_offsite.REPO_ROOT", tmp_path)
    snap = _artifact(tmp_path, "shipping.weekly-2026-08-09.db", "db")
    result = {
        "snapshot": str(snap),
        "knowledge_files": 10,
        "creds_files": 1,
        "reference_files": 4,
        "docs": 14,
        "pruned": 0,
        "uploaded": [(str(snap), {})],
        "upload_problems": ["shipping.weekly-2026-08-09.db: upload returned no Drive id (did NOT land offsite)"],
    }
    with pytest.raises(RuntimeError, match="did NOT land offsite"):
        _self_check_and_log(result, date(2026, 8, 9))

    log = (tmp_path / "_outputs" / "logs" / "backup-2026-08-09.log").read_text(encoding="utf-8")
    assert "DEGRADED" in log and "drive_verified=0/1" in log
