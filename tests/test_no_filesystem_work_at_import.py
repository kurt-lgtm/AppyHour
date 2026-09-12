r"""No module may do filesystem, network or database work when it is IMPORTED (separation spec §0.3,
plan R-13). AST scan, fail by name — the twin of ShipRouting/server/tests/test_no_filesystem_work_at_import.py
(same scanner; keep the two in step).

Why this is a test and not a code-review note — the failure lands in the OTHER repo, silently, late:

  2026-09-03, commit 1acc81f5: `matrix_commander.py:351` started calling
  `inventory_settings_path(for_write=True)` at MODULE IMPORT, and that helper
  (`appyhour_lib/paths.py:287`) mkdirs `C:\AppyHourData` with no OS guard. The ShipRouting console
  image bundles this repo and imports matrix_commander; on Linux `C:\AppyHourData` is a RELATIVE
  directory, created in the cwd, and ShipRouting's cache resolver selected it by existence: prewarm
  wrote one cache, the build read another. Four production runs shipped blind (`_SHIP_2026-09-07`,
  both `RMFG_20260908` runs, `ROUTE_TEST_909` at 0/5155 lanes, 128 air where the fix produces 13).
  Nothing in ShipRouting's history explained it (ShipRouting BUG_LOG 2026-09-08).

The rule: a module-level statement (outside `def` / `class` / lambda and outside the
`if __name__ == "__main__":` guard) may not CALL anything that creates, writes, moves or deletes on
the filesystem, opens a socket, or opens a database — directly, or through a helper KNOWN to do so
(KNOWN_IO_HELPERS). Reads are allowed; writes are not. Put the work in main() or a function the
caller invokes on purpose.

Scope = what the console image can import (PYTHONPATH /app/AppyHour + /app/AppyHour/AppyHourMCP):
root *.py, appyhour_lib/, AppyHourMCP/, InventoryReorder/ (the cut-order package), scripts/.
Deliberately OUT: dated run-once CLI probes (InventoryReorder/Errors/*, scripts/{archive,audits,
incident-fixes,swaps,utilities}) — never imported, and a `requests.get` at their top level is the
one-shot idiom, not an import hazard; tests/ and conftest.py (pytest's own idiom).

The two 09-03 offenders were STRICT xfails below until plan R-25 (2026-09-12) made
`matrix_commander.SETTINGS_PATH` lazy (`settings_path()` + PEP 562 `__getattr__`); they are now
plain passing tests so the regression cannot come back unnoticed.
"""
import ast
import importlib
import pathlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------------------- scope
SCAN_TREES = ("appyhour_lib", "AppyHourMCP", "InventoryReorder", "scripts")
SKIP_TREES = {".git", ".claude", ".Codex", "__pycache__", "tests", ".pytest_critic_gift", "dist",
              "build", "node_modules", ".venv", "venv", "_archive", "archive",
              # dated run-once CLI probes (core.md: one-shots), never imported by the image
              "Errors", "audits", "incident-fixes", "swaps", "utilities"}

# ------------------------------------------------------------------------------------- the ban
BANNED_MODULES = {"shutil", "subprocess", "requests", "urllib"}
BANNED_FUNCTIONS = {("os", "makedirs"), ("os", "mkdir"), ("os", "remove"), ("os", "unlink"),
                    ("os", "rename"), ("os", "replace"), ("os", "rmdir"), ("os", "system"),
                    ("sqlite3", "connect"), ("pymysql", "connect")}
BANNED_METHODS = {"mkdir", "write_text", "write_bytes", "touch", "unlink", "rmdir"}
WRITE_MODES = set("wax+")
# Helpers that perform filesystem work when called (documented, by name). A module-level call to
# one of these is import-time I/O by proxy — the exact 09-03 shape.
KNOWN_IO_HELPERS = {
    "inventory_settings_path": "appyhour_lib.paths: for_write=True mkdirs DATA_ROOT (paths.py:287)",
    "gel_calc_settings_path": "appyhour_lib.paths: same helper family, same mkdir",
    "settings_path": "appyhour_lib.paths: same helper family, same mkdir",
}

# Each entry needs a reason and must still trip the scanner (hygiene test). The two 09-03 offenders
# are NOT here — they are fixed (R-25) and guarded by the two named tests below.
ALLOWED: dict[str, str] = {
    "InventoryReorder/fulfillment_web/_check_demand.py":
        "leading-underscore run-once probe (module-level requests.post at :9), never imported; "
        "fulfillment_web is the Fulfillment and Order Tool session's surface (spec: leave alone) — "
        "found by R-13 2026-09-11, reported, not fixed here; that session moves it into main() or "
        "out of the app tree, then drops this row",
}
# Files whose offence is a strict xfail below (excluded from the repo-wide assertion so the xfail is
# the ONE place that names them; drop the row when the xfail comes off). Empty since R-25.
XFAIL_PENDING: set[str] = set()


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _write_mode(call, mode_pos=1):
    mode = None
    if len(call.args) > mode_pos and isinstance(call.args[mode_pos], ast.Constant):
        mode = call.args[mode_pos].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and bool(set(mode) & WRITE_MODES)


def _import_table(tree):
    table = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                table[alias.asname or alias.name] = (node.module.split(".")[0], alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                table[alias.asname or alias.name.split(".")[0]] = (alias.name.split(".")[0], None)
    return table


def _offence(call, imports):
    func = call.func
    dotted = _dotted(func)
    if isinstance(func, ast.Name):
        if dotted == "open":
            return "open(mode=write)" if _write_mode(call) else None
        module, attr = imports.get(dotted, (None, None))
        if module in BANNED_MODULES and not (attr or dotted)[:1].isupper():
            return f"{module}.{attr or dotted}"
        if (module, attr) in BANNED_FUNCTIONS:
            return f"{module}.{attr}"
        if (attr or dotted) in KNOWN_IO_HELPERS:
            return f"{attr or dotted}() [{KNOWN_IO_HELPERS[attr or dotted]}]"
        return None
    if isinstance(func, ast.Attribute):
        if func.attr == "open" and _write_mode(call, mode_pos=0):
            return f"{dotted or '<expr>.open'}(mode=write)"
        if func.attr in BANNED_METHODS:
            return dotted or f"<expr>.{func.attr}"
        if func.attr in KNOWN_IO_HELPERS:
            return f"{dotted or func.attr}() [{KNOWN_IO_HELPERS[func.attr]}]"
        if dotted:
            root = dotted.split(".")[0]
            module, _ = imports.get(root, (root, None))
            last = dotted.split(".")[-1]
            if module in BANNED_MODULES and not last[:1].isupper():
                # `requests.Session()` / `urllib.request.Request(url)` build an OBJECT and open
                # nothing; the socket opens when a method is called. Capitalised = constructor.
                return dotted
            if (module, last) in BANNED_FUNCTIONS:
                return dotted
    return None


def _is_main_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def import_time_io(source):
    tree = ast.parse(source)
    imports = _import_table(tree)
    found = []

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if _is_main_guard(node):
            return
        if isinstance(node, ast.Call):
            what = _offence(node, imports)
            if what:
                found.append((node.lineno, what))
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in tree.body:
        visit(stmt)
    return found


def _repo_py_files():
    roots = list(sorted(ROOT.glob("*.py")))
    for tree in SCAN_TREES:
        roots.extend(sorted((ROOT / tree).rglob("*.py")))
    for p in roots:
        rel = p.relative_to(ROOT)
        if set(rel.parts) & SKIP_TREES or p.name == "conftest.py":
            continue
        yield rel.as_posix(), p


def _scan(rel, path):
    try:
        return import_time_io(path.read_text(encoding="utf-8-sig", errors="replace"))
    except SyntaxError as e:
        return [(e.lineno or 0, f"SyntaxError: {e.msg}")]


def test_no_module_does_filesystem_network_or_db_work_at_import():
    bad = []
    for rel, p in _repo_py_files():
        if rel in ALLOWED or rel in XFAIL_PENDING:
            continue
        bad.extend(f"{rel}:{line}: {what}" for line, what in _scan(rel, p))
    assert not bad, ("filesystem/network/db work at IMPORT (separation §0.3 / R-13) — move it into "
                     "main() or a function the caller invokes:\n  " + "\n  ".join(bad))


def test_allowlist_names_only_files_that_still_trip_the_scanner():
    for rel, why in list(ALLOWED.items()) + [(r, "xfail pending") for r in XFAIL_PENDING]:
        assert why.strip(), rel
        p = ROOT / rel
        assert p.exists(), f"entry {rel} no longer exists — drop it"
        assert _scan(rel, p), f"entry {rel} no longer does import-time I/O — drop it"


# ------------------------------------------- the two 09-03 offenders (fixed R-25, kept as guards)
def test_matrix_commander_resolves_its_settings_path_lazily():
    hits = _scan("matrix_commander.py", ROOT / "matrix_commander.py")
    assert not hits, "matrix_commander.py does import-time I/O:\n  " + "\n  ".join(
        f"matrix_commander.py:{line}: {what}" for line, what in hits)


def test_importing_matrix_commander_creates_no_directory(monkeypatch):
    """Behavioural half: the 09-03 burn end to end. Import matrix_commander fresh with every mkdir
    recorded; the import must create nothing. (DATA_ROOT is a literal with no env override, so the
    real directory cannot be redirected — recording the call is the isolated way to observe it.)"""
    created = []                                  # record only — the real mkdir is never run here
    monkeypatch.setattr(pathlib.Path, "mkdir", lambda self, *a, **k: created.append(str(self)))
    monkeypatch.syspath_prepend(str(ROOT))
    for name in ("matrix_commander", "appyhour_lib.paths"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.import_module("matrix_commander")
    assert not created, f"importing matrix_commander created {created}"


# --------------------------------------------------------------------------------- self-tests
@pytest.mark.parametrize("snippet, what", [
    ("import os\nos.makedirs('x', exist_ok=True)", "os.makedirs"),
    ("from os import makedirs\nmakedirs('x')", "os.makedirs"),
    ("from pathlib import Path\nPath('x').mkdir(parents=True)", "<expr>.mkdir"),
    ("OUT.parent.mkdir(parents=True, exist_ok=True)", "OUT.parent.mkdir"),
    ("p.touch()", "p.touch"),
    ("f = open('x', 'w')", "open(mode=write)"),
    ("with open('x', mode='a') as f:\n    f.write('y')", "open(mode=write)"),
    ("Path('x').open('wb')", "<expr>.open(mode=write)"),
    ("import shutil\nshutil.rmtree('x')", "shutil.rmtree"),
    ("import subprocess\nsubprocess.run(['ls'])", "subprocess.run"),
    ("import requests\nR = requests.get('https://x')", "requests.get"),
    ("import urllib.request\nurllib.request.urlopen('https://x')", "urllib.request.urlopen"),
    ("import sqlite3\nCONN = sqlite3.connect('x.db')", "sqlite3.connect"),
    ("import pymysql\nCONN = pymysql.connect(host='h')", "pymysql.connect"),
    ("try:\n    from appyhour_lib.paths import inventory_settings_path as _p\n    S = _p(for_write=True)\n"
     "except ImportError:\n    S = None",
     "inventory_settings_path() [appyhour_lib.paths: for_write=True mkdirs DATA_ROOT (paths.py:287)]"),
])
def test_scanner_catches_each_banned_shape(snippet, what):
    hits = import_time_io(snippet)
    assert hits and hits[0][1] == what, hits


@pytest.mark.parametrize("snippet", [
    "import os\ndef main():\n    os.makedirs('x')",
    "class C:\n    def run(self):\n        Path('x').mkdir()",
    "import os\nif __name__ == '__main__':\n    os.makedirs('x')",
    "f = open('x')",
    "with open('x', 'r', encoding='utf-8') as f:\n    DATA = f.read()",
    "import os\n# os.makedirs('x') in a comment\n'''os.makedirs in a docstring'''",
    "TAG = TAG.replace('_', '-')",
    "def load():\n    return inventory_settings_path(for_write=True)",
    "import requests\nSESSION = requests.Session()",        # an object, not a request
    "from requests import Session\nSESSION = Session()",
    "import urllib.request\nREQ = urllib.request.Request('https://x')",
])
def test_scanner_ignores_calls_that_run_only_on_purpose(snippet):
    assert import_time_io(snippet) == []


def test_scope_covers_the_image_visible_modules_and_skips_one_shots():
    rels = {rel for rel, _ in _repo_py_files()}
    for must in ("matrix_commander.py", "box_simulation.py", "appyhour_lib/paths.py",
                 "AppyHourMCP/server.py"):
        assert must in rels, must
    assert not any(r.startswith(("InventoryReorder/Errors/", "scripts/archive/", "tests/")) for r in rels)
