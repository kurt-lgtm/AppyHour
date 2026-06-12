from datetime import date

from scripts.backup_offsite import prune_weekly_snapshots


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
