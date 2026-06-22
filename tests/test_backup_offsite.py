import zipfile
from datetime import date

from scripts.backup_offsite import prune_weekly_snapshots, zip_knowledge


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
