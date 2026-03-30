"""
Matrix Commander Web — pywebview desktop app for fulfillment matrix management.

Flask SPA on port 5188, served inside pywebview with netfx backend.
Dark FUI theme (charcoal + cyan) matching the fulfillment web app.

Usage:
    python app.py              # pywebview mode
    python app.py --browser    # browser mode (http://localhost:5188)
"""

import json
import os
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# Add parent dir for matrix_commander imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from matrix_commander import (
    CheckResult,
    parse_matrix,
    compute_demand,
    find_shortages,
    load_inventory_csv,
    load_inventory_settings,
    load_mfg_translations,
    load_settings_config,
    check_numeric_order_ids,
    check_zip_leading_zeroes,
    check_duplicate_columns,
    check_production_day,
    check_sku_mappings,
    check_mfg_onboarding,
    check_cexec_cheese_counts,
    identify_gift_orders,
    merge_gift_xlsx,
    finalize_xlsx,
    apply_swaps_to_xlsx,
    generate_matrix_xlsx,
    check_parent_fill,
    SwapDecision,
    SKU_TO_NAME,
    SUBSTITUTION_FAMILIES,
)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

# Global state for current session
STATE = {
    "xlsx_path": None,
    "gift_path": None,
    "orders": [],
    "product_columns": [],
    "unmapped": {},
    "demand": {},
    "inventory": {},
    "shortages": [],
    "validation_results": [],
    "ship_day": "SAT",
    "ship_date": "",
}

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


# ── Routes ────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


@app.route("/api/upload", methods=["POST"])
def upload_file():
    """Upload main XLSX or gift XLSX."""
    file = request.files.get("file")
    file_type = request.form.get("type", "main")  # "main" or "gift"

    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    filename = file.filename
    save_path = UPLOAD_DIR / filename
    file.save(str(save_path))

    if file_type == "gift":
        STATE["gift_path"] = str(save_path)
        return jsonify({"ok": True, "filename": filename, "type": "gift"})

    STATE["xlsx_path"] = str(save_path)
    return jsonify({"ok": True, "filename": filename, "type": "main"})


@app.route("/api/validate", methods=["POST"])
def validate():
    """Run all validation checks on the uploaded XLSX."""
    if not STATE["xlsx_path"]:
        return jsonify({"error": "No XLSX uploaded"}), 400

    body = request.get_json(silent=True) or {}
    STATE["ship_day"] = body.get("ship_day", STATE["ship_day"])
    STATE["ship_date"] = body.get("ship_date", STATE["ship_date"])

    try:
        orders, product_columns, unmapped = parse_matrix(STATE["xlsx_path"])
        STATE["orders"] = orders
        STATE["product_columns"] = product_columns
        STATE["unmapped"] = unmapped

        settings = load_settings_config()
        cex_ec = settings.get("cex_ec", {})
        cexec_splits = settings.get("cexec_splits", {})
        mfg_translations = load_mfg_translations()

        results = [
            check_numeric_order_ids(orders),
            check_zip_leading_zeroes(orders),
            check_duplicate_columns(product_columns),
            check_production_day(orders),
            check_sku_mappings(unmapped),
            check_mfg_onboarding(orders, mfg_translations),
            check_cexec_cheese_counts(orders, cex_ec, cexec_splits),
        ]
        STATE["validation_results"] = results

        regular, gift = identify_gift_orders(orders)

        return jsonify(
            {
                "ok": True,
                "order_count": len(orders),
                "regular_count": len(regular),
                "gift_count": len(gift),
                "checks": [
                    {"name": r.name, "passed": r.passed, "message": r.message, "details": r.details[:10]}
                    for r in results
                ],
                "all_passed": all(r.passed for r in results),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventory", methods=["POST"])
def inventory_check():
    """Run inventory cross-check. Optionally upload inventory CSV."""
    if not STATE["orders"]:
        return jsonify({"error": "Run validate first"}), 400

    # Check for uploaded inventory CSV
    file = request.files.get("file")
    if file and file.filename:
        save_path = UPLOAD_DIR / file.filename
        file.save(str(save_path))
        inventory = load_inventory_csv(save_path)
    else:
        inventory = load_inventory_settings()

    if not inventory:
        return jsonify({"error": "No inventory data available"}), 400

    STATE["inventory"] = inventory
    demand = compute_demand(STATE["orders"])
    STATE["demand"] = demand
    shortages = find_shortages(demand, inventory)
    STATE["shortages"] = shortages

    # Build inventory table
    food_demand = {sku: qty for sku, qty in demand.items() if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))}
    table = []
    for sku in sorted(food_demand.keys()):
        qty = food_demand[sku]
        avail = int(inventory.get(sku, 0))
        net = avail - qty
        status = "SHORT" if net < 0 else ("LOW" if net < 20 else "OK")
        table.append(
            {
                "sku": sku,
                "name": SKU_TO_NAME.get(sku, "???"),
                "demand": qty,
                "available": avail,
                "net": net,
                "status": status,
            }
        )

    shortage_data = []
    for s in shortages:
        shortage_data.append(
            {
                "sku": s.sku,
                "name": s.product_name,
                "demand": s.demand,
                "available": s.available,
                "shortage": s.shortage,
                "family": s.family,
                "candidates": [
                    {"sku": alt, "name": SKU_TO_NAME.get(alt, alt), "surplus": surplus}
                    for alt, surplus in s.swap_candidates
                ],
            }
        )

    return jsonify(
        {
            "ok": True,
            "sku_count": len(food_demand),
            "inventory_count": len(inventory),
            "shortage_count": len(shortages),
            "table": table,
            "shortages": shortage_data,
        }
    )


@app.route("/api/swap", methods=["POST"])
def apply_swap():
    """Apply a single swap decision to the XLSX."""
    if not STATE["xlsx_path"] or not STATE["orders"]:
        return jsonify({"error": "No data loaded"}), 400

    body = request.get_json()
    short_sku = body.get("short_sku")
    replacement_sku = body.get("replacement_sku")
    qty = body.get("qty", 0)

    if not short_sku or not replacement_sku or qty <= 0:
        return jsonify({"error": "Invalid swap parameters"}), 400

    decision = SwapDecision(short_sku=short_sku, replacement_sku=replacement_sku, qty=qty)

    try:
        fixed_path = apply_swaps_to_xlsx(STATE["xlsx_path"], [decision], STATE["orders"])
        STATE["xlsx_path"] = fixed_path

        # Re-parse and re-check
        orders, _, _ = parse_matrix(fixed_path)
        STATE["orders"] = orders
        demand = compute_demand(orders)
        STATE["demand"] = demand

        if STATE["inventory"]:
            shortages = find_shortages(demand, STATE["inventory"])
            STATE["shortages"] = shortages

        return jsonify(
            {
                "ok": True,
                "fixed_path": fixed_path,
                "remaining_shortages": len(STATE["shortages"]),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/finalize", methods=["POST"])
def finalize():
    """Merge gift sheet (if any) and finalize the XLSX."""
    if not STATE["xlsx_path"]:
        return jsonify({"error": "No XLSX loaded"}), 400

    body = request.get_json(silent=True) or {}
    ship_day = body.get("ship_day", STATE["ship_day"])
    ship_date = body.get("ship_date", STATE["ship_date"])

    working_path = STATE["xlsx_path"]

    try:
        # Merge gift sheet if provided
        if STATE["gift_path"]:
            working_path = merge_gift_xlsx(working_path, STATE["gift_path"])

        # MFG validation
        mfg_translations = load_mfg_translations()
        if mfg_translations:
            final_orders, _, _ = parse_matrix(working_path)
            result = check_mfg_onboarding(final_orders, mfg_translations)
            if not result.passed:
                return jsonify(
                    {
                        "error": "MFG onboarding check failed",
                        "details": result.details[:20],
                    }
                ), 400

        # Finalize
        final_path = finalize_xlsx(working_path, ship_day=ship_day, ship_date=ship_date)

        return jsonify(
            {
                "ok": True,
                "final_path": final_path,
                "filename": Path(final_path).name,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def generate():
    """Generate RMFG matrix directly from Shopify orders (replaces RMFG portal)."""
    body = request.get_json(silent=True) or {}
    rmfg_tag = body.get("tag", "")
    ship_day = body.get("ship_day", STATE["ship_day"])
    ship_date = body.get("ship_date", STATE["ship_date"])

    if not rmfg_tag:
        return jsonify({"error": "RMFG tag required (e.g. RMFG_20260328)"}), 400

    STATE["ship_day"] = ship_day
    STATE["ship_date"] = ship_date

    try:
        out_path = generate_matrix_xlsx(
            rmfg_tag,
            ship_day=ship_day,
            ship_date=ship_date,
            gift_path=STATE["gift_path"],
            output_dir=str(UPLOAD_DIR),
        )

        if not out_path:
            return jsonify({"error": "Generation failed — check console"}), 500

        STATE["xlsx_path"] = out_path

        # Auto-validate the generated file
        orders, product_columns, unmapped = parse_matrix(out_path)
        STATE["orders"] = orders
        STATE["product_columns"] = product_columns
        STATE["unmapped"] = unmapped

        settings = load_settings_config()
        cex_ec = settings.get("cex_ec", {})
        cexec_splits = settings.get("cexec_splits", {})
        mfg_translations = load_mfg_translations()

        results = [
            check_numeric_order_ids(orders),
            check_zip_leading_zeroes(orders),
            check_duplicate_columns(product_columns),
            check_production_day(orders),
            check_sku_mappings(unmapped),
            check_mfg_onboarding(orders, mfg_translations),
            check_cexec_cheese_counts(orders, cex_ec, cexec_splits),
            check_parent_fill(orders),
        ]
        STATE["validation_results"] = results
        regular, gift = identify_gift_orders(orders)

        return jsonify(
            {
                "ok": True,
                "filename": Path(out_path).name,
                "order_count": len(orders),
                "regular_count": len(regular),
                "gift_count": len(gift),
                "checks": [
                    {"name": r.name, "passed": r.passed, "message": r.message, "details": r.details[:10]}
                    for r in results
                ],
                "all_passed": all(r.passed for r in results),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/state", methods=["GET"])
def get_state():
    """Return current session state summary."""
    return jsonify(
        {
            "xlsx_loaded": STATE["xlsx_path"] is not None,
            "xlsx_name": Path(STATE["xlsx_path"]).name if STATE["xlsx_path"] else None,
            "gift_loaded": STATE["gift_path"] is not None,
            "gift_name": Path(STATE["gift_path"]).name if STATE["gift_path"] else None,
            "order_count": len(STATE["orders"]),
            "shortage_count": len(STATE["shortages"]),
            "ship_day": STATE["ship_day"],
            "ship_date": STATE["ship_date"],
        }
    )


# ── Launch ────────────────────────────────────────────────────────────

PORT = 5188


def run_flask():
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def main():
    browser_mode = "--browser" in sys.argv

    if browser_mode:
        print(f"Matrix Commander Web running at http://localhost:{PORT}")
        app.run(host="127.0.0.1", port=PORT, debug=True, use_reloader=False)
    else:
        import webview

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        webview.create_window(
            "Matrix Commander",
            f"http://127.0.0.1:{PORT}",
            width=1400,
            height=900,
            min_size=(1000, 700),
        )
        webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
