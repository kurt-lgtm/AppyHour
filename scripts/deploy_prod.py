"""Create or refresh pinned production checkouts under C:\\AppyHourProd."""
from __future__ import annotations

import subprocess
from pathlib import Path


PROD_ROOT = Path(r"C:\AppyHourProd")
REPOS = {
    "AppyHour": {
        "url": "https://github.com/kurt-lgtm/AppyHour.git",
        "path": PROD_ROOT / "AppyHour",
        "branch": "main",
    },
    "GelPackCalculator": {
        "url": "https://github.com/kurt-lgtm/GelPackCalculator.git",
        "path": PROD_ROOT / "AppyHour" / "GelPackCalculator",
        "branch": "master",
    },
    "ShipRouting": {
        "url": "https://github.com/kurt-lgtm/ShipRouting.git",
        "path": PROD_ROOT / "ShipRouting",
        "branch": "master",   # ShipRouting's default branch (created from local master 2026-06-11)
    },
}


def git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def clone_or_pull(name: str, spec: dict) -> dict:
    path = spec["path"]
    branch = spec["branch"]
    if path.exists():
        git(["fetch", "--prune", "origin"], cwd=path)
        git(["checkout", branch], cwd=path)
        git(["pull", "--ff-only", "origin", branch], cwd=path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(["clone", "--branch", branch, "--single-branch", spec["url"], str(path)])
    commit = git(["rev-parse", "--short", "HEAD"], cwd=path)
    full = git(["rev-parse", "HEAD"], cwd=path)
    return {"name": name, "branch": branch, "path": str(path), "commit": commit, "full": full}


def main() -> int:
    deployed = [clone_or_pull(name, spec) for name, spec in REPOS.items()]
    for item in deployed:
        print(f"{item['name']} {item['branch']} {item['commit']} {item['path']}")
    print("Scheduled tasks were not repointed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
