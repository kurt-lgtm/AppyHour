"""#9 — Safety factor sensitivity sweep.

For each locked snapshot, replay the thermal model varying safety_factor in
{5, 7, 10, 12, 15}%. For each setting:
  - which orders' tier shifts up/down?
  - what's the total cost delta?
  - what's the count at each risk level?
  - how many orders cross from margin>0 to margin<0 (under-pack risk)?

Helps pick a safety_factor that minimizes cost while keeping under-pack
exposure tolerable. Pure analytics — no production changes.

Output: _outputs/postmortems/safety_sweep-{ship_week}.md
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "GelPackCalculator"))

from gel_pack_shopify import (  # noqa: E402
    GEL_CONFIGS,
    MELT_EFFICIENCY,
    analyze_order,
    calc_surface_area,
)

DB_PATH = Path.home() / "AppData/Roaming/AppyHour/shipping.db"
OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\postmortems")
SETTINGS_PATH = Path.home() / "AppData/Roaming/AppyHour/gel_calc_shopify_settings.json"

SWEEP = [5, 7, 10, 12, 15]  # safety_factor values to sweep

STATE_2L_TO_FULL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("safety-sweep")


def find_min_config(q_safe: float) -> dict:
    """Lowest-cost GEL_CONFIG where effective_btu >= q_safe. Falls back to heaviest."""
    for c in GEL_CONFIGS:
        if c["btu"] * MELT_EFFICIENCY >= q_safe:
            return c
    return GEL_CONFIGS[-1]


def get_transit_type(state: str, transit_map: dict) -> str:
    full = STATE_2L_TO_FULL.get(state, state)
    return transit_map.get(full, "2-Day")


def sweep_snapshot(db: sqlite3.Connection, snapshot_id: str, settings: dict) -> dict:
    snap = db.execute(
        """
        SELECT snapshot_id, ship_week, ship_tag, total_orders,
               target_temp_f, safety_pct, hub_temp_f, box_settings_json
        FROM kori_snapshots WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if not snap:
        return {}

    box = json.loads(snap["box_settings_json"] or "{}")
    box_l = float(box.get("box_l") or settings.get("box_l", 12))
    box_w = float(box.get("box_w") or settings.get("box_w", 12))
    box_h = float(box.get("box_h") or settings.get("box_h", 12))
    thickness = float(box.get("thickness") or settings.get("thickness", 1.5))
    r_per_inch = float(box.get("r_per_inch") or settings.get("r_per_inch", 4.0))
    r_air = float(box.get("r_air_film") or settings.get("r_air_film", 0.17))
    hub_1d = float(box.get("hub_1d") or settings.get("hub_hours_1day", 3))
    hub_2d = float(box.get("hub_2d") or settings.get("hub_hours_2day", 6))
    hub_3d = float(box.get("hub_3d") or settings.get("hub_hours_3day", 9))
    target_temp = float(snap["target_temp_f"] or 50)
    hub_temp = float(snap["hub_temp_f"] or 70)
    surface_area = calc_surface_area(box_l, box_w, box_h)
    r_total = r_per_inch * thickness + r_air
    price_48 = float(settings.get("price_48oz", 0))
    price_24 = float(settings.get("price_24oz", 0))
    transit_map = settings.get("transit_types", {})

    rows = db.execute(
        """
        SELECT order_number, state, predicted_config,
               predicted_packs_48, predicted_packs_24, predicted_risk,
               dest_peak_temp_f
        FROM kori_snapshot_orders WHERE snapshot_id = ?
          AND dest_peak_temp_f IS NOT NULL
        """,
        (snapshot_id,),
    ).fetchall()

    if not rows:
        return {"snapshot": dict(snap), "error": "no orders with temp"}

    # Build per-sweep summary
    sweep_results: dict[int, dict] = {}
    # Baseline = predicted_config from snapshot (whatever was actually shipped)
    baseline_cost = 0.0
    baseline_tier_count: dict[str, int] = defaultdict(int)
    for r in rows:
        cost = (r["predicted_packs_48"] or 0) * price_48 + (r["predicted_packs_24"] or 0) * price_24
        baseline_cost += cost
        baseline_tier_count[r["predicted_config"] or "?"] += 1

    for sf in SWEEP:
        total_cost = 0.0
        tier_count: dict[str, int] = defaultdict(int)
        upgrades = 0
        downgrades = 0
        risk_count: dict[str, int] = defaultdict(int)
        for r in rows:
            ttype = get_transit_type(r["state"], transit_map)
            a = analyze_order(
                outside_temp=float(r["dest_peak_temp_f"]),
                transit_type=ttype,
                hub_hours_1day=hub_1d,
                hub_hours_2day=hub_2d,
                hub_hours_3day=hub_3d,
                hub_temp=hub_temp,
                surface_area=surface_area,
                r_total=r_total,
                target_temp=target_temp,
                safety_factor_pct=sf,
            )
            chosen = find_min_config(a["total_q_safe"])
            cost = chosen.get("48oz", 0) * price_48 + chosen.get("24oz", 0) * price_24
            total_cost += cost
            tier_count[chosen["name"]] += 1
            # Compare to baseline tier — count up/down moves
            base_name = r["predicted_config"]
            tier_idx = lambda name: next(  # noqa: E731
                (i for i, c in enumerate(GEL_CONFIGS) if c["name"] == name), -1
            )
            t_base, t_new = tier_idx(base_name), tier_idx(chosen["name"])
            if t_base >= 0 and t_new >= 0:
                if t_new > t_base:
                    upgrades += 1
                elif t_new < t_base:
                    downgrades += 1
            # Approximate risk via cap_pct + margin sign
            margin = chosen["btu"] * MELT_EFFICIENCY - a["total_q_safe"]
            if margin < -50:
                risk_count["CRITICAL"] += 1
            elif margin < 0:
                risk_count["HIGH"] += 1
            elif a.get("cap_pct", 0) >= 75:
                risk_count["MEDIUM"] += 1
            else:
                risk_count["LOW"] += 1
        sweep_results[sf] = {
            "total_cost": round(total_cost, 2),
            "tier_count": dict(tier_count),
            "upgrades_vs_baseline": upgrades,
            "downgrades_vs_baseline": downgrades,
            "risk_count": dict(risk_count),
            "cost_delta": round(total_cost - baseline_cost, 2),
        }

    return {
        "snapshot": dict(snap),
        "n_orders": len(rows),
        "baseline_cost": round(baseline_cost, 2),
        "baseline_tiers": dict(baseline_tier_count),
        "sweep": sweep_results,
    }


def build_markdown(result: dict) -> str:
    snap = result["snapshot"]
    out = [
        f"# Safety-Factor Sensitivity Sweep — ship_week {snap['ship_week']}\n",
        f"_Generated {datetime.now().isoformat(timespec='seconds')} by safety_factor_sweep.py_\n\n",
        f"## Cohort\n\n"
        f"- snapshot_id: `{snap['snapshot_id']}`\n"
        f"- ship_tag: `{snap['ship_tag']}` · orders analyzed: **{result['n_orders']}**\n"
        f"- baseline (as-shipped, safety_pct={snap['safety_pct']}): "
        f"total gel cost **${result['baseline_cost']}**\n\n",
        "## Sweep results\n\n",
        "| Safety% | Total cost | Δ vs baseline | Upgrades | Downgrades | LOW | MEDIUM | HIGH | CRITICAL |\n",
        "|---|---|---|---|---|---|---|---|---|\n",
    ]
    for sf in SWEEP:
        r = result["sweep"][sf]
        rc = r["risk_count"]
        out.append(
            f"| {sf}% | ${r['total_cost']} | "
            f"{'+' if r['cost_delta'] > 0 else ''}{r['cost_delta']} | "
            f"{r['upgrades_vs_baseline']} | {r['downgrades_vs_baseline']} | "
            f"{rc.get('LOW', 0)} | {rc.get('MEDIUM', 0)} | "
            f"{rc.get('HIGH', 0)} | {rc.get('CRITICAL', 0)} |\n"
        )
    out.append("\n## Tier distribution per safety level\n\n")
    out.append("| Tier | " + " | ".join(f"sf={sf}%" for sf in SWEEP) + " |\n")
    out.append("|---|" + "|".join(["---"] * len(SWEEP)) + "|\n")
    all_tiers = set()
    for sf in SWEEP:
        all_tiers.update(result["sweep"][sf]["tier_count"].keys())
    for tier in sorted(all_tiers, key=lambda t: next(
        (i for i, c in enumerate(GEL_CONFIGS) if c["name"] == t), 99
    )):
        row = [tier] + [str(result["sweep"][sf]["tier_count"].get(tier, 0)) for sf in SWEEP]
        out.append("| " + " | ".join(row) + " |\n")
    out.append("\n")
    out.append(
        "## Reading guide\n\n"
        "- **Lower safety_factor** = cheaper but more orders at HIGH/CRITICAL risk\n"
        "- **Higher safety_factor** = expensive over-pack, fewer warm-tickets but wasted gel\n"
        "- Sweet spot: lowest safety% where HIGH+CRITICAL stays within tolerated rate "
        "(empirical — needs warm-ticket history per cohort to validate)\n"
        "- Tier counts show where the model promotes orders as safety rises\n"
    )
    return "".join(out)


def main(snapshot_id: str | None = None) -> int:
    if not DB_PATH.exists():
        log.error(f"DB missing: {DB_PATH}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        settings = json.load(f)
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        if snapshot_id:
            ids = [snapshot_id]
        else:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT snapshot_id FROM kori_snapshots "
                    "WHERE fulfilled_at IS NOT NULL ORDER BY locked_at DESC LIMIT 5"
                )
            ]
        if not ids:
            log.warning("No canonical snapshots.")
            return 0
        for sid in ids:
            log.info(f"Sweeping {sid}")
            result = sweep_snapshot(db, sid, settings)
            if "error" in result:
                log.warning(f"{sid}: {result['error']}")
                continue
            md = build_markdown(result)
            out_path = OUT_DIR / f"safety_sweep-{result['snapshot']['ship_week']}.md"
            out_path.write_text(md, encoding="utf-8")
            log.info(f"Wrote {out_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(sid))
