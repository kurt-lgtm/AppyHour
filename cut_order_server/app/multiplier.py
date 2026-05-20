"""First-order multiplier — empirical Shopify ratio × user knob.

Empirical = trailing-90d avg units/order: first-time (orders_count==1) vs returning.
Per-SKU ratio; global fallback when n < MIN_N.
"""
from __future__ import annotations

from collections import defaultdict
from .config import PICKABLE_PREFIXES
from . import shopify_client

MIN_N_PER_SKU = 30


def compute_empirical_ratios() -> dict[str, float]:
    """Return {sku: ratio} where ratio = first_avg / returning_avg. Includes "__global__" key."""
    first_qty: dict[str, int] = defaultdict(int)
    first_orders: int = 0
    ret_qty: dict[str, int] = defaultdict(int)
    ret_orders: int = 0

    for order in shopify_client.fetch_orders_90d():
        customer = order.get("customer") or {}
        is_first = (customer.get("orders_count") or 0) == 1
        if is_first:
            first_orders += 1
        else:
            ret_orders += 1
        for li in order.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if not sku or not any(sku.startswith(p) for p in PICKABLE_PREFIXES):
                continue
            qty = int(float(li.get("quantity", 1)))
            (first_qty if is_first else ret_qty)[sku] += qty

    ratios: dict[str, float] = {}
    # Per-SKU ratios (only if both samples big enough)
    for sku in set(first_qty) | set(ret_qty):
        f_n = first_qty[sku]
        r_n = ret_qty[sku]
        if f_n >= MIN_N_PER_SKU and r_n >= MIN_N_PER_SKU and first_orders and ret_orders:
            f_avg = f_n / first_orders
            r_avg = r_n / ret_orders
            if r_avg > 0:
                ratios[sku] = f_avg / r_avg

    # Global fallback
    if first_orders and ret_orders:
        f_total = sum(first_qty.values())
        r_total = sum(ret_qty.values())
        if f_total and r_total:
            f_avg = f_total / first_orders
            r_avg = r_total / ret_orders
            ratios["__global__"] = f_avg / r_avg if r_avg else 1.0

    return ratios


def apply_multiplier(returning_units: int, first_order_units: int, ratio: float, user_knob: float) -> int:
    """Demand = returning + (first × ratio × knob). Apply to first-order subset only."""
    boost = ratio * user_knob
    return int(round(returning_units + first_order_units * boost))
