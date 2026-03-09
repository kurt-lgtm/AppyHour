"""
Fulfillment Planner -- Flask + pywebview
Gamified weekly cheese fulfillment planning.
Shares settings JSON with inventory_reorder.py.
"""
from __future__ import annotations

import json
import os
import sys
import math
import datetime
import csv
import io
import threading
from collections import defaultdict
from flask import Flask, render_template, jsonify, request, send_file

# ── Constants ───────────────────────────────────────────────────────────

SETTINGS_FILE = "inventory_reorder_settings.json"
CURATION_ORDER = ["MONG", "MDT", "OWC", "SPN", "ALPT", "ISUN", "HHIGH"]
EXTRA_CURATIONS = ["NMS", "BYO", "SS"]
ALL_CURATIONS = CURATION_ORDER + EXTRA_CURATIONS
WHEEL_TO_SLICE_FACTOR = 2.67

# Global extra assignment slots (single SKU across all curations)
GLOBAL_EXTRA_SLOTS = {
    "EX-EC":  {"category": "Cheese",        "prefix": "CH-"},
    "CEX-EM": {"category": "Meat",          "prefix": "MT-"},
    "EX-EM":  {"category": "Meat",          "prefix": "MT-"},
    "CEX-EA": {"category": "Accompaniment", "prefix": "AC-"},
    "EX-EA":  {"category": "Accompaniment", "prefix": "AC-"},
}

# ── Helpers ──────────────────────────────────────────────────────────

def _inv_qty(data):
    """Extract qty from an inventory entry (dict or raw int)."""
    return data.get("qty", 0) if isinstance(data, dict) else int(data or 0)


# ── Settings persistence (shared with main app) ────────────────────────

def _get_app_dir():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dist = os.path.join(base, "dist", SETTINGS_FILE)
    if os.path.exists(dist):
        return os.path.join(base, "dist")
    return base


def load_settings():
    path = os.path.join(_get_app_dir(), SETTINGS_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data):
    path = os.path.join(_get_app_dir(), SETTINGS_FILE)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ── Constraint checking ────────────────────────────────────────────────

def check_constraint(curation, prcjam_cheese, cexec_cheese,
                     recipes, pr_cjam, cex_ec):
    if curation not in CURATION_ORDER:
        return "OK"
    idx = CURATION_ORDER.index(curation)
    nearby = set()
    for offset in [-2, -1, 1, 2]:
        ni = idx + offset
        if 0 <= ni < len(CURATION_ORDER):
            nb = CURATION_ORDER[ni]
            for item in recipes.get(nb, []):
                sku = item[0] if isinstance(item, (list, tuple)) else item
                if isinstance(sku, str) and sku.startswith("CH-"):
                    nearby.add(sku)
            n_pr = pr_cjam.get(nb, {})
            if isinstance(n_pr, dict) and n_pr.get("cheese"):
                nearby.add(n_pr["cheese"])
            n_ec = cex_ec.get(nb, "")
            if n_ec:
                nearby.add(n_ec)
    violations = []
    if prcjam_cheese and prcjam_cheese in nearby:
        violations.append("PR")
    if cexec_cheese and cexec_cheese in nearby:
        violations.append("EC")
    return "CONFLICT: " + "+".join(violations) if violations else "OK"


# ── Flask app ───────────────────────────────────────────────────────────

app = Flask(__name__)
STATE = {"saved": {}, "csv_demand": {}}


def _s():
    """Get current settings."""
    return STATE["saved"]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    """Return all data needed for the UI."""
    s = _s()
    return jsonify({
        "inventory": s.get("inventory", {}),
        "wheel_inventory": s.get("wheel_inventory", {}),
        "open_pos": s.get("open_pos", []),
        "pr_cjam": s.get("pr_cjam", {}),
        "cex_ec": s.get("cex_ec", {}),
        "cexec_splits": s.get("cexec_splits", {}),
        "curation_recipes": s.get("curation_recipes", {}),
        "recharge_queued": s.get("recharge_queued", {}),
        "recharge_queued_resolved": s.get("recharge_queued_resolved", {}),
        "shopify_api_demand": s.get("shopify_api_demand", {}),
        "manual_demand": s.get("manual_demand", {}),
        "vendor_catalog": s.get("vendor_catalog", {}),
        "global_extras": s.get("global_extras", {}),
        "curations": ALL_CURATIONS,
        "curation_order": CURATION_ORDER,
    })


@app.route("/api/calculate", methods=["POST"])
def calculate():
    s = _s()
    inventory = s.get("inventory", {})
    wheel_inv = s.get("wheel_inventory", {})
    open_pos = s.get("open_pos", [])
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})
    rq_resolved = s.get("recharge_queued_resolved", {})
    rq_raw = s.get("recharge_queued", {})
    shopify_demand = s.get("shopify_api_demand", {})
    manual = s.get("manual_demand", {})
    csv_demand = STATE.get("csv_demand", {})
    recipes = s.get("curation_recipes", {})

    # 1. Inventory snapshot
    inv = {}
    for sku, data in inventory.items():
        inv[sku] = data.get("qty", 0) if isinstance(data, dict) else int(data)

    for wsku, wd in wheel_inv.items():
        if isinstance(wd, dict):
            w = float(wd.get("weight_lbs", 0))
            c = int(wd.get("count", 0))
            t = wd.get("target_sku", "")
            if t and w > 0 and c > 0:
                inv[t] = inv.get(t, 0) + int(w * c * WHEEL_TO_SLICE_FACTOR)

    for po in open_pos:
        sku = po.get("sku", "")
        if sku and po.get("status", "Open") == "Open":
            try:
                inv[sku] = inv.get(sku, 0) + int(float(po.get("qty", 0)))
            except (ValueError, TypeError):
                pass

    # 2. Demand
    d_direct = defaultdict(int)
    d_prcjam = defaultdict(int)
    d_cexec = defaultdict(int)
    d_exec = defaultdict(int)

    # Recharge resolved
    for month, data in rq_resolved.items():
        for suffix, count in data.get("pr_cjam", {}).items():
            info = pr_cjam.get(suffix, {})
            ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
            if ch:
                d_prcjam[ch] += int(count)

        for suffix, count in data.get("cex_ec", {}).items():
            sp = splits.get(suffix, {})
            if sp:
                total = int(count)
                rem = total
                items = list(sp.items())
                for i, (sk, ratio) in enumerate(items):
                    if i == len(items) - 1:
                        d_cexec[sk] += rem
                    else:
                        portion = int(total * float(ratio))
                        d_cexec[sk] += portion
                        rem -= portion
            else:
                ch = cex_ec.get(suffix, "")
                if ch:
                    d_cexec[ch] += int(count)

    # Recharge raw
    ge = s.get("global_extras", {})
    for month, skus in rq_raw.items():
        for sku, qty in skus.items():
            upper = sku.upper()
            if sku.startswith("CH-"):
                d_direct[sku] += int(qty)
            elif sku.startswith("EX-EC-"):
                suffix = sku.split("-", 2)[2] if sku.count("-") >= 2 else ""
                ch = cex_ec.get(suffix, "")
                if ch:
                    d_exec[ch] += int(qty)
            else:
                ge_resolved = ge.get(upper)
                if ge_resolved:
                    d_direct[ge_resolved] += int(qty)

    # Shopify
    for sku, qty in shopify_demand.items():
        if sku.startswith("CH-"):
            d_direct[sku] += int(qty)

    # CSV
    for sku, qty in csv_demand.items():
        if sku.startswith("CH-"):
            d_direct[sku] += int(qty)

    # Manual
    for sku, qty in manual.items():
        if sku.startswith("CH-"):
            d_direct[sku] += int(qty)

    # 3. Results
    all_ch = set()
    all_ch.update(k for k in inv if k.startswith("CH-"))
    all_ch.update(d_direct.keys(), d_prcjam.keys(),
                  d_cexec.keys(), d_exec.keys())

    results = []
    shortage_count = 0
    for sku in sorted(all_ch):
        avail = inv.get(sku, 0)
        dd = d_direct.get(sku, 0)
        dp = d_prcjam.get(sku, 0)
        dc = d_cexec.get(sku, 0)
        de = d_exec.get(sku, 0)
        total = dd + dp + dc + de
        net = avail - total

        if total == 0:
            status = "NO DEMAND"
        elif net < 0:
            status = "SHORTAGE"
            shortage_count += 1
        elif net < total * 0.2:
            status = "TIGHT"
        elif net > avail * 0.5 and avail > 200:
            status = "SURPLUS"
        else:
            status = "OK"

        results.append({
            "sku": sku, "available": avail,
            "direct": dd, "prcjam": dp, "cexec": dc, "exec": de,
            "total_demand": total, "net": net, "status": status,
        })

    status_order = {"SHORTAGE": 0, "TIGHT": 1, "OK": 2,
                    "SURPLUS": 3, "NO DEMAND": 4}
    results.sort(key=lambda r: (status_order.get(r["status"], 9), r["net"]))

    # Assignment demands per curation
    assign_demands = {}
    for cur in ALL_CURATIONS:
        pr_qty = sum(int(md.get("pr_cjam", {}).get(cur, 0))
                     for md in rq_resolved.values())
        ec_qty = sum(int(md.get("cex_ec", {}).get(cur, 0))
                     for md in rq_resolved.values())
        assign_demands[cur] = {"pr_qty": pr_qty, "ec_qty": ec_qty}

    # Shelf life
    today = datetime.date.today()
    shelf_items = []
    for sku, data in inventory.items():
        if not sku.startswith("CH-") or not isinstance(data, dict):
            continue
        dates = data.get("expiration_dates", [])
        if not dates:
            continue
        try:
            earliest = datetime.date.fromisoformat(dates[0])
        except (ValueError, IndexError):
            continue
        days = (earliest - today).days
        qty = data.get("qty", 0)
        if days <= 14 and qty > 0:
            shelf_items.append({
                "sku": sku, "days_left": days, "qty": qty,
                "action": "EXPIRED" if days < 0 else
                          "USE NOW" if days <= 7 else "Prioritize",
            })

    # Multi-week projections (weeks 2, 3, 4)
    weeks = []
    prev = {r["sku"]: r for r in results}
    for week_num in range(2, 6):
        week_results = []
        week_shortages = 0
        for r in results:
            sku = r["sku"]
            if not sku.startswith("CH-"):
                continue
            p = prev.get(sku, r)
            carry = max(0, p.get("proj_net", p["net"]))
            demand = r["total_demand"]
            proj_net = carry - demand
            if demand == 0:
                status = "NO DEMAND"
            elif proj_net < 0:
                status = "PLAN PO"
                week_shortages += 1
            elif proj_net < demand * 0.3:
                status = "TIGHT"
            else:
                status = "OK"
            entry = {
                "sku": sku, "carry_fwd": carry,
                "demand": demand, "net": proj_net,
                "status": status, "proj_net": proj_net,
            }
            week_results.append(entry)

        weeks.append({
            "week": week_num,
            "results": week_results,
            "shortages": week_shortages,
        })
        prev = {r["sku"]: r for r in week_results}

    return jsonify({
        "results": results,
        "shortages": shortage_count,
        "total_skus": len([r for r in results if r["total_demand"] > 0]),
        "total_units": sum(r["total_demand"] for r in results),
        "assign_demands": assign_demands,
        "shelf_life": shelf_items,
        "weeks": weeks,
    })


@app.route("/api/assignments")
def get_assignments():
    s = _s()
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})
    recipes = s.get("curation_recipes", {})

    rq_resolved = s.get("recharge_queued_resolved", {})

    rows = []
    for cur in ALL_CURATIONS:
        info = pr_cjam.get(cur, {})
        pr_ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
        ec_ch = cex_ec.get(cur, "")
        sp = splits.get(cur, {})
        split_text = (" / ".join(f"{int(float(v)*100)}% {k}"
                                 for k, v in sp.items()) if sp else "")
        constraint = check_constraint(cur, pr_ch, ec_ch, recipes, pr_cjam, cex_ec)
        pr_qty = sum(int(md.get("pr_cjam", {}).get(cur, 0))
                     for md in rq_resolved.values())
        ec_qty = sum(int(md.get("cex_ec", {}).get(cur, 0))
                     for md in rq_resolved.values())
        rows.append({
            "curation": cur, "prcjam_cheese": pr_ch,
            "cexec_cheese": ec_ch, "split": split_text,
            "constraint": constraint,
            "pr_qty": pr_qty, "ec_qty": ec_qty,
        })
    return jsonify(rows)


@app.route("/api/assign", methods=["POST"])
def set_assignment():
    data = request.json
    cur = data["curation"]
    slot = data["slot"]
    cheese = data["cheese"]
    s = _s()

    recipes = s.get("curation_recipes", {})
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})

    # Validate constraint
    test_pr = cheese if slot == "prcjam" else (
        pr_cjam.get(cur, {}).get("cheese", "")
        if isinstance(pr_cjam.get(cur), dict) else "")
    test_ec = cheese if slot == "cexec" else cex_ec.get(cur, "")
    constraint = check_constraint(cur, test_pr, test_ec, recipes, pr_cjam, cex_ec)

    if constraint != "OK":
        return jsonify({"ok": False, "error": constraint}), 400

    if slot == "prcjam":
        if isinstance(pr_cjam.get(cur), dict):
            pr_cjam[cur]["cheese"] = cheese
        else:
            pr_cjam[cur] = {"cheese": cheese, "jam": ""}
        s["pr_cjam"] = pr_cjam
    else:
        cex_ec[cur] = cheese
        s["cex_ec"] = cex_ec

    save_settings(s)
    return jsonify({"ok": True, "constraint": constraint})


@app.route("/api/candidates/<curation>/<slot>")
def get_candidates(curation, slot):
    s = _s()
    inventory = s.get("inventory", {})
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    recipes = s.get("curation_recipes", {})

    candidates = []
    for sku, data in inventory.items():
        if not sku.startswith("CH-"):
            continue
        qty = data.get("qty", 0) if isinstance(data, dict) else 0
        if qty <= 0:
            continue
        test_pr = sku if slot == "prcjam" else (
            pr_cjam.get(curation, {}).get("cheese", "")
            if isinstance(pr_cjam.get(curation), dict) else "")
        test_ec = sku if slot == "cexec" else cex_ec.get(curation, "")
        c = check_constraint(curation, test_pr, test_ec, recipes, pr_cjam, cex_ec)
        candidates.append({"sku": sku, "qty": qty, "constraint": c})

    candidates.sort(key=lambda x: (0 if x["constraint"] == "OK" else 1, -x["qty"]))
    return jsonify(candidates)


@app.route("/api/auto_assign", methods=["POST"])
def auto_assign():
    s = _s()
    inventory = s.get("inventory", {})
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    recipes = s.get("curation_recipes", {})
    rq = s.get("recharge_queued_resolved", {})

    headroom = {}
    for sku, data in inventory.items():
        if sku.startswith("CH-"):
            headroom[sku] = data.get("qty", 0) if isinstance(data, dict) else 0

    consumed = defaultdict(int)
    new_pr = {}
    new_ec = {}
    changes = []
    used = set()

    for cur in ALL_CURATIONS:
        est = sum(int(md.get("pr_cjam", {}).get(cur, 0)) for md in rq.values())
        if est == 0:
            est = 50
        cands = [(sk, q - consumed.get(sk, 0))
                 for sk, q in headroom.items()
                 if q - consumed.get(sk, 0) >= est and sk not in used
                 and check_constraint(cur, sk, "", recipes, pr_cjam, cex_ec) == "OK"]
        cands.sort(key=lambda x: -x[1])
        if cands:
            best = cands[0][0]
            old = pr_cjam.get(cur, {}).get("cheese", "") if isinstance(pr_cjam.get(cur), dict) else ""
            new_pr[cur] = {"cheese": best, "jam": ""}
            consumed[best] += est
            used.add(best)
            if best != old:
                changes.append(f"PR-CJAM-{cur}: {old} -> {best}")
        else:
            new_pr[cur] = pr_cjam.get(cur, {"cheese": "", "jam": ""})

    for cur in ALL_CURATIONS:
        est = sum(int(md.get("cex_ec", {}).get(cur, 0)) for md in rq.values())
        if est == 0:
            est = 20
        pr_ch = new_pr.get(cur, {}).get("cheese", "")
        cands = [(sk, q - consumed.get(sk, 0))
                 for sk, q in headroom.items()
                 if q - consumed.get(sk, 0) >= est and sk != pr_ch
                 and check_constraint(cur, pr_ch, sk, recipes, pr_cjam, cex_ec) == "OK"]
        cands.sort(key=lambda x: -x[1])
        if cands:
            best = cands[0][0]
            old = cex_ec.get(cur, "")
            new_ec[cur] = best
            consumed[best] += est
            if best != old:
                changes.append(f"CEX-EC-{cur}: {old} -> {best}")
        else:
            new_ec[cur] = cex_ec.get(cur, "")

    if changes:
        s["pr_cjam"] = new_pr
        s["cex_ec"] = new_ec
        save_settings(s)

    return jsonify({"changes": changes, "count": len(changes)})


# ── Global Extras ─────────────────────────────────────────────────────

@app.route("/api/global_extras")
def get_global_extras():
    """Return current global extra assignments with on-hand qty."""
    s = _s()
    ge = s.get("global_extras", {})
    inventory = s.get("inventory", {})
    result = {}
    for slot, meta in GLOBAL_EXTRA_SLOTS.items():
        sku = ge.get(slot, "")
        qty = 0
        if sku:
            qty = _inv_qty(inventory.get(sku, {}))
        result[slot] = {"sku": sku, "qty": qty,
                        "category": meta["category"], "prefix": meta["prefix"]}
    return jsonify(result)


@app.route("/api/set_global_extra", methods=["POST"])
def set_global_extra():
    """Set a single global extra assignment."""
    data = request.json
    slot = data.get("slot", "")
    sku = data.get("sku", "")
    if slot not in GLOBAL_EXTRA_SLOTS:
        return jsonify({"ok": False, "error": f"Unknown slot: {slot}"}), 400
    s = _s()
    if sku:
        inventory = s.get("inventory", {})
        if sku not in inventory:
            return jsonify({"ok": False, "error": f"SKU {sku} not in inventory"}), 400
    ge = s.get("global_extras", {})
    ge[slot] = sku
    s["global_extras"] = ge
    save_settings(s)
    return jsonify({"ok": True})


@app.route("/api/global_extra_candidates/<slot>")
def get_global_extra_candidates(slot):
    """Return candidate SKUs for a global extra slot, filtered by type."""
    if slot not in GLOBAL_EXTRA_SLOTS:
        return jsonify({"error": f"Unknown slot: {slot}"}), 400
    prefix = GLOBAL_EXTRA_SLOTS[slot]["prefix"]
    s = _s()
    inventory = s.get("inventory", {})
    candidates = []
    for sku, data in inventory.items():
        if not sku.startswith(prefix):
            continue
        qty = _inv_qty(data)
        if qty <= 0:
            continue
        name = data.get("name", "") if isinstance(data, dict) else ""
        candidates.append({"sku": sku, "name": name, "qty": qty})
    candidates.sort(key=lambda x: -x["qty"])
    return jsonify(candidates)


@app.route("/api/auto_assign_extras", methods=["POST"])
def auto_assign_extras():
    """Auto-assign all 5 global extra slots using highest-qty SKU of correct type."""
    s = _s()
    inventory = s.get("inventory", {})
    ge = s.get("global_extras", {})
    changes = []

    # Group slots by prefix to avoid picking same SKU for both slots of same type
    prefix_groups = defaultdict(list)
    for slot, meta in GLOBAL_EXTRA_SLOTS.items():
        prefix_groups[meta["prefix"]].append(slot)

    used_by_prefix = defaultdict(set)
    for prefix, slots in prefix_groups.items():
        # Build candidate list sorted by qty desc
        cands = []
        for sku, data in inventory.items():
            if not sku.startswith(prefix):
                continue
            qty = _inv_qty(data)
            if qty > 0:
                cands.append((sku, qty))
        cands.sort(key=lambda x: -x[1])

        for slot in slots:
            old = ge.get(slot, "")
            best = ""
            for sku, qty in cands:
                if sku not in used_by_prefix[prefix]:
                    best = sku
                    break
            if best:
                ge[slot] = best
                used_by_prefix[prefix].add(best)
                if best != old:
                    changes.append(f"{slot}: {old or '(empty)'} -> {best}")
            else:
                ge[slot] = old

    s["global_extras"] = ge
    save_settings(s)
    return jsonify({"changes": changes, "count": len(changes)})


@app.route("/api/suggest_fixes")
def suggest_fixes():
    # Trigger a calculate first
    s = _s()
    wheel_inv = s.get("wheel_inventory", {})
    open_pos = s.get("open_pos", [])

    # We need results - recalculate inline
    calc_resp = calculate()
    calc_data = calc_resp.get_json()

    suggestions = []
    for r in calc_data["results"]:
        if r["status"] != "SHORTAGE":
            continue
        deficit = abs(r["net"])
        fixes = []
        for wsku, wd in wheel_inv.items():
            if isinstance(wd, dict) and wd.get("target_sku") == r["sku"]:
                w = float(wd.get("weight_lbs", 0))
                c = int(wd.get("count", 0))
                p = int(w * c * WHEEL_TO_SLICE_FACTOR)
                if p > 0:
                    fixes.append(f"MFG: Cut {wsku} ({c} wheels = ~{p} units)")
        for po in open_pos:
            if po.get("sku") == r["sku"] and po.get("status") == "Open":
                fixes.append(f"PO: {po.get('qty','?')} units ETA {po.get('eta','?')}")
        if not fixes:
            fixes.append("Recipe swap, partial sub, or Wednesday PO")
        suggestions.append({"sku": r["sku"], "deficit": deficit, "fixes": fixes})

    return jsonify(suggestions)


@app.route("/api/variety_check")
def variety_check():
    s = _s()
    recipes = s.get("curation_recipes", {})
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})

    cur_cheeses = {}
    for cur in CURATION_ORDER:
        ch = set()
        for item in recipes.get(cur, []):
            sku = item[0] if isinstance(item, (list, tuple)) else item
            if isinstance(sku, str) and sku.startswith("CH-"):
                ch.add(sku)
        info = pr_cjam.get(cur, {})
        pr_ch = info.get("cheese", "") if isinstance(info, dict) else ""
        if pr_ch:
            ch.add(pr_ch)
        ec_ch = cex_ec.get(cur, "")
        if ec_ch:
            ch.add(ec_ch)
        for sk in splits.get(cur, {}):
            ch.add(sk)
        cur_cheeses[cur] = ch

    issues = []
    for i, cur in enumerate(CURATION_ORDER):
        for j in range(i + 1, min(i + 3, len(CURATION_ORDER))):
            nb = CURATION_ORDER[j]
            overlap = cur_cheeses.get(cur, set()) & cur_cheeses.get(nb, set())
            for sku in overlap:
                issues.append(f"{sku} in {cur} and {nb} ({j-i} apart)")

    pr_map = defaultdict(list)
    for cur in ALL_CURATIONS:
        info = pr_cjam.get(cur, {})
        ch = info.get("cheese", "") if isinstance(info, dict) else ""
        if ch:
            pr_map[ch].append(cur)
    for ch, curs in pr_map.items():
        if len(curs) > 1:
            issues.append(f"PR-CJAM duplicate: {ch} in {', '.join(curs)}")

    return jsonify(issues)


@app.route("/api/wed_po")
def wed_po():
    """Generate Wednesday PO lines. Uses RMFG data if loaded, else legacy."""
    inv = STATE.get("rmfg_inventory")
    sat_demand = STATE.get("rmfg_sat_demand")
    s = _s()
    vendor_catalog = s.get("vendor_catalog", {})

    # Use RMFG data if available
    if inv and sat_demand:
        all_ch = set(k for k in inv if k.startswith("CH-"))
        all_ch.update(k for k in sat_demand if k.startswith("CH-"))
        shortage_rows = []
        for sku in all_ch:
            avail = inv.get(sku, 0)
            demand = int(round(sat_demand.get(sku, 0)))
            net = avail - demand
            if net < 0:
                shortage_rows.append({"sku": sku, "net": net, "status": "SHORTAGE"})
    else:
        calc_resp = calculate()
        calc_data = calc_resp.get_json()
        shortage_rows = [r for r in calc_data["results"] if r["status"] == "SHORTAGE"]

    lines = []
    for r in shortage_rows:
        deficit = abs(r["net"])
        buf = max(deficit, int(deficit * 1.2))
        vi = vendor_catalog.get(r["sku"], {})
        cq = int(vi.get("case_qty", 1)) or 1
        cases = math.ceil(buf / cq)
        lines.append({
            "sku": r["sku"], "deficit": deficit,
            "order_qty": cases * cq, "cases": cases,
            "case_qty": cq, "vendor": vi.get("vendor", "?"),
        })
    lines.sort(key=lambda x: -x["deficit"])
    return jsonify(lines)


@app.route("/api/order_list")
def order_list():
    """Consolidated order list grouped by product type, not vendor."""
    inv = STATE.get("rmfg_inventory", {})
    s = _s()
    vendor_catalog = s.get("vendor_catalog", {})
    wheel_inv = s.get("wheel_inventory", {})
    open_pos = s.get("open_pos", [])
    inventory = s.get("inventory", {})

    # Gather all results across weeks
    all_shortages = {}  # sku -> total deficit

    # Check multi-week data
    for key in ["rmfg_sat_demand", "rmfg_tue_demand", "rmfg_nsat_demand"]:
        demand = STATE.get(key, {})
        for sku, qty in demand.items():
            q = int(round(qty))
            if q > 0:
                if sku not in all_shortages:
                    all_shortages[sku] = {"demand": 0, "avail": 0}
                all_shortages[sku]["demand"] += q

    # Compute availability from inventory
    for sku in all_shortages:
        avail = inv.get(sku, 0)
        if avail == 0:
            inv_entry = inventory.get(sku, {})
            avail = int(inv_entry.get("qty", 0)) if isinstance(inv_entry, dict) else int(inv_entry or 0)
        # Add wheel supply
        for wsku, wd in wheel_inv.items():
            if isinstance(wd, dict) and wd.get("target_sku") == sku:
                w = float(wd.get("weight_lbs", 0))
                c = int(wd.get("count", 0))
                avail += int(w * c * WHEEL_TO_SLICE_FACTOR)
        # Add open POs
        for po in open_pos:
            if po.get("sku") == sku and po.get("status", "").lower() in ("open", "ordered"):
                avail += int(po.get("qty", 0))
        all_shortages[sku]["avail"] = avail

    # Build order lines for items with net < 0
    lines = []
    for sku, d in all_shortages.items():
        net = d["avail"] - d["demand"]
        if net >= 0:
            continue
        deficit = abs(net)
        # Classify by type
        if sku.startswith("CH-"):
            category = "Cheese"
        elif sku.startswith("AC-"):
            category = "Accompaniment"
        elif sku.startswith("MT-"):
            category = "Meat"
        elif sku.startswith("PR-") or sku.startswith("CEX-"):
            category = "Pairing"
        else:
            category = "Other"

        vi = vendor_catalog.get(sku, {})
        cq = int(vi.get("case_qty", 1)) or 1
        buf = max(deficit, int(deficit * 1.2))
        cases = math.ceil(buf / cq)
        sku_name = ""
        inv_entry = inventory.get(sku, {})
        if isinstance(inv_entry, dict):
            sku_name = inv_entry.get("name", "")

        lines.append({
            "sku": sku,
            "name": sku_name,
            "category": category,
            "demand": d["demand"],
            "avail": d["avail"],
            "deficit": deficit,
            "order_qty": cases * cq,
            "cases": cases,
            "case_qty": cq,
            "vendor": vi.get("vendor", ""),
            "unit_cost": vi.get("unit_cost", ""),
        })

    # Sort by category then deficit
    cat_order = {"Cheese": 0, "Accompaniment": 1, "Meat": 2, "Pairing": 3, "Other": 4}
    lines.sort(key=lambda x: (cat_order.get(x["category"], 9), -x["deficit"]))
    return jsonify(lines)


@app.route("/api/order_list_csv")
def order_list_csv():
    """Export consolidated order list as CSV."""
    resp = order_list()
    lines = resp.get_json()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Category", "SKU", "Name", "Demand", "Available", "Deficit",
                     "Order Qty", "Cases", "Case Qty", "Vendor", "Unit Cost"])
    for r in lines:
        writer.writerow([
            r["category"], r["sku"], r["name"], r["demand"], r["avail"],
            r["deficit"], r["order_qty"], r["cases"], r["case_qty"],
            r["vendor"], r["unit_cost"],
        ])

    buf = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    today = datetime.date.today().strftime("%Y%m%d")
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name=f"order_list_{today}.csv")


@app.route("/api/email_po", methods=["POST"])
def email_po():
    """Email PO lines to vendors via SMTP."""
    import smtplib
    from email.mime.text import MIMEText

    s = _s()
    smtp_host = s.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(s.get("smtp_port", 587))
    smtp_user = s.get("smtp_user", "")
    smtp_pass = s.get("smtp_password", "")
    from_addr = s.get("depletion_email_from", smtp_user)

    if not smtp_user or not smtp_pass:
        return jsonify({"error": "SMTP not configured. Set smtp_user and smtp_password in settings."})

    data = request.json or {}
    to_addr = data.get("to", "")
    subject = data.get("subject", f"Purchase Order - {datetime.date.today()}")
    body = data.get("body", "")

    if not to_addr:
        return jsonify({"error": "No recipient email provided"})

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return jsonify({"ok": True, "message": f"PO emailed to {to_addr}"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/import_csv", methods=["POST"])
def import_csv():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    f = request.files["file"]
    content = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    s = _s()
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})

    imported = defaultdict(int)
    rows = 0

    if "All SKUs" in headers:
        for row in reader:
            rows += 1
            for sku_raw in row.get("All SKUs", "").split(","):
                sku = sku_raw.strip().upper()
                if not sku:
                    continue
                if sku.startswith("CH-"):
                    imported[sku] += 1
                elif sku.startswith("PR-CJAM-"):
                    suffix = sku.split("PR-CJAM-", 1)[1]
                    info = pr_cjam.get(suffix, {})
                    ch = info.get("cheese", "") if isinstance(info, dict) else ""
                    if ch:
                        imported[ch] += 1
                elif sku.startswith("CEX-EC-"):
                    suffix = sku.split("CEX-EC-", 1)[1]
                    sp = splits.get(suffix, {})
                    if sp:
                        for sk, ratio in sp.items():
                            imported[sk] += max(1, int(float(ratio)))
                    else:
                        ch = cex_ec.get(suffix, "")
                        if ch:
                            imported[ch] += 1
                elif sku.startswith("EX-EC-"):
                    suffix = sku.split("EX-EC-", 1)[1]
                    ch = cex_ec.get(suffix, "")
                    if ch:
                        imported[ch] += 1

    elif "line_item_sku" in headers:
        for row in reader:
            rows += 1
            sku = (row.get("line_item_sku") or "").strip().upper()
            try:
                qty = int(float(row.get("line_item_quantity", 1)))
            except (ValueError, TypeError):
                qty = 1
            if sku.startswith("CH-"):
                imported[sku] += qty
            elif sku.startswith("PR-CJAM-"):
                suffix = sku.split("PR-CJAM-", 1)[1]
                info = pr_cjam.get(suffix, {})
                ch = info.get("cheese", "") if isinstance(info, dict) else ""
                if ch:
                    imported[ch] += qty
            elif sku.startswith("CEX-EC-"):
                suffix = sku.split("CEX-EC-", 1)[1]
                ch = cex_ec.get(suffix, "")
                if ch:
                    imported[ch] += qty
    else:
        return jsonify({"error": f"Unrecognized CSV format. Headers: {headers}"}), 400

    STATE["csv_demand"] = dict(imported)
    return jsonify({
        "rows": rows,
        "skus": len([k for k in imported if k.startswith("CH-")]),
        "units": sum(imported.values()),
    })


@app.route("/api/export_csv")
def export_csv():
    """Export NET report as CSV. Uses RMFG data if loaded."""
    inv = STATE.get("rmfg_inventory")
    if inv:
        with app.test_request_context(json={}):
            calc_resp = calculate_rmfg()
            data = calc_resp.get_json() if hasattr(calc_resp, 'get_json') \
                else json.loads(calc_resp.data)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["SKU", "Available", "Sat Demand", "NET Sat",
                         "Tue Demand", "NET Tue", "Next Sat Demand",
                         "NET Final", "Total Demand", "Status"])
        for r in data["results"]:
            writer.writerow([
                r["sku"], r["available"],
                r.get("sat_demand", 0), r.get("net_sat", r["net"]),
                r.get("tue_demand", 0), r.get("net_tue", 0),
                r.get("next_sat_demand", 0), r.get("net_final", 0),
                r["total_demand"], r["status"],
            ])
    else:
        calc_resp = calculate()
        data = calc_resp.get_json()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["SKU", "Available", "Direct", "PRCJAM", "CEXEC",
                         "EXEC", "Total Demand", "NET", "Status"])
        for r in data["results"]:
            writer.writerow([r["sku"], r["available"], r["direct"],
                             r["prcjam"], r["cexec"], r["exec"],
                             r["total_demand"], r["net"], r["status"]])

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                     download_name=f"fulfillment_net_{ts}.csv")


@app.route("/api/split", methods=["POST"])
def set_split():
    data = request.json
    cur = data["curation"]
    splits = data.get("splits", {})
    s = _s()
    if not s.get("cexec_splits"):
        s["cexec_splits"] = {}
    if splits:
        s["cexec_splits"][cur] = splits
    else:
        s["cexec_splits"].pop(cur, None)
    save_settings(s)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════
#  AUTOMATION: Full RMFG folder loading + multi-window demand + substitutions
# ══════════════════════════════════════════════════════════════════════════

# ── SKU helpers (from weekly_demand_report.py) ─────────────────────────

EQUIV = {"CH-BRIE": "CH-EBRIE"}
SKIP_PREFIXES = ("AHB-", "BL-", "PK-", "TR-", "EX-")
KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
}
_MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS", "AHB-BVAL",
                     "AHB-MCUST-MS", "AHB-MCUST-NMS"}


def normalize_sku(sku):
    return EQUIV.get(sku, sku)


def resolve_curation_from_box_sku(sku):
    if not sku:
        return None
    sku = sku.strip().upper()
    if sku in _MONTHLY_PATTERNS:
        return "MONTHLY"
    if "MCUST-NMS" in sku:
        return "NMS"
    if "MCUST-MS" in sku or "CUR-MS" in sku or "BVAL" in sku:
        return "MS"
    for cur in KNOWN_CURATIONS:
        if cur in sku:
            return cur
    return None


def is_pickable(sku):
    upper = sku.upper()
    if any(upper.startswith(p) for p in SKIP_PREFIXES):
        return False
    if upper.startswith("PR-CJAM"):
        return False
    if upper.startswith("CEX-E"):
        return False
    if upper == "CEX-EM":
        return False
    return bool(sku.strip())


def resolve_pr_cjam(suffix):
    s = _s()
    pr_cjam = s.get("pr_cjam", {})
    info = pr_cjam.get(suffix, {})
    ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
    return {ch: 1} if ch else {}


def resolve_cex_ec(suffix):
    s = _s()
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})
    sp = splits.get(suffix, {})
    if sp:
        return dict(sp)  # fractional ratios, accumulated and rounded later
    ch = cex_ec.get(suffix, "")
    return {ch: 1} if ch else {}


# ── File detection ────────────────────────────────────────────────────

def detect_rmfg_files(folder):
    """Auto-detect RMFG data files in a folder."""
    files = os.listdir(folder)
    result = {
        "template_check": None,
        "product_inventory": None,
        "order_dashboard": None,
        "charges_queued": None,
        "march_charges": None,
    }
    for f in files:
        fl = f.lower()
        fp = os.path.join(folder, f)
        if not f.endswith(".csv"):
            continue
        if "template check" in fl or "template_check" in fl:
            result["template_check"] = fp
        elif "product inventory" in fl or "product_inventory" in fl:
            result["product_inventory"] = fp
        elif "order-dashboard" in fl or "order_dashboard" in fl:
            result["order_dashboard"] = fp
        elif fl.startswith("charges_queued"):
            result["charges_queued"] = fp
        elif "march charges" in fl or "march_charges" in fl:
            result["march_charges"] = fp
    return result


# ── Inventory loading ─────────────────────────────────────────────────

def load_inventory_from_files(template_path, product_inv_path,
                              po_additions=None, incoming=None,
                              corrections=None):
    """Load inventory from Template Check + Product Inventory fallback."""
    inv = {}

    # Primary: Template Check
    if template_path and os.path.exists(template_path):
        with open(template_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) < 2:
                    continue
                sku = row[0].strip()
                if not sku:
                    continue
                try:
                    qty = int(float(row[1]))
                except ValueError:
                    qty = 0
                inv[sku] = qty

    # PO additions
    if po_additions:
        for sku, add_qty in po_additions.items():
            inv[sku] = inv.get(sku, 0) + add_qty

    # Incoming
    if incoming:
        for sku, add_qty in incoming.items():
            inv[sku] = inv.get(sku, 0) + add_qty

    # Corrections (override)
    if corrections:
        for sku, qty in corrections.items():
            inv[sku] = qty

    # Fallback: Product Inventory
    if product_inv_path and os.path.exists(product_inv_path):
        fallback = {}
        with open(product_inv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 7:
                    continue
                sku = row[1].strip()
                if not sku or sku == "Product SKU":
                    continue
                try:
                    qty = int(float(row[6]))
                except (ValueError, IndexError):
                    qty = 0
                if sku not in fallback:
                    fallback[sku] = qty
                else:
                    fallback[sku] += qty
        for sku, qty in fallback.items():
            if sku not in inv and qty > 0:
                inv[sku] = qty

    # Apply equivalences
    for old, new in EQUIV.items():
        if old in inv:
            inv[new] = inv.get(new, 0) + inv.pop(old)

    return inv


# ── Demand parsers ────────────────────────────────────────────────────

def parse_order_dashboard(path):
    """Parse Shopify order-dashboard. Returns demand dicts + counts."""
    all_demand = defaultdict(int)
    first_order_demand = defaultdict(int)
    prcjam_counts = defaultdict(int)
    cexec_counts = defaultdict(int)
    first_order_count = 0
    total_order_count = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_order_count += 1
            tags = row.get("Order Tags", "")
            is_first = "Subscription First Order" in tags
            if is_first:
                first_order_count += 1

            all_skus = row.get("All SKUs", "")
            skus = [s.strip() for s in all_skus.split(",") if s.strip()]

            for sku in skus:
                upper = sku.upper()

                if upper.startswith("PR-CJAM-"):
                    suffix = upper.split("PR-CJAM-", 1)[1]
                    prcjam_counts[suffix] += 1
                    resolved = resolve_pr_cjam(suffix)
                    for rsku, rqty in resolved.items():
                        rsku = normalize_sku(rsku)
                        all_demand[rsku] += rqty
                        if is_first:
                            first_order_demand[rsku] += rqty
                    continue

                if upper.startswith("CEX-EC-"):
                    suffix = upper.split("CEX-EC-", 1)[1]
                    cexec_counts[suffix] += 1
                    resolved = resolve_cex_ec(suffix)
                    for rsku, rqty in resolved.items():
                        rsku = normalize_sku(rsku)
                        all_demand[rsku] += rqty
                        if is_first:
                            first_order_demand[rsku] += rqty
                    continue

                if upper == "CEX-EC":
                    cexec_counts["BARE"] += 1
                    continue

                if not is_pickable(sku):
                    continue

                sku = normalize_sku(sku)
                all_demand[sku] += 1
                if is_first:
                    first_order_demand[sku] += 1

    return {
        "all_demand": dict(all_demand),
        "first_order_demand": dict(first_order_demand),
        "first_order_count": first_order_count,
        "total_orders": total_order_count,
        "prcjam_counts": dict(prcjam_counts),
        "cexec_counts": dict(cexec_counts),
    }


def parse_charges_queued(path, target_date=None):
    """Parse Recharge charges_queued CSV. Returns {sku: qty}."""
    demand = defaultdict(float)
    charges_by_id = defaultdict(list)
    bare_skipped = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scheduled = row.get("scheduled_at", "")
            if target_date and target_date not in scheduled:
                continue
            cid = row.get("charge_id", "")
            sku = row.get("line_item_sku", "").strip()
            try:
                qty = int(float(row.get("line_item_quantity", "1") or "1"))
            except ValueError:
                qty = 1
            if sku:
                charges_by_id[cid].append((sku, qty))

    for cid, items in charges_by_id.items():
        box_sku = None
        for sku, _ in items:
            if sku.upper().startswith("AHB-"):
                box_sku = sku.upper()
                break
        curation = resolve_curation_from_box_sku(box_sku)

        for sku, qty in items:
            upper = sku.upper()

            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                if suffix == "GEN":
                    if curation and curation not in ("MONTHLY", None):
                        resolved = resolve_pr_cjam(curation)
                    else:
                        continue
                else:
                    resolved = resolve_pr_cjam(suffix)
                for rsku, rqty in resolved.items():
                    demand[normalize_sku(rsku)] += rqty * qty
                continue

            if upper == "CEX-EC":
                bare_skipped += qty
                continue

            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                resolved = resolve_cex_ec(suffix)
                for rsku, rqty in resolved.items():
                    demand[normalize_sku(rsku)] += rqty * qty
                continue

            if not is_pickable(sku):
                continue

            demand[normalize_sku(sku)] += qty

    return {sku: int(round(q)) for sku, q in demand.items()}, bare_skipped


def parse_march_charges(path, start_day, end_day, year=2026, month=3):
    """Parse MARCH CHARGES for a date range. Returns {sku: qty}, charge_count."""
    demand = defaultdict(float)
    charges_by_id = defaultdict(list)
    bare_skipped = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scheduled = row.get("scheduled_at", "")
            date_part = scheduled.split(" ")[0]
            parts = date_part.split("/")
            if len(parts) != 3:
                continue
            try:
                m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue
            if not (y == year and m == month and start_day <= d <= end_day):
                continue

            cid = row.get("charge_id", "")
            sku = row.get("line_item_sku", "").strip()
            try:
                qty = int(float(row.get("line_item_quantity", "1") or "1"))
            except ValueError:
                qty = 1
            if sku:
                charges_by_id[cid].append((sku, qty))

    for cid, items in charges_by_id.items():
        box_sku = None
        for sku, _ in items:
            if sku.upper().startswith("AHB-"):
                box_sku = sku.upper()
                break
        curation = resolve_curation_from_box_sku(box_sku)

        has_pr_cjam = False
        for sku, qty in items:
            upper = sku.upper()

            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                has_pr_cjam = True
                if suffix == "GEN":
                    if curation and curation not in ("MONTHLY", None):
                        resolved = resolve_pr_cjam(curation)
                    else:
                        continue
                else:
                    resolved = resolve_pr_cjam(suffix)
                for rsku, rqty in resolved.items():
                    demand[normalize_sku(rsku)] += rqty * qty
                continue

            if upper == "CEX-EC":
                bare_skipped += qty
                continue

            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                resolved = resolve_cex_ec(suffix)
                for rsku, rqty in resolved.items():
                    demand[normalize_sku(rsku)] += rqty * qty
                continue

            if upper == "CEX-EM":
                continue

            if not is_pickable(sku):
                continue

            demand[normalize_sku(sku)] += qty

        if not has_pr_cjam and curation and curation not in ("MONTHLY", None):
            for rsku, rqty in resolve_pr_cjam(curation).items():
                demand[normalize_sku(rsku)] += rqty

    return {sku: int(round(q)) for sku, q in demand.items()}, len(charges_by_id)


# ── Load RMFG folder endpoint ─────────────────────────────────────────

@app.route("/api/load_rmfg", methods=["POST"])
def load_rmfg():
    """Load all data from an RMFG folder. Auto-detects files."""
    data = request.json or {}
    folder = data.get("folder", "")

    # Allow relative path from project root
    if not os.path.isabs(folder):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        folder = os.path.join(base, folder)

    if not os.path.isdir(folder):
        return jsonify({"error": f"Folder not found: {folder}"}), 400

    files = detect_rmfg_files(folder)
    log_lines = []
    warnings = []

    # PO/incoming/corrections from request (no hardcoded defaults)
    po_additions = data.get("po_additions", {})
    incoming = data.get("incoming", {})
    corrections = data.get("corrections", {})

    # 1. Load inventory
    inv = load_inventory_from_files(
        files["template_check"], files["product_inventory"],
        po_additions, incoming, corrections,
    )
    ch_count = sum(1 for k in inv if k.startswith("CH-"))
    log_lines.append(f"Inventory: {len(inv)} SKUs ({ch_count} cheese)")

    # 2. Parse Saturday demand (order dashboard + charges_queued)
    sat_demand = defaultdict(int)
    dashboard_info = {}
    if files["order_dashboard"]:
        dashboard_info = parse_order_dashboard(files["order_dashboard"])
        for sku, qty in dashboard_info["all_demand"].items():
            sat_demand[sku] += qty
        log_lines.append(
            f"Dashboard: {dashboard_info['total_orders']} orders "
            f"({dashboard_info['first_order_count']} first)"
        )
    else:
        warnings.append("No order-dashboard CSV found")

    charges_bare = 0
    if files["charges_queued"]:
        # Auto-detect target date from filename
        fname = os.path.basename(files["charges_queued"])
        # charges_queued-2026.03.06-... → target date 2026-03-07 (next day)
        # But actually we want all charges in the file if they're for Sat
        # Try without date filter first, then with
        queued, charges_bare = parse_charges_queued(files["charges_queued"])
        for sku, qty in queued.items():
            sat_demand[sku] += qty
        log_lines.append(f"Charges queued: {sum(queued.values())} items")
        if charges_bare:
            warnings.append(f"{charges_bare} bare CEX-EC skipped")
    else:
        warnings.append("No charges_queued CSV found")

    # 3. Tuesday demand (first orders × 3)
    tue_demand = {}
    if dashboard_info.get("first_order_demand"):
        tue_demand = {
            sku: qty * 3
            for sku, qty in dashboard_info["first_order_demand"].items()
        }
        log_lines.append(
            f"Tuesday estimate: {dashboard_info['first_order_count']} "
            f"first orders × 3"
        )

    # 4. Next Saturday demand (MARCH CHARGES)
    next_sat_demand = {}
    next_sat_charges = 0
    if files["march_charges"]:
        next_sat_demand, next_sat_charges = parse_march_charges(
            files["march_charges"], 8, 14
        )
        log_lines.append(
            f"Next Saturday: {next_sat_charges} charges (3/8-3/14)"
        )
    else:
        warnings.append("No MARCH CHARGES CSV found")

    # Store in state
    STATE["rmfg_inventory"] = inv
    STATE["rmfg_sat_demand"] = dict(sat_demand)
    STATE["rmfg_tue_demand"] = tue_demand
    STATE["rmfg_next_sat_demand"] = next_sat_demand
    STATE["rmfg_dashboard"] = dashboard_info
    STATE["rmfg_folder"] = folder
    STATE["rmfg_files"] = {k: os.path.basename(v) if v else None
                           for k, v in files.items()}

    return jsonify({
        "ok": True,
        "files": STATE["rmfg_files"],
        "log": log_lines,
        "warnings": warnings,
        "inventory_count": len(inv),
        "cheese_count": ch_count,
        "sat_skus": len(sat_demand),
        "sat_units": sum(sat_demand.values()),
    })


# ── Full calculate using RMFG data ────────────────────────────────────

@app.route("/api/calculate_rmfg", methods=["POST"])
def calculate_rmfg():
    """Calculate NET using loaded RMFG folder data (multi-window)."""
    inv = STATE.get("rmfg_inventory", {})
    sat_demand = STATE.get("rmfg_sat_demand", {})
    tue_demand = STATE.get("rmfg_tue_demand", {})
    next_sat_demand = STATE.get("rmfg_next_sat_demand", {})

    if not inv and not sat_demand:
        return jsonify({"error": "No RMFG data loaded. Use Load Folder first."}), 400

    # If we have inventory (e.g. from Dropbox) but no parsed demand,
    # build demand from settings (Recharge + Shopify + manual)
    if inv and not sat_demand:
        s = _s()
        rq_resolved = s.get("recharge_queued_resolved", {})
        shopify = s.get("shopify_api_demand", {})
        manual = s.get("manual_demand", {})
        pr_cjam = s.get("pr_cjam", {})
        cex_ec = s.get("cex_ec", {})
        splits = s.get("cexec_splits", {})
        ge = s.get("global_extras", {})

        # Build weekly demand from settings sources
        settings_demand = defaultdict(int)
        # Recharge queued (use latest month)
        for month, data in rq_resolved.items():
            for suffix, count in data.get("pr_cjam", {}).items():
                info = pr_cjam.get(suffix, {})
                ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
                if ch:
                    settings_demand[normalize_sku(ch)] += int(count)
            for suffix, count in data.get("cex_ec", {}).items():
                ec = cex_ec.get(suffix, "")
                if isinstance(ec, str) and ec:
                    settings_demand[normalize_sku(ec)] += int(count)
                elif isinstance(ec, dict):
                    for esku, epct in ec.items():
                        settings_demand[normalize_sku(esku)] += int(count * epct)
            for sku, qty in data.get("direct", {}).items():
                ge_resolved = ge.get(sku.upper())
                if ge_resolved:
                    settings_demand[normalize_sku(ge_resolved)] += int(qty)
                else:
                    settings_demand[normalize_sku(sku)] += int(qty)
        # Shopify API demand (weekly)
        for sku, qty in shopify.items():
            settings_demand[normalize_sku(sku)] += int(qty)
        # Manual demand
        for sku, qty in manual.items():
            settings_demand[normalize_sku(sku)] += int(qty)

        sat_demand = dict(settings_demand)
        # Estimate Tuesday as ~30% of Saturday
        tue_demand = {sku: max(1, int(q * 0.3))
                      for sku, q in sat_demand.items() if q > 0}
        next_sat_demand = dict(sat_demand)

    # Collect all CH-* SKUs
    all_ch = set()
    all_ch.update(k for k in inv if k.startswith("CH-"))
    all_ch.update(k for k in sat_demand if k.startswith("CH-"))
    all_ch.update(k for k in tue_demand if k.startswith("CH-"))
    all_ch.update(k for k in next_sat_demand if k.startswith("CH-"))

    results = []
    shortage_count = 0
    for sku in sorted(all_ch):
        avail = inv.get(sku, 0)
        d_sat = int(round(sat_demand.get(sku, 0)))
        d_tue = int(round(tue_demand.get(sku, 0)))
        d_next = int(round(next_sat_demand.get(sku, 0)))
        total = d_sat + d_tue + d_next

        net_sat = avail - d_sat
        net_tue = net_sat - d_tue  # after Tuesday
        net_final = avail - total  # after next Saturday

        if d_sat == 0 and d_tue == 0 and d_next == 0:
            status = "NO DEMAND"
        elif net_sat < 0:
            status = "SHORTAGE"
            shortage_count += 1
        elif net_sat < d_sat * 0.2:
            status = "TIGHT"
        elif net_sat > avail * 0.5 and avail > 200:
            status = "SURPLUS"
        else:
            status = "OK"

        results.append({
            "sku": sku, "available": avail,
            "sat_demand": d_sat, "net_sat": net_sat,
            "tue_demand": d_tue, "net_tue": net_tue,
            "next_sat_demand": d_next, "net_final": net_final,
            "total_demand": total, "net": net_sat,
            "status": status,
            # Keep compat fields for the existing UI
            "direct": d_sat, "prcjam": 0, "cexec": 0, "exec": 0,
        })

    status_order = {"SHORTAGE": 0, "TIGHT": 1, "OK": 2,
                    "SURPLUS": 3, "NO DEMAND": 4}
    results.sort(key=lambda r: (status_order.get(r["status"], 9), r["net"]))

    # Dashboard info for assignment panel
    dashboard = STATE.get("rmfg_dashboard", {})
    prcjam_counts = dashboard.get("prcjam_counts", {})
    cexec_counts = dashboard.get("cexec_counts", {})

    # Multi-week: Tue and Next Sat as week tabs
    weeks = []
    # Week 2 = Tuesday
    tue_results = []
    tue_shortages = 0
    for r in results:
        if not r["sku"].startswith("CH-"):
            continue
        carry = max(0, r["net_sat"])
        demand = r["tue_demand"]
        proj = carry - demand
        if demand == 0:
            st = "NO DEMAND"
        elif proj < 0:
            st = "PLAN PO"
            tue_shortages += 1
        elif proj < demand * 0.3:
            st = "TIGHT"
        else:
            st = "OK"
        tue_results.append({
            "sku": r["sku"], "carry_fwd": carry,
            "demand": demand, "net": proj, "status": st,
        })
    weeks.append({"week": 2, "label": "Tuesday", "results": tue_results,
                  "shortages": tue_shortages})

    # Week 3 = Next Saturday
    nsat_results = []
    nsat_shortages = 0
    for r in results:
        if not r["sku"].startswith("CH-"):
            continue
        carry = max(0, r["net_sat"] - r["tue_demand"])
        demand = r["next_sat_demand"]
        proj = carry - demand
        if demand == 0:
            st = "NO DEMAND"
        elif proj < 0:
            st = "PLAN PO"
            nsat_shortages += 1
        elif proj < demand * 0.3:
            st = "TIGHT"
        else:
            st = "OK"
        nsat_results.append({
            "sku": r["sku"], "carry_fwd": carry,
            "demand": demand, "net": proj, "status": st,
        })
    weeks.append({"week": 3, "label": "Next Sat", "results": nsat_results,
                  "shortages": nsat_shortages})

    # Weeks 4-5 (Sat +3, Sat +4) — carry forward from previous week
    prev_week = {r["sku"]: r for r in nsat_results}
    for week_num in range(4, 6):
        wk_results = []
        wk_shortages = 0
        for r in results:
            if not r["sku"].startswith("CH-"):
                continue
            p = prev_week.get(r["sku"])
            carry = max(0, p["net"]) if p else 0
            demand = r["sat_demand"]  # assume Saturday-like demand
            proj = carry - demand
            if demand == 0:
                st = "NO DEMAND"
            elif proj < 0:
                st = "PLAN PO"
                wk_shortages += 1
            elif proj < demand * 0.3:
                st = "TIGHT"
            else:
                st = "OK"
            wk_results.append({
                "sku": r["sku"], "carry_fwd": carry,
                "demand": demand, "net": proj, "status": st,
            })
        weeks.append({"week": week_num,
                      "label": f"Sat +{week_num - 1}",
                      "results": wk_results,
                      "shortages": wk_shortages})
        prev_week = {r["sku"]: r for r in wk_results}

    # Shelf life
    s = _s()
    inventory = s.get("inventory", {})
    today = datetime.date.today()
    shelf_items = []
    for sku, data in inventory.items():
        if not sku.startswith("CH-") or not isinstance(data, dict):
            continue
        dates = data.get("expiration_dates", [])
        if not dates:
            continue
        try:
            earliest = datetime.date.fromisoformat(dates[0])
        except (ValueError, IndexError):
            continue
        days = (earliest - today).days
        qty = data.get("qty", 0)
        if days <= 14 and qty > 0:
            shelf_items.append({
                "sku": sku, "days_left": days, "qty": qty,
                "action": "EXPIRED" if days < 0 else
                          "USE NOW" if days <= 7 else "Prioritize",
            })

    return jsonify({
        "results": results,
        "shortages": shortage_count,
        "total_skus": len([r for r in results if r["total_demand"] > 0]),
        "total_units": sum(r["total_demand"] for r in results),
        "prcjam_counts": prcjam_counts,
        "cexec_counts": cexec_counts,
        "weeks": weeks,
        "shelf_life": shelf_items,
        "assign_demands": {},
    })


# ── Substitution engine ───────────────────────────────────────────────

@app.route("/api/substitutions")
def get_substitutions():
    """Suggest cheese substitutions for shortages.
    Uses loaded RMFG data to find surplus cheeses that could replace short ones.
    """
    inv = STATE.get("rmfg_inventory", {})
    sat_demand = STATE.get("rmfg_sat_demand", {})

    if not inv:
        return jsonify([])

    # Build NET for all CH-*
    nets = {}
    for sku in set(list(inv.keys()) + list(sat_demand.keys())):
        if not sku.startswith("CH-"):
            continue
        avail = inv.get(sku, 0)
        demand = sat_demand.get(sku, 0)
        nets[sku] = {"available": avail, "demand": demand, "net": avail - demand}

    # Find shortages and surplus candidates
    shortages = [(sku, d) for sku, d in nets.items() if d["net"] < 0]
    surplus = [(sku, d) for sku, d in nets.items()
               if d["net"] > 50 and d["demand"] > 0]
    surplus.sort(key=lambda x: -x[1]["net"])

    suggestions = []
    for sku, sdata in sorted(shortages, key=lambda x: x[1]["net"]):
        deficit = abs(sdata["net"])
        subs = []
        for cand_sku, cdata in surplus:
            if cand_sku == sku:
                continue
            headroom = cdata["net"]
            can_cover = min(deficit, headroom)
            subs.append({
                "sku": cand_sku,
                "headroom": headroom,
                "can_cover": can_cover,
                "covers_all": can_cover >= deficit,
            })
        # Also suggest any SKU with high inventory and no demand
        for sk, d in nets.items():
            if sk == sku or d["demand"] > 0:
                continue
            if d["available"] >= deficit:
                subs.append({
                    "sku": sk,
                    "headroom": d["available"],
                    "can_cover": deficit,
                    "covers_all": True,
                    "no_demand": True,
                })
        subs.sort(key=lambda x: (-x["covers_all"], -x["can_cover"]))
        suggestions.append({
            "sku": sku,
            "deficit": deficit,
            "available": sdata["available"],
            "demand": sdata["demand"],
            "substitutes": subs[:5],
        })

    return jsonify(suggestions)


# ── Run All endpoint ──────────────────────────────────────────────────

@app.route("/api/run_all", methods=["POST"])
def run_all():
    """One-click: load folder + calculate + suggest fixes."""
    data = request.json or {}
    folder = data.get("folder", "")

    # 1. Load folder
    load_req = type('obj', (object,), {'json': data, 'method': 'POST'})()
    with app.test_request_context(json=data):
        load_resp = load_rmfg()
        load_data = load_resp.get_json() if hasattr(load_resp, 'get_json') else json.loads(load_resp.data)
        if not load_data.get("ok"):
            return jsonify(load_data)

    # 2. Calculate
    with app.test_request_context(json={}):
        calc_resp = calculate_rmfg()
        calc_data = calc_resp.get_json() if hasattr(calc_resp, 'get_json') else json.loads(calc_resp.data)

    # 3. Substitutions
    subs = []
    with app.test_request_context():
        sub_resp = get_substitutions()
        subs = sub_resp.get_json() if hasattr(sub_resp, 'get_json') else json.loads(sub_resp.data)

    return jsonify({
        "load": load_data,
        "results": calc_data,
        "substitutions": subs,
    })


# ── List available RMFG folders ───────────────────────────────────────

@app.route("/api/rmfg_folders")
def list_rmfg_folders():
    """List RMFG_* folders in the project directory."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folders = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if os.path.isdir(path) and name.startswith("RMFG_"):
            files = detect_rmfg_files(path)
            found = sum(1 for v in files.values() if v)
            folders.append({"name": name, "files_found": found,
                            "total_files": 5})
    return jsonify(folders)


# ── Dropbox integration ───────────────────────────────────────────────

@app.route("/api/dropbox_sync", methods=["POST"])
def dropbox_sync():
    """Fetch latest inventory snapshot from Dropbox shared link."""
    import tempfile
    try:
        import requests as req
    except ImportError:
        return jsonify({"error": "requests library not installed"}), 500

    STATE["saved"] = load_settings()  # reload to pick up fresh tokens
    s = _s()
    app_key = s.get("dropbox_app_key", "")
    app_secret = s.get("dropbox_app_secret", "")
    refresh_token = s.get("dropbox_refresh_token", "")
    shared_link = s.get("dropbox_shared_link", "")

    direct_token = s.get("dropbox_access_token", "")
    has_api_creds = bool(app_key and app_secret and (refresh_token or direct_token))

    if not has_api_creds and not shared_link:
        return jsonify({"error": "Dropbox not configured. Set shared_link or app credentials in Settings."}), 400

    if has_api_creds:
        # Get access token — use refresh token if available, else use direct token
        if refresh_token:
            token_resp = req.post(
                "https://api.dropboxapi.com/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": app_key,
                    "client_secret": app_secret,
                }, timeout=15)
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]
        else:
            access_token = direct_token

        headers = {"Authorization": f"Bearer {access_token}",
                   "Content-Type": "application/json"}

        # Try multiple paths — direct account path first, then shared link
        attempts = []
        if shared_link:
            # Shared link with empty path = root of shared folder
            attempts.append({"path": "", "recursive": False,
                             "shared_link": {"url": shared_link.split("?")[0],
                                             "password": None}})
        attempts.append({"path": "/!AppyHour_SHARED/Product Inventory",
                         "recursive": False})
        # Try common folder name variations
        attempts.append({"path": "/!AppyHour_SHARED/Product%20Inventory",
                         "recursive": False})

        resp = None
        last_err = ""
        for list_body in attempts:
            resp = req.post(
                "https://api.dropboxapi.com/2/files/list_folder",
                headers=headers, json=list_body, timeout=15)
            if resp.status_code == 200:
                break
            last_err = resp.text[:300]

        if resp is None or resp.status_code != 200:
            return jsonify({"error": f"Dropbox list_folder failed: {last_err}"}), 400

        entries = resp.json().get("entries", [])
        # Accept any csv/xlsx file (don't filter by name — the folder IS the inventory folder)
        inv_files = [
            e for e in entries
            if e.get("name", "").lower().endswith((".csv", ".xlsx"))
        ]
        if not inv_files:
            return jsonify({"error": "No inventory files found on Dropbox"}), 404

        inv_files.sort(key=lambda e: e.get("server_modified", ""), reverse=True)
        newest = inv_files[0]
        name = newest["name"]

        # Download using file id (works for both shared link and direct)
        file_id = newest.get("id", "")
        fpath = newest.get("path_lower", "/" + name)
        dl_arg = {"path": file_id} if file_id else {"path": fpath}
        dl_resp = req.post(
            "https://content.dropboxapi.com/2/files/download",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Dropbox-API-Arg": json.dumps(dl_arg),
            }, timeout=30)
        dl_resp.raise_for_status()
    else:
        # Shared link only — try to scrape file links from the folder page
        # Fetch the folder page HTML to find file links
        page_resp = req.get(shared_link, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        page_resp.raise_for_status()
        html_text = page_resp.text

        # Look for file entries in the page — Dropbox embeds JSON data
        import re
        # Find file URLs in the HTML for .csv or .xlsx files containing "product inventory"
        # Dropbox shared folder pages contain file metadata in embedded JSON
        file_links = re.findall(
            r'(https://www\.dropbox\.com/scl/fi/[^"\'\\]+\.(?:csv|xlsx)[^"\'\\]*)',
            html_text, re.IGNORECASE)

        if not file_links:
            # Try alternate pattern
            file_links = re.findall(
                r'(https://[^"\'\\]*dropbox[^"\'\\]*(?:csv|xlsx)[^"\'\\]*)',
                html_text, re.IGNORECASE)

        # Filter for inventory-related files
        inv_links = [l for l in file_links
                     if "product" in l.lower() or "inventory" in l.lower()]
        if not inv_links:
            inv_links = file_links  # fallback to any csv/xlsx

        if not inv_links:
            return jsonify({"error": "No inventory files found in shared folder. "
                           "Configure Dropbox app credentials for reliable access."}), 404

        # Use the first (most recent) link — convert to direct download
        file_url = inv_links[0]
        # Strip existing query params and add dl=1
        base_url = file_url.split("?")[0]
        rlkey_match = re.search(r'rlkey=([^&"]+)', file_url)
        dl_url = base_url + "?dl=1"
        if rlkey_match:
            dl_url += "&rlkey=" + rlkey_match.group(1)

        dl_resp = req.get(dl_url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0"})
        dl_resp.raise_for_status()

        # Infer filename
        cd = dl_resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            name = cd.split("filename=")[-1].strip('" ').split("'")[-1]
        else:
            name = base_url.split("/")[-1] or "inventory.xlsx"

    # 4. Parse inventory
    inv = {}
    if name.lower().endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            return jsonify({"error": "openpyxl not installed for XLSX"}), 500
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.write(dl_resp.content)
        tmp.close()
        try:
            wb = openpyxl.load_workbook(tmp.name, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            headers = [str(c).strip() if c else "" for c in rows[0]]
            sku_col = next((i for i, h in enumerate(headers)
                          if h.lower() in ("product sku", "sku")), None)
            rmfg_col = next((i for i, h in enumerate(headers)
                           if h.upper() == "RMFG"), None)
            total_col = next((i for i, h in enumerate(headers)
                            if h.lower() == "total"), None)
            qty_col = rmfg_col if rmfg_col is not None else total_col

            if sku_col is None or qty_col is None:
                return jsonify({"error": f"Can't find SKU/qty columns in {name}"}), 400

            for row in rows[1:]:
                sku = str(row[sku_col]).strip() if row[sku_col] else ""
                if not sku:
                    continue
                try:
                    qty = int(float(str(row[qty_col] or 0)))
                except (ValueError, TypeError):
                    qty = 0
                sku = normalize_sku(sku)
                inv[sku] = inv.get(sku, 0) + qty
        finally:
            os.unlink(tmp.name)
    else:
        text = dl_resp.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            sku = row.get("Product SKU", row.get("SKU", "")).strip()
            if not sku:
                continue
            try:
                qty = int(float(row.get("RMFG", row.get("Total", "0")) or 0))
            except ValueError:
                qty = 0
            sku = normalize_sku(sku)
            inv[sku] = inv.get(sku, 0) + qty

    # Also add open PO quantities
    for po in s.get("open_pos", []):
        if po.get("status", "").lower() != "received":
            sku = normalize_sku(po.get("sku", ""))
            if sku:
                inv[sku] = inv.get(sku, 0) + int(po.get("qty", 0))

    ch_count = sum(1 for k in inv if k.startswith("CH-"))
    STATE["rmfg_inventory"] = inv

    return jsonify({
        "ok": True,
        "source": "dropbox",
        "file": name,
        "modified": newest.get("server_modified", ""),
        "inventory_count": len(inv),
        "cheese_count": ch_count,
    })


@app.route("/api/dropbox_status")
def dropbox_status():
    """Check if Dropbox is configured."""
    s = _s()
    return jsonify({
        "configured": bool(
            (s.get("dropbox_app_key") and (s.get("dropbox_refresh_token") or s.get("dropbox_access_token"))) or
            s.get("dropbox_shared_link")
        ),
        "has_shared_link": bool(s.get("dropbox_shared_link")),
    })


@app.route("/api/dropbox_auth_url")
def dropbox_auth_url():
    """Generate Dropbox OAuth2 authorization URL (no redirect, copy code)."""
    s = _s()
    app_key = s.get("dropbox_app_key", "")
    if not app_key:
        return jsonify({"error": "Set dropbox_app_key in settings first"}), 400
    url = (f"https://www.dropbox.com/oauth2/authorize"
           f"?client_id={app_key}"
           f"&response_type=code"
           f"&token_access_type=offline")
    return jsonify({"url": url, "instructions": "Open this URL, authorize, then paste the code at /api/dropbox_token?code=YOUR_CODE"})


@app.route("/api/dropbox_token")
def dropbox_token():
    """Exchange authorization code for permanent refresh token."""
    try:
        import requests as req
    except ImportError:
        return "requests module not installed", 500

    code = request.args.get("code", "").strip()
    if not code:
        return ("<h2>Dropbox Token Exchange</h2>"
                "<form action='/api/dropbox_token' method='get'>"
                "<label>Paste your authorization code:</label><br><br>"
                "<input type='text' name='code' style='width:400px;padding:8px;font-size:14px' placeholder='Paste code here...'>"
                "<br><br><button type='submit' style='padding:8px 20px;font-size:14px'>Submit</button>"
                "</form>")

    s = _s()
    app_key = s.get("dropbox_app_key", "")
    app_secret = s.get("dropbox_app_secret", "")

    resp = req.post("https://api.dropboxapi.com/oauth2/token", data={
        "code": code,
        "grant_type": "authorization_code",
        "client_id": app_key,
        "client_secret": app_secret,
    }, timeout=15)

    if resp.status_code != 200:
        return f"<h2>Token exchange failed</h2><pre>{resp.text}</pre>", 400

    data = resp.json()
    refresh_token = data.get("refresh_token", "")

    if refresh_token:
        s["dropbox_refresh_token"] = refresh_token
        s.pop("dropbox_access_token", None)
        save_settings(s)
        STATE["saved"] = load_settings()  # reload cached settings
        return ("<h2>Dropbox authorized!</h2>"
                "<p>Permanent refresh token saved. You can close this tab.</p>")
    else:
        return f"<h2>No refresh token</h2><pre>{data}</pre>", 400


# ── Recharge API integration ──────────────────────────────────────────

@app.route("/api/recharge_sync", methods=["POST"])
def recharge_sync():
    """Pull queued charges from Recharge API and resolve into cheese demand."""
    try:
        import requests as req
    except ImportError:
        return jsonify({"error": "requests library not installed"}), 500

    s = _s()
    api_token = s.get("recharge_api_token", "")
    if not api_token:
        return jsonify({"error": "Recharge API token not set in Settings"}), 400

    # Fetch queued charges
    session = req.Session()
    session.headers.update({
        "X-Recharge-Access-Token": api_token,
        "Accept": "application/json",
    })

    all_charges = []
    url = "https://api.rechargeapps.com/charges"
    # Only fetch charges scheduled in the next 4 weeks
    today = datetime.date.today()
    date_min = today.isoformat()
    date_max = (today + datetime.timedelta(days=28)).isoformat()
    params = {"status": "queued", "limit": 250,
              "scheduled_at_min": date_min, "scheduled_at_max": date_max}
    page = 1
    while True:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        charges = data.get("charges", [])
        if not charges:
            break
        all_charges.extend(charges)

        # Try cursor-based pagination first (2021-11 API)
        next_cursor = data.get("next_cursor")
        if next_cursor:
            params = {"cursor": next_cursor, "limit": 250}
            continue

        # Try Link header pagination (older API)
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            import re
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
                params = {}
                continue

        # Try page-based pagination
        if len(charges) == 250:
            page += 1
            params = {"status": "queued", "limit": 250, "page": page}
            continue

        break

    if not all_charges:
        return jsonify({"error": "No queued charges found"}), 404

    # Resolve into cheese demand using per-charge box context
    pr_cjam = s.get("pr_cjam", {})
    cex_ec = s.get("cex_ec", {})
    splits = s.get("cexec_splits", {})
    global_extras = s.get("global_extras", {})

    # Build 4 weekly windows from today
    today = datetime.date.today()
    # Find next 4 Saturdays
    days_to_sat = (5 - today.weekday()) % 7
    if days_to_sat == 0:
        days_to_sat = 7
    saturdays = [today + datetime.timedelta(days=days_to_sat + 7 * i)
                 for i in range(4)]

    # Bin charges by week: which Saturday window they fall into
    # Each charge's scheduled_at determines its window
    week_demands = [defaultdict(int) for _ in range(4)]
    total_by_month = defaultdict(lambda: defaultdict(float))
    total_charges_count = 0

    for charge in all_charges:
        scheduled = charge.get("scheduled_at", "")
        if not scheduled:
            continue
        total_charges_count += 1

        # Parse date
        try:
            sched_date = datetime.date.fromisoformat(scheduled[:10])
        except (ValueError, TypeError):
            continue

        # Save by month for persistence
        month_label = scheduled[:7]

        # Find which weekly window this charge falls into
        week_idx = None
        for i, sat in enumerate(saturdays):
            # Window: previous Sunday through Saturday
            window_start = sat - datetime.timedelta(days=6)
            if window_start <= sched_date <= sat:
                week_idx = i
                break
        if week_idx is None:
            # Before first window or after last — put in nearest
            if sched_date <= saturdays[0]:
                week_idx = 0
            elif sched_date <= saturdays[-1]:
                week_idx = 3
            else:
                continue  # beyond 4 weeks, skip

        # Find box SKU on this charge for curation context
        items = charge.get("line_items", [])
        box_sku = None
        for item in items:
            sku = (item.get("sku") or "").strip().upper()
            if sku.startswith("AHB-"):
                box_sku = sku
                break
        curation = resolve_curation_from_box_sku(box_sku)

        for item in items:
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            upper = sku.upper()
            qty = int(float(item.get("quantity", 1)))

            total_by_month[month_label][sku] += qty

            # PR-CJAM resolution with curation context
            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                if suffix == "GEN":
                    # Resolve using box curation
                    if curation and curation not in ("MONTHLY", None):
                        suffix = curation
                    else:
                        continue
                info = pr_cjam.get(suffix, {})
                ch = info.get("cheese", "") if isinstance(info, dict) \
                    else str(info)
                if ch:
                    week_demands[week_idx][normalize_sku(ch)] += qty
                continue

            # CEX-EC resolution
            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                if suffix in splits:
                    for ssku, pct in splits[suffix].items():
                        week_demands[week_idx][normalize_sku(ssku)] += \
                            int(qty * pct)
                else:
                    ec = cex_ec.get(suffix, "")
                    if ec:
                        week_demands[week_idx][normalize_sku(ec)] += qty
                continue

            if upper == "CEX-EC":
                if curation and curation not in ("MONTHLY", None):
                    ec = cex_ec.get(curation, "")
                    if isinstance(ec, str) and ec:
                        week_demands[week_idx][normalize_sku(ec)] += qty
                continue

            # Global extras resolution (bare SKUs across all curations)
            ge_resolved = global_extras.get(upper)
            if ge_resolved:
                week_demands[week_idx][normalize_sku(ge_resolved)] += qty
                continue

            if not is_pickable(sku):
                continue

            week_demands[week_idx][normalize_sku(sku)] += qty

    # Store per-week demand
    STATE["rmfg_sat_demand"] = dict(week_demands[0])      # This Saturday
    STATE["rmfg_tue_demand"] = dict(week_demands[1])       # Tuesday / week 2
    STATE["rmfg_next_sat_demand"] = dict(week_demands[2])  # Next Saturday
    STATE["rmfg_week4_demand"] = dict(week_demands[3])     # Week 4

    # Save to settings for persistence
    s["recharge_queued"] = {m: dict(skus)
                            for m, skus in total_by_month.items()}
    save_settings(s)
    STATE["saved"] = s

    ch_demand = sum(sum(v for k, v in wd.items() if k.startswith("CH-"))
                    for wd in week_demands)
    week_labels = ["This Sat", "Tuesday", "Next Sat", "Sat +3"]
    week_summary = [
        {"label": week_labels[i],
         "date": saturdays[i].isoformat(),
         "skus": len(week_demands[i]),
         "units": sum(week_demands[i].values())}
        for i in range(4)
    ]
    return jsonify({
        "ok": True,
        "total_charges": total_charges_count,
        "months": sorted(total_by_month.keys()),
        "weeks": week_summary,
        "cheese_demand_units": ch_demand,
    })


@app.route("/api/shopify_sync", methods=["POST"])
def shopify_sync():
    """Pull recent unfulfilled Shopify orders and add to demand."""
    try:
        import requests as req
    except ImportError:
        return jsonify({"error": "requests library not installed"}), 500

    s = _s()
    store = s.get("shopify_store_url", "").strip()
    token = s.get("shopify_access_token", "").strip()
    if not store or not token:
        return jsonify({"error": "Shopify store URL or access token not configured"}), 400

    if not store.startswith("http"):
        store = f"https://{store}.myshopify.com"

    api_version = "2024-01"
    session = req.Session()
    session.headers.update({
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    })

    # Pull unfulfilled orders from last 7 days
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    url = f"{store}/admin/api/{api_version}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "created_at_min": cutoff}

    all_orders = []
    while url:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return jsonify({"error": f"Shopify API error {resp.status_code}: {resp.text[:200]}"}), 400
        data = resp.json()
        orders = data.get("orders", [])
        all_orders.extend(orders)

        # Link header pagination
        url = None
        params = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            import re
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
                params = None

    # Aggregate SKU demand from line items
    shopify_demand = defaultdict(int)
    order_count = 0
    for order in all_orders:
        order_count += 1
        for item in order.get("line_items", []):
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            qty = int(float(item.get("quantity", 1)))
            nsku = normalize_sku(sku)
            if is_pickable(nsku):
                shopify_demand[nsku] += qty

    # Merge into week 1 (this Saturday) demand
    sat_demand = STATE.get("rmfg_sat_demand", {})
    for sku, qty in shopify_demand.items():
        sat_demand[sku] = sat_demand.get(sku, 0) + qty
    STATE["rmfg_sat_demand"] = sat_demand

    return jsonify({
        "ok": True,
        "orders": order_count,
        "skus": len(shopify_demand),
        "units": sum(shopify_demand.values()),
    })


@app.route("/api/shopify_status")
def shopify_status():
    s = _s()
    return jsonify({
        "configured": bool(s.get("shopify_store_url") and
                          s.get("shopify_access_token")),
    })


@app.route("/api/load_settings_inventory", methods=["POST"])
def load_settings_inventory():
    """Load inventory from settings JSON into RMFG state."""
    s = _s()
    inventory = s.get("inventory", {})
    inv = {}
    for sku, data in inventory.items():
        if isinstance(data, dict):
            qty = int(float(data.get("qty", 0)))
        else:
            qty = int(float(data))
        if qty > 0:
            inv[normalize_sku(sku)] = inv.get(normalize_sku(sku), 0) + qty

    # Add wheel supply
    wheel_inv = s.get("wheel_inventory", {})
    for wsku, wd in wheel_inv.items():
        if isinstance(wd, dict):
            w = float(wd.get("weight_lbs", 0))
            c = int(wd.get("count", 0))
            t = wd.get("target_sku", "")
            if t and w > 0 and c > 0:
                inv[t] = inv.get(t, 0) + int(w * c * WHEEL_TO_SLICE_FACTOR)

    # Add open POs
    for po in s.get("open_pos", []):
        if po.get("status", "").lower() != "received":
            sku = normalize_sku(po.get("sku", ""))
            if sku:
                inv[sku] = inv.get(sku, 0) + int(float(po.get("qty", 0)))

    STATE["rmfg_inventory"] = inv
    ch_count = sum(1 for k in inv if k.startswith("CH-"))
    return jsonify({
        "ok": True,
        "source": "settings",
        "inventory_count": len(inv),
        "cheese_count": ch_count,
    })


@app.route("/api/recharge_status")
def recharge_status():
    """Check if Recharge API is configured."""
    s = _s()
    return jsonify({
        "configured": bool(s.get("recharge_api_token")),
    })


# ══════════════════════════════════════════════════════════════════════════
#  ACTION CALENDAR — multi-week task schedule (PO, MFG, Crossdock, Ship)
# ══════════════════════════════════════════════════════════════════════════

def _next_weekday(start, weekday):
    """Return the next date on or after `start` that falls on `weekday` (0=Mon)."""
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + datetime.timedelta(days=days_ahead)


@app.route("/api/action_calendar", methods=["POST"])
def action_calendar():
    """Generate a multi-week action calendar from current data.

    Returns 4 weeks of daily tasks:
      - PO: purchase orders to place (Wed), with SKU/qty/vendor
      - MFG: manufacturing/production orders (Wed), wheel-to-slice
      - CROSSDOCK: incoming PO arrivals to receive
      - FULFILL: Tuesday + Saturday shipment volumes
      - INVENTORY: Monday/Friday inventory check reminders
    """
    s = _s()
    inv = STATE.get("rmfg_inventory") or {}
    sat_demand = STATE.get("rmfg_sat_demand") or {}
    tue_demand = STATE.get("rmfg_tue_demand") or {}
    next_sat_demand = STATE.get("rmfg_next_sat_demand") or {}
    open_pos = s.get("open_pos", [])
    vendor_catalog = s.get("vendor_catalog", {})
    wheel_inv = s.get("wheel_inventory", {})
    inventory = s.get("inventory", {})

    today = datetime.date.today()
    weeks = []

    # Build running inventory projection
    running_inv = {}
    for sku in set(list(inv.keys()) + list(sat_demand.keys()) +
                   list(tue_demand.keys()) + list(next_sat_demand.keys())):
        if sku.startswith("CH-") or sku.startswith("MT-") or sku.startswith("AC-"):
            running_inv[sku] = inv.get(sku, 0)

    # Demand windows: [(label, demand_dict, ship_weekday)]
    # ship_weekday: 5=Saturday, 1=Tuesday
    demand_windows = [
        ("This Saturday", sat_demand, 5),
        ("Tuesday", tue_demand, 1),
        ("Next Saturday", next_sat_demand, 5),
    ]

    # For weeks 4+, estimate demand as average of known windows
    avg_demand = {}
    total_windows = 0
    for label, dd, _ in demand_windows:
        if dd:
            total_windows += 1
            for sku, qty in dd.items():
                avg_demand[sku] = avg_demand.get(sku, 0) + qty
    if total_windows > 0:
        for sku in avg_demand:
            avg_demand[sku] = int(avg_demand[sku] / total_windows)

    # Generate 4 weeks of calendar
    week_start = today - datetime.timedelta(days=today.weekday())  # Monday

    for week_idx in range(4):
        ws = week_start + datetime.timedelta(weeks=week_idx)
        we = ws + datetime.timedelta(days=6)
        days = []

        # Determine which demand window applies this week
        if week_idx == 0:
            week_demand = sat_demand
            week_tue_demand = tue_demand
        elif week_idx == 1:
            week_demand = next_sat_demand
            week_tue_demand = {sku: int(qty * 0.3) for sku, qty in
                               next_sat_demand.items()} if next_sat_demand else {}
        else:
            week_demand = avg_demand
            week_tue_demand = {sku: int(qty * 0.3) for sku, qty in
                               avg_demand.items()}

        # Compute shortages for this week
        shortages = {}
        for sku in set(list(running_inv.keys()) + list(week_demand.keys())):
            avail = running_inv.get(sku, 0)
            demand = int(round(week_demand.get(sku, 0)))
            tue_d = int(round(week_tue_demand.get(sku, 0)))
            total = demand + tue_d
            net = avail - total
            if net < 0:
                shortages[sku] = {"deficit": abs(net), "avail": avail,
                                  "demand": total}

        week_po_lines = []
        week_mfg_lines = []

        for sku, info in sorted(shortages.items(), key=lambda x: -x[1]["deficit"]):
            if not sku.startswith("CH-"):
                continue
            deficit = info["deficit"]
            buf = max(deficit, int(deficit * 1.15))

            # Check if wheels can cover via MFG
            mfg_possible = 0
            for wsku, wd in wheel_inv.items():
                if isinstance(wd, dict) and wd.get("target_sku") == sku:
                    w = float(wd.get("weight_lbs", 0))
                    c = int(wd.get("count", 0))
                    mfg_possible += int(w * c * WHEEL_TO_SLICE_FACTOR)

            if mfg_possible >= deficit:
                wheels_needed = math.ceil(deficit / max(1,
                    WHEEL_TO_SLICE_FACTOR * float(
                        next((wd.get("weight_lbs", 7)
                              for wd in wheel_inv.values()
                              if isinstance(wd, dict) and
                              wd.get("target_sku") == sku), 7))))
                week_mfg_lines.append({
                    "sku": sku, "deficit": deficit,
                    "slices_needed": deficit,
                    "wheels_needed": wheels_needed,
                    "action": "CUT",
                })
            else:
                vi = vendor_catalog.get(sku, {})
                cq = int(vi.get("case_qty", 1)) or 1
                cases = math.ceil(buf / cq)
                week_po_lines.append({
                    "sku": sku, "deficit": deficit,
                    "order_qty": cases * cq, "cases": cases,
                    "case_qty": cq,
                    "vendor": vi.get("vendor", "TBD"),
                })

        # Build daily tasks for this week
        for day_offset in range(7):
            d = ws + datetime.timedelta(days=day_offset)
            dow = d.weekday()  # 0=Mon
            tasks = []

            # Monday: inventory check
            if dow == 0:
                tasks.append({
                    "type": "INVENTORY",
                    "title": "Check inventory report",
                    "detail": "Review fulfillment center inventory CSV",
                    "priority": "medium",
                })

            # Tuesday: fulfillment ship
            if dow == 1:
                tue_total = sum(week_tue_demand.values()) if week_tue_demand else 0
                tue_skus = len([k for k, v in week_tue_demand.items()
                                if v > 0]) if week_tue_demand else 0
                tasks.append({
                    "type": "FULFILL",
                    "title": f"Tuesday ship: {tue_total} units",
                    "detail": (f"{tue_skus} SKUs, first orders + requested"
                               if tue_skus else "First orders + requested"),
                    "priority": "high",
                    "units": tue_total,
                    "skus": tue_skus,
                })

            # Wednesday: POs + MFG
            if dow == 2:
                if week_po_lines:
                    total_po_units = sum(p["order_qty"] for p in week_po_lines)
                    vendors = list(set(p["vendor"] for p in week_po_lines))
                    tasks.append({
                        "type": "PO",
                        "title": f"Place POs: {len(week_po_lines)} lines, {total_po_units} units",
                        "detail": f"Vendors: {', '.join(vendors[:5])}",
                        "priority": "high",
                        "lines": week_po_lines,
                    })
                if week_mfg_lines:
                    total_slices = sum(m["slices_needed"] for m in week_mfg_lines)
                    tasks.append({
                        "type": "MFG",
                        "title": f"Production order: {total_slices} slices",
                        "detail": f"{len(week_mfg_lines)} SKUs to cut/wrap/label",
                        "priority": "high",
                        "lines": week_mfg_lines,
                    })

            # Friday: inventory check
            if dow == 4:
                tasks.append({
                    "type": "INVENTORY",
                    "title": "Check inventory report",
                    "detail": "Review fulfillment center inventory CSV",
                    "priority": "medium",
                })

            # Saturday: main fulfillment
            if dow == 5:
                sat_total = sum(week_demand.values()) if week_demand else 0
                sat_skus = len([k for k, v in week_demand.items()
                                if v > 0]) if week_demand else 0
                shortage_count = len(shortages)
                tasks.append({
                    "type": "FULFILL",
                    "title": f"Saturday ship: {sat_total} units",
                    "detail": (f"{sat_skus} SKUs, {shortage_count} shortages"
                               if shortage_count
                               else f"{sat_skus} SKUs, all covered"),
                    "priority": "critical" if shortage_count else "high",
                    "units": sat_total,
                    "skus": sat_skus,
                    "shortages": shortage_count,
                })

            # Crossdock: check open POs with ETA this day
            for po in open_pos:
                if po.get("status") != "Open":
                    continue
                eta_str = po.get("eta", "")
                try:
                    eta = datetime.date.fromisoformat(eta_str)
                except (ValueError, TypeError):
                    continue
                if eta == d:
                    tasks.append({
                        "type": "CROSSDOCK",
                        "title": f"Receive PO: {po.get('sku', '?')}",
                        "detail": f"{po.get('qty', '?')} units from {po.get('vendor', '?')}",
                        "priority": "high",
                        "sku": po.get("sku", ""),
                        "qty": po.get("qty", 0),
                    })

            days.append({
                "date": d.isoformat(),
                "dow": d.strftime("%a"),
                "day": d.day,
                "tasks": tasks,
                "is_today": d == today,
                "is_past": d < today,
            })

        # Deplete running inventory for next week
        for sku in running_inv:
            sat_d = int(round(week_demand.get(sku, 0)))
            tue_d = int(round(week_tue_demand.get(sku, 0)))
            running_inv[sku] = max(0, running_inv[sku] - sat_d - tue_d)

        weeks.append({
            "week": week_idx + 1,
            "start": ws.isoformat(),
            "end": we.isoformat(),
            "label": f"Week {week_idx + 1}: {ws.strftime('%b %d')} - {we.strftime('%b %d')}",
            "days": days,
            "po_lines": week_po_lines,
            "mfg_lines": week_mfg_lines,
            "shortages": len(shortages),
            "total_demand": sum(week_demand.values()) if week_demand else 0,
        })

    return jsonify({"weeks": weeks, "generated": today.isoformat()})


# ── Launch ──────────────────────────────────────────────────────────────

def run_webview():
    """Launch in a native window via pywebview."""
    import webview
    STATE["saved"] = load_settings()

    server = threading.Thread(
        target=lambda: app.run(port=5187, debug=False, use_reloader=False),
        daemon=True)
    server.start()

    import time
    time.sleep(0.5)

    webview.create_window(
        "Fulfillment Planner",
        "http://127.0.0.1:5187",
        width=1400, height=900, min_size=(1000, 700))
    webview.start()


def run_browser():
    """Launch in default browser."""
    STATE["saved"] = load_settings()
    import webbrowser
    webbrowser.open("http://127.0.0.1:5187")
    app.run(port=5187, debug=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true",
                        help="Open in browser instead of native window")
    args = parser.parse_args()

    STATE["saved"] = load_settings()

    if args.browser:
        run_browser()
    else:
        try:
            run_webview()
        except ImportError:
            print("pywebview not installed, falling back to browser")
            run_browser()
