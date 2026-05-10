# AppyHour — Cold Chain Fulfillment Platform

## Overview

Desktop analytics platform for Elevate Foods (elevatefoods.co), a subscription cheese/charcuterie box company. Built with Python + pywebview (netfx/.NET Framework backend) and tkinter. Manages shipping analytics, inventory forecasting, cut order generation, and order quality/error detection.

## Project Structure

```
AppyHour/
├── GelPackCalculator/       # Thermal analysis, gel pack sizing, Shopify integration (tkinter)
│   └── gel_pack_shopify.py  # Main app (~3200 lines, single-file tkinter)
├── InventoryReorder/        # Inventory forecasting, cut order generation (tkinter)
│   ├── inventory_reorder.py # Main app (tkinter, cohort-based forecasting)
│   ├── cut_order_generator.py  # Weekly cut order from Recharge+Shopify demand
│   ├── fulfillment_web/     # pywebview fulfillment dashboard
│   └── Errors/              # Error detection & fix scripts (24 scripts)
├── ShippingReports/         # Shipping analytics & cost analysis
│   └── ingest.py            # Data ingestion pipeline
├── AppyHourMCP/             # MCP server for Claude Code integration
│   ├── server.py
│   └── tools/               # shipping.py, inventory.py, gelcalc.py
└── pyproject.toml           # Project config (pytest, ruff, pyright)
```

## Run

```bash
# Python path on this machine (not in PATH for bash)
/c/Users/Work/anaconda3/python.exe

# GelPackCalculator
cd GelPackCalculator && python gel_pack_shopify.py

# InventoryReorder
cd InventoryReorder && python inventory_reorder.py

# Tests
pip install -e ".[dev]"
pytest
```

## Key Domain Concepts

### SKU Taxonomy
- **CH-** — Cheese (CH-MCPC, CH-BLR, CH-WWDI, CH-EBRIE, etc.)
- **MT-** — Meat (MT-LONZ, MT-TUSC)
- **AC-** — Artisan crafted items (AC-DTCH, AC-PRPE, AC-TCRISP)
- **AHB-** — Subscription box types (AHB-MED, AHB-LGE, AHB-MCUST-*, AHB-LCUST-*)
- **BL-** — Bulk/base items
- **PR-CJAM-** — Bonus cheese+jam pairings (1 per box)
- **CEX-EC-** — Extra cheese assignments (~40% of boxes, large only)
- **PK-/TR-/EX-** — Non-pickable items

Only CH/MT/AC count toward item count for error detection.

### Curations (Box Recipes)
11 standard: MONG, MDT, OWC, SPN, ALPN, ALPT, ISUN, HHIGH, NMS, BYO, SS, GEN, MS

### Box SKU → Curation Resolution
`AHB-MCUST-MONG` → MONG, `AHB-LGE` → MONTHLY, etc. See `resolve_curation_from_box_sku()` in `cut_order_generator.py`.

### Error Order Classes
- **Class 2/3** — Bundle selection missing/incomplete (Recharge charges)
- **Class 4/4b** — Double food item or duplicate curation write
- **Class 6** — Curation mismatch (food items don't match box curation)
- **Class 7** — Recharge charge missing RC IDs
- **Class 11** — Structural errors (excluded SKU, 3+ copies, missing box/category, bare CEX-EC)
- CH-MAFT is never assigned (ASSIGNMENT_EXCLUDE)

## API Integration Notes

### Recharge (Subscription Management)
- **Cursor pagination is mandatory** — page-based silently loops forever
- Always use `timeout=30`
- Rate limiting: respect 429 responses with retry-after
- `bundle_selections` PUT needs real collection_id (can't be empty)
- Skip/unskip must pass `purchase_item_ids`
- v2021-11: `variant_id` is nested dict, not string

### Shopify (Order Management)
- GraphQL order edit API: use `beginEdit` → `addVariant`/`setQuantity` → `commitEdit`
- Filter qty=0 (removed) line items from CalculatedOrder before counting
- Use `fulfillableQuantity` when available
- `_rc_bundle` property = Recharge curation (removable); no props = paid/extras (keep)

### pywebview (Desktop UI)
- Uses **netfx** (.NET Framework), NOT coreclr/.NET 8
- Bridge availability: use `waitForBridge()` polling, NOT `pywebviewready` events
- `evaluate_js` does NOT work from Python API threads — use polling instead

## Design Conventions

- Three fonts: **DM Sans** (table data), **Space Mono** (UI chrome, 11-13px weight 600), **Rajdhani** (numbers/display, 12px weight 400)
- Dark theme with ttk "clam" theme
- Immutable data patterns preferred
- Threading: API calls on daemon threads, UI updates via `root.after(0, callback)` or polling

## Testing

```bash
pytest                    # run all tests
pytest --cov              # with coverage
ruff check .              # lint
pyright                   # type check
```

Target: 80%+ coverage. TDD workflow (RED → GREEN → REFACTOR).

<!-- GSD:project-start source:PROJECT.md -->
## Project

**AppyHour Fulfillment Platform**

Desktop analytics platform for Elevate Foods — manages inventory forecasting, cut order generation, demand pipeline, and order quality. Current milestone: **v1.1 Cut Order Consolidation** — single source of truth for demand calculation, polished XLSX output, unified logic.

**Core Value:** Accurate, operator-friendly cut order generation with one code path for demand resolution.

### Current Milestone: v1.1 Cut Order Consolidation

- Phase 8: XLSX v2 + Demand Fixes (in progress)
- Phase 9: Parameterized Dates + Auto-Discovery
- Phase 10: Shared Demand Module

### Constraints

- **Live data only**: All testing against real Shopify/Recharge data — no staging
- **pywebview + netfx**: Desktop app uses .NET Framework backend, not coreclr
- **PR-CJAM-GEN**: Only generic PR-CJAM; curation-specific variants created by Shopify post-charge
- **Shared settings JSON**: Schema changes must be backward-compatible across 3 apps
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.10+ - All backend and desktop applications, MCP server, analytics, data processing
- JavaScript/HTML/CSS - Web UI components in `matrix_commander_web/`, pywebview frontend code
- Batch (.bat) - Build automation scripts
## Runtime
- Python 3.10+ (requires-python >=3.10)
- .NET Framework (netfx) - pywebview runs on .NET Framework, NOT coreclr/.NET 8
- pip (PyPA)
- Lockfile: No explicit lockfile (requirements managed via `pyproject.toml`)
## Frameworks
- `tkinter` (bundled with Python) - Desktop GUI for GelPackCalculator, InventoryReorder (3200+ line single-file apps)
- `pywebview>=6.1` - Embedded web UI for fulfillment dashboard (`InventoryReorder/fulfillment_web/`)
- `flask>=3.1.2` - Web server for fulfillment dashboard
- `mcp>=1.0.0` - Model Context Protocol server for Claude Desktop integration
- `openpyxl>=3.1.5` - Excel import/export for inventory snapshots, weekly production queries
- `pyyaml>=6.0.3` - Configuration and data serialization
- `pydantic>=2.12.4` - Data validation and type safety for MCP inputs/outputs
- `requests>=2.32.5` - HTTP client for Shopify Admin API, Recharge API, OpenWeatherMap, Gorgias, NWS alerts
- `aiohttp>=3.13.2` - Async HTTP (optional, currently unused - all calls use synchronous requests)
- `fpdf2>=2.8.7` - PDF generation for shipping reports and analytics
- `pytest>=8.0` - Test runner
- `pytest-cov>=5.0` - Code coverage measurement
- `ruff>=0.9.0` - Fast linter and formatter (replaces Black + flake8 + isort)
- `pyright>=1.1.390` - Static type checker
## Key Dependencies
- `requests` - HTTP client for all external API integration (Shopify, Recharge, weather, helpdesk)
- `openpyxl` - Excel I/O for inventory management and production forecasting
- `pydantic` - Type validation for MCP tool inputs (prevents runtime errors in Claude Desktop)
- `pywebview` - Embedded web view with Python-JavaScript bridge for desktop UIs
- `flask` - WSGI server for fulfillment web dashboard
- `mcp` - MCP stdio transport for Claude Desktop integration
- `pyyaml` - Settings/config serialization
- `fpdf2` - Shipping report PDF generation
## Configuration
- Settings stored as JSON files persisted next to executables:
- Also reads from `%APPDATA%/AppyHour/` directory on Windows
- Credentials for Google Sheets, Gorgias, and other services read from settings JSON or fallback locations
- `pyproject.toml` - PEP 517 project metadata with optional dependency groups (fulfillment, shipping, mcp, dev)
- `.ruff.toml` - Linter config: Python 3.10 target, 120 char line length, security checks enabled (flake8-bandit)
- `pyright` config embedded in `pyproject.toml` - Type checking mode "basic", Python 3.10
## Platform Requirements
- Windows 11 Pro (primary development environment)
- Python 3.10+ via Anaconda (`/c/Users/Work/anaconda3/python.exe`)
- Tcl/Tk DLLs for tkinter (bundled with Anaconda, explicitly included in PyInstaller spec)
- Git for version control
- Windows 7+ (via PyInstaller one-file standalone exes)
- .NET Framework runtime (for pywebview netfx backend) — NOT .NET Core/8
- No external dependencies required when distributed as exe (all DLLs bundled)
- Python 3.10+ runtime
- HTTP server (flask for web UI, MCP stdio for Claude Desktop)
- Can run on any OS (Windows, macOS, Linux)
## Build & Distribution
- Single-file windowed exes via `PyInstaller --onefile --windowed`
- PyInstaller spec explicitly includes Tcl/Tk DLLs from Anaconda distribution
- Build scripts: `build_exe.bat` in GelPackCalculator directory
- Output: `dist/GelPackCalculator.exe`, `dist/InventoryReorder.exe`
- PEP 440 versioned at `version = "1.0.0"` in pyproject.toml
- Entry point: `AppyHourMCP/server.py` (scripts-based invocation via FastMCP)
- Transport: stdio (subprocess communication)
- Requires: `mcp[cli]>=1.0.0`
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Lowercase with underscores: `cut_order_generator.py`, `inventory_reorder.py`, `thermal.py`
- Module files in subdirectories follow same pattern: `tools/google_sheets.py`, `tools/shipping.py`
- Test files: `test_*.py` (e.g., `test_error_detection.py`, `test_reorder.py`)
- Lowercase with underscores: `calculate_reorder_point()`, `decompose_bundles()`, `analyze_order()`
- Pure helper functions use imperative names: `is_pickable()`, `normalize_sku()`, `apply_churn_rate()`
- Private/internal functions prefixed with underscore: `_load_shipments()`, `_get_analyze()`
- Lowercase with underscores: `daily_usage`, `reorder_point`, `bundle_map`
- Constants uppercase: `HEAT_CAPACITY`, `TARGET_TEMP_DEFAULT`, `GEL_CONFIGS`, `PICKABLE_PREFIXES`
- Dictionary/dict keys use lowercase: `config["btu"]`, `result["risk"]`, `settings.get("recharge_api_token")`
- Classes use PascalCase when present: `CostAnalysisInput`, `TransitAnalysisInput` (Pydantic models in `AppyHourMCP/tools/`)
- Enum members uppercase: `GroupByChoice.STATE`, `GroupByChoice.CARRIER`
- Prefix with `is_` or `compute_`: `is_pickable()`, `is_on_time()`, `compute_reorder_status()`, `compute_wheel_supply()`
## Code Style
- Line length: 120 characters (configured in `pyproject.toml` → `tool.ruff.line-length`)
- Indentation: 4 spaces
- Use `from __future__ import annotations` for forward-compatible type hints
- Tool: `ruff` (configured in `pyproject.toml`)
- Enabled rules: E, W, F, I (isort), UP (pyupgrade), B (flake8-bugbear), S (security), SIM (simplify)
- Ignored: E501 (line length handled by formatter), S101/S105/S106 (test and security noise)
- Per-file: tests/ directory ignores S101, S106, E402
- Tool: `pyright` (basic mode)
- Python version: 3.10+
- Type hints expected on function signatures, especially public APIs
- Lazy-load types with `types.ModuleType | None` (see `tools/shipping.py` line 21)
## Import Organization
- Not used; relative imports and explicit sys.path manipulation preferred (see `conftest.py` lines 8-11)
- Root path management: `BASE = os.path.dirname(os.path.abspath(__file__))`
## Error Handling
- Tool: `print()` for CLI scripts (e.g., `build_cut_order_xlsx.py` lines 53, 76, 89, 102)
- No centralized logging library; simple print statements for diagnostic output
- Error messages sent as returned strings in MCP tools: `return format_error(str(e))`
## Comments
- Module docstring at top of file describing purpose (required)
- Function docstring explaining what it returns and key logic (for public APIs)
- Inline comments for non-obvious calculations or domain logic (e.g., `# 5 lbs * 10 wheels * 2.67 = 133.5 slices`)
- Section comments with `# ── [Section Name] ────...` separator (from `thermal.py` line 9)
- Python docstrings used (triple quotes), not elaborate TypeDoc format
- Docstrings include description + return type + example domain context
- Example from `thermal.py` lines 65-82:
## Function Design
- Small, focused functions (most under 30 lines)
- Complex business logic extracted to pure functions in appyhour_lib/ (e.g., `thermal.py`, `reorder.py`, `shipping.py`)
- GUI code and business logic strictly separated
- Positional for required, keyword for optional
- Use `dict | None = None` pattern with fallback, not `**kwargs`
- Type hints on all parameters (enforced by pyright basic mode)
- Return dict for complex results (e.g., `analyze_order()` returns dict with 13+ keys)
- Return list/tuples for collections
- Return str for status/enums (e.g., `"OUT_OF_STOCK"`, `"CRITICAL"`)
- Return bool for flags
- None for void operations or missing data (handled with `dict.get(key, default)`)
## Module Design
- Pure functions in `appyhour_lib/` are public (no underscore prefix)
- MCP tools use Pydantic models for input validation: `class CostAnalysisInput(BaseModel)`
- Internal helpers prefixed with underscore: `_load_shipments()`, `_get_analyze()`
- Not used; modules imported directly or via explicit imports
- **Pure Logic Layer** (`appyhour_lib/thermal.py`, `appyhour_lib/reorder.py`, `appyhour_lib/shipping.py`): No API/GUI dependencies, testable directly
- **Integration Layer** (`AppyHourMCP/tools/`): Pydantic models, MCP registration, API calls
- **CLI Layer** (`InventoryReorder/build_cut_order_xlsx.py`): Direct imports from integration, print diagnostics
- **Test Layer** (`tests/`): Tests pure functions and helpers, mocks API calls
## Constants and Defaults
## Pydantic Models
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- **MCP-first design** - AppyHourMCP server centralizes tools, exposing them to Claude Code
- **Layered architecture** - Shared business logic (`appyhour_lib/`) → Domain modules (GelPackCalculator, InventoryReorder, ShippingReports) → UI/CLI adapters
- **Data-driven workflows** - XLSX files drive matrix validation, demand forecasting, and fulfillment pipeline
- **API integration hub** - Recharge (subscriptions) + Shopify (orders) as primary data sources with demand reconciliation
## Layers
- Purpose: Pure functions and algorithms for reorder calculations, thermal analysis, and shipping risk assessment
- Location: `appyhour_lib/reorder.py`, `appyhour_lib/shipping.py`, `appyhour_lib/thermal.py`
- Contains: Domain functions, no API calls, no UI dependencies
- Depends on: Standard library only
- Used by: All other layers (MCP tools, web apps, standalone scripts)
- **GelPackCalculator** (`GelPackCalculator/`): Thermal gel pack sizing, shipping temperature analysis, invoice data ingestion
- **InventoryReorder** (`InventoryReorder/`): Weekly demand forecasting, cut order generation, production matrix validation
- **ShippingReports** (`ShippingReports/`): Shipping analytics, cost analysis, carrier performance tracking
- **Matrix Commander** (`matrix_commander.py` + `matrix_commander_web/`): Fulfillment pipeline orchestration
- Purpose: Exposes domain tools as MCP resources for Claude Code integration
- Location: `AppyHourMCP/tools/` (individual tool modules)
- Contains: Tool wrappers that call domain logic, authentication handling, API bridging
- Depends on: Recharge, Shopify, Google Sheets, Gorgias APIs
- Used by: Claude Desktop via MCP protocol
- **Tkinter apps** (`GelPackCalculator/gel_pack_*.py`, `InventoryReorder/inventory_reorder.py`): Cross-platform desktop with dark theme
- **pywebview apps** (`matrix_commander_web/app.py`, `InventoryReorder/fulfillment_web/`): Flask SPA wrapped in netfx desktop window
- **Web UI** (`matrix_commander_web/static/`, `matrix_commander_web/templates/`): Vanilla JS + CSS (no framework)
## Data Flow
- **XLSX as source of truth** - Production matrices drive all workflows
- **API caching** - Recharge/Shopify data fetched once per cycle, reconciled against matrix
- **Immutable updates** - Swap decisions generate new XLSX, never mutate original
- **Google Sheets for reporting** - Analytics sheets auto-generated from processed data
## Key Abstractions
- Purpose: Encapsulates validation results with error messages
- Location: `matrix_commander.py` (exported for use in web app)
- Pattern: Immutable result object with bool success, str message, dict errors
- Example: `check_numeric_order_ids(orders)` returns CheckResult with validation details
- Purpose: Represents a single SKU substitution decision
- Location: `matrix_commander.py`
- Fields: original_sku, replacement_sku, reason, count
- Pattern: Immutable swap record for audit trail
- Purpose: Reports Shopify GraphQL sync status
- Location: `matrix_commander.py`
- Fields: success, graphql_errors, order_id, applied_variants
- Pattern: Captures both success/failure and detailed API response
- `load_inventory_csv()`, `load_inventory_settings()`, `load_mfg_translations()` encapsulate data loading
- Each returns a dict or ConfigDict with validation
- Single source of truth for configuration (constants, mappings)
- Each tool module in `AppyHourMCP/tools/` exports `register(mcp)` function
- Tools are FastMCP-compatible callables with type hints
- Location: `AppyHourMCP/tools/*.py` (gelcalc.py, shopify.py, inventory.py, shipping.py, etc.)
## Entry Points
- Location: `AppyHourMCP/server.py`
- Triggers: Claude Desktop MCP client invocation
- Responsibilities: Load all tool modules, expose via MCP protocol, handle stdio transport
- Tools: 10+ including gelcalc, shopify, inventory, shipping, order_edit, matrix_qc, ops_summary_builder
- Location: `matrix_commander.py` (root level)
- Triggers: `python matrix_commander.py validate|check|full <args>`
- Responsibilities: File validation, inventory checking, shortage reporting
- Integration: Consumed by web app via function imports
- Location: `matrix_commander_web/app.py`
- Triggers: `python app.py` (pywebview) or `python app.py --browser` (http://localhost:5188)
- Responsibilities: Interactive XLSX upload, validation, swap workflow, Shopify sync
- Frontend: `static/app.js` (vanilla JS state machine)
- Location: `GelPackCalculator/gel_pack_shopify.py`
- Triggers: `python gel_pack_shopify.py`
- Responsibilities: Thermal analysis, hub/transit configuration, Shopify forecast lookups
- UI: tkinter with dark theme
- Location: `InventoryReorder/inventory_reorder.py`
- Triggers: `python inventory_reorder.py`
- Responsibilities: Demand forecasting, reorder point calculation, cut order generation
- UI: tkinter with dark theme
## Error Handling
- **Input validation:** `CheckResult` objects returned from validation functions rather than raising exceptions
- **API error handling:** Specific exception types for Recharge/Shopify failures
- **File I/O:** Path validation before read/write, UTF-8 encoding enforcement for XLSX
- **Web layer:** Flask error handlers in `matrix_commander_web/app.py` return JSON responses with 400/500 codes
- **Desktop apps:** Thread-safe error logging to stderr, UI updates via `root.after()` callback
## Cross-Cutting Concerns
- MCP server: stderr logging with timestamps (AppyHourMCP/server.py)
- Desktop apps: logs to console with thread context
- Web app: Flask request logging
- Domain layer: Pure functions return CheckResult or raise ValueError
- API layer: Pydantic models for request/response validation
- Input layer: File format checks (XLSX schema, CSV headers)
- Recharge: `RECHARGE_API_TOKEN` env var (v2021-11 header)
- Shopify: GraphQL endpoint + access token from `_get_shopify_auth()`
- Google Sheets: OAuth token refresh in `AppyHourMCP/tools/google_sheets.py`
- Gorgias: API token for customer lookup
- Constants: `AppyHourMCP/tools/constants.py` (NAME_TO_SKU, FOOD_PREFIXES, curations)
- Env vars: .env file (present, not read by tools — use os.environ)
- YAML configs: `GelPackCalculator/config.py` for hub definitions, carrier settings
- Recharge bundle selections cached per cycle (avoid re-fetching)
- Shopify variant lookups cached (price, gid)
- Inventory CSV loaded once per session
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
