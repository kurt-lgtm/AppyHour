"""Routing input exclusions. No I/O, credentials, or imports from either application."""


def is_gift_redemption_twin(order_name):
    """Kurt 2026-09-10: A-suffixed order numbers do not ship as separate orders."""
    return str(order_name or "").strip().upper().endswith("A")


def report_dropped_gifts(names):
    if names:
        preview = ", ".join(str(name) for name in names[:20])
        remaining = len(names) - 20
        tail = f" (+{remaining} more)" if remaining > 0 else ""
        print(f"[routing] Dropped gift redemption: {preview}{tail} "
              f"({len(names)} excluded).", flush=True)


def without_gift_redemption_twins(orders):
    """Preserve original identities and order; never merge twin quantities into a parent."""
    kept, dropped = [], []
    for order in orders:
        if is_gift_redemption_twin(order.get("name")):
            dropped.append(order.get("name"))
        else:
            kept.append(order)
    report_dropped_gifts(dropped)
    return kept
