"""Dev -> prod deploy for the scheduled-task tree (C:\\AppyHourProd\\AppyHour).

Dry-run DEFAULT: prints the exact drift list (stale / prod-newer / dev-only) and copies
NOTHING. `--apply` copies dev-newer tracked *.py files onto their prod counterparts and
appends one JSONL row per copy to _outputs/logs/deploy_prod.jsonl.

Why (2026-08-29): prod sat 9 -> 12 -> 20 files stale vs dev across Jul-Aug while the
scheduled tasks (appyhour_daily_*, carrier sync, postmortem) executed the stale tree —
"an undeployed fix is not a fix" (HEARTBEAT_RULES.md rule 9). This file previously held a
git-pull deploy (clone/pull origin/main — see git history); that path is DEAD: dev is
~322 commits ahead of origin/main (never pushed) and the prod checkout carries dirty
hand-edits a pull would fight. File-copy with guardrails is the honest mechanism.

Guardrails (NEGATIVES first):
- 🔴 NEVER a blind robocopy. Tracked set = exactly what automation_health.check_prod_parity
  monitors: dev `*.py` outside PARITY_SKIP_DIRS whose prod counterpart exists. Dev-only
  files are LISTED but copied only with --include-new (a fix split across a new module is
  half-deployed without it — the 07-27 guard-without-resolver burn).
- 🔴 NEVER copies *.db, .env*, __pycache__, logs, .git — enforced by a hard guard that
  raises, not skips, if such a path ever enters the copy set.
- 🔴 REFUSES --apply entirely (exit 2, zero copies) while ANY tracked file is newer in
  prod with differing bytes — that is a prod-side hand-edit; surface it, never clobber
  (HEARTBEAT_RULES rule 9 NEGATIVE). No force flag, by design. Reconcile dev first.
- Copies are read back and byte-compared after write (audit the artifact that landed).

Run:  python scripts/deploy_prod.py            # dry-run (exit 1 if drift, 0 if clean)
      python scripts/deploy_prod.py --apply    # Kurt's call — live tree for schtasks
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import automation_health as ah  # noqa: E402  (single source for roots/skip-dirs/keywords)

DEFAULT_LOG = Path(r"C:\Users\Work\Claude Projects\_outputs\logs\deploy_prod.jsonl")
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".env")
FORBIDDEN_PARTS = {"__pycache__", "logs", ".git"}


class ForbiddenPathError(RuntimeError):
    """A path that must never be deployed reached the copy set."""


def _assert_deployable(rel: Path) -> None:
    if rel.suffix.lower() in FORBIDDEN_SUFFIXES or rel.name.lower().startswith(".env"):
        raise ForbiddenPathError(f"refusing to deploy secret/db file: {rel}")
    if FORBIDDEN_PARTS & {p.lower() for p in rel.parts}:
        raise ForbiddenPathError(f"refusing to deploy from forbidden dir: {rel}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:10]


def _mt(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def classify(dev_root: Path, prod_root: Path) -> dict[str, list[dict]]:
    """Enumerate the tracked set the same way automation_health.check_prod_parity does."""
    out: dict[str, list[dict]] = {"stale": [], "prod_newer": [], "dev_only": []}
    for dev_file in sorted(dev_root.rglob("*.py")):
        rel = dev_file.relative_to(dev_root)
        if ah.PARITY_SKIP_DIRS & set(rel.parts):
            continue
        prod_file = prod_root / rel
        if not prod_file.exists():
            out["dev_only"].append({"rel": rel, "dev": dev_file})
            continue
        dev_bytes = dev_file.read_bytes()
        prod_bytes = prod_file.read_bytes()
        if dev_bytes == prod_bytes:
            continue
        text = dev_bytes.decode("utf-8", errors="replace")
        row = {
            "rel": rel, "dev": dev_file, "prod": prod_file,
            "dev_sha": _sha(dev_bytes), "prod_sha": _sha(prod_bytes),
            "db_relevant": any(k in text for k in ah.PARITY_KEYWORDS),
        }
        if dev_file.stat().st_mtime > prod_file.stat().st_mtime:
            out["stale"].append(row)
        else:  # prod newer OR equal-mtime-different-bytes: not provably dev-newer
            out["prod_newer"].append(row)
    return out


def print_report(c: dict[str, list[dict]], dev_root: Path, prod_root: Path) -> None:
    print(f"deploy_prod DRY-RUN  dev={dev_root}  prod={prod_root}")
    print(f"STALE — dev newer, would copy with --apply: {len(c['stale'])}")
    for r in c["stale"]:
        flag = "  [DB-relevant]" if r["db_relevant"] else ""
        print(f"  {r['rel']}  dev {_mt(r['dev'])} sha {r['dev_sha']}  "
              f"prod {_mt(r['prod'])} sha {r['prod_sha']}{flag}")
    print(f"PROD-NEWER — hand-edit? never clobbered; --apply REFUSES while these exist: "
          f"{len(c['prod_newer'])}")
    for r in c["prod_newer"]:
        print(f"  {r['rel']}  dev {_mt(r['dev'])} sha {r['dev_sha']}  "
              f"prod {_mt(r['prod'])} sha {r['prod_sha']}")
    print(f"DEV-ONLY — no prod counterpart; copied only with --include-new: "
          f"{len(c['dev_only'])}")
    for r in c["dev_only"]:
        print(f"  {r['rel']}  dev {_mt(r['dev'])}")


def apply_copies(c: dict[str, list[dict]], prod_root: Path, log_path: Path,
                 include_new: bool) -> int:
    if c["prod_newer"]:
        print(f"REFUSED: {len(c['prod_newer'])} file(s) newer in prod (hand-edit?) — "
              "reconcile into dev first; this tool never clobbers prod-side edits:")
        for r in c["prod_newer"]:
            print(f"  {r['rel']}  prod {_mt(r['prod'])} > dev {_mt(r['dev'])}")
        return 2
    todo = list(c["stale"])
    if include_new:
        todo += [{**r, "prod": prod_root / r["rel"], "new": True} for r in c["dev_only"]]
    if not todo:
        print("nothing to deploy — prod in sync with dev on the tracked set")
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        for r in todo:
            _assert_deployable(r["rel"])
            dev_bytes = r["dev"].read_bytes()
            prod_mtime_before = _mt(r["prod"]) if r["prod"].exists() else None
            r["prod"].parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(r["dev"], r["prod"])
            if r["prod"].read_bytes() != dev_bytes:  # audit the artifact that landed
                raise OSError(f"post-copy verify FAILED for {r['rel']} — bytes differ")
            log.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action": "copy", "rel": str(r["rel"]),
                "dev_mtime": _mt(r["dev"]), "prod_mtime_before": prod_mtime_before,
                "sha256_10": _sha(dev_bytes), "bytes": len(dev_bytes),
                "new_file": bool(r.get("new")), "prod_root": str(prod_root),
            }) + "\n")
            print(f"deployed {r['rel']}")
    print(f"deployed {len(todo)} file(s); logged to {log_path}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="copy dev->prod (default: dry-run)")
    ap.add_argument("--include-new", action="store_true",
                    help="with --apply, also copy dev-only files missing from prod")
    ap.add_argument("--dev-root", type=Path, default=ah.DEV_ROOT)
    ap.add_argument("--prod-root", type=Path, default=ah.PROD_ROOT)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)
    if not args.dev_root.exists() or not args.prod_root.exists():
        print(f"root missing: dev={args.dev_root} prod={args.prod_root}")
        return 2
    c = classify(args.dev_root, args.prod_root)
    if args.apply:
        return apply_copies(c, args.prod_root, args.log, args.include_new)
    print_report(c, args.dev_root, args.prod_root)
    return 1 if (c["stale"] or c["prod_newer"] or c["dev_only"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
