"""Patch to add recipe diff, auto-ramp, and apply endpoints."""
import os
target = os.path.join(os.path.dirname(__file__), "..", "..", "InventoryReorder", "fulfillment_web", "app.py")
target = os.path.normpath(target)
with open(target, "r", encoding="utf-8") as f:
    content = f.read()

marker = "# ── PO Draft Generator "
idx = content.index(marker)

ramp_code = '''# ── Recipe Diff & Auto-Ramp ─────────────────────────────────────────────

def compute_recipe_diff(old_recipes, new_recipes):
    """Compare two recipe dicts and return additions, removals, and swaps."""
    diff = {}
    all_curations = set(old_recipes.keys()) | set(new_recipes.keys())
    for cur in all_curations:
        old_items = old_recipes.get(cur, [])
        new_items = new_recipes.get(cur, [])
        old_skus = [normalize_sku(item[0]) if isinstance(item, (list, tuple)) else normalize_sku(item) for item in old_items]
        new_skus = [normalize_sku(item[0]) if isinstance(item, (list, tuple)) else normalize_sku(item) for item in new_items]
        old_set, new_set = set(old_skus), set(new_skus)
        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)
        swaps = []
        for i in range(min(len(old_skus), len(new_skus))):
            if old_skus[i] != new_skus[i]:
                swaps.append({"slot": i, "old": old_skus[i], "new": new_skus[i]})
        if added or removed or swaps:
            diff[cur] = {"added": added, "removed": removed, "swaps": swaps}
    return diff


def apply_auto_ramp(diff, settings):
    """Create sku_ramp entries for new SKUs detected in recipe diff."""
    ramp = settings.get("sku_ramp", {})
    ramp_weeks = int(settings.get("default_ramp_weeks", 3))
    today = datetime.date.today().isoformat()
    created = []
    for cur, changes in diff.items():
        for swap in changes.get("swaps", []):
            new_sku, old_sku = swap["new"], swap["old"]
            if new_sku not in ramp:
                ramp[new_sku] = {"replaces": old_sku, "intro_date": today, "ramp_weeks": ramp_weeks, "curation": cur, "auto_created": True}
                created.append({"sku": new_sku, "replaces": old_sku, "curation": cur})
        for sku in changes.get("added", []):
            if sku not in ramp and not any(s["new"] == sku for s in changes.get("swaps", [])):
                ramp[sku] = {"replaces": None, "intro_date": today, "ramp_weeks": ramp_weeks, "curation": cur, "auto_created": True, "use_curation_average": True}
                created.append({"sku": sku, "replaces": None, "curation": cur})
    settings["sku_ramp"] = ramp
    return created


def get_ramped_demand(sku, base_demand, settings):
    """Get demand for a SKU with ramp-up blend for new introductions."""
    ramp = settings.get("sku_ramp", {})
    entry = ramp.get(sku)
    if not entry:
        return base_demand.get(sku, 0)
    intro_date = entry.get("intro_date")
    ramp_weeks = entry.get("ramp_weeks", 3)
    replaces = entry.get("replaces")
    if not intro_date:
        return base_demand.get(sku, 0)
    try:
        intro = datetime.date.fromisoformat(intro_date)
    except (ValueError, TypeError):
        return base_demand.get(sku, 0)
    weeks_since = (datetime.date.today() - intro).days // 7
    if weeks_since >= ramp_weeks:
        return base_demand.get(sku, 0)
    actual = base_demand.get(sku, 0)
    if replaces:
        inherited = base_demand.get(replaces, 0)
    elif entry.get("use_curation_average"):
        floor_data = compute_curation_floor()
        inherited = floor_data.get(sku, {}).get("floor", 0) if isinstance(floor_data.get(sku), dict) else 0
    else:
        inherited = 0
    blend = weeks_since / ramp_weeks
    return round(inherited * (1.0 - blend) + actual * blend)


@app.route("/api/recipe_diff", methods=["POST"])
def recipe_diff():
    """Compare current recipes against proposed. Body: {new_recipes: {curation: [[sku, qty], ...]}}"""
    data = request.json or {}
    new_recipes = data.get("new_recipes", {})
    if not new_recipes:
        return jsonify({"error": "new_recipes required"}), 400
    s = _s()
    diff = compute_recipe_diff(s.get("curation_recipes", {}), new_recipes)
    if not diff:
        return jsonify({"ok": True, "changes": False, "message": "No recipe changes detected"})
    preview_settings = dict(s)
    preview_settings["sku_ramp"] = dict(s.get("sku_ramp", {}))
    ramp_entries = apply_auto_ramp(diff, preview_settings)
    ewma = s.get("shopify_ewma_demand", s.get("shopify_api_demand", {}))
    demand_impact = []
    for entry in ramp_entries:
        new_sku, old_sku = entry["sku"], entry.get("replaces")
        inherited = ewma.get(old_sku, 0) if old_sku else 0
        actual = ewma.get(new_sku, 0)
        floor_data = compute_curation_floor()
        floor_qty = floor_data.get(old_sku, {}).get("floor", 0) if old_sku and isinstance(floor_data.get(old_sku), dict) else 0
        demand_impact.append({"new_sku": new_sku, "replaces": old_sku, "curation": entry["curation"],
                              "inherited_demand": max(inherited, floor_qty), "current_actual": actual,
                              "week_1_demand": max(inherited, floor_qty), "week_4_demand": actual})
    return jsonify({"ok": True, "changes": True, "diff": diff, "ramp_entries": ramp_entries, "demand_impact": demand_impact})


@app.route("/api/recipe_apply", methods=["POST"])
def recipe_apply():
    """Apply new recipes and auto-create ramp entries. Body: {new_recipes: {curation: [[sku, qty], ...]}}"""
    data = request.json or {}
    new_recipes = data.get("new_recipes", {})
    if not new_recipes:
        return jsonify({"error": "new_recipes required"}), 400
    s = load_settings()
    current_recipes = s.get("curation_recipes", {})
    recipe_history = s.setdefault("recipe_history", [])
    recipe_history.append({"date": datetime.datetime.now().isoformat(), "recipes": dict(current_recipes)})
    if len(recipe_history) > 12:
        recipe_history[:] = recipe_history[-12:]
    diff = compute_recipe_diff(current_recipes, new_recipes)
    ramp_entries = apply_auto_ramp(diff, s)
    s["curation_recipes"].update(new_recipes)
    save_settings(s)
    STATE["saved"] = s
    return jsonify({"ok": True, "diff": diff, "ramp_entries_created": len(ramp_entries),
                    "ramp_entries": ramp_entries, "recipe_history_entries": len(recipe_history)})


'''

content = content[:idx] + ramp_code + content[idx:]
with open(target, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: Recipe diff + auto-ramp added")
