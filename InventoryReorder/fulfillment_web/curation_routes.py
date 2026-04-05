"""Flask Blueprint for curation management API endpoints.

Provides CRUD for curations (rotation + monthly), addon bundles,
demand preview with SKU dedup, and validation.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from curation_manager import (
    compute_demand_for_curation,
    compute_total_demand,
    delete_addon_bundle,
    delete_curation,
    duplicate_curation,
    get_curation,
    get_effective_monthly_curation,
    list_addon_bundles,
    list_curations,
    migrate_from_legacy,
    upsert_addon_bundle,
    upsert_curation,
    validate_curation,
)

bp = Blueprint("curations", __name__, url_prefix="/api/curations")

# These will be set by app.py at init
_get_state = None
_save = None


def init(get_state_fn, save_fn):
    """Wire up state accessors from the main app."""
    global _get_state, _save
    _get_state = get_state_fn
    _save = save_fn


def _settings():
    return _get_state()["saved"]


def _persist(s):
    _get_state()["saved"] = s
    _save(s)


# ── Curation CRUD ─────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
def api_list_curations():
    s = _settings()
    s = migrate_from_legacy(s)
    curations = list_curations(s)
    return jsonify({"curations": curations})


@bp.route("/<key>", methods=["GET"])
def api_get_curation(key):
    s = _settings()
    s = migrate_from_legacy(s)
    cur = get_curation(s, key)
    if cur is None:
        return jsonify({"error": f"Curation {key} not found"}), 404
    return jsonify(cur)


@bp.route("/<key>", methods=["PUT"])
def api_upsert_curation(key):
    data = request.get_json(force=True)
    s = _settings()
    s = migrate_from_legacy(s)

    # Validate before saving
    all_curations = s.get("curations_v2", {})
    # Exclude self from uniqueness checks
    check_curations = {k: v for k, v in all_curations.items() if k != key}
    errors = validate_curation(data, check_curations)

    s = upsert_curation(s, key, data)
    _persist(s)

    return jsonify({"ok": True, "key": key, "warnings": errors})


@bp.route("/<key>", methods=["DELETE"])
def api_delete_curation(key):
    s = _settings()
    s = delete_curation(s, key)
    _persist(s)
    return jsonify({"ok": True})


@bp.route("/<key>/duplicate", methods=["POST"])
def api_duplicate_curation(key):
    data = request.get_json(force=True) or {}
    new_key = data.get("new_key", f"{key}-copy")
    s = _settings()
    s = duplicate_curation(s, key, new_key)
    _persist(s)
    return jsonify({"ok": True, "new_key": new_key})


# ── Addon bundle CRUD ─────────────────────────────────────────────────

@bp.route("/addons", methods=["GET"])
def api_list_addons():
    s = _settings()
    bundles = list_addon_bundles(s)
    return jsonify({"addons": bundles})


@bp.route("/addons/<key>", methods=["PUT"])
def api_upsert_addon(key):
    data = request.get_json(force=True)
    s = _settings()
    s = upsert_addon_bundle(s, key, data)
    _persist(s)
    return jsonify({"ok": True, "key": key})


@bp.route("/addons/<key>", methods=["DELETE"])
def api_delete_addon(key):
    s = _settings()
    s = delete_addon_bundle(s, key)
    _persist(s)
    return jsonify({"ok": True})


# ── Demand preview ────────────────────────────────────────────────────

@bp.route("/demand-preview", methods=["POST"])
def api_demand_preview():
    """Preview demand for one or all curations with SKU dedup."""
    data = request.get_json(force=True) or {}
    s = _settings()
    s = migrate_from_legacy(s)

    box_counts = data.get("box_counts", {})
    target_date = data.get("target_date")

    total = compute_total_demand(s, box_counts, target_date)

    # Sort by demand descending
    sorted_demand = sorted(total.items(), key=lambda x: -x[1])
    return jsonify({"demand": sorted_demand, "total_skus": len(sorted_demand)})


@bp.route("/<key>/demand", methods=["POST"])
def api_curation_demand(key):
    """Preview demand for a single curation."""
    data = request.get_json(force=True) or {}
    s = _settings()
    s = migrate_from_legacy(s)

    cur = s.get("curations_v2", {}).get(key)
    if cur is None:
        return jsonify({"error": f"Curation {key} not found"}), 404

    box_count = data.get("count", 100)
    addon_bundles = s.get("addon_bundles", {})
    demand = compute_demand_for_curation(cur, box_count, addon_bundles)

    sorted_demand = sorted(demand.items(), key=lambda x: -x[1])
    return jsonify({"demand": sorted_demand, "count": box_count})


# ── Monthly curation lookup ───────────────────────────────────────────

@bp.route("/monthly/<box_type>", methods=["GET"])
def api_monthly_lookup(box_type):
    """Find the effective monthly curation for a box type."""
    s = _settings()
    s = migrate_from_legacy(s)
    target_date = request.args.get("date")
    cur = get_effective_monthly_curation(s, box_type, target_date)
    if cur is None:
        return jsonify({"error": f"No active monthly curation for {box_type}"}), 404
    return jsonify(cur)


# ── SKU catalog for dropdowns ─────────────────────────────────────────

@bp.route("/sku-catalog", methods=["GET"])
def api_sku_catalog():
    """Return available SKUs grouped by category for recipe editors."""
    s = _settings()
    inventory = s.get("inventory", {})
    skus = {"cheese": [], "meat": [], "accompaniment": [], "other": []}

    for sku, info in inventory.items():
        name = info.get("name", sku) if isinstance(info, dict) else sku
        entry = {"sku": sku, "name": name}
        if sku.startswith("CH-"):
            skus["cheese"].append(entry)
        elif sku.startswith("MT-"):
            skus["meat"].append(entry)
        elif sku.startswith("AC-"):
            skus["accompaniment"].append(entry)
        else:
            skus["other"].append(entry)

    # Also include SKUs from sku_mappings.json if available
    import os
    import json
    mappings_path = os.path.join(os.path.dirname(__file__), "sku_mappings.json")
    if os.path.exists(mappings_path):
        try:
            with open(mappings_path) as f:
                mappings = json.load(f)
            name_to_sku = mappings.get("name_to_sku", {})
            existing = {e["sku"] for cat in skus.values() for e in cat}
            for name, sku in name_to_sku.items():
                if sku not in existing:
                    entry = {"sku": sku, "name": name}
                    if sku.startswith("CH-"):
                        skus["cheese"].append(entry)
                    elif sku.startswith("MT-"):
                        skus["meat"].append(entry)
                    elif sku.startswith("AC-"):
                        skus["accompaniment"].append(entry)
        except Exception:
            pass

    for cat in skus.values():
        cat.sort(key=lambda x: x["sku"])

    return jsonify(skus)
