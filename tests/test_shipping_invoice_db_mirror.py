"""Verbatim-equality tests for the mirrors GelPackCalculator/shipping_invoice_db.py DECLARES.

A `KEEP IN SYNC` comment is not a mechanism (2026-09-11 dedupe audit D1: the fork it warns about
had already re-diverged in 10 functions). This file is the mechanism, in the shape of
tests/test_pp_origin.py::test_authority_zips_match_shiprouting_hub_roster — import BOTH sides and
assert equality, so drift fails a test instead of landing silently in shipping.db.

Two mirrors are pinned:
  1. The D1 fork — ShipRouting/server/shipping_invoice_db.py is a vendored copy of this module
     (`imap_invoices.py:122`). Every function both files define is compared by AST (body + args,
     docstring excluded — the AppyHour docstrings carry the KEEP-IN-SYNC notes themselves).
  2. `CARRIER_HUBS` at shipping_invoice_db.py:1203 — "Keep in sync with ShipRouting/lib/features.py".

🔴 The functions that ALREADY differ are marked xfail(strict=True) with the D1 reason. That is the
finding being recorded, not a pass: reconciling them changes what an invoice parse writes and is
item 2 of the audit (NEEDS KURT; INVOICE_INGEST_RULES.md is the SSOT). When item 2 lands, the xfail
rows go strict-XPASS and this file must be trimmed in the SAME commit.

Nothing here imports ShipRouting at runtime: the vendored copy and lib/features.py are READ as
source and parsed, so this test never opens shipping.db or reads Kori settings.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from appyhour_lib.paths import gelpack_root

_GELPACK = gelpack_root()
if str(_GELPACK) not in sys.path:
    sys.path.insert(0, str(_GELPACK))

import shipping_invoice_db as local_sidb  # noqa: E402

# SHIPROUTING_ROOT env first (the parcel_panel.py:39 precedent), hardcoded fallback like test_pp_origin.
_SR_ROOT = Path(os.environ.get("SHIPROUTING_ROOT") or r"C:/Users/Work/Claude Projects/ShipRouting")
_VENDORED = _SR_ROOT / "server" / "shipping_invoice_db.py"
_FEATURES = _SR_ROOT / "lib" / "features.py"
_LOCAL = Path(local_sidb.__file__)

pytestmark = pytest.mark.skipif(not _VENDORED.exists(),
                                reason=f"ShipRouting checkout not present at {_SR_ROOT}")

# D1 — measured 2026-09-11 (AST body+args, docstring excluded). identify_hub differs ONLY in its
# docstring (the KEEP-IN-SYNC note) and so is NOT listed: its logic is in sync.
D1_DIVERGED = {
    "_db_path", "_feedback_value", "init_db", "parse_fedex_csv_bytes", "parse_fedex_xlsx_bytes",
    "scan_gmail_invoices", "scan_local_invoices", "store_shipments", "sync_gmail",
}
D1_REASON = "D1 fork diverged — item 2 NEEDS KURT"


def _top_level_functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _signature_and_body(fn: ast.AST) -> str:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]                                   # drop the docstring
    return ast.dump(fn.args) + "\n" + ast.dump(ast.Module(body=body, type_ignores=[]))


def _module_level_literal(path: Path, name: str):
    """The value of a module-scope `NAME = <literal>` assignment, without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found as a module-level literal in {path}")


def _shared_function_names() -> list[str]:
    if not _VENDORED.exists():
        return []
    return sorted(set(_top_level_functions(_LOCAL)) & set(_top_level_functions(_VENDORED)))


def _param(name: str):
    if name in D1_DIVERGED:
        return pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=D1_REASON))
    return name


@pytest.mark.parametrize("name", [_param(n) for n in _shared_function_names()])
def test_vendored_copy_matches_local_function(name: str):
    """🔴 Every function both copies define must be VERBATIM equal (body + args). A diverged parser
    means the cloud ingest-worker and the local Kori ingest write different rows for the same
    invoice — the drift `appyhour_lib/invoice_parsers.py:13` records having burned once already."""
    local = _top_level_functions(_LOCAL)[name]
    vendored = _top_level_functions(_VENDORED)[name]
    assert _signature_and_body(local) == _signature_and_body(vendored), (
        f"{name} differs between GelPackCalculator/shipping_invoice_db.py and "
        f"ShipRouting/server/shipping_invoice_db.py")


def test_d1_diverged_list_is_exact():
    """The xfail list must be EXACTLY the diverged set — a function that drifts later must fail
    loudly here, and one that is reconciled must be removed from D1_DIVERGED (strict xfail)."""
    local, vendored = _top_level_functions(_LOCAL), _top_level_functions(_VENDORED)
    diverged = {n for n in set(local) & set(vendored)
                if _signature_and_body(local[n]) != _signature_and_body(vendored[n])}
    assert diverged == D1_DIVERGED, (
        f"newly diverged: {sorted(diverged - D1_DIVERGED)}; "
        f"reconciled (remove from D1_DIVERGED): {sorted(D1_DIVERGED - diverged)}")


@pytest.mark.xfail(strict=True, reason="CARRIER_HUBS mirror at shipping_invoice_db.py:1203 is stale "
                   "(still lists Veho@Nashville/Indianapolis and FedEx@Indianapolis; lacks Chicago and "
                   "Swedesboro) — dedupe audit item 3 finding, reconcile under INVOICE_INGEST_RULES.md")
def test_carrier_hubs_matches_shiprouting_features_baseline():
    """🔴 `shipping_invoice_db.CARRIER_HUBS` says 'Keep in sync with ShipRouting/lib/features.py'.
    features.CARRIER_HUBS_BASELINE is the code form of ROUTING_RULES §0; the mirror must be equal
    VERBATIM or the invoice-ingest legality check judges lanes by a roster the engine retired."""
    baseline = _module_level_literal(_FEATURES, "CARRIER_HUBS_BASELINE")
    assert {c: set(h) for c, h in local_sidb.CARRIER_HUBS.items()} == {c: set(h) for c, h in baseline.items()}
