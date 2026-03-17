"""Reorder point and demand calculation logic.

Pure functions extracted from InventoryReorder/inventory_reorder.py.
No GUI or API dependencies — import freely from any module.
"""

from __future__ import annotations


def calculate_reorder_point(
    daily_usage: float, lead_time_days: float, safety_stock: float
) -> float:
    """Reorder Point = (Daily Usage x Total Lead Time) + Safety Stock."""
    return (daily_usage * lead_time_days) + safety_stock


def calculate_total_lead_time(
    purchase_lt: float, production_lt: float, shipping_lt: float
) -> float:
    """Sum all lead time components."""
    return purchase_lt + production_lt + shipping_lt


def decompose_bundles(
    bundle_sku: str, quantity: float, bundle_map: dict[str, list[tuple[str, float]]]
) -> list[tuple[str, float]]:
    """Break a bundle SKU into component SKUs using bundle_map.

    Returns [(component_sku, total_qty), ...] or [(bundle_sku, quantity)]
    if not a bundle.
    """
    if bundle_sku in bundle_map:
        return [(comp_sku, quantity * comp_qty) for comp_sku, comp_qty in bundle_map[bundle_sku]]
    return [(bundle_sku, quantity)]


def apply_churn_rate(quantity: float, churn_pct: float) -> float:
    """Reduce quantity by churn percentage (e.g., 5% churn -> 95% remains)."""
    return quantity * (1.0 - churn_pct / 100.0)


def compute_wheel_supply(weight_lbs: float, count: int, slice_factor: float = 2.67) -> float:
    """Calculate slice supply from wheel inventory.

    Formula: weight_lbs * count * slice_factor
    """
    return weight_lbs * count * slice_factor


def compute_reorder_status(
    on_hand: float, reorder_point: float, daily_usage: float
) -> str:
    """Determine reorder status based on inventory levels.

    Returns: OUT_OF_STOCK, CRITICAL, REORDER, OK, or OVERSTOCK
    """
    if on_hand == 0 and daily_usage > 0:
        return "OUT_OF_STOCK"
    if reorder_point > 0 and on_hand <= reorder_point * 0.5:
        return "CRITICAL"
    if reorder_point > 0 and on_hand <= reorder_point:
        return "REORDER"
    if reorder_point > 0 and on_hand > reorder_point * 3:
        return "OVERSTOCK"
    return "OK"
