# Architecture

**Analysis Date:** 2026-04-04

## Pattern Overview

**Overall:** Multi-subsystem fulfillment automation platform with modular separation of concerns.

**Key Characteristics:**
- **MCP-first design** - AppyHourMCP server centralizes tools, exposing them to Claude Code
- **Layered architecture** - Shared business logic (`appyhour/`) → Domain modules (GelPackCalculator, InventoryReorder, ShippingReports) → UI/CLI adapters
- **Data-driven workflows** - XLSX files drive matrix validation, demand forecasting, and fulfillment pipeline
- **API integration hub** - Recharge (subscriptions) + Shopify (orders) as primary data sources with demand reconciliation

## Layers

**Business Logic (`appyhour/`):**
- Purpose: Pure functions and algorithms for reorder calculations, thermal analysis, and shipping risk assessment
- Location: `appyhour/reorder.py`, `appyhour/shipping.py`, `appyhour/thermal.py`
- Contains: Domain functions, no API calls, no UI dependencies
- Depends on: Standard library only
- Used by: All other layers (MCP tools, web apps, standalone scripts)

**Domain Modules:**
- **GelPackCalculator** (`GelPackCalculator/`): Thermal gel pack sizing, shipping temperature analysis, invoice data ingestion
  - Entry: `GelPackCalculator/gel_pack_shopify.py` (tkinter desktop app)
  - Functions: Temperature forecasting, hub transit modeling, feedback import
  
- **InventoryReorder** (`InventoryReorder/`): Weekly demand forecasting, cut order generation, production matrix validation
  - Entry: `InventoryReorder/inventory_reorder.py` (tkinter app)
  - Core: `build_cut_order_xlsx.py` generates weekly production orders from Recharge + Shopify demand
  
- **ShippingReports** (`ShippingReports/`): Shipping analytics, cost analysis, carrier performance tracking
  - Entry: `ingest.py` (data pipeline)
  - Functions: Invoice parsing, delivery time analysis, cost aggregation

- **Matrix Commander** (`matrix_commander.py` + `matrix_commander_web/`): Fulfillment pipeline orchestration
  - Standalone CLI: Validates XLSX, checks inventory, detects shortages
  - Web app: Flask SPA on port 5188, pywebview (netfx) desktop wrapper
  - Functions: Order validation, demand reconciliation, gift order detection, Shopify sync

**MCP Server (`AppyHourMCP/server.py`):**
- Purpose: Exposes domain tools as MCP resources for Claude Code integration
- Location: `AppyHourMCP/tools/` (individual tool modules)
- Contains: Tool wrappers that call domain logic, authentication handling, API bridging
- Depends on: Recharge, Shopify, Google Sheets, Gorgias APIs
- Used by: Claude Desktop via MCP protocol

**UI Layer:**
- **Tkinter apps** (`GelPackCalculator/gel_pack_*.py`, `InventoryReorder/inventory_reorder.py`): Cross-platform desktop with dark theme
- **pywebview apps** (`matrix_commander_web/app.py`, `InventoryReorder/fulfillment_web/`): Flask SPA wrapped in netfx desktop window
- **Web UI** (`matrix_commander_web/static/`, `matrix_commander_web/templates/`): Vanilla JS + CSS (no framework)

## Data Flow

**Weekly Fulfillment Cycle:**

1. **Data Ingestion** (Friday)
   - Recharge API: Fetch active subscriptions, bundle selections
   - Shopify API: Fetch order IDs, line items, fulfillableQuantity
   - `matrix_commander.py:compute_demand()` reconciles both sources

2. **Matrix Generation** (Saturday-Monday)
   - `InventoryReorder/build_cut_order_xlsx.py` creates production matrix
   - Input: Demand reconciliation + inventory levels + curation rules
   - Output: XLSX with columns for each SKU, rows for each order/curation combo

3. **Validation & QC** (Tuesday-Wednesday)
   - `matrix_commander.py validate <xlsx>`: Checks numeric order IDs, SKU mappings, production day
   - `matrix_commander_web/app.py` provides interactive validation + shortage report
   - Error detection: `InventoryReorder/Errors/*.py` scripts catch common fulfillment errors

4. **Inventory Check & Swaps** (Wednesday-Thursday)
   - Load inventory from CSV/JSON
   - `matrix_commander.py:find_shortages()` identifies insufficient SKU quantities
   - Interactive swap workflow: Select alternative from SUBSTITUTION_FAMILIES
   - `apply_swaps_to_xlsx()` updates production matrix

5. **Finalization & Sync** (Thursday-Friday)
   - `finalize_xlsx()` prepares for production
   - Gift order detection + merge: `identify_gift_orders()`, `merge_gift_xlsx()`
   - Shopify sync: `sync_order_to_shopify()` applies swaps via GraphQL order edit API
   - Recharge sync: Updates bundle selections if needed

**Shipping Analytics Flow:**

1. **Invoice Collection** (`GelPackCalculator/gmail_fedex_sync.py`, `download_fedex_imap.py`)
   - Download FedEx/OnTrac invoices via Gmail IMAP
   - Store in `shipping_invoice_db.py`

2. **Data Enrichment** (`ShippingReports/ingest.py`)
   - Parse invoice data (weight, destination, cost)
   - Lookup shipment temps from Shopify/webhook data
   - Enrich with delivery dates (UPS integration: `enrich_ups_delivery.py`)

3. **Analytics & Reporting**
   - Cost per shipment, carrier performance, on-time rates
   - Google Sheets integration for weekly ops reports

**State Management:**

- **XLSX as source of truth** - Production matrices drive all workflows
- **API caching** - Recharge/Shopify data fetched once per cycle, reconciled against matrix
- **Immutable updates** - Swap decisions generate new XLSX, never mutate original
- **Google Sheets for reporting** - Analytics sheets auto-generated from processed data

## Key Abstractions

**CheckResult (dataclass):**
- Purpose: Encapsulates validation results with error messages
- Location: `matrix_commander.py` (exported for use in web app)
- Pattern: Immutable result object with bool success, str message, dict errors
- Example: `check_numeric_order_ids(orders)` returns CheckResult with validation details

**SwapDecision (dataclass):**
- Purpose: Represents a single SKU substitution decision
- Location: `matrix_commander.py`
- Fields: original_sku, replacement_sku, reason, count
- Pattern: Immutable swap record for audit trail

**SyncResult (dataclass):**
- Purpose: Reports Shopify GraphQL sync status
- Location: `matrix_commander.py`
- Fields: success, graphql_errors, order_id, applied_variants
- Pattern: Captures both success/failure and detailed API response

**Repository-like Pattern:**
- `load_inventory_csv()`, `load_inventory_settings()`, `load_mfg_translations()` encapsulate data loading
- Each returns a dict or ConfigDict with validation
- Single source of truth for configuration (constants, mappings)

**MCP Tool Pattern:**
- Each tool module in `AppyHourMCP/tools/` exports `register(mcp)` function
- Tools are FastMCP-compatible callables with type hints
- Location: `AppyHourMCP/tools/*.py` (gelcalc.py, shopify.py, inventory.py, shipping.py, etc.)

## Entry Points

**MCP Server (Claude Code Integration):**
- Location: `AppyHourMCP/server.py`
- Triggers: Claude Desktop MCP client invocation
- Responsibilities: Load all tool modules, expose via MCP protocol, handle stdio transport
- Tools: 10+ including gelcalc, shopify, inventory, shipping, order_edit, matrix_qc, ops_summary_builder

**Matrix Commander CLI:**
- Location: `matrix_commander.py` (root level)
- Triggers: `python matrix_commander.py validate|check|full <args>`
- Responsibilities: File validation, inventory checking, shortage reporting
- Integration: Consumed by web app via function imports

**Matrix Commander Web:**
- Location: `matrix_commander_web/app.py`
- Triggers: `python app.py` (pywebview) or `python app.py --browser` (http://localhost:5188)
- Responsibilities: Interactive XLSX upload, validation, swap workflow, Shopify sync
- Frontend: `static/app.js` (vanilla JS state machine)

**GelPackCalculator Desktop:**
- Location: `GelPackCalculator/gel_pack_shopify.py`
- Triggers: `python gel_pack_shopify.py`
- Responsibilities: Thermal analysis, hub/transit configuration, Shopify forecast lookups
- UI: tkinter with dark theme

**InventoryReorder Desktop:**
- Location: `InventoryReorder/inventory_reorder.py`
- Triggers: `python inventory_reorder.py`
- Responsibilities: Demand forecasting, reorder point calculation, cut order generation
- UI: tkinter with dark theme

## Error Handling

**Strategy:** Layered validation with early detection and detailed user feedback.

**Patterns:**

- **Input validation:** `CheckResult` objects returned from validation functions rather than raising exceptions
  - Example: `check_numeric_order_ids()` returns CheckResult with list of invalid order IDs
  - Allows caller to decide: fail fast vs. collect all errors

- **API error handling:** Specific exception types for Recharge/Shopify failures
  - Retry logic with exponential backoff in `AppyHourMCP/tools/shopify.py`
  - Timeout: always use `timeout=30` for Recharge calls
  - Rate limiting: respect 429 responses with retry-after header

- **File I/O:** Path validation before read/write, UTF-8 encoding enforcement for XLSX
  - Example: `force UTF-8 output on Windows` (matrix_commander.py:30-31)

- **Web layer:** Flask error handlers in `matrix_commander_web/app.py` return JSON responses with 400/500 codes
  - Frontend validation before upload (max 50MB)

- **Desktop apps:** Thread-safe error logging to stderr, UI updates via `root.after()` callback

## Cross-Cutting Concerns

**Logging:** 
- MCP server: stderr logging with timestamps (AppyHourMCP/server.py)
- Desktop apps: logs to console with thread context
- Web app: Flask request logging

**Validation:** 
- Domain layer: Pure functions return CheckResult or raise ValueError
- API layer: Pydantic models for request/response validation
- Input layer: File format checks (XLSX schema, CSV headers)

**Authentication:** 
- Recharge: `RECHARGE_API_TOKEN` env var (v2021-11 header)
- Shopify: GraphQL endpoint + access token from `_get_shopify_auth()`
- Google Sheets: OAuth token refresh in `AppyHourMCP/tools/google_sheets.py`
- Gorgias: API token for customer lookup

**Configuration:** 
- Constants: `AppyHourMCP/tools/constants.py` (NAME_TO_SKU, FOOD_PREFIXES, curations)
- Env vars: .env file (present, not read by tools — use os.environ)
- YAML configs: `GelPackCalculator/config.py` for hub definitions, carrier settings

**Caching:**
- Recharge bundle selections cached per cycle (avoid re-fetching)
- Shopify variant lookups cached (price, gid)
- Inventory CSV loaded once per session

---

*Architecture analysis: 2026-04-04*
