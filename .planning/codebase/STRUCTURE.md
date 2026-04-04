# Codebase Structure

**Analysis Date:** 2026-04-04

## Directory Layout

```
AppyHour/
├── appyhour/                       # Shared business logic (no UI, no API)
│   ├── __init__.py
│   ├── reorder.py                  # Demand/reorder calculations
│   ├── shipping.py                 # Shipping risk & thermal logic
│   └── thermal.py                  # Thermal analysis (box, insulation, transit)
│
├── AppyHourMCP/                    # MCP server + tools for Claude Code
│   ├── server.py                   # MCP server entrypoint
│   ├── tools/                      # Tool implementations
│   │   ├── constants.py            # NAME_TO_SKU, FOOD_PREFIXES, curations
│   │   ├── context.py              # Context/reference tools
│   │   ├── gelcalc.py              # Gel pack sizing tool
│   │   ├── google_sheets.py        # Sheets API integration
│   │   ├── gorgias.py              # Gorgias CRM lookup
│   │   ├── gorgias_sheets_sync.py  # Sync Gorgias data to Sheets
│   │   ├── inventory.py            # Inventory query tool
│   │   ├── matrix_qc.py            # Matrix validation tool
│   │   ├── ops_summary_builder.py  # Weekly ops report generator
│   │   ├── order_edit.py           # Shopify order edit tool
│   │   ├── shipping.py             # Shipping analytics tool
│   │   ├── shopify.py              # Shopify GraphQL wrapper
│   │   ├── run_enrich_v2.py        # Enrichment runner
│   │   └── rebuild_ops_formulas.py # Formula rebuilder
│   ├── utils.py                    # Shared utility functions
│   └── order_lookup.py             # Order search utility
│
├── GelPackCalculator/              # Thermal analysis desktop app
│   ├── gel_pack_shopify.py         # Main tkinter app (primary)
│   ├── gel_pack_webview.py         # Alternate pywebview version
│   ├── appyhour_desktop.py         # Desktop app utilities
│   ├── appyhour_sku_map.py         # SKU name mapping
│   ├── config.py                   # Hub/carrier configuration
│   ├── shipping_invoice_db.py      # Invoice database schema
│   ├── google_integration.py       # Google Sheets API wrapper
│   ├── gmail_fedex_sync.py         # FedEx invoice fetcher
│   ├── download_fedex_imap.py      # IMAP downloader
│   ├── download_ontrac_imap.py     # OnTrac IMAP downloader
│   ├── invoice_scanner.py          # PDF invoice parser
│   ├── forecast_gel_from_charges.py # Demand forecasting
│   ├── import_feedback_csv.py      # Customer feedback import
│   ├── import_*.py                 # Data import utilities
│   ├── weekly_scheduler.py         # Weekly task scheduler
│   ├── web_ui/                     # HTML/JS UI for web version
│   │   ├── app.js                  # Main application logic
│   │   ├── shipping.js             # Shipping-specific UI
│   │   ├── kori.js                 # Mascot animation engine
│   │   └── action-flash.js         # Notification system
│   └── dist/                       # Packaged app distribution
│
├── InventoryReorder/               # Demand forecasting & cut order generation
│   ├── inventory_reorder.py        # Main tkinter app
│   ├── build_cut_order_xlsx.py     # XLSX generator (core)
│   ├── ship_dates.py               # Ship date calendar logic
│   ├── inventory_demand_report.py  # Demand reporting
│   ├── fulfillment_web/            # Pywebview fulfillment dashboard
│   │   ├── app.py                  # Flask SPA entrypoint
│   │   ├── static/
│   │   │   ├── app.js              # Frontend state machine
│   │   │   ├── shipping.js
│   │   │   ├── kori.js             # Shared mascot
│   │   │   └── styles.css
│   │   ├── templates/
│   │   │   └── index.html
│   │   └── uploads/                # Uploaded file storage
│   └── Errors/                     # Error detection scripts (24 scripts)
│       ├── error_order_rules.py    # Error class definitions
│       ├── detect_*.py             # Error detection scripts
│       ├── check_*.py              # Validation checks
│       ├── fix_*.py                # Error fixers
│       └── find_*.py               # Analysis helpers
│
├── ShippingReports/                # Shipping analytics
│   ├── ingest.py                   # Data pipeline entrypoint
│   └── enrich_ups_delivery.py      # UPS delivery enrichment
│
├── matrix_commander.py             # Fulfillment pipeline validator (standalone)
├── matrix_commander_web/           # Fulfillment web app
│   ├── app.py                      # Flask SPA + pywebview wrapper
│   ├── static/
│   │   ├── app.js                  # Interactive validation UI
│   │   ├── styles.css              # Dark FUI theme
│   ├── templates/
│   │   └── index.html
│   └── uploads/                    # Uploaded XLSX storage
│
├── tests/                          # Test suite
│   ├── conftest.py                 # pytest configuration
│   ├── fixtures/                   # Test data files
│   ├── test_cut_order_helpers.py
│   ├── test_error_detection.py
│   ├── test_reorder.py
│   ├── test_routing_tags.py
│   ├── test_shipping.py
│   ├── test_thermal.py
│   └── test_weekly_cycle_e2e.py
│
├── pyproject.toml                  # Project config (dependencies, pytest, ruff, pyright)
├── .env                            # Environment variables (Recharge token, Shopify endpoint)
├── conftest.py                     # Root pytest config
└── *.py (root utilities)
    ├── _gen_swap_csv.py            # Swap generation utility
    ├── compare_matrix.py           # Matrix comparison tool
    ├── cheesemonger_onboarding_doc.py # Documentation generator
    └── other analysis scripts
```

## Directory Purposes

**appyhour/:**
- Purpose: Reusable business logic, pure functions
- Contains: Domain algorithms (reorder points, thermal analysis, risk classification)
- Key files: `reorder.py`, `shipping.py`, `thermal.py`
- Imported by: All other modules (MCP tools, web apps, scripts)

**AppyHourMCP/:**
- Purpose: Claude Code integration via MCP protocol
- Contains: Tool wrappers, API authentication, data transformers
- Key files: `server.py` (entrypoint), `tools/*.py` (tool implementations)
- Run: `python server.py` starts stdio-based MCP server

**GelPackCalculator/:**
- Purpose: Thermal analysis, gel pack sizing, shipping temperature forecasting
- Contains: Desktop app (tkinter), invoice ingestion, Google Sheets integration
- Key files: `gel_pack_shopify.py` (primary tkinter app), `config.py` (hub settings)
- Run: `python gel_pack_shopify.py` launches desktop window

**InventoryReorder/:**
- Purpose: Demand forecasting, cut order generation, weekly fulfillment planning
- Contains: Tkinter inventory app, error detection scripts, fulfillment web dashboard
- Key files: `inventory_reorder.py` (tkinter app), `build_cut_order_xlsx.py` (core logic)
- Run: `python inventory_reorder.py` or `python fulfillment_web/app.py`

**ShippingReports/:**
- Purpose: Shipping analytics pipeline
- Contains: Invoice parsing, delivery data enrichment, cost analysis
- Key files: `ingest.py` (entrypoint), `enrich_ups_delivery.py`

**matrix_commander_web/:**
- Purpose: Interactive fulfillment matrix validation, inventory checking, Shopify sync
- Contains: Flask SPA, pywebview desktop wrapper, XLSX upload/processing
- Key files: `app.py` (Flask + pywebview), `static/app.js` (vanilla JS UI)
- Run: `python app.py` (pywebview) or `python app.py --browser` (http://localhost:5188)

**tests/:**
- Purpose: Unit, integration, and E2E tests
- Contains: Test cases organized by feature
- Key files: `test_error_detection.py` (error order rules), `test_weekly_cycle_e2e.py` (full workflow)
- Run: `pytest`

## Key File Locations

**Entry Points:**
- `AppyHourMCP/server.py` - MCP server for Claude Code
- `GelPackCalculator/gel_pack_shopify.py` - Thermal analysis desktop app
- `InventoryReorder/inventory_reorder.py` - Inventory forecasting desktop app
- `matrix_commander_web/app.py` - Fulfillment matrix web app
- `matrix_commander.py` - CLI validator (used by web app)

**Configuration:**
- `pyproject.toml` - Python dependencies, pytest, ruff, pyright config
- `.env` - Environment variables (Recharge token, Shopify endpoint, Google API key)
- `AppyHourMCP/tools/constants.py` - SKU taxonomy, curations, substitution families
- `GelPackCalculator/config.py` - Hub definitions, carrier settings, thermal defaults

**Core Logic:**
- `appyhour/reorder.py` - Reorder point calculations, bundle decomposition, churn logic
- `appyhour/shipping.py` - Gel pack recommendations, transit risk classification
- `appyhour/thermal.py` - Surface area, R-value calculations, thermal analysis
- `InventoryReorder/build_cut_order_xlsx.py` - Production matrix generation
- `matrix_commander.py` - XLSX validation, demand reconciliation, shortage detection

**Testing:**
- `tests/fixtures/` - Sample XLSX, CSV, JSON files
- `tests/test_error_detection.py` - Error order validation
- `tests/test_weekly_cycle_e2e.py` - Full fulfillment workflow

**API Integration:**
- `AppyHourMCP/tools/shopify.py` - GraphQL order edit, variant lookup
- `AppyHourMCP/tools/google_sheets.py` - Sheets API wrapper
- `AppyHourMCP/tools/gorgias.py` - Gorgias CRM lookup
- `GelPackCalculator/gmail_fedex_sync.py` - FedEx invoice fetcher

## Naming Conventions

**Files:**
- `*_*.py` - Snake case for Python modules
- `build_*.py` - Generator/builder pattern (e.g., `build_cut_order_xlsx.py`)
- `check_*.py` - Validation functions (e.g., `check_numeric_order_ids()`)
- `find_*.py` - Search/analysis scripts in `InventoryReorder/Errors/`
- `test_*.py` - Test files (pytest discovers automatically)

**Directories:**
- `tools/` - MCP tool implementations
- `static/` - Frontend assets (JS, CSS)
- `templates/` - HTML templates
- `uploads/` - Temporary file storage
- `Errors/` - Error detection and correction scripts
- `fixtures/` - Test data files

**Functions:**
- `calculate_*()` - Pure calculation functions
- `load_*()` - Data loading from files/APIs
- `check_*()` - Validation functions (return CheckResult)
- `find_*()` - Search/query functions (return list or dict)
- `apply_*()` - Transformation functions (return modified copy)
- `sync_*()` - API synchronization functions
- `register()` - MCP tool registration (exported by each tool module)

**Variables:**
- `SKU_TO_NAME`, `NAME_TO_SKU` - Mapping dicts (UPPER_CASE for constants)
- `SUBSTITUTION_FAMILIES` - Cheese/meat family groupings
- `DEFAULT_BOX`, `DEFAULT_INSULATION` - Configuration constants
- `STATE` - Global session state dict (matrix_commander_web/app.py)

**Classes/Dataclasses:**
- `CheckResult` - Validation result (success, message, errors)
- `SwapDecision` - SKU substitution record
- `SyncResult` - Shopify sync status

## Where to Add New Code

**New Feature (API tool for Claude Code):**
- Primary code: `AppyHourMCP/tools/{feature}.py`
- Export: Function with signature `register(mcp: FastMCP) -> None`
- Example: `gelcalc.py`, `shipping.py`, `order_edit.py`
- Integration: Import and call `{feature}.register(mcp)` in `server.py`

**New Validation Check:**
- Implementation: Add to `matrix_commander.py` or `InventoryReorder/Errors/check_*.py`
- Return type: `CheckResult` dataclass with errors dict
- Integration: Call from web app validation pipeline or CLI

**New Business Logic Function:**
- Location: `appyhour/{domain}.py` (reorder.py, shipping.py, or new file)
- Pattern: Pure functions, no side effects, type hints
- Tests: `tests/test_{domain}.py`

**New Desktop Feature:**
- Tkinter: Add to `GelPackCalculator/gel_pack_shopify.py` or `InventoryReorder/inventory_reorder.py`
- Web: Add Flask route to `matrix_commander_web/app.py` or `InventoryReorder/fulfillment_web/app.py`
- Frontend: Update `static/app.js` (fetch POST, update DOM)

**New Test:**
- Location: `tests/test_{feature}.py`
- Structure: Use conftest fixtures, organize by feature
- Coverage: Aim for 80%+ (pytest --cov)

## Special Directories

**InventoryReorder/Errors/:**
- Purpose: Error detection and correction scripts (24 total)
- Generated: No, manually maintained
- Committed: Yes, each script is a tool for specific error class
- Pattern: Each script imports from `matrix_commander.py`, runs detection, outputs CSV/XLSX

**AppyHourMCP/tools/__pycache__/, GelPackCalculator/dist/:**
- Purpose: Build artifacts
- Generated: Yes (Python bytecode, packaged app)
- Committed: No (.gitignore)

**matrix_commander_web/uploads/, InventoryReorder/fulfillment_web/uploads/:**
- Purpose: Temporary uploaded file storage
- Generated: Yes (created by web app)
- Committed: No (created at runtime)

**tests/fixtures/:**
- Purpose: Sample test data (XLSX, CSV, JSON)
- Generated: No, manually created fixtures
- Committed: Yes, used by tests

---

*Structure analysis: 2026-04-04*
