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

🔴 CONSTRAINTS SSOT: `scripts/DEPLOY_PROD_RULES.md` — read it before changing this file or
loosening any refusal in it. Rules land there FIRST, in the same commit as the code.

Guardrails (NEGATIVES first):
- 🔴 REFUSES --apply (exit 3, zero copies) when the deploy would land a file prod cannot IMPORT —
  a consumer whose provider module or symbol is missing/stale in the destination tree. A byte-
  verified copy is not a runnable deployment: on 2026-09-02 `carrier_mix_pivot.py` landed
  byte-perfect against a prod `appyhour_lib/credentials.py` from 06-23 with no
  `get_google_credentials`, and died on ImportError every run (ENGINEERING_GOTCHAS D1, the #1
  unguarded class). The check is AST-only in an isolated, production-shaped subprocess — it never
  imports a target module (half this tree writes live data at import) and never trusts bytes or
  the dev-mapped editable install. No force flag. See DEPLOY_PROD_RULES.md N1-N8.
- 🔴 NEVER a blind robocopy. Tracked set = exactly what automation_health.check_prod_parity
  monitors: dev `*.py` outside PARITY_SKIP_DIRS whose prod counterpart exists. Dev-only
  files are LISTED but copied only with --include-new (a fix split across a new module is
  half-deployed without it — the 07-27 guard-without-resolver burn).
- 🔴 NEVER copies *.db, .env*, __pycache__, logs, .git — enforced by a hard guard that
  raises, not skips, if such a path ever enters the copy set.
- 🔴 REFUSES --apply entirely (exit 2, zero copies) while ANY tracked file is newer in
  prod with differing bytes — that is a prod-side hand-edit; surface it, never clobber
  (HEARTBEAT_RULES rule 9 NEGATIVE). No force flag, by design. Reconcile dev first.
- 🔴 NEVER ship an unrelated session's work as a side effect of an urgent fix. The dev tree
  is shared by parallel sessions, so "everything that is stale" is NOT the same set as "the
  fix I verified". 2026-08-31: the daily_shipping_sync lock fix needed to reach prod the same
  day and the stale list also carried two InventoryReorder cut-order files from another
  session, unreviewed and untested by this one. `--only <glob>` (repeatable) restricts BOTH
  the report and the copy set; out-of-scope files are listed as SKIPPED so scoping is never
  silent. Unscoped runs are unchanged — deploy everything remains the default.
- Every overwritten prod file is copied to `<name>.bak-YYYYMMDD` first (`-HHMMSS` appended
  rather than overwriting a same-day backup), and the path is recorded in the JSONL row.
  Without it, a bad deploy has no rollback that does not depend on dev still being intact.
- Copies are read back and byte-compared after write (audit the artifact that landed).

Run:  python scripts/deploy_prod.py            # dry-run (exit 1 if drift, 0 if clean)
      python scripts/deploy_prod.py --apply    # Kurt's call — live tree for schtasks
      python scripts/deploy_prod.py --only "scripts/automation_health.py" --apply   # scoped
  🔴 `--only "GelPackCalculator/*"` is REFUSED (exit 2) since 2026-09-12: GelPackCalculator moved out of
  this tree (Claude Projects/GelPackCalculator, plan R-35 phase 1). Prod still runs the nested copy
  under C:/AppyHourProd/AppyHour/GelPackCalculator until Phase 2 (Kurt-gated) — see
  DEPLOY_PROD_RULES.md "GelPackCalculator".
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PREFLIGHT_CHILD_FLAG = "--preflight-child"

# 🔴 In child mode this process MODELS PROD and must not import automation_health: that module
# does `sys.path.insert(0, <dev repo root>)` at import time and pulls `appyhour_lib` through the
# pip EDITABLE INSTALL mapped to the dev tree (gotcha D2). Importing it here would put the dev
# tree on the very sys.path this check exists to prove is production-shaped. The child is stdlib
# only, by construction.
if PREFLIGHT_CHILD_FLAG in sys.argv:
    ah = None
else:
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
    # 🔴 `*_RULES.md` rides along with the code (2026-09-07). A guard whose constraints doc
    # cannot reach prod is the split the constraints-first gate exists to stop: prod ran
    # write_preflight.py while WRITE_PREFLIGHT_RULES.md stayed dev-side, so the deployed
    # refusal had no deployed statement of what it refuses or why it is not overridable.
    # Deliberately NOT all `*.md`: 147 dev docs, 16 already drifted and 31 dev-only, would
    # bury real code drift under README noise. Constraints docs are the class that governs
    # runtime behaviour — the rest are prose.
    for dev_file in sorted([*dev_root.rglob("*.py"), *dev_root.rglob("*_RULES.md")]):
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


def _matches(rel: Path, patterns: tuple[str, ...]) -> bool:
    """True when `rel` matches any --only glob. Both slash styles accepted, case-insensitive
    (Windows paths), and a bare directory pattern implies everything under it."""
    if not patterns:
        return True
    posix = rel.as_posix().lower()
    for raw in patterns:
        pat = raw.replace("\\", "/").lower()
        if fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(posix, pat.rstrip("/") + "/*"):
            return True
    return False


def scope(c: dict[str, list[dict]], patterns: tuple[str, ...]) -> tuple[dict, dict]:
    """Split the classification into (selected, skipped) by --only. Never silent: the caller
    prints the skipped set, so scoping a deploy can't hide drift it chose not to ship."""
    sel: dict[str, list[dict]] = {}
    skip: dict[str, list[dict]] = {}
    for bucket, rows in c.items():
        sel[bucket] = [r for r in rows if _matches(r["rel"], patterns)]
        skip[bucket] = [r for r in rows if not _matches(r["rel"], patterns)]
    return sel, skip


def print_report(c: dict[str, list[dict]], dev_root: Path, prod_root: Path,
                 skipped: dict[str, list[dict]] | None = None) -> None:
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
    if skipped:
        n = sum(len(v) for v in skipped.values())
        print(f"SKIPPED by --only — real drift this run deliberately does NOT ship: {n}")
        for bucket in ("stale", "prod_newer", "dev_only"):
            for r in skipped.get(bucket, []):
                print(f"  [{bucket}] {r['rel']}  dev {_mt(r['dev'])}")


# --- IMPORT PREFLIGHT (2026-09-07) ------------------------------------------------------
# 🔴 WHY. ENGINEERING_GOTCHAS D1, the #1 unguarded class: "the consumer shipped without its
# provider". 2026-09-02, THREE instances in one day — `ShippingReports/carrier_mix_pivot.py`
# reached prod while prod's `appyhour_lib/credentials.py` was still the 2026-06-23 copy with no
# `get_google_credentials`, so EVERY run died on ImportError; `lib.canon` missing from a prod
# ShipRouting tree; a `GoogleIntegration` constructor whose prod signature still took a path.
# Nine more files were held back by hand from the same fate. An independent instance on
# 2026-07-27 makes it two dated shapes, not one accident.
#
# 🔴 WHAT THE EXISTING VERIFICATION CANNOT SEE. `apply_copies` reads each copy back and compares
# BYTES. That proves the artifact landed; it cannot prove the tree it landed in can run it. Both
# `--include-new` (new providers are opt-in) and `--only` (narrows the set) produce exactly the
# half-deploy above, and every one of those deploys byte-verified perfectly.
#
# NEGATIVES that shaped this check — each one is a design that would have been worse:
# - 🔴 **NEVER import a target module to observe its imports.** Half this tree does real work at
#   module scope: Slack posts, Google Sheet writes, Shopify order edits, DB opens. A deploy gate
#   that fires a live write is worse than no gate. Resolution here is AST + filesystem ONLY. If a
#   fact cannot be established without executing something, it is reported UNCHECKED, never
#   assumed fine (dynamic `importlib.import_module`, `from x import *`, a provider that defines
#   names through `globals()` or a module `__getattr__`).
# - 🔴 **NEVER trust bytes, hashes or an in-process import.** The check resolves each imported
#   module and symbol against the DESTINATION tree's own files. `appyhour_lib` is a pip editable
#   install mapped to the DEV tree (D2), so an in-process `import appyhour_lib.credentials` would
#   answer from dev and report a false PASS on a prod tree that is missing it. The check therefore
#   runs in an ISOLATED subprocess (`-E -s -B`) whose sys.path is rewritten to prod shape —
#   `sys.path[0]` = the prod root, every dev/workspace entry dropped — and asserts, in its own
#   result, that `appyhour_lib` was never imported. Path-anchored resolution + no import is what
#   makes the editable install irrelevant.
# - 🔴 **NEVER refuse on breakage this deploy did not introduce.** The check grades the tree BEFORE
#   and AFTER the copy and refuses only on violations that are NEW. A gate that fires on another
#   session's pre-existing mess becomes noise, and noise gets bypassed (gotcha A1) — but the
#   pre-existing set is PRINTED, never dropped, or the day it becomes reachable is invisible.
# - 🔴 **NEVER add a force flag.** Same doctrine as the prod-newer refusal above: the fix is to
#   widen `--only` / add `--include-new` so the provider ships in the SAME deploy, not to override.
# - Third-party and stdlib imports are OUT OF SCOPE on purpose: dev and prod share one interpreter
#   and one site-packages, so `gspread` is not a two-trees problem. A module is in scope only if it
#   resolves inside the DEV tree — i.e. it is first-party code that must be deployed.
#
# Resolution semantics are deliberately the SAME as rule 19's
# (`automation_health._resolve_module` / `_module_imports`): dir-anchored, never a bare-basename
# match. They live here as a second copy because the child must not import automation_health (see
# the module header) — `tests/test_deploy_prod_import_preflight.py::ResolverParityWithRule19`
# pins the two together so they cannot drift.
PREFLIGHT_TIMEOUT_S = 300
_OPTIONAL_EXC = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


class _PlainTree:
    """The destination tree exactly as it is on disk right now (the BEFORE state)."""

    def is_file(self, p: Path) -> bool:
        return p.is_file()

    def source(self, p: Path) -> bytes | None:
        try:
            return p.read_bytes()
        except OSError:
            return None


class _OverlayTree(_PlainTree):
    """The destination tree AS IT WILL BE once this deploy's copy set has landed. A path in the
    overlay exists even if prod has no such file yet, and its CONTENT is the dev bytes that are
    about to be written — grading the post-state, not the pre-state."""

    def __init__(self, overlay: dict[str, Path]):
        self.overlay = overlay

    def is_file(self, p: Path) -> bool:
        return os.path.normcase(str(p)) in self.overlay or p.is_file()

    def source(self, p: Path) -> bytes | None:
        src = self.overlay.get(os.path.normcase(str(p)))
        return super().source(src if src is not None else p)


def _pf_resolve_module(mod: str, search_dirs: list[Path], tree: _PlainTree) -> Path | None:
    """`a.b.c` -> the .py that WOULD be imported, under the first search dir that has it.
    Directory-anchored on purpose: a bare-basename match would resolve `utils` to any of the eight
    `utils.py` in the tree and invent a provider that is not there (rule 19's reasoning)."""
    parts = mod.split(".")
    for d in search_dirs:
        leaf = d.joinpath(*parts)
        if tree.is_file(leaf.with_suffix(".py")):
            return leaf.with_suffix(".py")
        if tree.is_file(leaf / "__init__.py"):
            return leaf / "__init__.py"
    return None


def _pf_is_type_checking(test) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
        (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _pf_imports(tree: ast.AST) -> list[dict]:
    """Every import a module would EXECUTE, with the two flags that decide how hard to grade it:
    `optional` (lexically inside a try/except ImportError — a deliberate soft dependency) and the
    `if TYPE_CHECKING:` block, which never executes and is dropped entirely.

    Function bodies are included: a lazily-imported module is still executed by the consumer, and
    that is exactly where the 09-02 credentials import lived."""
    out: list[dict] = []

    def walk(node, optional: bool) -> None:
        for st in ast.iter_child_nodes(node):
            if isinstance(st, ast.If) and _pf_is_type_checking(st.test):
                walk_body(st.orelse, optional)
                continue
            if isinstance(st, ast.Try):
                soft = optional or any(
                    h.type is None or _exc_names(h.type) & _OPTIONAL_EXC for h in st.handlers)
                walk_body(st.body, soft)
                for h in st.handlers:
                    walk_body(h.body, optional)
                walk_body(st.orelse + st.finalbody, optional)
                continue
            if isinstance(st, ast.Import):
                for a in st.names:
                    out.append({"module": a.name, "level": 0, "names": [], "star": False,
                                "lineno": st.lineno, "optional": optional})
            elif isinstance(st, ast.ImportFrom):
                out.append({"module": st.module or "", "level": st.level,
                            "names": [a.name for a in st.names if a.name != "*"],
                            "star": any(a.name == "*" for a in st.names),
                            "lineno": st.lineno, "optional": optional})
            walk(st, optional)

    def walk_body(body, optional: bool) -> None:
        for st in body:
            walk(ast.Module(body=[st], type_ignores=[]), optional)

    def _exc_names(node) -> set[str]:
        elts = node.elts if isinstance(node, ast.Tuple) else [node]
        return {e.id if isinstance(e, ast.Name) else getattr(e, "attr", "") for e in elts}

    walk(tree, False)
    return out


def _pf_provided(path: Path, tree: _PlainTree) -> set[str] | None:
    """Top-level names a provider module would expose. None = OPAQUE (a star-import, a module
    `__getattr__`, a `globals()` write, or unparseable source) — symbols against an opaque provider
    are reported UNCHECKED rather than refused, because a false refusal at a deploy gate costs more
    than a missed check that is printed."""
    src = tree.source(path)
    if src is None:
        return None
    try:
        mod = ast.parse(src.decode("utf-8", errors="replace"), str(path))
    except (SyntaxError, ValueError):
        return None
    names: set[str] = set()
    opaque = False
    for node in ast.walk(mod):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("globals", "vars", "setattr"):
            opaque = True
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if node.name == "__getattr__":
                opaque = True
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    opaque = True
                else:
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            with contextlib_suppress():
                names.update(x for x in ast.literal_eval(node.value) if isinstance(x, str))
    return None if opaque else names


class contextlib_suppress:
    """Local stand-in so the child stays on a 6-import stdlib surface."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def _pf_check(consumer_rel: str, src: bytes, consumer_dir: Path, dest_root: Path,
              dev_root: Path, tree: _PlainTree) -> tuple[list[dict], list[dict]]:
    """Grade one consumer's imports against `tree` (a destination-tree view). Returns
    (violations, unchecked). NEVER imports anything."""
    viol: list[dict] = []
    unchecked: list[dict] = []
    try:
        parsed = ast.parse(src.decode("utf-8", errors="replace"), consumer_rel)
    except (SyntaxError, ValueError) as e:
        return ([{"kind": "UNPARSEABLE", "consumer": consumer_rel, "provider": "", "symbol": "",
                  "lineno": 0,
                  "message": f"UNPARSEABLE: '{consumer_rel}' does not parse as Python ({e}) — "
                             "it would die on import in prod"}], [])
    dev_dirs = [dev_root / Path(consumer_rel).parent, dev_root]
    dest_dirs = [consumer_dir, dest_root]
    plain_dev = _PlainTree()
    for rec in _pf_imports(parsed):
        where = f"{consumer_rel}:{rec['lineno']}"
        if rec["level"]:
            base = consumer_dir
            for _ in range(rec["level"] - 1):
                base = base.parent
            dev_base = (dev_root / Path(consumer_rel).parent)
            for _ in range(rec["level"] - 1):
                dev_base = dev_base.parent
            dirs, ddirs = [base], [dev_base]
        else:
            dirs, ddirs = dest_dirs, dev_dirs
        mod = rec["module"]
        targets = [mod] if mod else []
        # `from . import x` / `from .pkg import x`: each name may itself be the submodule.
        for name in (rec["names"] if not mod else []):
            targets.append(name)
        for t in targets or ([""] if rec["level"] else []):
            if not t:
                continue
            in_dev = _pf_resolve_module(t, ddirs, plain_dev)
            if in_dev is None:
                continue  # stdlib or third-party: shared interpreter, not a two-trees problem
            hit = _pf_resolve_module(t, dirs, tree)
            if hit is None:
                viol.append({
                    "kind": "MISSING_PROVIDER", "consumer": consumer_rel, "provider": t,
                    "symbol": "", "lineno": rec["lineno"],
                    "message": (f"MISSING PROVIDER: consumer '{consumer_rel}' imports "
                                f"'{t}' (line {rec['lineno']}) and that module does NOT exist in "
                                f"the destination tree after this deploy — the provider "
                                f"'{in_dev.name}' is being left behind. Ship it in the SAME "
                                f"deploy (widen --only, add --include-new).")})
                continue
            if rec["star"]:
                unchecked.append({"consumer": consumer_rel, "message":
                                  f"UNCHECKED: {where} `from {t} import *` — symbols not verified"})
                continue
            if not mod:
                continue  # `from . import x` resolved as a submodule: existence was the check
            provided = _pf_provided(hit, tree)
            if provided is None:
                unchecked.append({"consumer": consumer_rel, "message":
                                  f"UNCHECKED: {where} provider '{t}' is opaque (star-import, "
                                  "__getattr__, globals() write, or unparseable) — symbols not "
                                  "verified"})
                continue
            for name in rec["names"]:
                if name in provided:
                    continue
                if _pf_resolve_module(f"{t}.{name}", dirs, tree) is not None:
                    continue  # a submodule of a package, not an attribute
                sub_dev = _pf_resolve_module(f"{t}.{name}", ddirs, plain_dev)
                if sub_dev is not None and not rec["optional"]:
                    # `from pkg import submodule` where the SUBMODULE is the thing left behind.
                    # Name the missing FILE, not the package that fails to expose it.
                    viol.append({
                        "kind": "MISSING_PROVIDER", "consumer": consumer_rel,
                        "provider": f"{t}.{name}", "symbol": "", "lineno": rec["lineno"],
                        "message": (f"MISSING PROVIDER: consumer '{consumer_rel}' imports the "
                                    f"submodule '{t}.{name}' (line {rec['lineno']}) and it does "
                                    f"NOT exist in the destination tree after this deploy — the "
                                    f"provider '{sub_dev.name}' is being left behind. Ship it in "
                                    f"the SAME deploy (widen --only, add --include-new).")})
                    continue
                if rec["optional"]:
                    unchecked.append({"consumer": consumer_rel, "message":
                                      f"UNCHECKED: {where} optional import of '{name}' from "
                                      f"'{t}' is missing in prod but guarded by except ImportError"})
                    continue
                viol.append({
                    "kind": "MISSING_SYMBOL", "consumer": consumer_rel, "provider": t,
                    "symbol": name, "lineno": rec["lineno"],
                    "message": (f"MISSING SYMBOL: consumer '{consumer_rel}' imports '{name}' from "
                                f"provider '{t}' (line {rec['lineno']}), but the destination "
                                f"tree's copy of that provider ({hit}) does not define it — that "
                                f"provider is STALE and must ship in the SAME deploy. This is the "
                                f"2026-09-02 credentials outage exactly.")})
        if rec["optional"] and any(v["consumer"] == consumer_rel for v in viol[-1:]):
            soft = viol.pop()
            unchecked.append({"consumer": consumer_rel, "message":
                              f"UNCHECKED: {where} optional import — {soft['message']}"})
    return viol, unchecked


def _pf_run(job: dict) -> dict:
    """The whole check, inside the sanitized child. Grades the destination tree BEFORE and AFTER
    the copy set lands and reports only the DIFFERENCE as blocking."""
    dev_root = Path(job["dev_root"])
    dest_root = Path(job["prod_root"])
    overlay = {os.path.normcase(str(dest_root / r["rel"])): Path(r["dev"]) for r in job["copy"]}
    after, before = _OverlayTree(overlay), _PlainTree()
    v_after: list[dict] = []
    v_before_keys: set[tuple] = set()
    unchecked: list[dict] = []
    checked = 0
    for r in job["copy"]:
        rel = r["rel"]
        if not rel.lower().endswith(".py"):
            continue  # *_RULES.md rides along with the code but has no import closure
        dest_file = dest_root / rel
        dev_bytes = Path(r["dev"]).read_bytes()
        v, u = _pf_check(rel, dev_bytes, dest_file.parent, dest_root, dev_root, after)
        v_after.extend(v)
        unchecked.extend(u)
        checked += 1
        if dest_file.is_file():
            pv, _ = _pf_check(rel, dest_file.read_bytes(), dest_file.parent, dest_root,
                              dev_root, before)
            v_before_keys.update((x["kind"], x["consumer"], x["provider"], x["symbol"])
                                 for x in pv)
    new, pre = [], []
    for v in v_after:
        key = (v["kind"], v["consumer"], v["provider"], v["symbol"])
        (pre if key in v_before_keys else new).append(v)
    return {"violations": new, "preexisting": pre, "unchecked": unchecked, "checked": checked}


def _pf_child_main(job_path: str) -> int:
    """Isolated child. Rewrites sys.path into PRODUCTION shape before grading anything, and ships
    the proof of that shape back with the result so the caller (and the tests) can verify it."""
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    prod_root = str(Path(job["prod_root"]).resolve())
    here = Path(__file__).resolve()
    workspace = Path(r"C:\Users\Work\Claude Projects")
    # 🔴 `dev_like` is what the PROOF is about (is any DEV tree still visible?) and must NOT be
    # derived from this file's own location: the deployed copy of this script lives INSIDE the
    # prod tree, so `parents[1]` there IS the prod root and the proof would libel it as a dev
    # entry. `strip` is the wider set actually removed from sys.path — it additionally drops the
    # script's own directory, which the interpreter inserts automatically and which is the dev
    # scripts dir whenever this runs from the dev tree.
    dev_like = [Path(job["dev_root"]).resolve(), workspace]
    strip = [*dev_like, here.parent]

    def under(entry: str, roots) -> bool:
        n = os.path.normcase(os.path.abspath(entry))
        return any(n == os.path.normcase(str(r)) or n.startswith(os.path.normcase(str(r)) + os.sep)
                   for r in roots)

    kept = [p for p in sys.path if p and not under(p, strip)]
    sys.path[:] = [prod_root] + [p for p in kept
                                 if os.path.normcase(p) != os.path.normcase(prod_root)]
    proof = {
        "pid": os.getpid(),
        "sys_path_0": sys.path[0],
        "sys_path": list(sys.path),
        "dev_entries": [p for p in sys.path if under(p, dev_like)],
        "workspace_entries": [p for p in sys.path if under(p, [workspace])],
        "appyhour_lib_imported": any(m == "appyhour_lib" or m.startswith("appyhour_lib.")
                                     for m in sys.modules),
    }
    out = _pf_run(job)
    out["path_proof"] = proof
    out["appyhour_lib_imported_after"] = any(m == "appyhour_lib" or m.startswith("appyhour_lib.")
                                             for m in sys.modules)
    print("<<<PREFLIGHT>>>" + json.dumps(out) + "<<<END>>>")
    return 0


def preflight_imports(todo: list[dict], dev_root: Path, prod_root: Path,
                      timeout: int = PREFLIGHT_TIMEOUT_S) -> dict:
    """Run the import preflight for `todo` (the exact copy set) in an isolated, production-shaped
    subprocess. FAILS CLOSED: a crashed, timed-out or unparseable child is a BLOCKING violation,
    never a pass — "checker broken" is not "tree healthy" (HEARTBEAT_RULES rule 1)."""
    job = {"dev_root": str(dev_root), "prod_root": str(prod_root),
           "copy": [{"rel": str(r["rel"]), "dev": str(r["dev"])} for r in todo]}
    fd, jf = tempfile.mkstemp(suffix=".json", prefix="deploy_preflight_")
    os.close(fd)
    try:
        Path(jf).write_text(json.dumps(job), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        try:
            proc = subprocess.run(  # noqa: S603 — argv is this interpreter + this file + a temp
                [sys.executable, "-E", "-s", "-B", str(Path(__file__).resolve()),
                 PREFLIGHT_CHILD_FLAG, jf],   # path we just wrote; no shell, no user input
                capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except subprocess.TimeoutExpired:
            return _pf_failed(f"import preflight TIMED OUT after {timeout}s")
        raw = proc.stdout or ""
        if "<<<PREFLIGHT>>>" not in raw or "<<<END>>>" not in raw:
            return _pf_failed(f"import preflight produced no result (rc={proc.returncode}); "
                              f"stderr: {(proc.stderr or '').strip()[:400]}")
        blob = raw.split("<<<PREFLIGHT>>>", 1)[1].split("<<<END>>>", 1)[0]
        try:
            res = json.loads(blob)
        except ValueError as e:
            return _pf_failed(f"import preflight result unreadable ({e})")
        res.setdefault("violations", [])
        res.setdefault("preexisting", [])
        res.setdefault("unchecked", [])
        res["error"] = None
        return res
    finally:
        with contextlib_suppress():
            os.unlink(jf)


def _pf_failed(msg: str) -> dict:
    return {"violations": [{"kind": "CHECKER_FAILED", "consumer": "", "provider": "",
                            "symbol": "", "lineno": 0,
                            "message": f"REFUSED — {msg}. The import closure of this deploy is "
                                       "UNKNOWN, and unknown is not green."}],
            "preexisting": [], "unchecked": [], "checked": 0, "path_proof": {}, "error": msg}


def print_preflight(res: dict) -> None:
    p = res.get("path_proof") or {}
    if p:
        print(f"IMPORT PREFLIGHT — checked {res.get('checked', 0)} file(s) in an isolated "
              f"process (pid {p.get('pid')}) on a production-shaped path: "
              f"sys.path[0]={p.get('sys_path_0')}, dev entries={len(p.get('dev_entries', []))}, "
              f"appyhour_lib imported={p.get('appyhour_lib_imported')}")
    for v in res["violations"]:
        print(f"  🔴 {v['message']}")
    for v in res["preexisting"]:
        print(f"  info: PRE-EXISTING (not introduced by this deploy, not blocking): {v['message']}")
    for u in res["unchecked"]:
        print(f"  info: {u['message']}")


def copy_set(c: dict[str, list[dict]], prod_root: Path, include_new: bool) -> list[dict]:
    """The exact rows `--apply` would copy. Shared with the preflight so the thing that is graded
    is the thing that ships — not a re-derived approximation of it."""
    todo = list(c["stale"])
    if include_new:
        todo += [{**r, "prod": prod_root / r["rel"], "new": True} for r in c["dev_only"]]
    return todo


def _dev_root_of(todo: list[dict]) -> Path | None:
    """Recover the dev root from a copy row (dev == dev_root / rel). Keeps apply_copies'
    signature stable for existing callers while the preflight still gets a real dev tree."""
    for r in todo:
        rel, dev = Path(r["rel"]), Path(r["dev"])
        n = len(rel.parts)
        return dev.parents[n - 1] if n else dev.parent
    return None


def _backup(prod_file: Path) -> Path:
    """Copy the prod file aside before it is overwritten. Never clobbers an earlier backup —
    a same-day one gets the time appended (never-overwrite-prior-output rule)."""
    dest = prod_file.with_name(prod_file.name + f".bak-{datetime.now():%Y%m%d}")
    if dest.exists():
        dest = prod_file.with_name(prod_file.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(prod_file, dest)
    if dest.read_bytes() != prod_file.read_bytes():
        raise OSError(f"backup verify FAILED for {prod_file} — refusing to overwrite it")
    return dest


def apply_copies(c: dict[str, list[dict]], prod_root: Path, log_path: Path,
                 include_new: bool, dev_root: Path | None = None) -> int:
    if c["prod_newer"]:
        print(f"REFUSED: {len(c['prod_newer'])} file(s) newer in prod (hand-edit?) — "
              "reconcile into dev first; this tool never clobbers prod-side edits:")
        for r in c["prod_newer"]:
            print(f"  {r['rel']}  prod {_mt(r['prod'])} > dev {_mt(r['dev'])}")
        return 2
    todo = copy_set(c, prod_root, include_new)
    if not todo:
        print("nothing to deploy — prod in sync with dev on the tracked set")
        return 0
    # 🔴 IMPORT PREFLIGHT BEFORE THE FIRST COPY (gotcha D1). A byte-verified copy is not a
    # runnable deployment: a consumer whose provider is missing or stale in prod lands perfectly
    # and dies on import, every run, silently until someone looks. Zero copies on refusal — same
    # all-or-nothing shape as the prod-newer refusal, and deliberately with no force flag.
    dev_root = dev_root or _dev_root_of(todo)
    if dev_root is None:
        print("REFUSED: cannot locate the dev root for the import preflight — unknown is not green")
        return 3
    res = preflight_imports(todo, dev_root, prod_root)
    print_preflight(res)
    if res["violations"]:
        print(f"REFUSED: {len(res['violations'])} import/dependency violation(s) — this deploy "
              "would land code prod cannot run. NOTHING was copied. Fix by shipping the provider "
              "in the SAME deploy (widen --only, add --include-new); there is no override.")
        return 3
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        for r in todo:
            _assert_deployable(r["rel"])
            dev_bytes = r["dev"].read_bytes()
            existed = r["prod"].exists()
            prod_mtime_before = _mt(r["prod"]) if existed else None
            bak = _backup(r["prod"]) if existed else None   # rollback that doesn't need dev
            r["prod"].parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(r["dev"], r["prod"])
            if r["prod"].read_bytes() != dev_bytes:  # audit the artifact that landed
                raise OSError(f"post-copy verify FAILED for {r['rel']} — bytes differ")
            log.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action": "copy", "rel": str(r["rel"]),
                "dev_mtime": _mt(r["dev"]), "prod_mtime_before": prod_mtime_before,
                "sha256_10": _sha(dev_bytes), "bytes": len(dev_bytes),
                "backup": str(bak) if bak else None,
                "new_file": bool(r.get("new")), "prod_root": str(prod_root),
            }) + "\n")
            print(f"deployed {r['rel']}" + (f"  (backup: {bak.name})" if bak else "  (new)"))
    print(f"deployed {len(todo)} file(s); logged to {log_path}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="copy dev->prod (default: dry-run)")
    ap.add_argument("--include-new", action="store_true",
                    help="with --apply, also copy dev-only files missing from prod")
    ap.add_argument("--only", action="append", default=[], metavar="GLOB",
                    help="restrict report AND copy set to paths matching this glob "
                         "(repeatable, e.g. --only 'scripts/*'). Skipped drift is "
                         "still listed — scoping is never silent.")
    ap.add_argument("--no-preflight", action="store_true",
                    help="dry-run only: skip the import preflight report. 🔴 Has NO effect on "
                         "--apply — the preflight is not overridable there, by design.")
    ap.add_argument("--dev-root", type=Path, default=ah.DEV_ROOT)
    ap.add_argument("--prod-root", type=Path, default=ah.PROD_ROOT)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)
    if not args.dev_root.exists() or not args.prod_root.exists():
        print(f"root missing: dev={args.dev_root} prod={args.prod_root}")
        return 2
    patterns = tuple(args.only)
    if any("gelpackcalculator" in p.lower() for p in patterns):
        print("REFUSED: GelPackCalculator is no longer inside the AppyHour dev tree (moved to "
              "Claude Projects/GelPackCalculator on 2026-09-12, plan R-35 phase 1), so this deploy "
              "cannot map it. Prod still runs the NESTED copy under "
              r"C:\AppyHourProd\AppyHour\GelPackCalculator (7 scheduled tasks) until Phase 2 "
              "(Kurt-gated: re-home prod + retarget the tasks). GelPackCalculator deploys from its own "
              "repo after Phase 2. See DEPLOY_PROD_RULES.md 'GelPackCalculator'.")
        return 2
    c = classify(args.dev_root, args.prod_root)
    c, skipped = scope(c, patterns)
    if patterns:
        print(f"SCOPED to {list(patterns)}")
    if args.apply:
        if skipped and sum(len(v) for v in skipped.values()):
            print(f"NOT shipping {sum(len(v) for v in skipped.values())} out-of-scope file(s):")
            for bucket in ("stale", "prod_newer", "dev_only"):
                for r in skipped.get(bucket, []):
                    print(f"  [{bucket}] {r['rel']}")
        return apply_copies(c, args.prod_root, args.log, args.include_new, args.dev_root)
    print_report(c, args.dev_root, args.prod_root, skipped if patterns else None)
    todo = copy_set(c, args.prod_root, args.include_new)
    if todo and not args.no_preflight:
        # The dry run grades the SAME copy set --apply would ship, so a half-deploy is visible
        # before anyone types --apply. It reads only; it copies nothing either way.
        print_preflight(preflight_imports(todo, args.dev_root, args.prod_root))
    return 1 if (c["stale"] or c["prod_newer"] or c["dev_only"]) else 0


if __name__ == "__main__":
    if PREFLIGHT_CHILD_FLAG in sys.argv:
        sys.exit(_pf_child_main(sys.argv[sys.argv.index(PREFLIGHT_CHILD_FLAG) + 1]))
    sys.exit(main(sys.argv[1:]))
