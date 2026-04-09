# /// script
# requires-python = ">=3.10"
# dependencies = ["flask", "pywebview"]
# ///

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

from pipeline.checkpoint_store import CheckpointStore
from pipeline.pipeline_state import PipelineState, PipelineStage
from pipeline.dry_run_guard import DryRunGuard, DryRunViolationError
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
    sync_order_to_shopify,
    SyncResult,
    _get_shopify_auth,
    _fetch_orders_by_tag,
    _lookup_zero_variant_gids,
)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

FULFILLMENT_APP_URL = "http://127.0.0.1:5187"


def _push_depletion_to_fulfillment(
    orders: list,
    ship_day: str,
    source: str = "matrix_commander",
) -> dict:
    """Fire-and-forget: push demand totals as depletion to fulfillment inventory journal.

    Only food SKUs (CH-/MT-/AC-) are sent. Never raises — returns status dict.
    """
    import requests as _requests

    demand = compute_demand(orders)
    food_skus = {
        sku: qty
        for sku, qty in demand.items()
        if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))
        and qty > 0
    }

    if not food_skus:
        return {"skipped": True, "reason": "no food SKUs in demand"}

    try:
        resp = _requests.post(
            f"{FULFILLMENT_APP_URL}/api/import_depletion_from_matrix",
            json={"ship_day": ship_day, "depletions": food_skus, "source": source},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"skipped": True, "reason": str(exc)}

# Session-scoped state (XLSX path, parsed orders, inventory — not pipeline stage)
SESSION_STATE = {
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

# Pipeline-stage state persisted to .pipeline/checkpoint.json
_checkpoint = CheckpointStore()

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
        SESSION_STATE["gift_path"] = str(save_path)
        return jsonify({"ok": True, "filename": filename, "type": "gift"})

    SESSION_STATE["xlsx_path"] = str(save_path)
    return jsonify({"ok": True, "filename": filename, "type": "main"})


@app.route("/api/validate", methods=["POST"])
def validate():
    """Run all validation checks on the uploaded XLSX."""
    if not SESSION_STATE["xlsx_path"]:
        return jsonify({"error": "No XLSX uploaded"}), 400

    body = request.get_json(silent=True) or {}
    SESSION_STATE["ship_day"] = body.get("ship_day", SESSION_STATE["ship_day"])
    SESSION_STATE["ship_date"] = body.get("ship_date", SESSION_STATE["ship_date"])

    try:
        orders, product_columns, unmapped = parse_matrix(SESSION_STATE["xlsx_path"])
        SESSION_STATE["orders"] = orders
        SESSION_STATE["product_columns"] = product_columns
        SESSION_STATE["unmapped"] = unmapped

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
        SESSION_STATE["validation_results"] = results

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
    if not SESSION_STATE["orders"]:
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

    SESSION_STATE["inventory"] = inventory
    demand = compute_demand(SESSION_STATE["orders"])
    SESSION_STATE["demand"] = demand
    shortages = find_shortages(demand, inventory)
    SESSION_STATE["shortages"] = shortages

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
    if not SESSION_STATE["xlsx_path"] or not SESSION_STATE["orders"]:
        return jsonify({"error": "No data loaded"}), 400

    body = request.get_json()
    short_sku = body.get("short_sku")
    replacement_sku = body.get("replacement_sku")
    qty = body.get("qty", 0)

    if not short_sku or not replacement_sku or qty <= 0:
        return jsonify({"error": "Invalid swap parameters"}), 400

    decision = SwapDecision(short_sku=short_sku, replacement_sku=replacement_sku, qty=qty)

    try:
        fixed_path = apply_swaps_to_xlsx(SESSION_STATE["xlsx_path"], [decision], SESSION_STATE["orders"])
        SESSION_STATE["xlsx_path"] = fixed_path

        # Re-parse and re-check
        orders, _, _ = parse_matrix(fixed_path)
        SESSION_STATE["orders"] = orders
        demand = compute_demand(orders)
        SESSION_STATE["demand"] = demand

        if SESSION_STATE["inventory"]:
            shortages = find_shortages(demand, SESSION_STATE["inventory"])
            SESSION_STATE["shortages"] = shortages

        return jsonify(
            {
                "ok": True,
                "fixed_path": fixed_path,
                "remaining_shortages": len(SESSION_STATE["shortages"]),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/finalize", methods=["POST"])
def finalize():
    """Merge gift sheet (if any) and finalize the XLSX."""
    if not SESSION_STATE["xlsx_path"]:
        return jsonify({"error": "No XLSX loaded"}), 400

    body = request.get_json(silent=True) or {}
    ship_day = body.get("ship_day", SESSION_STATE["ship_day"])
    ship_date = body.get("ship_date", SESSION_STATE["ship_date"])

    working_path = SESSION_STATE["xlsx_path"]

    try:
        # Merge gift sheet if provided
        if SESSION_STATE["gift_path"]:
            working_path = merge_gift_xlsx(working_path, SESSION_STATE["gift_path"])

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
    ship_day = body.get("ship_day", SESSION_STATE["ship_day"])
    ship_date = body.get("ship_date", SESSION_STATE["ship_date"])

    if not rmfg_tag:
        return jsonify({"error": "RMFG tag required (e.g. RMFG_20260328)"}), 400

    SESSION_STATE["ship_day"] = ship_day
    SESSION_STATE["ship_date"] = ship_date

    try:
        out_path = generate_matrix_xlsx(
            rmfg_tag,
            ship_day=ship_day,
            ship_date=ship_date,
            gift_path=SESSION_STATE["gift_path"],
            output_dir=str(UPLOAD_DIR),
        )

        if not out_path:
            return jsonify({"error": "Generation failed — check console"}), 500

        SESSION_STATE["xlsx_path"] = out_path

        # Auto-validate the generated file
        orders, product_columns, unmapped = parse_matrix(out_path)
        SESSION_STATE["orders"] = orders
        SESSION_STATE["product_columns"] = product_columns
        SESSION_STATE["unmapped"] = unmapped

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
        SESSION_STATE["validation_results"] = results
        regular, gift = identify_gift_orders(orders)

        # Auto-push depletion to fulfillment inventory journal
        depletion_result = _push_depletion_to_fulfillment(orders, ship_day)
        print(f"  [depletion] {depletion_result}")

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
                "depletion_pushed": depletion_result,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sync", methods=["POST"])
def sync_to_shopify():
    """Sync matrix assignments to Shopify orders via $0 variant order edits."""
    if not SESSION_STATE["orders"]:
        return jsonify({"error": "No orders loaded — run validate or generate first"}), 400

    body = request.get_json(silent=True) or {}
    rmfg_tag = body.get("rmfg_tag", "").strip()
    mode = body.get("mode", "smart")
    dry_run = body.get("dry_run", True)
    pass_number = body.get("pass_number", 1)

    if not rmfg_tag:
        return jsonify({"error": "rmfg_tag required (e.g. RMFG_20260328)"}), 400

    if mode not in ("smart", "conservative"):
        return jsonify({"error": "mode must be 'smart' or 'conservative'"}), 400

    # Validate pass_number (T-02-04)
    if pass_number not in (1, 2):
        return jsonify({"error": "pass_number must be 1 or 2"}), 400

    # Pass gate: Pass 2 blocked until PASS1_COMPLETE (D-07, SYNC-04)
    if pass_number == 2:
        checkpoint_state: PipelineState | None = CheckpointStore().load()
        if checkpoint_state is None or checkpoint_state.stage != PipelineStage.PASS1_COMPLETE:
            return jsonify(
                {
                    "error": "pass1_not_complete",
                    "message": "Run Pass 1 first and verify live orders before starting Pass 2.",
                }
            ), 409

    try:
        # Build matrix lookup: order_name -> {sku: qty}
        matrix_by_order: dict[str, dict[str, int]] = {}
        all_matrix_skus: set[str] = set()
        for row in SESSION_STATE["orders"]:
            order_name = str(row.get("order_id", row.get("Order", ""))).strip().replace("#", "")
            if not order_name:
                continue
            skus: dict[str, int] = {}
            for col, val in row.items():
                if isinstance(val, (int, float)) and val > 0 and isinstance(col, str):
                    if any(col.startswith(p) for p in ("CH-", "MT-", "AC-", "PK-", "TR-")):
                        skus[col] = int(val)
                        all_matrix_skus.add(col)
            matrix_by_order[order_name] = skus

        # Auth and Shopify data
        base, auth_headers = _get_shopify_auth()
        shopify_orders = _fetch_orders_by_tag(base, auth_headers, rmfg_tag)

        if not shopify_orders:
            return jsonify({"error": f"No unfulfilled Shopify orders found with tag '{rmfg_tag}'"}), 404

        # Look up $0 variant GIDs
        variant_gids = _lookup_zero_variant_gids(base, auth_headers, all_matrix_skus)

        # Match Shopify orders to matrix rows
        matched = []
        unmatched_shopify = []
        for so in shopify_orders:
            so_name = so["name"].replace("#", "")
            if so_name in matrix_by_order:
                matched.append((so, matrix_by_order[so_name]))
            else:
                unmatched_shopify.append(so_name)

        if dry_run:
            # Simulate without making changes
            counts = {"updated": 0, "skipped": 0, "gift": 0, "duplicate": 0, "error": 0}
            preview_details: list[dict] = []
            for so, m_skus in matched:
                result = _simulate_sync(so, m_skus, variant_gids, mode)
                counts[result.status] = counts.get(result.status, 0) + 1
                preview_details.append(
                    {
                        "order": result.order_name,
                        "status": result.status,
                        "added_skus": result.added_skus,
                        "error": result.error,
                    }
                )

            return jsonify(
                {
                    "ok": True,
                    "dry_run": True,
                    "matched": len(matched),
                    "unmatched": len(unmatched_shopify),
                    "counts": counts,
                    "details": preview_details[:100],
                    "variant_gids_found": len(variant_gids),
                    "variant_gids_missing": len(all_matrix_skus - set(variant_gids.keys())),
                }
            )

        # Enforce dry-run guard before any Shopify mutation (D-12, T-03-01)
        DryRunGuard(dry_run=dry_run).assert_can_mutate()

        # Live sync with ThreadPoolExecutor
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[SyncResult] = []
        errors_detail: list[dict] = []

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(sync_order_to_shopify, base, auth_headers, so, m_skus, variant_gids, mode): so["name"]
                for so, m_skus in matched
            }
            for future in as_completed(futures):
                order_label = futures[future]
                try:
                    r = future.result()
                    results.append(r)
                    if r.status == "error":
                        errors_detail.append({"order": r.order_name, "error": r.error})
                except Exception as exc:
                    results.append(SyncResult(order_label, "error", error=str(exc)))
                    errors_detail.append({"order": order_label, "error": str(exc)})

        counts = {"updated": 0, "skipped": 0, "gift": 0, "duplicate": 0, "error": 0}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1

        return jsonify(
            {
                "ok": True,
                "dry_run": False,
                "matched": len(matched),
                "unmatched": len(unmatched_shopify),
                "counts": counts,
                "errors": errors_detail,
                "total_synced": len(results),
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _simulate_sync(order: dict, matrix_skus: dict[str, int], variant_gids: dict[str, str], mode: str) -> SyncResult:
    """Dry-run simulation of sync logic (no Shopify API calls)."""
    order_name = order["name"].replace("#", "")
    tags_lower = order.get("tags", "").lower()

    if "gift redemption" in tags_lower:
        return SyncResult(order_name, "gift")

    current_skus: dict[str, int] = {}
    for li in order.get("line_items", []):
        sku = (li.get("sku") or "").strip()
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if sku and fq > 0:
            current_skus[sku] = current_skus.get(sku, 0) + fq

    to_add: list[str] = []
    duplicates: list[str] = []
    for sku in matrix_skus:
        if not any(sku.startswith(p) for p in ("CH-", "MT-", "AC-", "PK-", "TR-")):
            continue
        if sku in current_skus:
            duplicates.append(sku)
        elif sku in variant_gids:
            to_add.append(sku)

    if not to_add and not duplicates:
        return SyncResult(order_name, "skipped")

    if duplicates and mode == "conservative":
        return SyncResult(order_name, "duplicate", error=f"Dupes: {', '.join(duplicates)}")

    if not to_add:
        return SyncResult(order_name, "skipped")

    return SyncResult(order_name, "updated", added_skus=to_add)


@app.route("/api/state", methods=["GET"])
def get_state():
    """Return merged session + pipeline-stage state.

    Session fields (in-memory): xlsx, orders, shortages, ship info.
    Pipeline fields (from checkpoint.json): stage, dry_run, pass1, pass2.
    """
    pipeline_state = _checkpoint.load()
    return jsonify(
        {
            # Session-scoped fields
            "xlsx_loaded": SESSION_STATE["xlsx_path"] is not None,
            "xlsx_name": Path(SESSION_STATE["xlsx_path"]).name if SESSION_STATE["xlsx_path"] else None,
            "gift_loaded": SESSION_STATE["gift_path"] is not None,
            "gift_name": Path(SESSION_STATE["gift_path"]).name if SESSION_STATE["gift_path"] else None,
            "order_count": len(SESSION_STATE["orders"]),
            "shortage_count": len(SESSION_STATE["shortages"]),
            "ship_day": SESSION_STATE["ship_day"],
            "ship_date": SESSION_STATE["ship_date"],
            # Pipeline-stage fields from checkpoint.json (None → IDLE defaults)
            "pipeline_stage": pipeline_state.stage.name if pipeline_state else "IDLE",
            "dry_run": pipeline_state.dry_run if pipeline_state else True,
            "pass1": pipeline_state.pass1.to_dict()
            if pipeline_state
            else {"succeeded": [], "failed": [], "skipped": []},
            "pass2": pipeline_state.pass2.to_dict()
            if pipeline_state
            else {"succeeded": [], "failed": [], "skipped": []},
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
