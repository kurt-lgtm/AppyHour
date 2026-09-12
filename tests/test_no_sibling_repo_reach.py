r"""No live module may REACH INTO THE SIBLING REPO (ShipRouting) by path or import (reorg end-state
criterion 1, plan R-34). AST scan, fail by file:line — the twin of
ShipRouting/server/tests/test_no_sibling_repo_reach.py (same scanner, sibling names swapped; keep
the two in step) and the same enforcement shape as test_no_filesystem_work_at_import.py.

Why this is a test and not a code-review note — Kurt 2026-09-12: "the whole point of this big reorg
is that everything sits cleanly in its own repo … make sure that's what the end case is." Twelve
live AppyHour modules reach into ShipRouting today — almost all for ONE thing, the shared canon
(`lib.canon` / `lib.hubs` / `lib.qc_gate` / `lib.histdb`) — by assuming the other repo sits beside
this one on Kurt's disk. That is false in every worktree, in the console image (which bundles
AppyHour INSIDE ShipRouting at /app/AppyHour, not beside it) and after the split; and unlike
ShipRouting→AppyHour (a pinned dependency, R-11/R-12), NOTHING pins ShipRouting for this repo, so a
reach here is an unversioned dependency on whatever happens to be checked out. A reach is any of
these, in code that runs (docstrings and comments are not code):

  * `sys.path.insert/append(...)` whose argument mentions the sibling name;
  * `<something> / "ShipRouting"` or `os.path.join(<something>, "ShipRouting")` — the literal-free
    spelling of the same reach;
  * a string literal naming the sibling ROOT — `Claude Projects\ShipRouting`, `/app/ShipRouting`;
  * `import` / `from ... import` of ShipRouting's top-level packages (`lib`, `server`, `milp`) —
    one row per file (the fix is per file: R-35 extracts the canon into a package or a DO-table
    read, consumed as a DEPENDENCY, and the whole file stops reaching at once).

There is NO sanctioned seam in this direction and NO allowlist: a reach is never "allowed", only
"not fixed yet". The known offenders are STRICT xfails below (KNOWN_REACHES), one per site, each
tagged with the R-35 seat that removes it. Strict = the moment a seat lands its fix the xfail
XPASSes and FAILS, so the row must come off in the same commit.

🔴 `GelPackCalculator/` is GITIGNORED in this repo (.gitignore:46) — it exists only on the
workstation. Its four sites are scanned whenever the directory is present (the workstation) and
SKIPPED, not silently passed, wherever it is absent (CI, worktrees, the image).
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------------------- scope
SIBLING = "ShipRouting"
# ShipRouting's top-level packages. `scripts` is NOT listed: this repo has its own `scripts/`.
SIBLING_MODULES: frozenset = frozenset({"lib", "server", "milp"})
# Root *.py (non-recursive) + every live tree. GelPackCalculator is gitignored — scanned if present.
SCAN_TREES = ("appyhour_lib", "AppyHourMCP", "AppyHourShippingMCP", "InventoryReorder",
              "ShippingReports", "scripts", "order_checks", "cut_order_server",
              "matrix_commander_web", "ingest", "dlt_ingest", "pipeline", "agents", "scenario",
              "GelPackCalculator")
SKIP_TREES = {".git", ".claude", ".Codex", ".planning", ".serena", ".pipeline", ".codegraph",
              "__pycache__", "tests", "dist", "build", "node_modules", ".venv", "venv",
              "_archive", "_retired", "archive", "scratchpad", "_outputs", "research", "docs",
              # dated run-once CLI probes (core.md: one-shots)
              "Errors", "incident-fixes"}
# A join onto "ShipRouting" whose base mentions one of these is a data dir, not the repo.
DATA_DIR_MARKERS = ("APPDATA", "LOCALAPPDATA", "AppData", "FLOW_CACHE_DIR", "/tmp")

GELPACK_PRESENT = (ROOT / "GelPackCalculator").is_dir()
GELPACK_ABSENT = pytest.mark.skipif(
    not GELPACK_PRESENT,
    reason="GelPackCalculator/ is gitignored (.gitignore:46) — present only on the workstation")

# ------------------------------------------------------------------ known reaches (R-35 seats)
# file:line of every reach in this repo, measured by THIS scanner 2026-09-12 (R-34). The twelve
# files of the gate report (reorg-end-state-gate-2026-09-12) are all here; the report's line is the
# path reach, and a second row on the same file is the sibling IMPORT the path enables (both must
# go). Rows past the report's twelve are the SAME class in spellings its grep did not cover
# (`WS / "ShipRouting"`), found by the AST scan. Seats per plan R-35.
# Strict xfail: fixing a site FAILS this test until its row is removed.
KNOWN_REACHES = [
    # INVENTORY team / VF sheet
    ("matrix_commander.py", 1225, "R-35 INVENTORY/VF: sys.path literal -> lib.qc_gate (col-L QC at export)"),
    ("matrix_commander.py", 1229, "R-35 INVENTORY/VF: from lib.qc_gate import ... (enabled by :1225)"),
    ("scripts/vf_items.py", 194, "R-35 INVENTORY/VF: sys.path literal -> lib.qc_gate.is_reship_tag"),
    ("scripts/vf_items.py", 198, "R-35 INVENTORY/VF: from lib.qc_gate import is_reship_tag (enabled by :194)"),
    # DATA CLOUD
    ("appyhour_lib/cloud_reads.py", 76, "R-35 DATA CLOUD: _SHIPROUTING literal -> lib.histdb"),
    ("appyhour_lib/cloud_reads.py", 184, "R-35 DATA CLOUD: from lib import histdb (enabled by :76)"),
    ("AppyHourMCP/wednesday_ops_run.py", 38, "R-35 DATA CLOUD: SHIPROUTING literal (subprocess cwd + script paths)"),
    # TRACKING
    ("ShippingReports/build_pp_origin_hub.py", 59, "R-35 TRACKING: _SR_LIB literal -> import canon"),
    ("ShippingReports/carrier_mix_pivot.py", 77, "R-35 TRACKING: parents[2] / 'ShipRouting' -> lib.canon"),
    ("ShippingReports/carrier_mix_pivot.py", 83, "R-35 TRACKING: from lib import canon (enabled by :77)"),
    ("scripts/invoice_loaders/acct_backfill.py", 50, "R-35 TRACKING: sys.path literal -> server.durable_store"),
    ("scripts/invoice_loaders/acct_backfill.py", 51, "R-35 TRACKING: from server.durable_store import _conn (enabled by :50)"),
    ("scripts/invoice_loaders/acct_backfill_fast.py", 32, "R-35 TRACKING: sys.path literal -> server.durable_store"),
    ("scripts/invoice_loaders/acct_backfill_fast.py", 40, "R-35 TRACKING: from server.durable_store import _conn (enabled by :32)"),
    # ROUTING (GelPackCalculator incl. kori/) — gitignored tree, see GELPACK_ABSENT
    ("GelPackCalculator/kori/routing_v2.py", 19, "R-35 ROUTING: _SHIPROUTING literal -> lib.engine et al"),
    ("GelPackCalculator/kori/routing_v2.py", 53, "R-35 ROUTING: from lib.flags import ... (+ lib.engine/optimizer/... enabled by :19)"),
    ("GelPackCalculator/kori/gel_pack_webview.py", 1243, "R-35 ROUTING: from lib.postmortem / lib.optimizer (enabled by routing_v2's insert)"),
    ("GelPackCalculator/parcel_panel.py", 39, "R-35 ROUTING: SHIPROUTING_ROOT-or-literal -> server.pp_ratelimit"),
    ("GelPackCalculator/parcel_panel.py", 43, "R-35 ROUTING: from server.pp_ratelimit import ... (enabled by :39)"),
    ("GelPackCalculator/easypost_tracking.py", 27, "R-35 ROUTING: sys.path literal -> lib.origin"),
    ("GelPackCalculator/easypost_tracking.py", 29, "R-35 ROUTING: from lib.origin import ... (enabled by :27)"),
    ("GelPackCalculator/seventeentrack_tracking.py", 26, "R-35 ROUTING: sys.path literal -> lib.origin"),
    ("GelPackCalculator/seventeentrack_tracking.py", 28, "R-35 ROUTING: from lib.origin import ... (enabled by :26)"),
    # found by the AST scan, not in the gate report (same class)
    ("scripts/automation_health.py", 1367, "R-35 (unassigned — Forge to seat): WORKSPACE_ROOT / 'ShipRouting' rglob of the sibling's scripts"),
    ("scripts/failed_tags_corpus.py", 48, "R-35 ROUTING: WS / 'ShipRouting' -> lib.features/hubs/zip_loaders"),
    ("scripts/failed_tags_corpus.py", 120, "R-35 ROUTING: from lib.features import ... (enabled by :48)"),
    ("scripts/repair_cloud_fulfillments.py", 255, "R-35 DATA CLOUD: WORKSPACE / 'ShipRouting' (etl_history subprocess)"),
    ("scripts/restore_check.py", 58, "R-35 (unassigned — Forge to seat): asserts ShipRouting/ sits BESIDE AppyHour/ — the layout the reorg retires"),
]

# Absolute roots only: `C:\...\Claude Projects\ShipRouting`, `/app/ShipRouting`, `X:\...\ShipRouting\...`.
# A relative `ShipRouting/x.py` in a message string is prose (a citation), not a root.
ROOT_LITERAL = re.compile(
    r"Claude Projects(?:\\\\|\\|/)+" + SIBLING + r"(?![A-Za-z0-9_])"
    r"|^/(?:[^/]+/)*" + SIBLING + r"(?:/|$)"
    r"|^[A-Za-z]:(?:\\\\|\\|/).*?(?:\\\\|\\|/)" + SIBLING + r"(?:\\\\|\\|/|$)")


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _constants_in(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n


def _mentions_data_dir(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and any(m in n.value for m in DATA_DIR_MARKERS):
            return True
        if isinstance(n, ast.Name) and "appdata" in n.id.lower():
            return True
    return False


def _join_onto_sibling(node):
    """`X / "ShipRouting"` or `os.path.join(X, "ShipRouting", ...)` with X not a data dir — the
    literal-free reach (the sibling assumed beside this checkout)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        operands = (node.left, node.right)
    elif isinstance(node, ast.Call) and (_dotted(node.func) or "").split(".")[-1] == "join":
        operands = tuple(node.args)
    else:
        return False
    if not any(isinstance(o, ast.Constant) and o.value == SIBLING for o in operands):
        return False
    return not any(_mentions_data_dir(o) for o in operands)


def find_reaches(source, modules=SIBLING_MODULES):
    """[(line, what)] for every reach into the sibling repo in code that runs."""
    tree = ast.parse(source)
    found = []
    seen = set()
    imported = []                                           # one row per FILE: the fix is per file

    def hit(line, what):
        if line not in seen:
            seen.add(line)
            found.append((line, what))

    def visit(node):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            return                                          # a docstring is not code
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level:
                return                                      # relative = self
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            imported.extend((node.lineno, n) for n in names if n.split(".")[0] in modules)
            return
        if isinstance(node, ast.Call) and _dotted(node.func) in ("sys.path.insert", "sys.path.append"):
            for c in _constants_in(node):
                if SIBLING in c.value:
                    hit(node.lineno, f"{_dotted(node.func)}(... {c.value!r})")
                    break
        if _join_onto_sibling(node):
            hit(node.lineno, f"path join onto {SIBLING!r} (sibling assumed beside this checkout)")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and ROOT_LITERAL.search(node.value):
            hit(node.lineno, f"sibling root literal {node.value!r}")
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in tree.body:
        visit(stmt)
    if imported:
        line, name = imported[0]
        more = f" (+{len(imported) - 1} more)" if len(imported) > 1 else ""
        hit(line, f"import of sibling module `{name}`{more}")
    return sorted(found)


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
        return find_reaches(path.read_text(encoding="utf-8-sig", errors="replace"))
    except SyntaxError as e:
        return [(e.lineno or 0, f"SyntaxError: {e.msg}")]


KNOWN = {(rel, line) for rel, line, _ in KNOWN_REACHES}


def test_no_live_module_reaches_into_the_sibling_repo():
    bad = []
    for rel, p in _repo_py_files():
        bad.extend(f"{rel}:{line}: {what}" for line, what in _scan(rel, p) if (rel, line) not in KNOWN)
    assert not bad, (f"live code reaches into the sibling repo ({SIBLING}) — read the fact from the "
                     "DO database or consume the extracted canon as a dependency (R-35); never a "
                     "path-insert:\n  " + "\n  ".join(bad))


@pytest.mark.parametrize("rel, line", [
    pytest.param(rel, line, id=f"{rel}:{line}",
                 marks=[pytest.mark.xfail(strict=True, reason=reason)]
                 + ([GELPACK_ABSENT] if rel.startswith("GelPackCalculator/") else []))
    for rel, line, reason in KNOWN_REACHES
])
def test_known_reach_is_fixed(rel, line):
    p = ROOT / rel
    assert p.exists(), f"{rel} no longer exists — drop its KNOWN_REACHES row"
    hits = {ln for ln, _ in _scan(rel, p)}
    assert line not in hits, f"{rel}:{line} still reaches into {SIBLING}"


def test_known_reaches_name_each_site_once_with_a_seat():
    assert len(KNOWN) == len(KNOWN_REACHES), "duplicate KNOWN_REACHES row"
    for rel, line, reason in KNOWN_REACHES:
        assert reason.startswith("R-35 "), f"{rel}:{line} must name its R-35 seat"


# --------------------------------------------------------------------------------- self-tests
@pytest.mark.parametrize("snippet", [
    r'import sys; sys.path.insert(0, r"C:\Users\Work\Claude Projects\ShipRouting")',
    r'sys.path.append("C:\\Users\\Work\\Claude Projects\\ShipRouting\\lib")',
    'sys.path.insert(0, "/app/ShipRouting")',
    'SR = Path("C:/Users/Work/Claude Projects/ShipRouting/lib")',
    'SR = Path(__file__).resolve().parents[2] / "ShipRouting"',
    'sys.path.insert(0, str(_HERE.parents[2] / "ShipRouting"))',
    'SR = os.path.join(os.path.dirname(AH), "ShipRouting")',
    'SR = os.environ.get("SHIPROUTING_ROOT") or r"C:\\Users\\Work\\Claude Projects\\ShipRouting"',
    'SR = r"C:\\Users\\Work\\Claude Projects\\ShipRouting"  # even with a trailing comment',
    "from lib.canon import normalize_carrier",
    "from lib import canon",
    "import lib.engine as _eng",
    "from server.durable_store import _conn",
    "import milp.solve",
    "def f():\n    from lib.qc_gate import is_reship_tag",
    "try:\n    from lib.origin import CITY_HUB\nexcept Exception:\n    CITY_HUB = None",
])
def test_scanner_catches_each_reach_shape(snippet):
    assert find_reaches(snippet), snippet


def test_imports_collapse_to_one_row_per_file():
    src = "from lib.flags import ensure_flag_defaults\nfrom lib.engine import compute_routing\nimport milp.solve\n"
    assert find_reaches(src) == [(1, "import of sibling module `lib.flags` (+2 more)")]


@pytest.mark.parametrize("snippet", [
    "from appyhour_lib.db import connect_ro",
    "from scripts.utilities import x",                                      # this repo's scripts/
    "import server  # AppyHourMCP/server.py is THIS repo's -- but see SIBLING_MODULES: server IS listed",
    '"""docstring citing ShipRouting/INVOICE_INGEST_RULES.md §1"""',
    "# sys.path.insert(0, r'C:\\Users\\Work\\Claude Projects\\ShipRouting') in a comment",
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))',       # self-root
    'ROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT))',
    'from .lib import x',                                                   # relative = self
    'NAME = "ShipRouting"',                                                 # a word, not a path
    'NOTE = "mirrors ShipRouting/server/sync_invoices.py:219"',             # a citation, not a root
    'p = os.path.join(os.environ.get("APPDATA", ""), "ShipRouting", "x")',  # a data dir
    'p = os.path.join(os.environ.get("FLOW_CACHE_DIR") or "/tmp", "ShipRouting")',
])
def test_scanner_ignores_self_prose_and_the_data_dir(snippet):
    if snippet.startswith("import server"):
        pytest.skip("`server` is a listed sibling package; AppyHourMCP imports its own server.py "
                    "nowhere in live code (verified 2026-09-12) — a new one would surface by name")
    assert find_reaches(snippet) == [], find_reaches(snippet)


def test_scope_covers_the_live_trees_and_skips_one_shots():
    rels = {rel for rel, _ in _repo_py_files()}
    for must in ("matrix_commander.py", "box_simulation.py", "appyhour_lib/paths.py",
                 "AppyHourMCP/server.py", "ShippingReports/carrier_mix_pivot.py",
                 "scripts/vf_items.py", "scripts/invoice_loaders/acct_backfill.py"):
        assert must in rels, must
    assert not any(r.startswith(("InventoryReorder/Errors/", "scripts/archive/", "scripts/incident-fixes/",
                                 "tests/", "_archive/", "scratchpad/")) for r in rels)
    if GELPACK_PRESENT:
        assert "GelPackCalculator/kori/routing_v2.py" in rels
