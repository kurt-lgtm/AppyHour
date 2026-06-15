"""Unit tests for the pure conditional-swap logic + dry-run safety.
No live Shopify calls — the live mutation is gated behind a separate live-order test.
Run: python test_conditional_swap.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from shopify_swap import resolve_conditional_adds, execute_conditional_swap

TARGET_MAP = {
    "MONG": {"cheese": "CH-BLR", "jams": [("AC-BLBALS", 1)]},
    "HHIGH": {"cheese": "CH-FONT", "jams": [("AC-RPJ", 1), ("AC-FIG", 2)]},
    "GEN": {"cheese": "CH-ALPHA", "jams": []},
}

def _eq(got, want, label):
    assert got == want, f"{label}: got {got!r}, want {want!r}"
    print(f"  ok: {label}")

def test_resolver():
    _eq(resolve_conditional_adds("MONG", TARGET_MAP), [("CH-BLR", 1), ("AC-BLBALS", 1)], "MONG cheese+1 jam")
    _eq(resolve_conditional_adds("HHIGH", TARGET_MAP), [("CH-FONT", 1), ("AC-RPJ", 1), ("AC-FIG", 2)], "HHIGH cheese+2 jams")
    _eq(resolve_conditional_adds("GEN", TARGET_MAP), [("CH-ALPHA", 1)], "GEN cheese only")
    # different parent -> different target (the whole point of conditional-target)
    assert resolve_conditional_adds("MONG", TARGET_MAP) != resolve_conditional_adds("HHIGH", TARGET_MAP)
    print("  ok: per-parent targets differ")
    # fail loud on unknown parent
    try:
        resolve_conditional_adds("NOPE", TARGET_MAP); raise AssertionError("should have raised")
    except KeyError:
        print("  ok: unknown parent raises KeyError (fail loud)")

def test_dry_run_never_mutates():
    # dry_run=True must return a plan and NOT touch the network (store/token are dummies)
    r = execute_conditional_swap("dummy", "dummy", "gid://x/1",
                                 removes=["CH-IPAC", "AC-RPJ"], adds=[("gid://variant/9", 1)],
                                 dry_run=True)
    _eq(r["dry_run"], True, "dry_run flag")
    _eq(r["success"], True, "dry_run success")
    _eq(r["removes"], ["CH-IPAC", "AC-RPJ"], "dry_run echoes removes")
    _eq(r["adds"], [("gid://variant/9", 1)], "dry_run echoes adds")
    print("  ok: dry_run returns plan, no mutation")

if __name__ == "__main__":
    print("test_resolver"); test_resolver()
    print("test_dry_run_never_mutates"); test_dry_run_never_mutates()
    print("\nALL PASS")
