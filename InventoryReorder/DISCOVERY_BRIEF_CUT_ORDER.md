# Discovery Brief: Cut Order Calculator Codebase

## Prior Context
- Memory from `.claude/projects/.../memory/MEMORY.md` confirms: cut-order-xlsx-process, cut-order-logic, demand-pipeline-architecture, mong-demand-heuristics, curation-rotation-rules.
- No claude-mem search results found (project may not be indexed in claude-mem).

## File Inventory

| File | Path | Lines | Role | Exists |
|------|------|-------|------|--------|
| build_cut_order_xlsx.py | `C:/Users/Work/Claude Projects/AppyHour/InventoryReorder/build_cut_order_xlsx.py` | 518 | Standalone XLSX generator | VERIFIED |
| cut_order_generator.py | `C:/Users/Work/Claude Projects/AppyHour/InventoryReorder/cut_order_generator.py` | 822 | Standalone cut order script (CSV-based) | VERIFIED |
| app.py | `C:/Users/Work/Claude Projects/AppyHour/InventoryReorder/fulfillment_web/app.py` | 8109 | Web app backend (Flask) | VERIFIED |
| app.js | `C:/Users/Work/Claude Projects/AppyHour/InventoryReorder/fulfillment_web/static/app.js` | 5275 | Frontend JS (single SPA) | VERIFIED |

---

## 1. build_cut_order_xlsx.py (518 lines)

### Functions
| Line | Signature | Purpose |
|------|-----------|---------|
| L29 | `def main()` | Entry point: fetches demand, builds XLSX |
| L41 | `def sku_name(sku)` | SKU display name lookup |
| L320 | `def _write_slot_table(ws, start_row, label, slots, w1_count, w2_count)` | Writes monthly box slot table section into Excel worksheet |

### Constants
- `BASE` (L19): script directory
- `AHB_MED_SLOTS` (L290): 9 slots — 2 CH, 2 MT, 1 AC crackers, 2 AC, 1 PR-CJAM-GEN Cheese, 1 PR-CJAM-GEN Jam
- `AHB_CMED_SLOTS` (L297): 9 slots — 4 CH, 0 MT, 1 AC crackers, 2 AC, 1 PR-CJAM-GEN Cheese, 1 PR-CJAM-GEN Jam
- `AHB_LGE_SLOTS` (L304): 11 slots — 3 CH, 3 MT, 1 AC crackers, 2 AC, 1 PR-CJAM-GEN Cheese, 1 PR-CJAM-GEN Jam
- Comment at L289: "Slot definitions -- must match MONTHLY_BOX_SLOTS in fulfillment_web/app.py"

### Imports
- `from inventory_demand_report import (fetch_recharge_api, fetch_shopify_orders, PICKABLE_PREFIXES, ...)`
- Uses `openpyxl` for XLSX generation

### Demand Flow
1. Fetches Recharge charges via `fetch_recharge_api(recharge_token)` -> returns (rc_wk1, rc_wk2, rc_wk1_curations, rc_wk2_curations, rc_wk1_large, rc_wk2_large, _, _, rc_wk1_med_monthly, rc_wk2_med_monthly, rc_wk1_cmed_monthly, rc_wk2_cmed_monthly, rc_wk1_lge_monthly, rc_wk2_lge_monthly)
2. Fetches Shopify orders via `fetch_shopify_orders(settings)` -> returns (sh_wk1_addon, sh_wk2_addon, sh_wk1_curations, sh_wk2_curations, sh_wk1_large, sh_wk2_large, sh_wk1_med, sh_wk2_med, sh_wk1_lge, sh_wk2_lge)
3. First-order projection: counts "Subscription First Order" tagged Shopify orders from last 3 days, projects MONG-only demand forward, adds to sh_wk1_addon
4. Merges curation counts: Recharge + Shopify into wk1_curations, wk2_curations, wk1_large, wk2_large
5. MONTHLY boxes: adds Recharge monthly counts to wk1_med/wk2_med/wk1_lge/wk2_lge (Shopify MONTHLY/CMED already included in sh_wk*_med)
6. Writes slot tables for AHB-MED, AHB-CMED, AHB-LGE with editable SKU assignment cells and SUMIF demand formulas

---

## 2. cut_order_generator.py (822 lines)

### Functions
| Line | Signature | Purpose |
|------|-----------|---------|
| L37 | `def normalize_sku(sku)` | Uppercase + equivalence map (CH-BRIE -> CH-EBRIE) |
| L41 | `def is_pickable(sku)` | Excludes AHB-, BL-, PK-, TR-, EX-, PR-CJAM, CEX- prefixes |
| L60 | `def resolve_curation_from_box_sku(sku)` | Maps box SKU to curation name (MONTHLY, NMS, MS, or known curation) |
| L142 | `def load_settings()` | Loads inventory_reorder_settings.json from dist/ |
| L147 | `def load_sliced_inventory()` | Loads sliced inventory from RMFG CSV template check |
| L167 | `def load_recharge_csv(settings)` | Loads Recharge queued charges CSV, resolves PR-CJAM/CEX-EC to cheese demand |
| L268 | `def load_shopify_csv(settings)` | Loads Shopify order-dashboard CSV, resolves demand + first-order x3 projection |
| L291 | `def _resolve_order_skus(sku_counts, curation, target)` | Inner helper: resolves PR-CJAM/CEX-EC SKUs into cheese demand |
| L408 | `def pull_recharge_api(settings)` | Live pull from Recharge API (cursor pagination) |
| L532 | `def pull_shopify_api(settings)` | Live pull from Shopify API |
| L601 | `def main()` | Entry point: loads settings, demand from CSV or API, generates cut order |

### Constants & Configuration
| Line | Name | Value/Description |
|------|------|-------------------|
| L23 | `BASE` | Script directory |
| L24 | `SETTINGS_PATH` | `dist/inventory_reorder_settings.json` |
| L25 | `TEMPLATE_CSV` | `Orders RMFG_20260310 - Template Check.csv` |
| L26 | `RMFG_DIR` | `RMFG_20260310` |
| L28 | `OUTPUT_CSV` | `production_orders/cut_order_20260311.csv` |
| L30 | `WHEEL_TO_SLICE` | 2.67 |
| L33 | `EQUIV` | `{"CH-BRIE": "CH-EBRIE"}` |
| L34 | `SKIP_PREFIXES` | `("AHB-", "BL-", "PK-", "TR-", "EX-")` |
| L52 | `KNOWN_CURATIONS` | Set of 14: MONG, MDT, OWC, SPN, ALPN, ALPT, ISUN, HHIGH, NMS, BYO, SS, GEN, MS |
| L56 | `_MONTHLY_PATTERNS` | Set: AHB-MED, AHB-LGE, AHB-CMED, AHB-CUR-MS, AHB-BVAL, AHB-MCUST-MS, AHB-MCUST-NMS |

### PR-CJAM Overrides (L78-91)
Hardcoded for current week:
```python
PR_CJAM_OVERRIDES = {
    "ISUN": "CH-BAP", "MDT": "CH-TTBRIE", "SPN": "CH-MCPC",
    "MONG": "CH-BLR", "OWC": "CH-MCPC", "ALPN": "CH-MCPC",
    "ALPT": "CH-MCPC", "HHIGH": "CH-TIP", "BYO": "CH-TIP",
    "GEN": "CH-MCPC", "NMS": "CH-MCPC", "SS": "CH-TIP",
}
```

### CEX-EC Overrides (L95-107)
```python
CEX_EC_OVERRIDES = {
    "MONG": "CH-WWDI", "MDT": None (splits), "OWC": "CH-WMANG",
    "SPN": "CH-MSMG", "ALPN": "CH-UCONE", "ISUN": "CH-CTGOD",
    "HHIGH": "CH-HCGU", "BYO": "CH-HCGU", "SS": "CH-MSMG",
    "NMS": "CH-MCPC", "MS": "CH-6COM",
}
```

### CEX-EC Splits (L110-112)
```python
CEXEC_SPLITS_OVERRIDES = {
    "MDT": {"CH-MCPC": 0.64, "CH-MSMG": 0.36},
}
```

### Wheel Inventory (L115-139)
Hardcoded dict of ~24 cheese SKUs with wheels, weight_lbs, and pre-calculated slices.

### SKU Resolution Logic (duplicated in load_recharge_csv and load_shopify_csv)
1. **PR-CJAM-{SUFFIX}**: Check `PR_CJAM_OVERRIDES[suffix]` first, then `settings["pr_cjam"][suffix]["cheese"]`, then skip if "GEN"
2. **CEX-EC-{SUFFIX}**: Check `CEXEC_SPLITS_OVERRIDES[suffix]` for split ratios, else `CEX_EC_OVERRIDES[suffix]`, else `settings["cex_ec"][suffix]`
3. **CEX-EC** (bare, no suffix): Use curation context to resolve via splits or overrides
4. **Global extras**: `settings["global_extras"][sku]`
5. **Regular SKUs**: Direct demand via `normalize_sku()`
6. MONTHLY curations are EXCLUDED from demand (routed through slot tables instead)

### First-Order Projection Logic (in load_shopify_csv, L375-402)
- Only MONG first orders get x3 projection
- `FIRST_ORDER_MULTIPLIER = 2` (adds 2x on top of 1x already counted = 3x total)
- `MONG_PROJECT_SKUS` limits projection to specific recipe + PR-CJAM + CEX-EC SKUs
- Other curations close Saturday 2am, so no projection needed

### Settings Keys Used
- `recharge_api_token`, `shopify_store_url`, `shopify_access_token`
- `pr_cjam` (dict of {curation: {cheese: sku, jam: sku}})
- `cex_ec` (dict of {curation: cheese_sku})
- `cexec_splits` (dict of {curation: {sku: ratio}})
- `global_extras` (dict of {sku: resolved_sku})
- `inventory` (dict of {sku: {qty, name, ...}})

---

## 3. fulfillment_web/app.py — Cut Order Related (8109 lines total)

### Module-Level Constants (Cut Order Related)
| Line | Name | Value |
|------|------|-------|
| L30 | `WHEEL_TO_SLICE_FACTOR` | 2.67 |
| L33 | `ASSIGNMENT_EXCLUDE` | `{"CH-MAFT"}` |
| L36-42 | `GLOBAL_EXTRA_SLOTS` | EX-EC->CH, CEX-EM->MT, EX-EM->MT, CEX-EA->AC, EX-EA->AC |
| L44 | `MONTHLY_BOX_TYPES` | `["AHB-MED", "AHB-CMED", "AHB-LGE"]` |
| L45-67 | `MONTHLY_BOX_SLOTS` | Same slot definitions as build_cut_order_xlsx.py (MED=9, CMED=9, LGE=11 slots) |

### Key Functions
| Line | Signature | Purpose |
|------|-----------|---------|
| L133 | `check_constraint(curation, prcjam_cheese, cexec_cheese, recipes, pr_cjam, cex_ec)` | Validates assignment constraints (uniqueness, adjacency) |
| L488 | `get_candidates(curation, slot)` | Returns candidate cheeses for a curation slot |
| L516 | `get_monthly_boxes()` | Returns monthly box config with slot assignments and counts |
| L549 | `set_monthly_box_assign()` | Sets a monthly box slot assignment |
| L570 | `get_monthly_box_candidates(box_type, slot_index)` | Returns candidates for a monthly box slot |
| L589 | `set_monthly_box_count()` | Sets monthly box subscription count |
| L708 | `get_global_extra_candidates(slot)` | Returns candidates for global extra slot |
| L1212 | `resolve_pr_cjam(suffix)` | Resolves PR-CJAM using current settings |
| L1218 | `resolve_cex_ec(suffix)` | Resolves CEX-EC using current settings |
| L1225 | `resolve_pr_cjam_with(suffix, pr_cjam_dict)` | Resolves PR-CJAM with explicit dict |
| L1232 | `resolve_cex_ec_with(suffix, cex_ec_dict, splits_dict)` | Resolves CEX-EC with explicit dict+splits |
| L1241 | `resolve_demand(direct_demand, prcjam_counts, cexec_counts, pr_cjam, cex_ec, splits)` | Core demand resolver: returns (demand_dict, attribution_dict) |
| L2147 | `apply_churn_to_demand(demand, recurring_demand, churn_rates, weeks_out)` | Applies churn rates to demand |
| L2189 | `apply_unified_forecast(demand, recurring_demand, first_order_demand, churn_rates, shopify_trend_data, repeat_rate, reship_buffer_pct, weeks_out)` | Full forecast: churn + trend + first-order repeat + addon + reship |
| L2646 | `_build_queued_runway_demand(recharge_queued, pr_cjam, cex_ec, splits, saturdays)` | Builds multi-week demand from Recharge queued charges |
| L3523 | `get_cut_order()` | Generates cut order from current demand/inventory/wheel data |
| L3662 | `demand_breakdown(sku)` | Returns attribution for a single SKU |
| L3670 | `get_cut_order_interactive()` | Interactive cut order with raw components for client-side resolve |
| L3840 | `cut_quantities()` | GET/POST cut quantity inputs (persist to settings) |
| L3856 | `export_cut_order_csv()` | Export cut order as CSV download |
| L3921 | `projection_settings()` | GET/POST first-order projection settings |
| L7506 | `generate_cut_order_pdf(cut_lines, summary)` | Generates PDF for email |
| L7583 | `email_cut_order()` | Emails cut order PDF via SMTP |
| L7657 | `schedule_cut_order_email()` | Stores email schedule config |

### API Routes (Cut Order)
| Route | Method | Handler | Purpose |
|-------|--------|---------|---------|
| `/api/cut_order` | POST | `get_cut_order()` | Server-side resolved cut order |
| `/api/cut_order_interactive` | POST | `get_cut_order_interactive()` | Raw components + inventory for client-side resolve |
| `/api/cut_quantities` | GET/POST | `cut_quantities()` | Persist/retrieve user cut inputs |
| `/api/cut_order_csv` | GET | `export_cut_order_csv()` | CSV download |
| `/api/projection_settings` | GET/POST | `projection_settings()` | First-order projection config |
| `/api/first_order_override` | POST | `set_first_order_override()` | Per-SKU first-order demand override |
| `/api/first_order_overrides` | GET | `get_first_order_overrides()` | Current overrides + rolling averages |
| `/api/email_cut_order` | POST | `email_cut_order()` | Send cut order email |
| `/api/schedule_cut_order_email` | POST | `schedule_cut_order_email()` | Store email schedule |
| `/api/swap_execute` | POST | `swap_execute()` | Execute SKU swaps (L4148) |
| `/api/candidates/<cur>/<slot>` | GET | `get_candidates()` | Assignment candidates |
| `/api/monthly_boxes` | GET | `get_monthly_boxes()` | Monthly box config |

### STATE Keys Used (runtime, not persisted)
- `rmfg_inventory` — Current inventory from RMFG folder
- `rmfg_direct_sat` — Direct demand (pickable SKUs) for Saturday
- `rmfg_prcjam_sat` — PR-CJAM counts by curation for Saturday
- `rmfg_cexec_sat` — CEX-EC counts by curation for Saturday
- `rmfg_direct_rc` — Recharge direct demand
- `rmfg_prcjam_rc` — Recharge PR-CJAM counts
- `rmfg_cexec_rc` — Recharge CEX-EC counts
- `rmfg_direct_sh` — Shopify direct demand
- `rmfg_prcjam_sh` — Shopify PR-CJAM counts
- `rmfg_cexec_sh` — Shopify CEX-EC counts
- `rmfg_sat_demand` — Resolved Saturday demand
- `rmfg_attribution` — Demand attribution per SKU
- `rmfg_tue_demand` — Tuesday demand
- `bulk_weights` — Bulk raw material weights
- `shopify_first_order_demand` — First-order demand by SKU
- `demand_source` — Source labels per SKU
- `demand_source_ts` — Timestamp of demand source data

### Settings Keys Used (persisted JSON)
- `pr_cjam` — {curation: {cheese: sku, jam: sku}}
- `cex_ec` — {curation: cheese_sku}
- `cexec_splits` — {curation: {sku: ratio}}
- `global_extras` — {slot_sku: resolved_sku}
- `inventory` — {sku: {qty, name, category, ...}}
- `wheel_inventory` — {sku: {weight_lbs, count, target_sku}}
- `bulk_conversions` — {keyword: {sku, packet_oz}}
- `monthly_box_recipes` — {month: {box_type: [[slot, sku, qty], ...]}}
- `monthly_box_counts` — {box_type: count}
- `first_order_projection` — {enabled: bool, active_curation: str, multiplier: int, recipe_only: bool}
- `first_order_overrides` — {sku: override_qty}
- `cut_quantities` — {wk1: {sku: qty}, wk2: {sku: qty}}
- `shopify_weeks_back` — int (default 8)

### resolve_demand() Details (L1241-1290)
```
Input: direct_demand, prcjam_counts, cexec_counts, pr_cjam, cex_ec, splits
Output: (demand_dict, attribution_dict)

1. Direct demand: sku -> qty (pass-through)
2. PR-CJAM: for each curation suffix, resolve_pr_cjam_with(suffix, pr_cjam) -> cheese SKU, add count
3. CEX-EC: for each curation suffix, resolve_cex_ec_with(suffix, cex_ec, splits) -> cheese SKU(s), add count(s)
4. Attribution tracks: {sku: {direct: N, prcjam: {cur: N}, cexec: {cur: N}}}
```

### get_cut_order_interactive() Response Shape (L3670-3838)
Returns JSON with:
- `skus`: {sku: {name, sliced, wheel_potential, bulk_potential, ...}} — per-SKU inventory + supply
- `raw_components`: {wk1: {direct: {}, prcjam_counts: {}, cexec_counts: {}}} — unresolved demand for client-side SUMIF
- `wk2_demand`: {sku: qty} — week 2 pre-resolved (server-side)
- `assignments`: {pr_cjam: {}, cex_ec: {}, cexec_splits: {}} — current assignment config
- `monthly_slots`: monthly box slot assignments
- `monthly_counts`: monthly box subscription counts
- `saved_cuts`: {wk1: {}, wk2: {}} — persisted user cut inputs
- `ship_dates`: [date1, date2] — upcoming Saturdays
- `all_curations`: list of all active curations

### Projection Settings Shape
```python
first_order_projection = {
    "enabled": True,
    "active_curation": "MONG",
    "multiplier": 3,       # clamped 1-10
    "recipe_only": True,
}
```

---

## 4. fulfillment_web/static/app.js — Cut Order View (5275 lines total)

### Key State Variables
| Line | Name | Type | Purpose |
|------|------|------|---------|
| L4167 | `cutOrderData` | object|null | Legacy cut order data |
| L4170 | `coData` | object|null | Raw data from /api/cut_order_interactive |
| L4171 | `coCuts` | {wk1: {}, wk2: {}} | User's cut quantity inputs |
| L4172 | `coSaveTimer` | timer|null | Debounce timer for saving cuts |
| L13 | `demandMode` | 'discrete'|'churned' | Demand display mode |

### CutOrderCalc Object (L4174-4252)
Client-side SUMIF calculator with two methods:
1. **`resolve(rawComponents, assignments)`** (L4176-4208):
   - Direct demand: pass-through
   - PR-CJAM: `rawComponents.prcjam_counts[cur]` -> `assignments.pr_cjam[cur].cheese` -> add count
   - CEX-EC: check `assignments.cexec_splits[cur]` for split ratios first, else `assignments.cex_ec[cur]` -> add count
   - Skips `BARE` curation for CEX-EC
   - Returns `{sku: total_demand}`

2. **`calculate(data, cuts, assignments)`** (L4212-4251):
   - Resolves wk1 demand client-side via `this.resolve(data.raw_components.wk1, assignments)`
   - wk2 demand comes pre-resolved from server: `data.wk2_demand`
   - For each SKU: computes sliced, supply, avail, dmdW1, afterW1, cutW1, goodW1, needW1, dmdW2, afterW2, cutW2, goodW2, needW2
   - Sorts by category: CH- first, then MT-, then AC-

### Functions (Cut Order Related)
| Line | Name | Purpose |
|------|------|---------|
| L789 | `loadMonthlyBoxes()` | Fetches /api/monthly_boxes |
| L794 | `renderMonthlyBoxes(data)` | Renders monthly box assignment UI |
| L844 | `openMonthlyBoxPicker(boxType, slotIndex)` | Opens picker for monthly box slot |
| L3198 | `toggleDemandMode()` | Switches discrete/churned demand display |
| L4254 | `loadCutOrder()` | Alias for loadCutOrderInteractive() |
| L4256 | `loadCutOrderInteractive()` | Fetches /api/cut_order_interactive, stores in coData |
| L4295 | `renderCutOrderInteractive()` | Renders interactive cut order table |
| L4446 | `recalcCutOrder()` | Re-runs CutOrderCalc.calculate() and re-renders |
| L4558 | `renderCutOrder(data)` | Legacy renderer (redirects to interactive if coData exists) |
| L4727 | `exportCutOrderCSV()` | Downloads CSV via /api/cut_order_csv |
| L4731 | `emailCutOrder()` | Sends email via /api/email_cut_order |
| L4859 | `loadRunwayMonthly()` | Loads monthly runway forecast |
| L4874 | `renderRunwayMonthly(data)` | Renders monthly runway grid |
| L4900 | `renderRunwayMonthlyGrid(skus, labels)` | Grid layout for runway |
| L5003 | `toggleMonthlyBreakdown(sku)` | Expands monthly breakdown per SKU |
| L5122 | `toggleDemandBreakdown(sku)` | Expands demand attribution per SKU |

### Tab/View Integration
- Cut order view ID: `cutorder-view` (L2130)
- Loaded when tab switched to 'cutorder' (L2143)
- Pre-loaded in background on page load (L1877)
- `renderCutOrder()` at L4558 redirects to `renderCutOrderInteractive()` if `coData` exists (L4560)

---

## Existing Patterns & Reusable Code

### Pattern: Deferred Resolution (Client-Side SUMIF)
The web app stores raw demand components (direct, prcjam_counts, cexec_counts) in STATE instead of pre-resolved demand. Client-side `CutOrderCalc.resolve()` re-resolves on every assignment change. This is the core pattern for interactive assignment editing.
- Found in: `app.js:CutOrderCalc.resolve()` (L4176) and `app.py:resolve_demand()` (L1241)

### Pattern: Dual Resolution (Overrides + Settings Fallback)
Both cut_order_generator.py and app.py resolve PR-CJAM/CEX-EC using:
1. Hardcoded overrides first (in generator) / settings first (in web app)
2. Settings JSON fallback
3. Skip if unresolvable
- Found in: `cut_order_generator.py:_resolve_order_skus()` (L291) and `app.py:resolve_pr_cjam_with()` (L1225)

### Pattern: Attribution Tracking
Every resolved demand SKU tracks its source: `{sku: {direct: N, prcjam: {cur: N}, cexec: {cur: N}}}`
- Found in: `app.py:resolve_demand()` (L1241) and `/api/demand_breakdown/<sku>` route

### Pattern: MONTHLY Box Exclusion
MONTHLY curations (AHB-MED, AHB-LGE, AHB-CMED) are excluded from per-curation PR-CJAM/CEX-EC demand and routed through separate editable slot tables instead.
- Found in: `cut_order_generator.py` L219,243,302,325,485 (guard: `if curation and curation not in ("MONTHLY", None)`)
- Found in: `build_cut_order_xlsx.py` L284-315 (slot tables)

### Utility: normalize_sku()
Uppercase + equivalence map. Exists in both cut_order_generator.py (L37) and app.py.

### Utility: is_pickable()
Excludes non-food SKUs. Exists in cut_order_generator.py (L41).

### Utility: resolve_curation_from_box_sku()
Maps box SKU to curation. Exists in cut_order_generator.py (L60).

---

## Dependencies & Integration Points

### External APIs
- **Recharge API**: `api.rechargeapps.com/charges` — cursor pagination, used by both standalone and web app
- **Shopify Admin API**: Orders endpoint — used for first-order projection and demand pull
- **SMTP**: Email cut order PDF

### Shared Settings File
- Path: `dist/inventory_reorder_settings.json` (or next to frozen exe)
- Shared between: inventory_reorder.py (tkinter), fulfillment_web/app.py (Flask), cut_order_generator.py (standalone)
- Contains: pr_cjam, cex_ec, cexec_splits, inventory, wheel_inventory, bulk_conversions, monthly_box_recipes, monthly_box_counts, first_order_projection, first_order_overrides, cut_quantities

### Shared Module
- `inventory_demand_report` — imported by build_cut_order_xlsx.py for `fetch_recharge_api`, `fetch_shopify_orders`, `PICKABLE_PREFIXES`

### Code Duplication
- SKU resolution logic is duplicated 3 times:
  1. `cut_order_generator.py:load_recharge_csv()` (L167-265)
  2. `cut_order_generator.py:load_shopify_csv()` (L268-403) with nested `_resolve_order_skus()` (L291)
  3. `app.py:resolve_demand()` (L1241) + `resolve_pr_cjam_with()` + `resolve_cex_ec_with()`
- Monthly box slot definitions duplicated in build_cut_order_xlsx.py (L290-310) and app.py (L45-67)
- MONTHLY exclusion guard duplicated across multiple functions

---

## Risks & Constraints

- **Hardcoded week data in cut_order_generator.py**: PR_CJAM_OVERRIDES, CEX_EC_OVERRIDES, CEXEC_SPLITS_OVERRIDES, WHEEL_INVENTORY, and file paths (RMFG_20260310) are all hardcoded to a specific week. Must be updated manually each week. Confidence: HIGH
- **Slot definition drift**: build_cut_order_xlsx.py and app.py each define their own slot lists. Comment at L289 warns they must match. No automated enforcement. Confidence: HIGH
- **Triple code duplication for SKU resolution**: Any change to resolution logic must be applied in 3 places. Confidence: HIGH
- **wk2 demand asymmetry**: wk1 demand is resolved client-side (interactive), but wk2 is pre-resolved server-side. Different code paths. Confidence: VERIFIED
- **MONTHLY box demand routing**: Monthly boxes bypass normal per-curation demand and flow through slot tables. Complex interaction between slot assignments, monthly_box_recipes, and monthly_box_counts. Confidence: VERIFIED
- **Large file sizes**: app.py (8109 lines) and app.js (5275 lines) are monolithic. High coupling between cut order logic and other features. Confidence: HIGH
- **Settings file shared across 3 apps**: Any schema change must be backward-compatible. Confidence: HIGH

---

## Unresolved Questions

- UNVERIFIED: The `inventory_demand_report` module imported by build_cut_order_xlsx.py -- its location and full API were not traced. Could not verify if it contains additional demand logic.
- UNCLEAR: Whether `pull_recharge_api()` and `pull_shopify_api()` in cut_order_generator.py share any code with the web app's Recharge/Shopify integration (different code paths suspected).
- UNCLEAR: The `generate_cut_order_pdf()` function at L7506 was not fully analyzed -- only confirmed to exist.
- UNCLEAR: Whether the `schedule_cut_order_email()` endpoint has any cron/scheduler backend or is UI-only config storage.
- UNVERIFIED: The `renderCutOrderInteractive()` function body in app.js (L4295-4446) was indexed but not fully extracted -- contains the HTML rendering logic for the interactive table.

---

## Discovery Metadata
- claude-mem searched: yes (no results found)
- MEMORY.md consulted: yes (6 relevant memory files referenced)
- Files examined: 4
- Functions cataloged: ~35
- API routes cataloged: 12
- Settings keys cataloged: ~20
- STATE keys cataloged: ~15
- Discovery depth: thorough
