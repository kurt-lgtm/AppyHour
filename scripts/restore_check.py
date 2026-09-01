#!/usr/bin/env python3
"""Audit a fresh machine for AppyHour restore readiness.

Companion to RESTORE.md. Reports which runtime pieces, repos, creds, and
settings are present/missing, and (with --grep-paths) lists every repo file
still hardcoding the old `C:\\Users\\Work` path so you can sweep them.

Usage:
    python scripts/restore_check.py
    python scripts/restore_check.py --grep-paths
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

OLD_PATH_TOKEN = r"C:\Users\Work"
REPO_ROOT = Path(__file__).resolve().parent.parent

OK, MISS, WARN = "[ OK ]", "[MISS]", "[WARN]"


def _appdata() -> Path:
    # On Windows: %APPDATA%. Elsewhere: best-effort so the script still runs.
    return Path(os.environ.get("APPDATA", str(Path.home() / ".config")))


def check_runtime() -> list[tuple[str, str]]:
    rows = []
    rows.append((OK if sys.version_info >= (3, 10) else MISS,
                 f"Python {sys.version_info.major}.{sys.version_info.minor} (need >=3.10)"))
    for tool in ("git", "gh", "node", "npm"):
        rows.append((OK if shutil.which(tool) else MISS, f"tool on PATH: {tool}"))
    try:
        import tkinter  # noqa: F401
        rows.append((OK, "tkinter importable"))
    except Exception:
        rows.append((MISS, "tkinter importable (Anaconda usually bundles it)"))
    for mod in ("requests", "openpyxl", "pydantic", "yaml", "tenacity"):
        try:
            __import__(mod)
            rows.append((OK, f"pip package: {mod}"))
        except Exception:
            rows.append((MISS, f"pip package: {mod}  (pip install -e '.[dev,fulfillment,shipping,mcp]')"))
    return rows


def check_repos() -> list[tuple[str, str]]:
    projects = REPO_ROOT.parent
    rows = []
    rows.append((OK if (REPO_ROOT / "pyproject.toml").exists() else MISS, "AppyHour repo (this dir)"))
    rows.append((OK if (REPO_ROOT / "GelPackCalculator").exists() else MISS,
                 "GelPackCalculator/ INSIDE AppyHour/ (path-coupled)"))
    rows.append((OK if (projects / "ShipRouting").exists() else MISS,
                 "ShipRouting/ side-by-side with AppyHour/"))
    return rows


def check_data_and_creds() -> list[tuple[str, str]]:
    # 🔴 THIS USED TO CHECK %APPDATA%\AppyHour FOR ALL FOUR FILES, which by 2026-08-31 was
    # backwards: shipping.db moved to C:\AppyHourData on 07-08 (MSIX virtualizes %APPDATA%, and
    # the 07-22 split-brain came from writing the Roaming copy for 9 days), and BOTH settings
    # JSONs were merged onto C:\AppyHourData on 08-31 after three weeks of packaged-vs-unpackaged
    # divergence. A restore audit that probes the retired location reports MISS on a correctly
    # restored machine and OK on a wrongly restored one — worse than not checking at all.
    # portal_creds.json was NOT part of that migration and still lives under %APPDATA%.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from appyhour_lib.paths import DATA_ROOT, GEL_CALC_SETTINGS_NAME, INVENTORY_SETTINGS_NAME, db_path

    rows = [(OK if DATA_ROOT.exists() else MISS, f"canonical data root: {DATA_ROOT}")]
    rows.append((OK if db_path().exists() else MISS, f"  shipping.db -> {db_path()}"))
    for name in (GEL_CALC_SETTINGS_NAME, INVENTORY_SETTINGS_NAME):
        canonical = DATA_ROOT / name
        if canonical.exists():
            rows.append((OK, f"  {name}"))
        elif (_appdata() / "AppyHour" / name).exists():
            # Present but at the retired path: a restore that stops here re-creates the split.
            rows.append((WARN, f"  {name}  (LEGACY %APPDATA% copy only - move to {DATA_ROOT})"))
        else:
            rows.append((MISS, f"  {name}"))
    ad = _appdata() / "AppyHour"
    rows.append((OK if (ad / "portal_creds.json").exists() else MISS,
                 f"  portal_creds.json (still %APPDATA%: {ad})"))
    rows.append((OK if (Path.home() / ".knowledge").exists() else MISS, "~/.knowledge vault"))
    rows.append((OK if (Path.home() / ".claude" / "skills").exists() else MISS, "~/.claude/skills"))
    # Env-var alternative to the JSON creds
    env_ok = bool(os.environ.get("SHOPIFY_STORE_URL") and os.environ.get("SHOPIFY_ACCESS_TOKEN"))
    rows.append((OK if env_ok else WARN, "SHOPIFY_STORE_URL + SHOPIFY_ACCESS_TOKEN env (optional if JSON present)"))
    return rows


def grep_old_paths() -> list[Path]:
    hits = []
    skip = {".git", "__pycache__", "node_modules", ".venv"}
    for p in REPO_ROOT.rglob("*"):
        if p.is_dir() or any(part in skip for part in p.parts):
            continue
        try:
            if OLD_PATH_TOKEN in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(p.relative_to(REPO_ROOT))
        except (OSError, ValueError):
            continue
    return sorted(hits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grep-paths", action="store_true",
                    help=f"list repo files still containing '{OLD_PATH_TOKEN}'")
    args = ap.parse_args()

    if args.grep_paths:
        hits = grep_old_paths()
        if not hits:
            print(f"No files contain '{OLD_PATH_TOKEN}'. Path sweep clean.")
        else:
            print(f"{len(hits)} file(s) still hardcode '{OLD_PATH_TOKEN}' — patch to your new path:")
            for h in hits:
                print(f"  {h}")
        return 0

    missing = 0
    for title, rows in (("RUNTIME", check_runtime()),
                        ("REPOS", check_repos()),
                        ("DATA / CREDS", check_data_and_creds())):
        print(f"\n=== {title} ===")
        for status, label in rows:
            print(f"  {status} {label}")
            if status == MISS:
                missing += 1

    print(f"\n{'-' * 50}")
    print(f"{missing} required item(s) missing. See RESTORE.md for each step.")
    print("Run with --grep-paths to audit hardcoded old paths.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
