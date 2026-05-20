"""Demand orchestrator — Phase 2.

RC queued + SH unfulfilled, no-overlap (per cut_order_demand_model.md).
Day-of-week ship-tag rule (per ship_week.py).
AHB-X + BL-* override: replace SH-pulled qty with manual form input.
First-order multiplier applied to first-order subset only.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .config import PICKABLE_PREFIXES, ADDON_AHB_PREFIXES, AHB_X_SKUS
from .curation import resolve_curation
from .ship_week import compute_ship_week, ShipWeek
from . import shopify_client, recharge_client


@dataclass
class DemandResult:
    ship_week: ShipWeek
    per_sku: dict[str, int]                          # final demand (post-override, post-multiplier)
    rc_by_sku: dict[str, int] = field(default_factory=dict)
    sh_by_sku: dict[str, int] = field(default_factory=dict)
    first_order_by_sku: dict[str, int] = field(default_factory=dict)  # subset of sh_by_sku
    overrides: dict[str, int] = field(default_factory=dict)           # AHB-X + BL-* manual entries
    ahb_x_orders: dict[str, int] = field(default_factory=dict)        # current SH count per AHB-X SKU
    bl_skus_seen: dict[str, int] = field(default_factory=dict)        # current SH count per BL-* SKU


def _pull_recharge(ship_week: ShipWeek) -> tuple[dict[str, int], int]:
    """Returns (per_sku_qty, charge_count)."""
    sku_qty: dict[str, int] = defaultdict(int)
    n = 0
    for charge in recharge_client.fetch_queued_charges(ship_week.wk1_start, ship_week.wk1_end):
        sched = (charge.get("scheduled_at") or "")[:10]
        if not sched:
            continue
        try:
            d = date.fromisoformat(sched)
        except ValueError:
            continue
        if not (ship_week.wk1_start <= d <= ship_week.wk1_end):
            continue
        n += 1
        for item in charge.get("line_items", []):
            sku = (item.get("sku") or "").strip()
            if not sku or not any(sku.startswith(p) for p in PICKABLE_PREFIXES):
                continue
            sku_qty[sku] += int(float(item.get("quantity", 1)))
    return dict(sku_qty), n


def _pull_shopify(ship_week: ShipWeek) -> dict:
    """Returns dict with sh_by_sku, first_order_by_sku, ahb_x_orders, bl_skus_seen."""
    sh: dict[str, int] = defaultdict(int)
    first_sh: dict[str, int] = defaultdict(int)
    ahb_x: dict[str, int] = defaultdict(int)
    bl_skus: dict[str, int] = defaultdict(int)

    for order in shopify_client.fetch_open_orders():
        tags = order.get("tags", "") or ""
        if not any(t in tags for t in ship_week.tags):
            continue

        customer = order.get("customer") or {}
        is_first = (customer.get("orders_count") or 0) == 1

        for li in order.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if not sku:
                continue
            upper = sku.upper()
            qty = int(float(li.get("quantity", 1)))

            # AHB-X manual-entry source (track current orders for UI display)
            if upper in AHB_X_SKUS or upper.startswith("AHB-X"):
                ahb_x[sku] += qty
                continue  # do not add to sh_by_sku — manual override owns this
            # BL-* manual-entry source
            if upper.startswith("BL-"):
                bl_skus[sku] += qty
                continue

            if not any(upper.startswith(p) for p in PICKABLE_PREFIXES):
                continue

            sh[sku] += qty
            if is_first:
                first_sh[sku] += qty

    return {
        "sh_by_sku": dict(sh),
        "first_order_by_sku": dict(first_sh),
        "ahb_x_orders": dict(ahb_x),
        "bl_skus_seen": dict(bl_skus),
    }


def generate(
    today: date | None = None,
    overrides: dict[str, int] | None = None,
    multiplier_knob: float = 1.0,
    empirical_ratios: dict[str, float] | None = None,
    skip_recharge_on_monday: bool = True,
) -> DemandResult:
    """Run the demand pipeline. Mon-of-ship-week: skip RC (flushed)."""
    sw = compute_ship_week(today)
    overrides = overrides or {}
    ratios = empirical_ratios or {}

    # Mon-regime: RC queued is flushed
    today_actual = today or date.today()
    skip_rc = skip_recharge_on_monday and today_actual.weekday() == 0 and today_actual == sw.wk1_start

    rc_by_sku: dict[str, int] = {}
    if not skip_rc:
        rc_by_sku, _ = _pull_recharge(sw)

    sh_data = _pull_shopify(sw)
    sh_by_sku = sh_data["sh_by_sku"]
    first_by_sku = sh_data["first_order_by_sku"]

    # Combine RC + SH per no-overlap rule (sum, never max)
    combined: dict[str, int] = defaultdict(int)
    for sku, q in rc_by_sku.items():
        combined[sku] += q
    for sku, q in sh_by_sku.items():
        combined[sku] += q

    # Apply first-order multiplier to first-order subset only
    if ratios and multiplier_knob:
        from .multiplier import apply_multiplier
        global_ratio = ratios.get("__global__", 1.0)
        for sku in list(combined.keys()):
            f = first_by_sku.get(sku, 0)
            if not f:
                continue
            ret_units = combined[sku] - f
            ratio = ratios.get(sku, global_ratio)
            combined[sku] = apply_multiplier(ret_units, f, ratio, multiplier_knob)

    # Layer manual overrides (AHB-X + BL-*): they REPLACE SH pull for those SKUs.
    # Since we already skipped them in _pull_shopify, just stamp the overrides in.
    for sku, qty in overrides.items():
        combined[sku] = int(qty)

    return DemandResult(
        ship_week=sw,
        per_sku=dict(combined),
        rc_by_sku=rc_by_sku,
        sh_by_sku=sh_by_sku,
        first_order_by_sku=first_by_sku,
        overrides=overrides,
        ahb_x_orders=sh_data["ahb_x_orders"],
        bl_skus_seen=sh_data["bl_skus_seen"],
    )
