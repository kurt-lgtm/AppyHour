"""Create offsite-ready AppyHour backups and upload them with gws."""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    path = base / "AppyHour"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return Path(os.environ.get("AH_DB_OVERRIDE", "") or app_dir() / "shipping.db")


def snapshot_sqlite(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"shipping.db not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(str(dst))
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def logic_docs() -> list[Path]:
    docs = [
        REPO_ROOT / "SHIPPING_PIPELINE.md",
        REPO_ROOT / "REBUILD-WITH-AI.md",
        REPO_ROOT / "HANDOFF.md",
    ]
    docs.extend(sorted((REPO_ROOT / ".claude" / "plans").glob("2026-06-1*.md")))
    return [p for p in docs if p.exists()]


def zip_logic_docs(dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    docs = logic_docs()
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in docs:
            zf.write(path, path.relative_to(REPO_ROOT))
    return len(docs)


def upload(path: Path) -> None:
    # Windows: bare "gws" is a shim (.cmd/.exe) that bare-list subprocess can't resolve
    # (WinError 2). Resolve the real executable; fall back to shell=True.
    import shutil
    exe = shutil.which("gws")
    if exe:
        subprocess.run([exe, "drive", "+upload", str(path)], check=True)
    else:
        subprocess.run(f'gws drive +upload "{path}"', shell=True, check=True)


def prune_weekly_snapshots(backup_dir: Path, keep_days: int = 28, today: date | None = None) -> int:
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    pruned = 0
    for path in backup_dir.glob("shipping.weekly-*.db"):
        try:
            stamp = path.stem.removeprefix("shipping.weekly-")
            if datetime.strptime(stamp, "%Y-%m-%d").date() < cutoff:
                path.unlink()
                pruned += 1
        except Exception:
            continue
    return pruned


def run(today: date | None = None) -> dict:
    day = today or date.today()
    backup_dir = app_dir() / "backups"
    snapshot = backup_dir / f"shipping.weekly-{day:%Y-%m-%d}.db"
    artifacts = REPO_ROOT / "_outputs" / "artifacts"
    docs_zip = artifacts / f"coldchain-logic-backup-{day:%Y-%m-%d}.zip"

    snapshot_sqlite(db_path(), snapshot)
    doc_count = zip_logic_docs(docs_zip)
    upload(snapshot)
    upload(docs_zip)
    pruned = prune_weekly_snapshots(backup_dir, today=day)
    return {
        "snapshot": str(snapshot),
        "docs_zip": str(docs_zip),
        "docs": doc_count,
        "pruned": pruned,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true", help="Create files but skip gws upload")
    args = parser.parse_args(argv)
    try:
        if args.no_upload:
            original_upload = globals()["upload"]
            globals()["upload"] = lambda path: None
            try:
                result = run()
            finally:
                globals()["upload"] = original_upload
        else:
            result = run()
        print(
            "backup_offsite ok "
            f"snapshot={result['snapshot']} docs_zip={result['docs_zip']} "
            f"docs={result['docs']} pruned={result['pruned']}"
        )
        return 0
    except Exception as exc:
        try:
            from appyhour_lib.notify import notify

            notify(f"backup_offsite failed: {type(exc).__name__}: {exc}", level="error")
        except Exception:
            pass
        print(f"backup_offsite fail error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
