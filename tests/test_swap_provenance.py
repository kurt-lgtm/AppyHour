"""Provenance gate — replays the real wk0824 CH-SOT burn.

Fixture rows are the actual order numbers and variant gids from 2026-08-21, not invented
shapes: 6 orders were swapped that the Matrixify import never touched.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swap_provenance import (  # noqa: E402
    ProvenanceError, applied_orders, gate_targets, require_single_variant, variant_split,
)

IN_BOX = "gid://shopify/ProductVariant/50896457040152"   # $0 applied variant
NATIVE = "gid://shopify/ProductVariant/50611339559192"   # $11 catalog, original content

# the six that actually shipped wrong
BURNED = ["175942", "175688", "175686", "175559", "175392", "174781"]


@pytest.fixture
def mtx(tmp_path):
    p = tmp_path / "matrixify.csv"
    p.write_text(
        "Name,child_sku,parent_sku\n"
        "#175100,CH-SOT,AHB-MCUST-MS\n"
        "#175101,CH-SOT,AHB-LCUST-GEN\n"
        "#175102,AC-MFJ,AHB-MCUST-MS\n",
        encoding="utf-8")
    return p


def test_applied_orders_reads_the_file(mtx):
    assert applied_orders(mtx, "CH-SOT") == {"175100", "175101"}


def test_sku_never_applied_raises(mtx):
    with pytest.raises(ProvenanceError):
        applied_orders(mtx, "CH-QOTA")


def test_gate_refuses_orders_absent_from_import(mtx):
    targets = [{"name": "#175100"}, {"name": "#175101"}] + [{"name": f"#{o}"} for o in BURNED]
    keep, refuse = gate_targets(targets, "CH-SOT", mtx)
    assert [t["name"] for t in keep] == ["#175100", "#175101"]
    assert [t["name"] for t in refuse] == [f"#{o}" for o in BURNED]


def test_gate_tolerates_bare_order_numbers(mtx):
    keep, refuse = gate_targets([{"name": "175100"}], "CH-SOT", mtx)
    assert len(keep) == 1 and not refuse


def test_two_variants_is_two_populations():
    targets = [{"old_variant_gid": IN_BOX}] * 222 + [{"old_variant_gid": NATIVE}] * 6
    groups = variant_split(targets)
    assert len(groups) == 2
    assert len(groups[IN_BOX]) == 222 and len(groups[NATIVE]) == 6


def test_require_single_variant_aborts_the_burn():
    targets = [{"old_variant_gid": IN_BOX}] * 222 + [{"old_variant_gid": NATIVE}] * 6
    with pytest.raises(ProvenanceError, match="different populations"):
        require_single_variant(targets)


def test_require_single_variant_passes_when_clean():
    assert require_single_variant([{"old_variant_gid": IN_BOX}] * 5) == IN_BOX


# --- per-order cap (Kurt 2026-08-25) -------------------------------------------------
from swap_provenance import cap_swaps_per_order  # noqa: E402

# order 7305663676696, 2026-08-14 12:36 - five tray swaps in 28 seconds
HER_ORDER = [
    {"name": "#175000", "old_sku": s}
    for s in ("TR-AAB", "TR-ROSE", "TR-DGG", "TR-PICVE", "TR-ALPAP")
]


def test_cap_holds_back_everything_past_two():
    allowed, over = cap_swaps_per_order(HER_ORDER, cap=2)
    assert [t["old_sku"] for t in allowed] == ["TR-AAB", "TR-ROSE"]
    assert [t["old_sku"] for t in over] == ["TR-DGG", "TR-PICVE", "TR-ALPAP"]


def test_cap_is_per_order_not_global():
    targets = HER_ORDER[:2] + [{"name": "#175001", "old_sku": "TR-DGG"}]
    allowed, over = cap_swaps_per_order(targets, cap=2)
    assert len(allowed) == 3 and not over


def test_repeat_sku_on_same_order_is_one_sku():
    targets = [{"name": "#1", "old_sku": "TR-AAB"}, {"name": "#1", "old_sku": "TR-AAB"},
               {"name": "#1", "old_sku": "TR-ROSE"}, {"name": "#1", "old_sku": "TR-DGG"}]
    allowed, over = cap_swaps_per_order(targets, cap=2)
    assert len(allowed) == 3 and [t["old_sku"] for t in over] == ["TR-DGG"]


def test_cap_tolerates_hash_prefix():
    targets = [{"name": "175000", "old_sku": "TR-AAB"}, {"name": "#175000", "old_sku": "TR-ROSE"},
               {"name": "175000", "old_sku": "TR-DGG"}]
    allowed, over = cap_swaps_per_order(targets, cap=2)
    assert len(allowed) == 2 and len(over) == 1


# --- login-OR-customize guardrail ----------------------------------------------------
from swap_provenance import drop_protected  # noqa: E402


def test_logged_in_customer_is_dropped():
    """Customer 257748298 logged in 2026-08-10 and still got 5 swaps on 08-14."""
    targets = [{"name": "#175000", "email": "her@example.com", "old_sku": "TR-AAB"},
               {"name": "#175001", "email": "other@example.com", "old_sku": "TR-AAB"}]
    ok, blocked = drop_protected(targets, {"her@example.com"})
    assert [t["name"] for t in ok] == ["#175001"]
    assert [t["name"] for t in blocked] == ["#175000"]


def test_protection_is_case_and_space_insensitive():
    targets = [{"name": "#1", "email": "  HER@Example.com "}]
    ok, blocked = drop_protected(targets, {"her@example.com"})
    assert not ok and len(blocked) == 1


def test_no_protected_set_blocks_nothing():
    targets = [{"name": "#1", "email": "a@b.co"}]
    ok, blocked = drop_protected(targets, set())
    assert len(ok) == 1 and not blocked


# --- guard_batch: fails CLOSED (Kurt: "never happens again unless I say so") ----------
from swap_provenance import guard_batch  # noqa: E402

_T = ["_SHIP_2026-08-31", "RMFG_20260828"]   # ordinary tags; notably NOT "PR box"
BATCH = [
    {"name": "#175000", "email": "her@example.com", "old_sku": "TR-AAB", "tags": _T},
    {"name": "#175000", "email": "her@example.com", "old_sku": "TR-ROSE", "tags": _T},
    {"name": "#175000", "email": "her@example.com", "old_sku": "TR-DGG", "tags": _T},
    {"name": "#175001", "email": "ok@example.com", "old_sku": "TR-AAB", "tags": _T},
]


def test_missing_login_scan_refuses():
    with pytest.raises(ProvenanceError, match="login scan"):
        guard_batch(BATCH, protected_emails=set(), login_scan_ran=False,
                    customize_scan_ran=True)


def test_missing_customize_scan_refuses():
    with pytest.raises(ProvenanceError, match="customize scan"):
        guard_batch(BATCH, protected_emails=set(), login_scan_ran=True,
                    customize_scan_ran=False)


def test_none_protected_set_refuses_even_when_scans_ran():
    with pytest.raises(ProvenanceError, match="protected_emails is None"):
        guard_batch(BATCH, protected_emails=None, login_scan_ran=True,
                    customize_scan_ran=True)


def test_kurt_override_is_the_only_way_past():
    r = guard_batch(BATCH, protected_emails=None, login_scan_ran=False,
                    customize_scan_ran=False, kurt_override="do it anyway")
    assert r["report"]["kurt_override"] == "do it anyway"


def test_guard_applies_protection_then_cap():
    r = guard_batch(BATCH, protected_emails={"her@example.com"},
                    login_scan_ran=True, customize_scan_ran=True)
    assert [t["name"] for t in r["allowed"]] == ["#175001"]
    assert len(r["protected"]) == 3
    assert r["report"]["dropped_protected"] == 3


def test_cap_still_applies_to_unprotected_customers():
    r = guard_batch(BATCH, protected_emails=set(), login_scan_ran=True,
                    customize_scan_ran=True, cap=2)
    assert [t["old_sku"] for t in r["over_cap"]] == ["TR-DGG"]
    assert r["report"]["executing"] == 3


# --- "PR box" is never swappable (Kurt 2026-08-25) ------------------------------------
from swap_provenance import blocked_by_tag  # noqa: E402

# real tag list from order #178014, _SHIP_2026-08-31
PR_TAGS = ["_SHIP_2026-08-31", "PR box", "RMFG_20260828", "Simple Bundles - Bundle Order"]
NORMAL_TAGS = ["_SHIP_2026-08-31", "RMFG_20260828", "Subscription First Order"]


def test_pr_box_order_is_blocked():
    targets = [{"name": "#178014", "tags": PR_TAGS}, {"name": "#178015", "tags": NORMAL_TAGS}]
    ok, blocked = blocked_by_tag(targets)
    assert [t["name"] for t in ok] == ["#178015"]
    assert [t["name"] for t in blocked] == ["#178014"]


def test_tag_match_is_case_insensitive():
    ok, blocked = blocked_by_tag([{"name": "#1", "tags": ["PR BOX"]},
                                  {"name": "#2", "tags": ["pr box"]}])
    assert not ok and len(blocked) == 2


def test_missing_tags_fails_closed():
    """No tag evidence = blocked, never swapped on assumption."""
    ok, blocked = blocked_by_tag([{"name": "#1"}])
    assert not ok and len(blocked) == 1


def test_guard_batch_blocks_pr_box_even_under_override():
    r = guard_batch([{"name": "#178014", "email": "t@e.co", "old_sku": "CH-ASST", "tags": PR_TAGS},
                     {"name": "#178015", "email": "a@b.co", "old_sku": "CH-ASST", "tags": NORMAL_TAGS}],
                    protected_emails=None, login_scan_ran=False, customize_scan_ran=False,
                    kurt_override="go")
    assert [t["name"] for t in r["allowed"]] == ["#178015"]
    assert [t["name"] for t in r["tag_blocked"]] == ["#178014"]
    assert r["report"]["tag_blocked"] == 1
