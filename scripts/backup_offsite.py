"""Create offsite-ready AppyHour backups and upload them to Google Drive.

Backs up the LOCAL, single-copy assets that git does NOT hold: the shipping.db
analytics/routing DB, the ~/.knowledge vault + durable ~/.claude state, the
credential set, and the box-size reference xlsx. Code stays on GitHub (Tier A)
and is not re-imaged here.

Upload uses the drive.file OAuth token (gws-INDEPENDENT) with a gws fallback.

Modes:
  python scripts/backup_offsite.py                 # weekly: zips + encrypted creds + Drive upload
  python scripts/backup_offsite.py --no-upload     # produce artifacts, skip upload
  python scripts/backup_offsite.py --daily --dest E:\\AppyHourBackups   # daily local db snapshot, no upload
"""
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

# 🔴 Pin THIS tree's appyhour_lib before any import of it (HEARTBEAT_RULES rule 19, 2026-09-03).
# appyhour_lib is a pip editable install mapped to the DEV tree, so without this line the prod copy
# (C:\AppyHourProd, run by the Weekly Offsite Backup schtask) imported DEV heartbeat/notify — the
# file was byte-identical in both trees and still ran the wrong library. The lib imports below are
# function-level (:525/:599/:678); a module-level pin covers them all. Caught by
# automation_health.check_prod_entry_points on its first run.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def app_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    path = base / "AppyHour"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    # Canonical since 2026-07-08: C:\AppyHourData (outside MSIX-virtualized %APPDATA%,
    # REBUILD-WITH-AI.md §5.1). Legacy %APPDATA% path = pre-migration fallback only.
    # 2026-07-22: key on the canonical DIR, not the file — this runs at LOGON via
    # sync_logon's post-ingest snapshot, and a transient file-invisibility at login
    # must not silently snapshot the legacy path (same race that split-brained
    # sync_logon for 9 days; see appyhour_lib/paths.py db_path docstring).
    override = os.environ.get("AH_DB_OVERRIDE", "")
    if override:
        return Path(override)
    root = Path(r"C:\AppyHourData")
    if root.exists():
        return root / "shipping.db"
    return app_dir() / "shipping.db"


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
    # routing-engine EXPLAINER HTML (stakeholder-facing clickable flowcharts) — part of the system's
    # documentation; lives in the shared _outputs sink (one level up from this repo).
    docs.extend(sorted((REPO_ROOT.parent / "_outputs" / "artifacts").glob("cold-chain-*.html")))
    docs.append(REPO_ROOT.parent / "AGENTS-START-HERE.md")   # agent onboarding map (workspace root, not git-tracked)
    return [p for p in docs if p.exists()]


def zip_logic_docs(dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    docs = logic_docs()
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in docs:
            try:
                arc = path.relative_to(REPO_ROOT)
            except ValueError:
                arc = Path(path.name)   # files outside the repo (e.g. the _outputs/artifacts explainer HTML) → flat
            zf.write(path, arc)
    return len(docs)


# Skip these heavy/regenerable parts when zipping any tree.
# SECURITY (P1, 2026-07-02 audit): "browser_state"/"browser_profile" — the notebooklm
# skill keeps a Chromium profile (Cookies, Login Data = LIVE Google session) under
# skills/notebooklm/data/. The filename secret-filter below only catches *.md names,
# so binary credential stores must be excluded at the directory level. NEVER let a
# browser profile into the cleartext, Drive-uploaded knowledge zip.
_ZIP_SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".venv", "venv",
                   "browser_state", "browser_profile"}


def knowledge_roots() -> list[Path]:
    """Vault + durable Claude state to back up (NOT the logic zip). The whole
    local Claude/knowledge brain: Obsidian vault, skills, hooks, agents, plans,
    scheduled-tasks, rules, commands, and the per-project memory dirs.
    Deliberately EXCLUDES the heavy regenerable trees (sessions, plugins,
    caches, the multi-GB .claude.json history)."""
    home = Path.home()
    cc = home / ".claude"
    cands = [
        home / ".knowledge",
        cc / "skills",
        cc / "hooks",
        cc / "agents",
        cc / "plans",
        cc / "scheduled-tasks",
        cc / "rules",
        cc / "commands",
    ]
    cands += sorted((cc / "projects").glob("*/memory"))  # per-project memory (small markdown)
    return [p for p in cands if p.exists()]


def claude_secret_files() -> list[Path]:
    """Durable Claude config FILES that may hold secrets (API keys, tokens, MCP
    auth). Backed up INSIDE the encrypted creds bundle, NEVER the cleartext
    knowledge zip — settings can gain a secret later even if it has none today."""
    cc = Path.home() / ".claude"
    return [p for p in (cc / "settings.json", cc / "settings.local.json") if p.exists()]


def zip_knowledge(dst: Path) -> int:
    """Zip vault + durable Claude state, preserving top-level dir names relative
    to home (".knowledge/...", ".claude/skills/..."). Returns file count."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    roots = knowledge_roots()
    base = Path.home()
    count = 0
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or (set(path.parts) & _ZIP_SKIP_PARTS):
                    continue
                # SECURITY (P1, 2026-06-25 audit): the per-project memory dirs hold
                # gorgias-credentials.md / rmfg-translator-credentials.md with LIVE secret values.
                # NEVER sweep credential/secret markdown into this CLEARTEXT, Drive-uploaded zip —
                # secrets belong only in the encrypted creds bundle (cred_files -> encrypt_creds).
                _n = path.name.lower()
                if path.suffix.lower() == ".md" and any(
                        k in _n for k in ("credential", "creds", "secret", "password", "token", "apikey", "api-key")):
                    continue
                zf.write(path, path.relative_to(base))
                count += 1
    return count


# 🔴 THE TEST FOR SKIPPING SOMETHING IS NOT "IS IT REGENERABLE" (2026-08-10). It is: what does
# regenerating COST, and what is broken while you regenerate? `carrier_tnt_cache.json` was skipped
# here as a "regenerable cache" — true, and wrong: ~19,000 ShipEngine lanes at a measured 46% 429
# rate is DAYS of wall clock, and for all of those days the cache is cold, which means unmeasured
# lanes score `unproven`, lose to proven lanes at farther hubs, and reintroduce the misroute class
# that put ~110 boxes on the wrong hub in wk0810. Same reasoning retires "regenerable" for the
# lane_audit jsonl and the DistVol xlsx. Answer both questions in a comment before adding a skip.
_CANON_DIR_SKIP = {
    "shipping.db",            # snapshotted separately via snapshot_sqlite (live-safe sqlite backup)
    "shipping.db-wal", "shipping.db-shm", "shipping.db.writelock",
}


def canonical_dir_files() -> list[Path]:
    """Everything in `C:\\AppyHourData` except the DB (handled separately) and its sqlite sidecars.

    🔴 ENUMERATE THE DIRECTORY, NEVER ONE FILENAME. `db_path()` looks for `shipping.db` by name, so
    anything else landing beside it was unbacked BY CONSTRUCTION and silently — which is exactly
    what happened when the carrier-TNT quote cache moved here on 2026-08-10 to escape the MSIX
    `%APPDATA%` overlay split. Enumerating inverts the failure mode: forgetting to ADD something
    no longer loses it; forgetting to SKIP something merely costs a little space."""
    root = Path(r"C:\AppyHourData")
    if not root.is_dir():
        return []
    return [p for p in sorted(root.glob("*"))
            if p.is_file() and p.name not in _CANON_DIR_SKIP
            and ".orphan-" not in p.name and ".MIGRATED-" not in p.name]


def reference_files() -> list[Path]:
    """Single-copy reference data NOT in git that the engine hard-depends on — must not fall out of the
    backup set again (the DistVol lookup was recovered from the old SSD in the 2026-06 restore):
      - box-size DistVol lookup (box_simulation.py crashes without it),
      - carrier COVERAGE files = the routing serviceability AUTHORITY (Veho/OnTrac zip lists; lose these and
        the serviceability gate can't tell who serves a zip),
      - mfg_translations.csv = RMFG product→column mapping (gitignored, so NOT in the code backup),
      - everything else in the canonical data dir (see canonical_dir_files — currently the
        carrier-TNT quote cache, ~19k lanes that cost days of rate-limited API time to rebuild).
    All non-secret → cleartext zip is fine. Flat-named in the zip, so keep filenames distinct."""
    desktop = Path.home() / "Desktop"
    routing = app_dir() / "routing"
    cands = [
        desktop / "Onboarded Items with DistVol - Updated.xlsx",
        desktop / "DistVol_Proposal.xlsx",
        routing / "veho_ground_plus.xlsx",
        routing / "ontrac_master.xlsx",
        REPO_ROOT / "mfg_translations.csv",
    ]
    return [p for p in cands if p.exists()] + canonical_dir_files()


def zip_reference(dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    files = reference_files()
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in files:
            zf.write(path, path.name)
    return len(files)


def cred_files() -> list[Path]:
    """Credential/secret files that are in NO other backup. The 2026-06 restore
    had to retype these by hand because they were never offsite. Collected from
    %APPDATA%\\AppyHour\\: *.json settings (minus regenerable caches + rolling
    backups), *.txt API keys (e.g. shipengine_api_key.txt), everything under
    portal_profiles/, plus the repo-root .env. (cut_order_server/.env is also
    checked but does not currently exist.)"""
    base = app_dir()
    # 🔴 Before adding a name here, answer BOTH questions in the _CANON_DIR_SKIP comment above:
    # what does regenerating cost, and what is broken while you regenerate? "Regenerable" alone
    # is not the test — that reasoning is what left the quote cache unbacked.
    # `carrier_tnt_cache.json` stays listed because the file MOVED to C:\AppyHourData on
    # 2026-08-10 and is now backed up by canonical_dir_files(); the legacy %APPDATA% copy is a
    # renamed .MIGRATED- corpse. `sync_heartbeat.json` is the SAME shape as of 2026-09-01: it
    # moved to C:\AppyHourData too (MSIX was serving the writer and the checker two different
    # physical files), so canonical_dir_files() now sweeps it up — 658 bytes, and per that
    # function's own rule forgetting to SKIP something only costs space while forgetting to ADD
    # it loses the file. Both names stay listed here to keep any stale %APPDATA% copy — which is
    # now a dead legacy read-fallback, not state — out of the credential set.
    _SKIP_JSON = {"carrier_tnt_cache.json", "sync_heartbeat.json"}

    def _is_junk(name: str) -> bool:
        return (
            name in _SKIP_JSON
            or ".bak" in name
            or ".backup-" in name
            or ".broken" in name
            or "CORRUPT" in name
        )

    paths = [p for p in sorted(base.glob("*.json")) if not _is_junk(p.name)]
    paths += sorted(base.glob("*.txt"))
    # NOTE: portal_profiles/ (Chrome profile clones) is deliberately EXCLUDED — its
    # cookies/login-data are App-Bound-encrypted (machine-locked, non-portable to a
    # restore target) and it bloats to ~470 files of regenerable browser state. The
    # actual carrier-portal credentials live in portal_creds.json (caught by *.json).
    for env in (REPO_ROOT / ".env", REPO_ROOT / "cut_order_server" / ".env"):
        if env.exists():
            paths.append(env)
    # the backup's OWN Drive OAuth token — without it a restore can't even re-run this uploader (chicken-and-egg)
    tok = REPO_ROOT / "InventoryReorder" / "dist" / "drive_oauth_token.json"
    if tok.exists():
        paths.append(tok)
    paths += claude_secret_files()  # settings*.json → encrypted, never the cleartext knowledge zip
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
    cc = Path.home() / ".claude"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for path in files:
            # %APPDATA%/AppyHour/... → keep subpath; ~/.claude/... → claude/...; repo → repo/.env
            try:
                arc = path.relative_to(base)
            except ValueError:
                try:
                    arc = Path("claude") / path.relative_to(cc)
                except ValueError:
                    try:
                        arc = Path("repo") / path.relative_to(REPO_ROOT)
                    except ValueError:
                        arc = Path(path.name)
            zf.write(path, str(arc))
    salt = os.urandom(16)
    token = _fernet_for(passphrase, salt).encrypt(buf.getvalue())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(_ENC_MAGIC + salt + token)
    return len(files)


_drive_svc = None  # cached Drive service for this process


def upload(path: Path) -> dict:
    """Upload one artifact to Google Drive and RETURN the Drive metadata.

    Primary path = the drive.file OAuth token (gws-INDEPENDENT, via the sibling
    drive_backup_upload module). Falls back to the gws CLI only if the OAuth
    path is unavailable, so a missing or expired token degrades instead of
    silently dropping the offsite copy.

    The return value is what makes the run HONEST: ``run()`` re-reads Drive's
    reported id+size for every artifact and hard-fails when an upload didn't
    actually land (2026-08-09 — before this, the run logged "backup OK" off the
    local zips alone and an upload that never happened was indistinguishable
    from one that did).
    """
    global _drive_svc
    try:
        sd = str(Path(__file__).resolve().parent)
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import drive_backup_upload as dbu

        if _drive_svc is None:
            _drive_svc = dbu._drive()
        return dbu.upload(_drive_svc, path) or {}
    except Exception as exc:
        exe = shutil.which("gws")
        if not exe:
            raise
        print(
            f"backup_offsite: OAuth upload failed ({type(exc).__name__}: {exc}); falling back to gws",
            file=sys.stderr,
        )
        subprocess.run([exe, "drive", "+upload", str(path)], check=True)
        return _gws_lookup(exe, path.name)


def _gws_lookup(exe: str, name: str) -> dict:
    """Read back the just-uploaded file's id+size via gws so the fallback path is
    verifiable too. `gws drive +upload` prints nothing we can trust, and an
    unverifiable upload is exactly the silent-success this module must not have.
    Returns {} on any failure -> the caller reports it as a problem."""
    import json as _json

    q = f"name = '{name}' and trashed = false"
    params = _json.dumps({"q": q, "fields": "files(id,name,size)", "orderBy": "createdTime desc",
                          "pageSize": 1, "supportsAllDrives": True, "includeItemsFromAllDrives": True})
    try:
        out = subprocess.run([exe, "drive", "files", "list", "--params", params],
                             capture_output=True, text=True, timeout=120)
        files = _json.loads(out.stdout).get("files", [])
        return files[0] if files else {}
    except Exception:
        return {}


def verify_uploads(uploaded: list[tuple[Path, dict]]) -> list[str]:
    """Compare each artifact's LOCAL bytes against what Drive says it stored.

    Catches, per acceptance 2026-08-09: a zero-byte artifact, an upload that
    returned no id (never landed), and a truncated/partial resumable upload.
    """
    problems: list[str] = []
    for path, res in uploaded:
        if res.get("skipped"):
            continue
        local = path.stat().st_size if path.exists() else 0
        if local == 0:
            problems.append(f"{path.name}: local artifact is 0 bytes")
            continue
        if not res.get("id"):
            problems.append(f"{path.name}: upload returned no Drive id (did NOT land offsite)")
            continue
        remote = int(res.get("size") or 0)
        if remote != local:
            problems.append(f"{path.name}: Drive size {remote} != local {local} (partial upload)")
    return problems


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


def prune_orphan_sidecars(backup_dir: Path) -> int:
    """Remove orphaned SQLite WAL/SHM sidecars left next to past snapshots. The
    live DB is in app_dir(), not here, so any *.db-shm/*.db-wal in a backup dir
    is a leftover and safe to delete."""
    pruned = 0
    for path in list(backup_dir.glob("*.db-shm")) + list(backup_dir.glob("*.db-wal")):
        try:
            path.unlink()
            pruned += 1
        except Exception:
            continue
    return pruned


def _self_check_and_log(result: dict, day: date) -> None:
    """Fail-loud guard + one-line log. The backup previously rotted unnoticed by
    silently skipping legs; assert the critical ones produced output and record a
    status line either way. HARD-fails only on the irreplaceable DB snapshot."""
    snap = Path(result.get("snapshot", ""))
    snap_bytes = snap.stat().st_size if snap.exists() else 0
    problems: list[str] = []
    if snap_bytes == 0:
        problems.append("shipping.db snapshot missing/empty")
    if result.get("knowledge_files", 0) == 0:
        problems.append("knowledge bundle empty (vault/skills not found)")
    if result.get("creds_skipped"):
        problems.append(f"creds skipped: {result['creds_skipped']}")
    # 2026-08-09: an OFFSITE backup that stayed on this machine is not a backup.
    # Drive-verified bytes are part of the pass condition, not a nice-to-have.
    upload_problems = list(result.get("upload_problems") or [])
    problems.extend(upload_problems)
    verified = sum(1 for _p, r in result.get("uploaded", []) if r.get("id"))
    status = "OK" if not problems else "DEGRADED"
    line = (
        f"{datetime.now():%Y-%m-%d %H:%M:%S} backup {status} "
        f"snapshot_bytes={snap_bytes} knowledge={result.get('knowledge_files', 0)} "
        f"creds={result.get('creds_files', 0)} reference={result.get('reference_files', 0)} "
        f"docs={result.get('docs', 0)} pruned={result.get('pruned', 0)} "
        f"drive_verified={verified}/{len(result.get('uploaded', []))}"
        + (f" problems={'; '.join(problems)}" if problems else "")
    )
    log_dir = REPO_ROOT / "_outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"backup-{day:%Y-%m-%d}.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if snap_bytes == 0 or upload_problems:
        raise RuntimeError("backup self-check FAILED: " + "; ".join(problems))


def run(today: date | None = None) -> dict:
    day = today or date.today()
    backup_dir = db_path().parent / "backups"
    snapshot = backup_dir / f"shipping.weekly-{day:%Y-%m-%d}.db"
    artifacts = REPO_ROOT / "_outputs" / "artifacts"
    docs_zip = artifacts / f"coldchain-logic-backup-{day:%Y-%m-%d}.zip"
    knowledge_zip = artifacts / f"coldchain-knowledge-backup-{day:%Y-%m-%d}.zip"
    reference_zip = artifacts / f"coldchain-reference-backup-{day:%Y-%m-%d}.zip"
    creds_enc = artifacts / f"coldchain-creds-backup-{day:%Y-%m-%d}.zip.enc"

    snapshot_sqlite(db_path(), snapshot)
    doc_count = zip_logic_docs(docs_zip)
    knowledge_count = zip_knowledge(knowledge_zip)
    reference_count = zip_reference(reference_zip)

    uploaded: list[tuple[Path, dict]] = []

    def _up(path: Path) -> None:
        # resolve `upload` at CALL time so --no-upload's monkeypatch still applies
        res = globals()["upload"](path)
        uploaded.append((path, res if isinstance(res, dict) else {}))

    _up(snapshot)
    _up(docs_zip)
    if knowledge_count:
        _up(knowledge_zip)
    if reference_count:
        _up(reference_zip)

    # Creds: encrypt-or-skip. Never upload secrets in plaintext, and never let a
    # missing passphrase fail the rest of the backup.
    passphrase = os.environ.get("AH_BACKUP_PASSPHRASE", "")
    creds_count = 0
    creds_skipped = ""
    if passphrase:
        creds_count = encrypt_creds(creds_enc, passphrase)
        if creds_count:
            _up(creds_enc)
    elif cred_files():
        creds_skipped = "AH_BACKUP_PASSPHRASE unset"
        print(
            "backup_offsite WARNING: cred files present but AH_BACKUP_PASSPHRASE "
            "unset; skipping creds backup (refusing to upload plaintext secrets)",
            file=sys.stderr,
        )

    pruned = prune_weekly_snapshots(backup_dir, today=day)
    pruned += prune_orphan_sidecars(backup_dir)
    result = {
        "snapshot": str(snapshot),
        "docs_zip": str(docs_zip),
        "docs": doc_count,
        "knowledge_zip": str(knowledge_zip) if knowledge_count else "",
        "knowledge_files": knowledge_count,
        "reference_zip": str(reference_zip) if reference_count else "",
        "reference_files": reference_count,
        "creds_enc": str(creds_enc) if creds_count else "",
        "creds_files": creds_count,
        "creds_skipped": creds_skipped,
        "pruned": pruned,
        "uploaded": [(str(p), r) for p, r in uploaded],
        "upload_problems": verify_uploads(uploaded),
    }
    _self_check_and_log(result, day)
    # Dead-man-switch (HEARTBEAT_RULES.md): success beat + optional external healthchecks ping.
    # Both fire-and-forget — a good backup must exit 0 even if the watchers can't be told.
    try:
        from appyhour_lib.heartbeat import beat
        beat("offsite-backup")
    except Exception:
        pass
    hc_url = os.environ.get("HEALTHCHECKS_BACKUP_URL")
    if hc_url:
        try:
            import requests
            requests.get(hc_url, timeout=10)
        except Exception:
            pass
    return result


def run_daily(dest_root: Path, today: date | None = None, keep: int = 14) -> dict:
    """Daily LOCAL snapshot of shipping.db to a SECOND physical disk (the
    repurposed E:). No zips, no upload — just the most-churned asset, kept N
    snapshots deep. Scheduled separately from the weekly offsite run."""
    day = today or date.today()
    daily_dir = dest_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    snap = daily_dir / f"shipping.daily-{day:%Y-%m-%d}.db"
    snapshot_sqlite(db_path(), snap)
    snaps = sorted(daily_dir.glob("shipping.daily-*.db"))
    pruned = 0
    for old in (snaps[:-keep] if len(snaps) > keep else []):
        try:
            old.unlink()
            pruned += 1
        except Exception:
            continue
    pruned += prune_orphan_sidecars(daily_dir)
    return {"snapshot": str(snap), "kept": min(len(snaps), keep), "pruned": pruned}


def snapshot_after_ingest(keep: int = 20, now: datetime | None = None) -> dict:
    """Safe LOCAL snapshot of shipping.db taken at the END of every ingest.

    Why: the weekly offsite backup is too coarse — the 2026-06-27 corruption
    sat between weekly snapshots and ~2 days of churn were at risk. Snapshotting
    after each ingest bounds worst-case loss to a single ingest cycle.

    INTEGRITY GATE (critical): run ``PRAGMA quick_check`` first and REFUSE to
    snapshot a corrupt DB. Without this, the post-ingest backup would happily
    copy a torn 22 MB database over the last good 137 MB snapshot — turning a
    minor incident into total loss. A corrupt DB leaves the prior good
    snapshots untouched and raises a loud notification instead.

    Returns a status dict: status ∈ {"ok", "skipped-corrupt", "error"}.
    """
    src = db_path()
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Integrity gate — read-only so it never locks out the live writer.
    detail = "ok"
    try:
        ro = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            ro.execute("PRAGMA busy_timeout=10000")
            rows = ro.execute("PRAGMA quick_check(1)").fetchall()
        finally:
            ro.close()
        healthy = len(rows) == 1 and rows[0][0] == "ok"
        if not healthy:
            detail = f"quick_check={rows[:1]}"
    except sqlite3.DatabaseError as exc:
        healthy = False
        detail = f"{type(exc).__name__}: {exc}"

    if not healthy:
        msg = f"shipping.db failed integrity gate ({detail}) — refusing to snapshot a corrupt DB"
        print("backup_offsite: " + msg, file=sys.stderr)
        try:
            from appyhour_lib.notify import notify

            notify(f"post-ingest backup SKIPPED: {msg}", level="error")
        except Exception:
            pass
        return {"status": "skipped-corrupt", "detail": detail}

    # 2. Consistent online-backup snapshot (cannot tear a live DB).
    stamp = f"{now or datetime.now():%Y-%m-%d_%H%M%S}"
    snap = backup_dir / f"shipping.after-ingest-{stamp}.db"
    snapshot_sqlite(src, snap)

    # 3. Rotate: keep the last N after-ingest snapshots (weekly/daily are separate).
    snaps = sorted(backup_dir.glob("shipping.after-ingest-*.db"))
    pruned = 0
    for old in snaps[:-keep] if len(snaps) > keep else []:
        try:
            old.unlink()
            pruned += 1
        except Exception:
            continue
    pruned += prune_orphan_sidecars(backup_dir)
    return {
        "status": "ok",
        "snapshot": str(snap),
        "bytes": snap.stat().st_size if snap.exists() else 0,
        "kept": min(len(snaps), keep),
        "pruned": pruned,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true", help="Create files but skip Drive upload")
    parser.add_argument("--daily", action="store_true",
                        help="Daily local shipping.db snapshot to --dest (no zips, no upload)")
    parser.add_argument("--dest", help="Destination root for --daily (e.g. E:\\AppyHourBackups)")
    parser.add_argument("--after-ingest", action="store_true",
                        help="Integrity-gated local snapshot to backups/ (call at end of every ingest)")
    args = parser.parse_args(argv)
    try:
        if args.after_ingest:
            result = snapshot_after_ingest()
            print("backup_offsite after-ingest " + " ".join(f"{k}={v}" for k, v in result.items()))
            return 0 if result.get("status") == "ok" else 1

        if args.daily:
            if not args.dest:
                print("backup_offsite: --daily requires --dest", file=sys.stderr)
                return 2
            result = run_daily(Path(args.dest))
            print(
                f"backup_offsite daily ok snapshot={result['snapshot']} "
                f"kept={result['kept']} pruned={result['pruned']}"
            )
            return 0

        if args.no_upload:
            original_upload = globals()["upload"]
            globals()["upload"] = lambda path: {"skipped": True}
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
            f"reference_files={result['reference_files']} creds_files={result['creds_files']}"
            f"{' (' + result['creds_skipped'] + ')' if result['creds_skipped'] else ''} "
            f"drive_verified={sum(1 for _p, r in result['uploaded'] if r.get('id'))}/"
            f"{len(result['uploaded'])} "
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
