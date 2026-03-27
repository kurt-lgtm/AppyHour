# Discovery Brief: 6 MCP Server Optimizations

## Prior Context
No prior claude-mem observations found for AppyHourMCP.

## Affected Files & Symbols

| File | Key Symbols | Role | Confidence |
|------|-------------|------|------------|
| `AppyHourMCP/server.py` | `mcp`, `register()` calls | Tool registration hub; imports 9 tool modules | VERIFIED |
| `AppyHourMCP/utils.py` | `get_gelcalc_settings()`, `get_inventory_settings()`, `format_error()` | Shared path setup, settings cache, error formatting | VERIFIED |
| `AppyHourMCP/tools/shopify.py` | `_get_client()`, `register()` | Shopify tools: fetch_orders, analyze_orders, update_order_tags | VERIFIED |
| `AppyHourMCP/tools/shipping.py` | `_load_shipments()`, `register()` | Shipping tools: cost/transit analysis, misroutes, routing tags | VERIFIED |
| `AppyHourMCP/tools/context.py` | `MEMORY_DIR`, `register()` | Memory resources, inventory snapshot, cut order config, errors, depletions | VERIFIED |
| `AppyHourMCP/tools/ops_summary_builder.py` | `_APPDATA_SETTINGS`, `_load_shipment_volumes()`, `SPREADSHEET_ID` | Ops summary report builder with Google Sheets integration | VERIFIED |
| `compare_matrix.py` (root) | Script-level logic, `NAME_TO_SKU` | Compares production matrix Excel vs Shopify orders, finds discrepancies | VERIFIED |
| `_gen_swap_csv.py` (root) | Script-level logic, `NAME_TO_SKU` | Generates per-order swap CSV from matrix vs Shopify diff | VERIFIED |
| `InventoryReorder/Errors/swap_curation_skus.py` | `gql()`, `fix_order()`, `find_swap_orders()` | Reference implementation for GraphQL order edits | VERIFIED |

## Registration Pattern

`server.py` imports each tool module and calls `module.register(mcp)`. Inside `register()`, tools are decorated with `@mcp.tool()`. Pydantic `BaseModel` subclasses define inputs. This pattern is consistent across all 9 modules.

## Optimization 1: Unify Shopify Credentials

**Current state (VERIFIED):**
- `shopify.py` uses GelPackCalculator's OAuth client-credentials flow: `ShopifyClient(store, client_id, client_secret)` from `gel_pack_shopify.py:687`. This does a `POST /admin/oauth/access_token` with `grant_type=client_credentials` to get a 24h token.
- `shipping.py:apply_zip_routing_tags()` (line 332-336) uses InventoryReorder's static access token: `settings.get("shopify_access_token")` via `get_inventory_settings()`. This is a permanent Admin API access token with broader scopes (used by all `InventoryReorder/Errors/` scripts for GraphQL order edits).
- The OAuth token from GelPackCalculator may lack scopes needed for order editing (no `write_order_edits` scope found in codebase).

**Credential sources:**
- GelPackCalculator: `gel_calc_shopify_settings.json` -> `shopify_store`, `shopify_client_id`, `shopify_client_secret` (OAuth client credentials)
- InventoryReorder: `inventory_reorder_settings.json` -> `shopify_store_url`, `shopify_access_token` (static Admin API token)

**Key finding:** The InventoryReorder token is already used by 10+ scripts in `InventoryReorder/Errors/` for GraphQL order edits. All swap scripts (`swap_curation_skus.py`, `swap_fowc_owc_to_mcpc.py`, etc.) use this token pattern.

## Optimization 2: Add Swap/Order-Edit Tool

**Existing pattern (VERIFIED in `swap_curation_skus.py`):**
1. `orderEditBegin(id: $id)` -> gets `calculatedOrder.id` + `lineItems`
2. `orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: 0)` to remove old SKU
3. `orderEditAddVariant(id: $id, variantId: $variantId, quantity: 1, allowDuplicates: true)` to add new SKU ($0 variant)
4. `orderEditCommit(id: $id, notifyCustomer: false, staffNote: "...")` to finalize

**15 existing swap scripts** in `InventoryReorder/Errors/` all follow this exact pattern. They all hardcode `InventoryReorder/dist/inventory_reorder_settings.json` for credentials.

**Dependencies for new tool:** Needs access to `$0 variant GIDs` for each SKU. These are currently hardcoded per-script (e.g., `SWAPS` dict in `swap_curation_skus.py`). A generic tool would need variant GID lookup or accept it as input.

## Optimization 3: Add Production Matrix QC Tool

**Current state (VERIFIED):**
- `compare_matrix.py` is a standalone 221-line script. It:
  1. Reads `AHB_WeeklyProductionQuery_03-17-26_vF.xlsx` (hardcoded filename, line 83)
  2. Parses `Access_LIVE` sheet, maps product names to SKUs via `NAME_TO_SKU` (80 entries)
  3. Fetches Shopify orders by RMFG tag (hardcoded `RMFG_20260317`, line 112)
  4. Compares matrix assignments vs Shopify line items
  5. Reports: SKUs in matrix but missing from Shopify, and vice versa
  6. AC-PRPE replacement analysis
- Uses InventoryReorder credentials (static access token)
- Hardcoded to relative path `'AHB_WeeklyProductionQuery_03-17-26_vF.xlsx'`

**To wrap as MCP tool:** Needs parameterized Excel path, RMFG tag, and possibly configurable NAME_TO_SKU map.

## Optimization 4: Add Swap CSV Generator Tool

**Current state (VERIFIED):**
- `_gen_swap_csv.py` is a standalone 155-line script. It:
  1. Reads same Excel file (hardcoded `C:/Users/Work/Claude Projects/AppyHour/AHB_WeeklyProductionQuery_03-17-26_vF.xlsx`, line 54)
  2. Fetches Shopify orders by RMFG tag (hardcoded `RMFG_20260317`, line 81)
  3. Generates per-order swap list (AC- prefix only, line 114)
  4. Writes CSV to `C:/Users/Work/Downloads/ac-swap-list-2026-03-17.csv` (line 138)
  5. Prints remove/add summary counts
- Uses InventoryReorder credentials
- Shares `NAME_TO_SKU` dict with `compare_matrix.py` (nearly identical, slight differences in entries)

**Overlap:** `compare_matrix.py` and `_gen_swap_csv.py` share ~60% logic (Excel parsing, Shopify fetch, NAME_TO_SKU). A shared utility could reduce duplication.

## Optimization 5: Fix Hardcoded Paths

**Verified hardcoded paths:**

| File | Line | Hardcoded Value | Issue |
|------|------|----------------|-------|
| `context.py` | 10 | `Path.home() / ".claude" / "projects" / "C--Users-Work" / "memory"` | Hardcoded Claude project path fragment `C--Users-Work` |
| `ops_summary_builder.py` | 19 | `Path(os.environ.get("APPDATA", "")) / "AppyHour" / "gel_calc_shopify_settings.json"` | Uses APPDATA env var (Windows-specific but appropriate) |
| `ops_summary_builder.py` | 168 | `Path(__file__) ... / "Issue & Resolution Guide (2).xlsx"` | Relative to script (OK pattern, but filename has space + parens) |
| `ops_summary_builder.py` | 20 | `SPREADSHEET_ID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"` | Hardcoded Google Sheet ID |
| `shipping.py` | 355 | `TAG = "!FedEx 2Day - Dallas_AHB!"` | Hardcoded routing tag (line 355) |
| `compare_matrix.py` | 83 | `'AHB_WeeklyProductionQuery_03-17-26_vF.xlsx'` | Hardcoded Excel filename with date |
| `_gen_swap_csv.py` | 54 | `'C:/Users/Work/Claude Projects/AppyHour/AHB_WeeklyProductionQuery_03-17-26_vF.xlsx'` | Absolute path with date |
| `_gen_swap_csv.py` | 138 | `'C:/Users/Work/Downloads/ac-swap-list-2026-03-17.csv'` | Hardcoded output path |
| `compare_matrix.py` | 4 | `'InventoryReorder/dist/inventory_reorder_settings.json'` | Relative path (works from AppyHour root) |

**context.py MEMORY_DIR (line 10):** The `C--Users-Work` fragment is a Claude Desktop convention for project paths. This will break on any other machine or user account.

## Optimization 6: Add Error Handling to shipping.py

**Current state (VERIFIED):**
- `_load_shipments()` (lines 43-58) already has a two-path fallback: tries `output/shipments.json` first, then falls back to newest `.json` in `data/` directory. If neither exists, raises `FileNotFoundError`.
- All tool functions wrap calls in `try/except` and return `format_error(e, context)`.
- `apply_zip_routing_tags()` (lines 314-484) has comprehensive error handling but **silently breaks out of pagination** on non-200 status (line 369-370: `if resp.status_code != 200: break`). No error is surfaced to the caller.
- No graceful handling if `config.yaml` is missing for `detect_misroutes()` (line 211-213 checks `config_path.exists()` and falls back to empty territories -- this IS graceful).

## Dependencies & Integration Points

- `shopify.py` depends on `gel_pack_shopify.py` (GelPackCalculator) for `ShopifyClient` class and analysis functions.
- `shipping.py` depends on `reports.analyze` and `reports.recommend` (ShippingReports).
- `context.py` depends on `InventoryReorder/dist/inventory_reorder_settings.json`.
- `ops_summary_builder.py` depends on `google_integration.py` (in GelPackCalculator), `openpyxl`, and Google Sheets API.
- `compare_matrix.py` and `_gen_swap_csv.py` depend on `openpyxl`, `requests`, and InventoryReorder settings.
- `server.py` imports ALL tool modules at startup; a failure in any one blocks the entire server.

## Test Coverage

**VERIFIED: Zero test files exist for MCP tools.** The only test-like files are:
- `tools/test_gorgias_lookup.py` (a manual test script, not pytest)
- No `tests/` directory in `AppyHourMCP/`
- No pytest configuration for MCP tools

## Risks & Constraints

- **Scope mismatch (HIGH):** The GelPackCalculator OAuth token used by `shopify.py` may lack `write_order_edits` scope. Swapping to InventoryReorder's static token would unify but requires verifying the static token has all needed scopes.
- **NAME_TO_SKU drift (MEDIUM):** `compare_matrix.py` and `_gen_swap_csv.py` have slightly different `NAME_TO_SKU` dicts. A shared constant would prevent drift.
- **Server startup fragility (MEDIUM):** All 9 tool modules are imported at server start. A missing dependency in any module kills the entire server. No lazy-import fallback at the server level.
- **No tests (HIGH):** Zero test coverage means refactoring risks regressions with no safety net.
- **Hardcoded date strings (HIGH):** Both standalone scripts have date-specific values (`RMFG_20260317`, Excel filenames with dates) that change weekly. MCP tool versions must parameterize these.

## Fan-Out Assessment

| Change | Files Touched | New Files | Callers Affected |
|--------|--------------|-----------|-----------------|
| 1. Unify credentials | `shopify.py`, `utils.py` | 0 | 3 Shopify tools |
| 2. Swap/order-edit tool | `server.py`, new tool file | 1 | 0 (new) |
| 3. Matrix QC tool | `server.py`, new tool file | 1 | 0 (new) |
| 4. Swap CSV tool | `server.py`, new tool file | 1 | 0 (new) |
| 5. Fix hardcoded paths | `context.py`, `ops_summary_builder.py`, `shipping.py` | 0 | 3-5 tools |
| 6. Error handling | `shipping.py` | 0 | 1 tool |

## LITE vs FULL Recommendation

**LITE (changes 1, 5, 6):** Low risk, no new files. Touches 4 existing files. Could be done in one session. Unblocks credential issues and fixes brittleness.

**FULL (all 6):** Adds 3 new tool files + shared utility for NAME_TO_SKU. Higher risk due to zero test coverage. Recommend writing tests for existing tools before adding new ones, or at minimum writing tests alongside new tools.

## Unresolved Questions

- UNVERIFIED: What scopes the GelPackCalculator OAuth token actually has (would need to inspect the Shopify app configuration in the admin panel, not in code).
- UNVERIFIED: Whether `google_integration.py` import in `ops_summary_builder.py` works reliably (it's in GelPackCalculator, added to sys.path at line 17).
- UNCLEAR: Whether `compare_matrix.py` and `_gen_swap_csv.py` should become one combined tool or remain separate (they serve different purposes but share heavy overlap).
- UNCLEAR: Where `$0 variant GIDs` should be stored for a generic swap tool (currently hardcoded per-script; could use settings JSON or a lookup API call).

## Discovery Metadata
- claude-mem searched: yes (no results)
- Files examined: 11
- Discovery depth: thorough
