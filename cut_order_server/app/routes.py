"""Main routes — stubs for Phase 1, expanded in Phases 2-6."""
from __future__ import annotations

from flask import Blueprint, render_template
from .auth import login_required

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def index():
    from .config import AHB_X_SKUS, BL_SEED_SKUS
    from .ship_week import compute_ship_week
    sw = compute_ship_week()
    return render_template(
        "index.html",
        wk1_start=sw.wk1_start.isoformat(),
        wk1_end=sw.wk1_end.isoformat(),
        ship_tags=list(sw.tags),
        ahb_x_skus=list(AHB_X_SKUS),
        bl_skus=list(BL_SEED_SKUS),
    )


@main_bp.route("/healthz")
def healthz():
    return {"ok": True}, 200


# Phase 2 — demand generator
@main_bp.route("/demand", methods=["POST"])
@login_required
def demand():
    from flask import request, jsonify
    from . import demand as demand_mod

    body = request.get_json(silent=True) or {}
    overrides_raw = body.get("overrides") or {}
    overrides = {str(k).strip(): int(v) for k, v in overrides_raw.items() if str(v).strip()}
    knob = float(body.get("multiplier_knob", 1.0))
    ratios = body.get("empirical_ratios") or {}

    result = demand_mod.generate(
        overrides=overrides,
        multiplier_knob=knob,
        empirical_ratios=ratios,
    )

    return jsonify({
        "wk1_start": result.ship_week.wk1_start.isoformat(),
        "wk1_end": result.ship_week.wk1_end.isoformat(),
        "ship_tags": list(result.ship_week.tags),
        "per_sku": result.per_sku,
        "rc_by_sku": result.rc_by_sku,
        "sh_by_sku": result.sh_by_sku,
        "first_order_by_sku": result.first_order_by_sku,
        "ahb_x_orders": result.ahb_x_orders,
        "bl_skus_seen": result.bl_skus_seen,
        "overrides_applied": result.overrides,
    })


@main_bp.route("/multiplier/ratios", methods=["POST"])
@login_required
def multiplier_ratios():
    """Compute empirical first-order ratios from trailing 90d Shopify."""
    from flask import jsonify
    from .multiplier import compute_empirical_ratios
    return jsonify(compute_empirical_ratios())


@main_bp.route("/calc", methods=["POST"])
@login_required
def calc():
    from flask import request, jsonify
    from . import calc as calc_mod

    body = request.get_json(silent=True) or {}
    demand_per_sku = {str(k).strip().upper(): int(v) for k, v in (body.get("demand_per_sku") or {}).items()}
    if not demand_per_sku:
        return jsonify({"error": "demand_per_sku required"}), 400
    result = calc_mod.calculate(demand_per_sku)
    return jsonify({
        "snapshot_date": result.snapshot_date,
        "rows": [
            {
                "raw_name": r.raw_name,
                "pack_size": r.pack_size,
                "uom": r.uom,
                "box_demand_lbs": r.box_demand_lbs,
                "tray_demand_lbs": r.tray_demand_lbs,
                "total_demand_lbs": r.total_demand_lbs,
                "available_lbs": r.available_lbs,
                "cut_lbs": r.cut_lbs,
                "cut_packs": r.cut_packs,
                "contributing_skus": r.contributing_skus,
            }
            for r in result.rows
        ],
        "short_finished_skus": result.short_finished_skus,
    })


@main_bp.route("/run", methods=["POST"])
@login_required
def run():
    """End-to-end: demand → calc → xlsx → DO Spaces → signed URL."""
    from flask import request, jsonify
    from uuid import uuid4
    from datetime import datetime
    from . import demand as demand_mod
    from . import calc as calc_mod
    from .xlsx_writer import build_xlsx
    from .spaces import upload_xlsx

    body = request.get_json(silent=True) or {}
    overrides_raw = body.get("overrides") or {}
    overrides = {str(k).strip().upper(): int(v) for k, v in overrides_raw.items() if str(v).strip()}
    knob = float(body.get("multiplier_knob", 1.0))
    ratios = body.get("empirical_ratios") or {}

    d = demand_mod.generate(overrides=overrides, multiplier_knob=knob, empirical_ratios=ratios)
    demand_dict = {
        "wk1_start": d.ship_week.wk1_start.isoformat(),
        "wk1_end": d.ship_week.wk1_end.isoformat(),
        "ship_tags": list(d.ship_week.tags),
        "per_sku": d.per_sku,
        "rc_by_sku": d.rc_by_sku,
        "sh_by_sku": d.sh_by_sku,
        "first_order_by_sku": d.first_order_by_sku,
        "overrides_applied": d.overrides,
    }
    c = calc_mod.calculate(d.per_sku)
    calc_dict = {
        "snapshot_date": c.snapshot_date,
        "rows": [
            {
                "raw_name": r.raw_name, "pack_size": r.pack_size, "uom": r.uom,
                "box_demand_lbs": r.box_demand_lbs, "tray_demand_lbs": r.tray_demand_lbs,
                "total_demand_lbs": r.total_demand_lbs, "available_lbs": r.available_lbs,
                "cut_lbs": r.cut_lbs, "cut_packs": r.cut_packs,
                "contributing_skus": r.contributing_skus,
            } for r in c.rows
        ],
    }
    xlsx_bytes = build_xlsx(
        demand_result=demand_dict,
        calc_result=calc_dict,
        multiplier_knob=knob,
        multiplier_ratios=ratios,
    )
    run_id = uuid4().hex[:12]
    filename = f"cut_order_{d.ship_week.wk1_end.isoformat()}.xlsx"
    url = upload_xlsx(content=xlsx_bytes, run_id=run_id, filename=filename)
    return jsonify({
        "run_id": run_id,
        "download_url": url,
        "filename": filename,
        "wk1_end": d.ship_week.wk1_end.isoformat(),
        "ship_tags": list(d.ship_week.tags),
        "demand_total_skus": len(d.per_sku),
        "cut_rows": len(c.rows),
        "snapshot_date": c.snapshot_date,
    })


@main_bp.route("/history")
@login_required
def history():
    return {"todo": "phase 7 — history (needs Postgres)"}, 501
