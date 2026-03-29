# PLAN REVIEW: Swap Manager View

## Overall Assessment
The plan has a **fundamental feasibility problem**: it proposes creating new `swap_routes.py` importing from `AppyHourMCP/tools/order_edit.py`, but the fulfillment_web app **already has working swap routes** (lines 4098-4211 in app.py) backed by a purpose-built `shopify_swap.py` module in the same directory. The plan must be revised to build on the existing foundation rather than introducing a parallel, incompatible swap path.

## Attack Plan Results

### Feasibility Audit

**CRITICAL: Plan ignores existing swap infrastructure.**
- `shopify_swap.py` (9.6KB) already exists at `InventoryReorder/fulfillment_web/shopify_swap.py` with `find_swap_targets()`, `execute_swap()`, `execute_bulk_swap()`, and `lookup_variant_gid()`.
- `app.py` already has 4 swap routes at lines 4098-4211: `/api/swap_preview`, `/api/swap_execute`, `/api/swap_progress`, `/api/swap_cancel`.
- These routes already use `_swap_progress` dict with background threading + polling -- the exact pattern the plan proposes to create from scratch.
- The plan says to import `_swap_order_skus` and `_lookup_variant_gids` from `AppyHourMCP/tools/order_edit.py` -- but those are **MCP tool functions** with a different auth pattern (`from utils import get_shopify_auth` which reads settings via `get_inventory_settings()` in the MCP package). The fulfillment web app reads credentials directly from its own settings: `s.get("shopify_store_url")`, `s.get("shopify_access_token")`.

**The plan references non-existent registration pattern.**
- Plan says: `from swap_routes import register_swap_routes; register_swap_routes(app)` -- but app.py has **zero** route registration patterns. All 40+ routes are `@app.route()` decorators directly in app.py. No module has ever been registered this way.

### Completeness Check

1. **No mention of existing swap routes** -- The plan must address whether to replace, extend, or coexist with `/api/swap_preview`, `/api/swap_execute`, `/api/swap_progress`, `/api/swap_cancel`.

2. **Missing: multiple swap pairs per execution** -- Existing routes handle one old_sku->new_sku pair at a time. The plan's UI shows multiple simultaneous pairs but doesn't detail how the execute endpoint handles multi-pair atomicity or ordering.

3. **Missing: ship tag auto-detection** -- Existing code uses `compute_ship_week()` from `ship_dates.py` to auto-detect the ship tag. Plan's `/api/swap/ship-tags` fetches from Shopify but doesn't reference this existing helper.

4. **Missing: Recharge auth flow details** -- Plan says "sync Recharge bundle_selections" but doesn't specify how to authenticate. `fix_rc_class23_bundles.py` reads `settings["recharge_api_token"]` (same key app.py already uses at line 4341: `s.get("recharge_api_token")`). Plan should explicitly state it uses the existing settings key.

5. **Missing: error recovery for partial swap failures** -- If 3 of 5 swap pairs succeed and pair 4 fails, what state is the system in? No rollback strategy.

### Risk Assessment

1. **Auth pattern mismatch is real but misdirected.** The plan correctly identifies auth as a risk but proposes `sys.path.insert` to import from `order_edit.py`. This is unnecessary -- `shopify_swap.py` already takes `(store_url, token)` as explicit parameters, matching how app.py passes credentials. No cross-package imports needed.

2. **Concurrent swap safety.** Existing code uses `_swap_progress["running"]` as a mutex (line 4155). This is not thread-safe -- a race condition exists between checking and setting. Plan proposes 8-worker ThreadPoolExecutor but doesn't address this.

3. **Single-pair assumption in shopify_swap.py.** `find_swap_targets()` filters for one `old_sku` at a time. Multi-pair swaps need either sequential calls (slower) or a refactored function. Plan doesn't address this.

### Backward Compatibility

1. **Existing swap callers in app.js** -- The dashboard likely has swap trigger buttons that call `/api/swap_preview` etc. Adding new routes at `/api/swap/*` (with slashes) while old ones are at `/api/swap_*` (underscores) could work, but may confuse maintenance.

2. **`_swap_progress` global state** -- If both old and new swap paths coexist, they share the same progress state and would conflict.

### Ordering & Dependencies

1. Plan says "Import and register swap routes" in app.py (step 6) but creating `swap_routes.py` is step 2. The dependency is correct but the registration pattern itself is wrong (see Feasibility).

2. `sku_mappings.json` (step 1) is loaded at startup into STATE, but step 2 (`swap_routes.py`) references it. If the module is imported before STATE is populated, it will fail unless lazy-loaded.

### Negative Constraints

The plan has a partial "Files NOT Modified" section (good), but lacks:
- "Do NOT create a Flask blueprint" -- stated implicitly but should be explicit
- "Do NOT duplicate the Shopify GraphQL helpers" -- critical given `shopify_swap.py` already has `_gql()` and `_rest_get()`
- "Do NOT break existing `/api/swap_preview` callers" -- backward compat boundary

## Findings Summary

| # | Severity | Confidence | Finding |
|---|----------|------------|---------|
| 1 | CRITICAL | HIGH | Plan ignores existing `shopify_swap.py` and 4 working swap routes in app.py |
| 2 | CRITICAL | HIGH | Plan proposes importing from `order_edit.py` (MCP tool with incompatible auth) instead of using `shopify_swap.py` (already matches app.py auth pattern) |
| 3 | CRITICAL | HIGH | `register_swap_routes(app)` pattern does not exist anywhere in the codebase -- all routes are `@app.route` in app.py |
| 4 | HIGH | HIGH | No strategy for coexistence/migration from existing `/api/swap_*` routes |
| 5 | HIGH | MEDIUM | Multi-pair swap execution lacks atomicity/partial-failure handling |
| 6 | HIGH | HIGH | `_swap_progress` global dict is not thread-safe -- race condition between check and set |
| 7 | MEDIUM | HIGH | Plan's switchView integration is correct pattern but omits `views` dict entry (line 2159-2167 in app.js) |
| 8 | MEDIUM | MEDIUM | Recharge sync auth is solvable but plan doesn't specify it uses `s.get("recharge_api_token")` already available |
| 9 | MEDIUM | LOW | `sku_mappings.json` loaded into STATE at startup may not be available when swap_routes module is first imported |
| 10 | LOW | HIGH | Plan estimates ~300 lines JS but existing swap UI code + multi-pair + matrix mode will likely be 500-700 lines |

## Detailed Findings

### Finding 1: Existing swap infrastructure ignored (CRITICAL, HIGH confidence)
**What:** `shopify_swap.py` (9.6KB, 270+ lines) already exists in `fulfillment_web/` with `find_swap_targets()`, `lookup_variant_gid()`, `execute_swap()`, `execute_bulk_swap()`. App.py already has `/api/swap_preview`, `/api/swap_execute`, `/api/swap_progress`, `/api/swap_cancel` at lines 4098-4211.
**Why it matters:** Building a parallel swap path wastes effort and creates two incompatible code paths for the same operation. Maintaining both is a liability.
**Suggested fix:** Revise the plan to EXTEND `shopify_swap.py` with multi-pair support and matrix parsing, and extend the existing app.py swap routes rather than creating `swap_routes.py`.

### Finding 2: Wrong import target for swap functions (CRITICAL, HIGH confidence)
**What:** Plan says to import `_swap_order_skus` and `_lookup_variant_gids` from `AppyHourMCP/tools/order_edit.py` via `sys.path`. These functions use `from utils import get_shopify_auth, shopify_graphql` -- the MCP package's auth, which reads settings via a different path (`get_inventory_settings()` in AppyHourMCP/utils.py).
**Why it matters:** The fulfillment web app authenticates with `s.get("shopify_store_url")` and `s.get("shopify_access_token")` from its own settings loader. `shopify_swap.py` already accepts `(store_url, token)` as parameters -- no cross-package import needed.
**Suggested fix:** Use `shopify_swap.py` functions directly. They already implement the same GraphQL order edit pattern (`beginEdit -> setQuantity(0) -> addVariant -> commitEdit`) with compatible auth.

### Finding 3: Route registration pattern doesn't exist (CRITICAL, HIGH confidence)
**What:** Plan proposes `from swap_routes import register_swap_routes; register_swap_routes(app)`. App.py has 40+ routes, ALL defined as `@app.route()` decorators directly in the file. No external module has ever registered routes.
**Why it matters:** Introducing a new pattern in an 8K-line file adds cognitive overhead. More practically, Flask route registration via function call requires either `app.route()` calls inside the function or a blueprint -- the plan explicitly says no blueprints.
**Suggested fix:** Either (a) add new routes directly to app.py in the existing swap section (lines 4098+), or (b) if extraction is desired, use a Blueprint despite the plan's reluctance -- it's the idiomatic Flask way to split routes.

### Finding 4: No migration strategy for existing swap routes (HIGH, HIGH confidence)
**What:** Four swap endpoints already exist with underscore naming (`/api/swap_preview`, etc.). Plan proposes slash naming (`/api/swap/preview`, etc.). No mention of deprecation, migration, or backward compatibility.
**Why it matters:** If app.js or other callers reference the old endpoints, they'll break silently.
**Suggested fix:** Audit app.js for existing swap endpoint calls. Either keep the existing URL scheme or add redirects.

### Finding 5: Multi-pair atomicity gap (HIGH, MEDIUM confidence)
**What:** The UI shows multiple swap pairs executed together. The plan's execute flow doesn't address what happens if pair 2 of 4 fails mid-execution.
**Why it matters:** Partial swaps leave orders in an inconsistent state -- some swapped, some not, with no record of which succeeded.
**Suggested fix:** Execute pairs sequentially, log each result independently, and report partial success/failure in the response. Add per-pair status to swap_history.

### Finding 6: Thread safety on _swap_progress (HIGH, HIGH confidence)
**What:** Existing code checks `if _swap_progress["running"]` then sets it to True -- classic TOCTOU race. Plan proposes 8-worker ThreadPoolExecutor which would make this worse.
**Why it matters:** Two simultaneous swap requests could both pass the guard and corrupt shared state.
**Suggested fix:** Use `threading.Lock()` around the running check+set (similar to the existing `_recharge_sync_lock` at line 4788).

### Finding 7: switchView dict entry missing from plan (MEDIUM, HIGH confidence)
**What:** Plan shows `switchView('swapmanager')` call but doesn't mention adding the entry to the `views` dict at app.js line 2159-2167, which maps view names to DOM elements.
**Why it matters:** Without the dict entry, `views[view]` returns undefined and the view panel won't display.
**Suggested fix:** Explicitly list adding `swapmanager: document.getElementById('swapmanager-view')` to the views dict.

### Finding 8: Recharge auth unspecified (MEDIUM, MEDIUM confidence)
**What:** Plan says "sync Recharge bundle_selections" but doesn't specify auth source. App.py already reads `s.get("recharge_api_token")` (line 4341, 4805).
**Why it matters:** Without explicit mention, implementer might try to import from `fix_rc_class23_bundles.py` which has its own `RC_TOKEN_READ = settings["recharge_api_token"]` pattern.
**Suggested fix:** State explicitly: "Use `_s().get('recharge_api_token')` -- same pattern as existing Recharge sync at line 4805."

## Positive Observations
- Correct identification that app.py is too large and new logic should be in a separate module
- Good UI layout design -- the split-panel with manual/matrix modes is well thought out
- Swap history schema is clean and useful for audit trails
- Correct that existing standalone scripts should be deprecated rather than deleted
- Testing plan covers the right layers (unit, route, integration, manual QA)

## Recommendation
**REVISE PLAN** -- The three CRITICAL findings require significant restructuring:

1. Base the implementation on `shopify_swap.py` (already exists, correct auth pattern), not `order_edit.py`
2. Extend the existing swap routes in app.py rather than introducing a novel registration pattern
3. Address migration/coexistence with the 4 existing swap endpoints

The UI design, history schema, and testing plan are solid and can be kept. The backend approach needs rework.
