# Discovery Brief: 3 Fulfillment Web App Features

## Prior Context
No prior session context found in claude-mem for these specific features.
Relevant memory entries consulted: demand-pipeline-architecture, cut-order-logic, fulfillment-app-redesign, production-matrix-swaps, shopify-order-edit-patterns.

---

## Feature 1: Auto-Depletion from Dropbox

### Current State

**Depletion files** are `AHB_WeeklyProductionQuery*.xlsx` — parsed by `parse_depletion_xlsx()` (app.py L1368-1408). Parser reads columns matching `"AHB (S_REG):"`, resolves product names to SKUs via meal-type-export CSV mapping, sums per-SKU quantities.

**Three depletion entry points exist:**

| Route | Line | Trigger | Description |
|-------|------|---------|-------------|
| `/api/depletion_parse` | L6096 | Manual upload button | Parses uploaded XLSX, returns preview to drawer |
| `/api/depletion_apply` | L6141 | "Apply" button in drawer | Subtracts sku_totals from STATE inventory |
| `/api/auto_deplete` | L6201 | Called by `runAll()` JS | Auto-finds latest 2 WeeklyProductionQuery XLSX in local dirs |

**Current auto_deplete search dirs** (L6208-6221): Searches `Shipments/` subfolders under project root and parent. Pattern: `*.xlsx` with `weeklyproductionquery` in filename. Tracks applied files in `settings.depletion_applied_files` to prevent re-application.

**Dropbox sync** (`/api/dropbox_sync`, L4273-4574): Downloads the **inventory snapshot** (Product Inventory CSV/XLSX) from Dropbox, NOT depletion files. Uses Dropbox API v2 `files/list_folder` + `files/download`. Auth via refresh_token or shared_link.

**run_all pipeline** (L4094-4251): Step 1 = Dropbox sync (inventory), Step 2 = local depletion from `Shipments/` dir (date-filtered by snapshot date), Step 3 = Recharge, Step 4 = Shopify, Step 5 = Calculate.

**Key gap**: Depletion XLSX files are NOT on Dropbox currently. They're only searched locally. The feature would need to either (a) list/download depletion files from a Dropbox folder, or (b) watch a Dropbox folder for new depletion files.

### Frontend (index.html + app.js)

- **Depletion button**: L41 `<button onclick="uploadDepletion()">Depletion</button>` — triggers hidden file input (L45-46)
- **Depletion drawer**: L748-758 `#depletion-drawer` — shows parse results, "Apply Depletion" button
- **JS flow**: `uploadDepletion()` (app.js L2859) -> `handleDepletionFile()` (L2863) -> POST to `/api/depletion_parse` -> `renderDepletionResults()` (L2889) -> user clicks Apply -> `applyDepletion()` (L2941) -> POST to `/api/depletion_apply`
- **runAll()** (app.js L1791): calls `/api/auto_deplete` at step 3 (L1831-1833)

### Affected Files & Symbols

| File | Key Symbols | Role | Confidence |
|------|-------------|------|------------|
| `fulfillment_web/app.py` L4273-4574 | `dropbox_sync()` | Downloads inventory from Dropbox | VERIFIED |
| `fulfillment_web/app.py` L6096-6138 | `depletion_parse()` | Parses uploaded XLSX | VERIFIED |
| `fulfillment_web/app.py` L6141-6198 | `depletion_apply()` | Applies depletion to inventory | VERIFIED |
| `fulfillment_web/app.py` L6201-6315 | `auto_deplete()` | Auto-finds local XLSX files | VERIFIED |
| `fulfillment_web/app.py` L4094-4251 | `run_all()` | Full pipeline orchestrator | VERIFIED |
| `fulfillment_web/app.py` L1368-1478 | `parse_depletion_xlsx()`, `map_depletion_to_skus()` | Core parsing logic | VERIFIED |
| `fulfillment_web/static/app.js` L2859-2978 | `uploadDepletion()`, `handleDepletionFile()`, `applyDepletion()` | Frontend depletion flow | VERIFIED |
| `fulfillment_web/static/app.js` L1831-1833 | runAll step 3 | Calls auto_deplete | VERIFIED |
| `fulfillment_web/templates/index.html` L41,L45-46,L748-758 | Depletion button, input, drawer | UI elements | VERIFIED |

### Dropbox Settings Keys (from settings_config, L6916-6963)
- `dropbox_app_key`, `dropbox_app_secret`, `dropbox_refresh_token`, `dropbox_shared_link`, `dropbox_access_token`
- These are NOT in the `allowed` set for settings_config POST (L6946-6952) — they're managed via dedicated auth routes

### Reusable Patterns
- Dropbox API auth/download pattern: L4295-4362 (token refresh, list_folder, files/download)
- File-already-applied tracking: `settings.depletion_applied_files` list (L6244, L6300)
- Snapshot before/after depletion: `_take_snapshot()` pattern (L6156, L6191)
- Journal entry pattern for activity log (L6179-6186)

---

## Feature 2: Swap Integration on Shortage Rows

### Current State

**Shortage data sources in the app:**

| Location | Line | Context |
|----------|------|---------|
| `/api/cut_order` | L3518-3653 | Returns `shortages[]` list with full cut order line data |
| `/api/cut_order_interactive` | L3664-3830 | Returns per-SKU data for interactive cut order view |
| `/api/substitutions` | L4013-4089 | Returns shortage+surplus suggestions (no swap execution) |
| `/api/wed_po` | L850-900 | Generates PO lines from shortages |
| Dashboard NET table | JS L4074-4159 | Shows NEED badges on shortage rows |

**Cut order shortage line structure** (L3603-3617):
```python
{"sku", "sliced", "rc_demand", "sh_demand", "total_demand", "gap",
 "wheels_to_cut", "wheels_available", "wheel_weight", "pcs_from_cut",
 "net", "status", "attribution"}
```

**Substitution panel** (app.js L1885-1936): Shows deficit + candidate surplus cheeses. Currently read-only — no "execute swap" button. UI is in `#subs-overlay` / `#subs-panel` (index.html L974-987).

**NO existing swap/GraphQL code in the web app** — VERIFIED by grep. All swap logic is in standalone scripts under `InventoryReorder/Errors/`.

### Swap Script Pattern (from swap_bras_to_sop.py, L1-169)

Canonical 3-step GraphQL swap pattern:
1. **`orderEditBegin`** (L93-103): Opens edit session, returns `calculatedOrder.lineItems`
2. **`orderEditSetQuantity`** (L114-117): Sets old SKU line item qty to 0
3. **`orderEditAddVariant`** (L121-126): Adds new SKU variant with original qty
4. **`orderEditCommit`** (L128-133): Commits with `notifyCustomer: false` + staffNote

**Variant lookup**: Uses `productVariants(first:5, query:"sku:NEW_SKU")` GraphQL query, selects $0 variant.

**Target filtering**: Fetches unfulfilled orders with `_SHIP_` tag, checks `_rc_bundle` property to identify curation items (vs paid items which should not be swapped).

**Key concern**: The swap scripts use settings directly for Shopify credentials (`shopify_store_url`, `shopify_access_token`). The web app already has these in settings (used by `/api/shopify_sync`).

### Files with swap patterns (33 scripts found)

| File | Pattern |
|------|---------|
| `Errors/swap_bras_to_sop.py` | MT-BRAS -> MT-SOP |
| `Errors/swap_brie_to_ebrie.py` | CH-BRIE -> CH-EBRIE |
| `Errors/swap_lfolive_to_ppcm.py` | AC-LFOLIVE -> AC-PPCM |
| `Errors/swap_marc_to_rhaz.py` | AC-MARC -> AC-RHAZ |
| `Errors/swap_curation_skus.py` | Generic curation swap |
| `AppyHourMCP/tools/order_edit.py` | MCP tool for order editing |

### Frontend Insertion Points

- **Substitution panel** (index.html L974-987): Each shortage item shows substitutes. A "Swap" button could be added per-substitute in the `sub-item` div (app.js L1920-1925).
- **Cut order view** (index.html L432-553): Shortage rows show `NEED X` badges. A swap action could be added per-row.
- **Dashboard NET table**: Status column shows `SHORTAGE` — could add swap trigger.

### Dependencies
- Shopify GraphQL API credentials: `shopify_store_url` + `shopify_access_token` (already in settings)
- Variant GIDs: Need lookup by SKU (pattern in swap scripts)
- Order targeting: Need `_SHIP_` tag + `_rc_bundle` property filtering
- Rate limiting: 0.5s sleep between operations (per shopify-rate-limiting memory)

---

## Feature 3: Auto Date Detection for Cut Orders

### Current State

**generate_cut_order.py** (standalone script, 524 lines): Has **NO date-based logic at all**. Uses `datetime.now()` only for:
- XLSX title: `date_str = datetime.now().strftime("%B %d, %Y")` (L252)
- Output filename: `date_tag = datetime.now().strftime("%Y-%m-%d")` (L490)

Demand is pulled live from Recharge (queued charges) + Shopify (unfulfilled orders) — no date filtering. The script generates a single cut order for "now" with no week concept.

**Web app cut order** (`/api/cut_order_interactive`, L3664-3830): Uses **Wk1 (Saturday)** and **Wk2 (Tuesday)** demand windows from STATE. These are populated by:
- Recharge sync (`/api/recharge_sync`, L4654): Bins charges by scheduled_at date into Saturday/Tuesday windows
- Shopify sync (`/api/shopify_sync`, L4976): Uses `_SHIP_` tags to bin orders

**Date references in cut order interactive response** (L3804-3829):
```python
"demand_source": STATE.get("demand_source") or settings_source or "none",
"demand_source_ts": STATE.get("demand_source_ts") or s.get("demand_source_ts", ""),
```

**No date detection or date configuration exists in the cut order view.** The Wk1/Wk2 labels in the HTML are hardcoded as "Dmd W1" / "Dmd W2" (index.html L479-486).

### Where Dates ARE Determined

| Location | Method | Lines |
|----------|--------|-------|
| Recharge sync | Bins by `scheduled_at` into Sat/Tue windows | app.py ~L4654+ |
| Shopify sync | Uses `_SHIP_YYYY-MM-DD` tags | app.py ~L4976+ |
| run_all depletion | Extracts date from filename `MM-DD-YY` pattern | app.py L4144 |
| Dropbox snapshot | Extracts date from filename `MM-DD-YY` | app.py L4557 |

### cut_order_xlsx_process.md (from memory)
States: "MONTHLY demand flow (excluded from rc_wk1, routed through SUMIF slot tables), weekly update checklist (dates, depletions, output filename). Slots must match `fulfillment_web/app.py`."

### Frontend Cut Order View (index.html L432-553)

Summary bar: Wk1 Needs, Wk2 Needs, Total Demand, SKUs, Source (L436-456)
Actions: Hide zero toggle, Refresh, Export CSV (L457-463)
Table headers: SKU, Name, Avail, Supply, [sep], Dmd W1, After W1, Cut W1, Status, [sep], Dmd W2, After W2, Cut W2, Status (L473-488)
Projection settings panel: Active Curation selector, Multiplier, Enabled checkbox, History Weeks (L503-543)

**JS**: `loadCutOrderInteractive()` (app.js L4047) fetches from `/api/cut_order_interactive` and renders. `renderCutOrderInteractive()` (L4074) builds the table. `CutOrderCalc.calculate()` is called at L4077 — client-side calculation.

### What "Auto Date Detection" Would Need

1. **Determine next ship dates**: Calculate next Saturday and next Tuesday from current date
2. **Display actual dates** instead of "Dmd W1" / "Dmd W2" in column headers (currently hardcoded at index.html L479-486)
3. **Possibly auto-set the date range** for Recharge charge filtering and Shopify order tag matching
4. **For generate_cut_order.py**: Could add date awareness for output naming, but the script already names by today's date

### Affected Files & Symbols

| File | Key Symbols | Role | Confidence |
|------|-------------|------|------------|
| `fulfillment_web/app.py` L3664-3830 | `get_cut_order_interactive()` | Returns raw data with Wk1/Wk2 windows | VERIFIED |
| `fulfillment_web/app.py` L3914-3937 | `projection_settings()` | First-order projection config | VERIFIED |
| `fulfillment_web/app.py` L3518-3653 | `get_cut_order()` | Server-side cut order calculation | VERIFIED |
| `fulfillment_web/static/app.js` L4047-4159 | `loadCutOrderInteractive()`, `renderCutOrderInteractive()` | Client-side cut order rendering | VERIFIED |
| `fulfillment_web/templates/index.html` L432-553 | Cut order view HTML | Table headers, summary bar | VERIFIED |
| `generate_cut_order.py` L1-524 | `main()`, `generate_xlsx()` | Standalone cut order script | VERIFIED |

---

## Cross-Cutting Dependencies

### Shared State (app.py)
- `STATE["rmfg_inventory"]` — current inventory (mutated by depletion)
- `STATE["bulk_weights"]` — wheel/block data from Dropbox sync
- `STATE["rmfg_direct_sat"]`, `STATE["rmfg_prcjam_sat"]`, `STATE["rmfg_cexec_sat"]` — Wk1 demand components
- `STATE["rmfg_tue_demand"]` — Wk2 pre-resolved demand
- `STATE["dropbox_snapshot_date"]` — used to filter depletion files by date
- `STATE["demand_source"]`, `STATE["demand_source_ts"]` — demand provenance

### Settings (inventory_reorder_settings.json)
- `depletion_applied_files` — tracks which depletion files have been applied
- `depletion_history` — log of all depletions
- `sku_translations` — product name -> SKU mapping
- `shopify_store_url`, `shopify_access_token` — needed for swap integration
- `pr_cjam`, `cex_ec`, `cexec_splits` — assignment configs affecting demand resolution

### File Sizes
- `app.py`: 7927 lines (very large single file)
- `app.js`: 5054 lines (very large single file)
- `index.html`: 1015 lines
- `generate_cut_order.py`: 524 lines

---

## Risks & Constraints

- **app.py size (7927 lines)**: Adding swap GraphQL logic will further bloat this file. Consider extracting to a helper module. Confidence: HIGH
- **Shopify rate limiting**: Swap operations need 0.5s delays between orders. Bulk swaps on shortage rows could take minutes for 50+ orders. Need async/progress pattern. Confidence: HIGH
- **Dropbox depletion file location**: Currently depletion files are NOT on Dropbox — only inventory snapshots are. Need to confirm where depletion files would live on Dropbox. Confidence: HIGH
- **STATE mutation**: `depletion_apply` mutates `STATE["rmfg_inventory"]` in-place (L6162). All downstream calculations depend on this. Confidence: VERIFIED
- **No date config in cut order**: The web app has no UI for setting/overriding ship dates. Dates flow implicitly from Recharge/Shopify sync timestamps. Confidence: VERIFIED
- **generate_cut_order.py is date-agnostic**: It pulls live demand with no date filtering. Auto-date detection would be a new concept for this script. Confidence: VERIFIED

## Unresolved Questions

- UNCLEAR: For Feature 1, where exactly on Dropbox would depletion XLSX files be stored? Same folder as inventory? A subfolder?
- UNCLEAR: For Feature 2, should swaps target all unfulfilled orders or only next ship date? The standalone scripts filter by `_SHIP_` tag.
- UNCLEAR: For Feature 3, what specific "date detection" is needed — is it just showing "Mar 28" instead of "Wk1" in column headers, or does it include auto-detecting which Recharge charges map to which ship date?
- UNVERIFIED: The Recharge sync binning logic (around L4654+) — not read in detail; it determines how charges map to Sat/Tue windows.

## Discovery Metadata
- claude-mem searched: yes (3 queries, 0 results)
- Files examined: 7 (app.py, app.js, index.html, generate_cut_order.py, swap_bras_to_sop.py, plus greps across Errors/)
- Discovery depth: thorough
