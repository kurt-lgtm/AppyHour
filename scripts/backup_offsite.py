"""Create offsite-ready AppyHour backups and upload them with gws."""
from __future__ import annotations

import argparse
import base64
import io
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
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in docs:
            zf.write(path, path.relative_to(REPO_ROOT))
    return len(docs)


def knowledge_roots() -> list[Path]:
    """Vault + skills dirs to back up. NOT in the logic zip — these are the
    operator's Obsidian vault and Claude skills, which the weekly job
    historically skipped (only one snapshot ever made, 2026-06-11)."""
    home = Path.home()
    return [p for p in (home / ".knowledge", home / ".claude" / "skills") if p.exists()]


def zip_knowledge(dst: Path) -> int:
    """Zip vault + skills, preserving top-level dir names. Returns file count."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    roots = knowledge_roots()
    base = Path.home()  # arcname relative to home → ".knowledge/...", ".claude/skills/..."
    count = 0
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for root in roots:
            for path in root.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    zf.write(path, path.relative_to(base))
                    count += 1
    return count


def cred_files() -> list[Path]:
    """Credential/secret files that are in NO other backup. The 2026-06 restore
    had to retype these by hand because they were never offsite. Settings JSONs
    live flat in %APPDATA%\\AppyHour\\; the cut-order server keeps its secrets in
    a .env. The DB lives in the same dir but is backed up separately, so only
    *.json (non-recursive) is collected here."""
    paths = sorted(app_dir().glob("*.json"))
    env = REPO_ROOT / "cut_order_server" / ".env"
    if env.exists():
        paths.append(env)
    return paths


# Encrypted-blob framing: magic + 16-byte scrypt salt + Fernet token.
_ENC_MAGIC = b"AHENC1\n"
_SCRYPT_N = 2**14


def _fernet_for(passphrase: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    key = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=8, p=1).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_creds(dst: Path, passphrase: str) -> int:
    """Zip the cred files IN MEMORY (never plaintext on disk) and write only the
    encrypted blob to ``dst``. Returns the number of files included (0 = nothing
    to back up, nothing written)."""
    files = cred_files()
    if not files:
        return 0
    base = app_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in files:
            # creds dir → bare name; the repo .env → "cut_order_server/.env"
            arc = path.name if path.parent == base else path.relative_to(REPO_ROOT)
            zf.write(path, str(arc))
    salt = os.urandom(16)
    token = _fernet_for(passphrase, salt).encrypt(buf.getvalue())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(_ENC_MAGIC + salt + token)
    return len(files)


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
    knowledge_zip = artifacts / f"coldchain-knowledge-backup-{day:%Y-%m-%d}.zip"
    creds_enc = artifacts / f"coldchain-creds-backup-{day:%Y-%m-%d}.zip.enc"

    snapshot_sqlite(db_path(), snapshot)
    doc_count = zip_logic_docs(docs_zip)
    knowledge_count = zip_knowledge(knowledge_zip)
    upload(snapshot)
    upload(docs_zip)
    if knowledge_count:
        upload(knowledge_zip)

    # Creds: encrypt-or-skip. Never upload secrets in plaintext, and never let a
    # missing passphrase fail the rest of the backup.
    passphrase = os.environ.get("AH_BACKUP_PASSPHRASE", "")
    creds_count = 0
    creds_skipped = ""
    if passphrase:
        creds_count = encrypt_creds(creds_enc, passphrase)
        if creds_count:
            upload(creds_enc)
    elif cred_files():
        creds_skipped = "AH_BACKUP_PASSPHRASE unset"
        print(
            "backup_offsite WARNING: cred files present but AH_BACKUP_PASSPHRASE "
            "unset; skipping creds backup (refusing to upload plaintext secrets)",
            file=sys.stderr,
        )

    pruned = prune_weekly_snapshots(backup_dir, today=day)
    return {
        "snapshot": str(snapshot),
        "docs_zip": str(docs_zip),
        "docs": doc_count,
        "knowledge_zip": str(knowledge_zip) if knowledge_count else "",
        "knowledge_files": knowledge_count,
        "creds_enc": str(creds_enc) if creds_count else "",
        "creds_files": creds_count,
        "creds_skipped": creds_skipped,
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
            f"docs={result['docs']} knowledge_files={result['knowledge_files']} "
            f"creds_files={result['creds_files']}"
            f"{' (' + result['creds_skipped'] + ')' if result['creds_skipped'] else ''} "
            f"pruned={result['pruned']}"
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
