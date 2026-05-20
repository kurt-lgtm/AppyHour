"""Calculator — explode finished-SKU demand into raw-ingredient cut.

Two demand sources:
  1. Box-side SKUs (CH-, MT-, AC-): consume processed inventory (INV_TOTAL_BOX).
     If short, cut raw via RECIPE_BOX (qty / conversion / yield * pack_size lbs).
  2. Tray SKUs (TR-*): explode via RECIPE_TRAY (oz/yield → lbs per tray),
     consume raw lbs from INV_TOTAL_TRAY pool.

Output: cut list per RAW ingredient, in raw-pack-units.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .config import PAR_MIN
from . import ltf

OZ_PER_LB = 16.0


@dataclass
class RawCutRow:
    raw_name: str
    pack_size: float
    uom: str
    box_demand_lbs: float = 0.0      # lbs needed from raw to satisfy short box-side demand
    tray_demand_lbs: float = 0.0     # lbs needed for tray production
    total_demand_lbs: float = 0.0    # sum
    available_lbs: float = 0.0       # INV_TOTAL_BOX (raw side) + INV_TOTAL_TRAY (tray side)
    cut_lbs: float = 0.0             # max(0, demand - available + PAR padding)
    cut_packs: float = 0.0           # cut_lbs / pack_size
    contributing_skus: dict = field(default_factory=dict)  # {sku: qty} demand traceback


@dataclass
class CalcResult:
    rows: list[RawCutRow]
    short_finished_skus: dict[str, float]  # {sku: shortage_units}
    snapshot_date: Optional[str]


def _pack_size_lbs(pack_size: float, uom: str) -> float:
    """Convert pack size to lbs based on UoM."""
    if not pack_size:
        return 0.0
    u = (uom or "").strip().lower()
    if u in {"lb", "lbs", "pound", "pounds"}:
        return pack_size
    if u in {"kg", "kgs"}:
        return pack_size * 2.20462
    if u in {"oz", "ounce", "ounces"} or "oz" in u:
        return pack_size / OZ_PER_LB
    if u == "g":
        return pack_size / 453.592
    return pack_size  # unknown — treat as lbs


def calculate(
    demand_per_sku: dict[str, int],
    ltf_data: dict | None = None,
    par_min_packs: int = PAR_MIN,
) -> CalcResult:
    """Run the full calc against demand + LTF snapshot."""
    data = ltf_data or ltf.read_all()
    recipe_box: dict = data["recipe_box"]
    recipe_tray: dict = data["recipe_tray"]
    inv_box: dict[str, float] = data["inv_total_box"]
    inv_tray: dict[str, float] = data["inv_total_tray"]

    # Aggregate raw needs by raw_ingredient_name
    raw_needs_tray: dict[str, float] = defaultdict(float)        # lbs
    raw_needs_box: dict[str, float] = defaultdict(float)         # lbs
    raw_meta: dict[str, dict] = {}                                # {raw: {pack_size, uom}}
    contrib: dict[str, dict[str, float]] = defaultdict(dict)      # {raw: {sku: qty}}
    short_skus: dict[str, float] = {}

    # ─── Box-side: CH-, MT-, AC- ─────────────────────────────────────────
    # Consume processed inventory first; cut raw only for shortfall.
    for sku, qty in demand_per_sku.items():
        if qty <= 0:
            continue
        u = sku.upper()
        if not (u.startswith("CH-") or u.startswith("MT-") or u.startswith("AC-")):
            continue
        recipe = recipe_box.get(u)
        if not recipe:
            continue  # no mapping — drop (will surface as orphan in xlsx phase)
        avail = inv_box.get(u, 0.0)
        shortage = max(0.0, qty - avail)
        if shortage <= 0:
            continue
        short_skus[u] = shortage
        # Convert shortage (processed units) → raw lbs
        # conversion = processed_units_per_raw_pack, yield_pct = fraction
        if recipe.conversion <= 0:
            continue
        raw_packs = shortage / recipe.conversion / max(recipe.yield_pct, 0.0001)
        pack_lbs = _pack_size_lbs(recipe.pack_size, recipe.uom)
        raw_lbs = raw_packs * pack_lbs
        raw_needs_box[recipe.raw_name] += raw_lbs
        raw_meta.setdefault(recipe.raw_name, {"pack_size": recipe.pack_size, "uom": recipe.uom})
        contrib[recipe.raw_name][u] = contrib[recipe.raw_name].get(u, 0.0) + shortage

    # ─── Tray-side: TR-* ─────────────────────────────────────────────────
    # Explode via RECIPE_TRAY: per (tray, component) raw_lbs = (oz × qty) / yield / 16
    for sku, qty in demand_per_sku.items():
        if qty <= 0:
            continue
        u = sku.upper()
        if not u.startswith("TR-"):
            continue
        components = recipe_tray.get(u)
        if not components:
            continue
        for comp in components:
            yld = max(comp.yield_pct, 0.0001)
            raw_oz = comp.oz * qty / yld
            raw_lbs = raw_oz / OZ_PER_LB
            raw_needs_tray[comp.raw_ingredient] += raw_lbs
            # Best-effort pack_size/uom — look up via component_sku in RECIPE_BOX
            if comp.raw_ingredient not in raw_meta:
                proc = recipe_box.get(comp.component_sku)
                if proc:
                    raw_meta[comp.raw_ingredient] = {"pack_size": proc.pack_size, "uom": proc.uom}
                else:
                    raw_meta[comp.raw_ingredient] = {"pack_size": 0.0, "uom": ""}
            contrib[comp.raw_ingredient][u] = contrib[comp.raw_ingredient].get(u, 0.0) + qty

    # ─── Combine demand vs availability, compute cut ─────────────────────
    all_raws = set(raw_needs_box) | set(raw_needs_tray) | set(inv_tray) | set(
        r.raw_name for r in recipe_box.values()
    )
    rows: list[RawCutRow] = []
    for raw in sorted(all_raws):
        meta = raw_meta.get(raw, {"pack_size": 0.0, "uom": ""})
        box_d = raw_needs_box.get(raw, 0.0)
        tray_d = raw_needs_tray.get(raw, 0.0)
        total_d = box_d + tray_d
        # Tray-side availability from INV_TOTAL_TRAY (lbs)
        avail_lbs = inv_tray.get(raw, 0.0)
        # Box-side raw availability from INV_TOTAL_BOX raw_to_processed reversed?
        # Skip — INV_TOTAL_BOX already covers processed; box_d already represents shortfall.

        pack_lbs = _pack_size_lbs(meta["pack_size"], meta["uom"])
        cut_lbs = max(0.0, total_d - avail_lbs)
        # PAR padding: ensure at least PAR_MIN packs worth on-hand after demand
        if pack_lbs > 0:
            par_lbs = par_min_packs * pack_lbs
            if (avail_lbs - total_d) < par_lbs:
                cut_lbs = max(cut_lbs, par_lbs - (avail_lbs - total_d))
        cut_packs = cut_lbs / pack_lbs if pack_lbs > 0 else 0.0

        if total_d == 0 and cut_lbs == 0:
            continue  # skip dead rows

        rows.append(RawCutRow(
            raw_name=raw,
            pack_size=meta["pack_size"],
            uom=meta["uom"],
            box_demand_lbs=round(box_d, 2),
            tray_demand_lbs=round(tray_d, 2),
            total_demand_lbs=round(total_d, 2),
            available_lbs=round(avail_lbs, 2),
            cut_lbs=round(cut_lbs, 2),
            cut_packs=round(cut_packs, 2),
            contributing_skus={k: round(v, 2) for k, v in contrib.get(raw, {}).items()},
        ))

    return CalcResult(rows=rows, short_finished_skus=short_skus, snapshot_date=data.get("snapshot_date"))
