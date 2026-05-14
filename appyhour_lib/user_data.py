"""User-uploaded data persistence — durable across Claude sessions.

Problem this solves
===================
When a user @-references a file from Downloads (or anywhere else) in a
Claude conversation, that file's location lives only in conversation
context. Next session starts cold, has no memory of it, and the user
has to re-upload — eroding trust ("you forgot the Orlando proposal").

Architecture
============
On first reference, copy the file into a durable, structured location:

    %APPDATA%\\AppyHour\\user-data\\<YYYY-MM-DD>-<topic-slug>\\
        manifest.json          — paths + timestamps + tags (NO conversation text)
        <original-filename>     — the file (verbatim copy)

`%APPDATA%` chosen (NOT `~/.claude/`) so:
  • Customer PII (tracking IDs, addresses) doesn't leak into AAAK or
    PreCompact summaries sent to LLM API
  • Stays on the user's machine; gitignored at AppyHour repo root
  • Co-located with shipping.db for natural backup grouping

The `manifest.json` records ONLY metadata — never conversation excerpts:
  {
    "session_date": "2026-05-14",
    "topic": "fedex-rate-proposal-orlando",
    "files": [{"name": "...", "size": ..., "added": "...", "source": "..."}],
    "tags": ["fedex", "rates", "orlando"]
  }

Future session SessionStart hook scans for recent topics (TTL: 60 days,
then archive to `_outputs/archive/`) and surfaces them in [Restore].
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .paths import appyhour_appdata

__all__ = [
    "user_data_root",
    "save_user_file",
    "list_topics",
    "find_topic",
    "sweep_old_topics",
]

# Topics older than this go to archive (or get deleted if archive=False)
TTL_DAYS = 60


def user_data_root() -> Path:
    """Return the user-data root dir. Creates if missing."""
    p = appyhour_appdata() / "user-data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(s: str) -> str:
    """Filesystem-safe lowercase slug."""
    s = re.sub(r"[^a-zA-Z0-9-]+", "-", s.strip().lower())
    return re.sub(r"-+", "-", s).strip("-") or "untitled"


def _topic_dir(session_date: str, topic: str) -> Path:
    d = user_data_root() / f"{session_date}-{_slug(topic)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_manifest(td: Path) -> dict:
    mf = td / "manifest.json"
    if not mf.exists():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_manifest(td: Path, m: dict) -> None:
    (td / "manifest.json").write_text(
        json.dumps(m, indent=2, default=str), encoding="utf-8"
    )


def save_user_file(
    source_path: str | Path,
    *,
    topic: str,
    tags: list[str] | None = None,
    session_date: str | None = None,
    copy: bool = True,
) -> Path:
    """Save a user-uploaded file to durable storage.

    Args:
        source_path: where the file currently lives (typically Downloads).
        topic: short slug grouping related files (e.g. "fedex-rate-proposal").
        tags: extra searchable tags. NO conversation text.
        session_date: YYYY-MM-DD (defaults to today).
        copy: True (default) preserves original; False moves the file.

    Returns:
        Path to the durable copy.
    """
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"source not found: {src}")

    session_date = session_date or datetime.now().strftime("%Y-%m-%d")
    td = _topic_dir(session_date, topic)
    dst = td / src.name

    if not dst.exists():
        if copy:
            shutil.copy2(src, dst)
        else:
            shutil.move(str(src), str(dst))

    # Update manifest (metadata only — no conversation excerpts)
    m = _read_manifest(td)
    m.setdefault("session_date", session_date)
    m.setdefault("topic", topic)
    m.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    m.setdefault("tags", [])
    if tags:
        for t in tags:
            if t not in m["tags"]:
                m["tags"].append(t)
    m.setdefault("files", [])
    if not any(f.get("name") == src.name for f in m["files"]):
        m["files"].append({
            "name": src.name,
            "size": dst.stat().st_size,
            "added": datetime.now().isoformat(timespec="seconds"),
            "source_dir": str(src.parent),
        })
    _write_manifest(td, m)
    return dst


def list_topics(*, days: int = 60) -> list[dict]:
    """List active topics newer than `days`. Most recent first.

    Each item: {dir, manifest, age_days}
    """
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for td in sorted(user_data_root().iterdir()):
        if not td.is_dir():
            continue
        m = _read_manifest(td)
        if not m:
            continue
        try:
            sd = datetime.fromisoformat(m.get("session_date", "1970-01-01"))
        except ValueError:
            continue
        if sd < cutoff:
            continue
        out.append({
            "dir": td,
            "manifest": m,
            "age_days": (datetime.now() - sd).days,
        })
    out.sort(key=lambda x: x["manifest"].get("updated_at", ""), reverse=True)
    return out


def find_topic(query: str) -> list[Path]:
    """Substring search topics by name + tags. Returns matching dirs."""
    q = query.lower().strip()
    hits = []
    for td in user_data_root().iterdir():
        if not td.is_dir():
            continue
        if q in td.name.lower():
            hits.append(td)
            continue
        m = _read_manifest(td)
        haystack = " ".join([
            m.get("topic", ""),
            " ".join(m.get("tags", [])),
            " ".join(f.get("name", "") for f in m.get("files", [])),
        ]).lower()
        if q in haystack:
            hits.append(td)
    return hits


def sweep_old_topics(*, ttl_days: int = TTL_DAYS, archive_dir: Path | None = None) -> dict:
    """Move topics older than ttl_days to archive_dir (or delete if None).

    Idempotent + safe — never touches the manifest of a kept topic.
    Returns {archived: n, deleted: n}.
    """
    cutoff = datetime.now() - timedelta(days=ttl_days)
    archived = deleted = 0
    for td in user_data_root().iterdir():
        if not td.is_dir():
            continue
        m = _read_manifest(td)
        try:
            sd = datetime.fromisoformat(m.get("session_date", "1970-01-01"))
        except ValueError:
            continue
        if sd >= cutoff:
            continue
        if archive_dir:
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(td), str(archive_dir / td.name))
            archived += 1
        else:
            shutil.rmtree(td, ignore_errors=True)
            deleted += 1
    return {"archived": archived, "deleted": deleted}
