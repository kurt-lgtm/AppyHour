# Technology Stack

**Analysis Date:** 2025-02-10

## Languages

**Primary:**
- Python 3.10+ - All backend and desktop applications, MCP server, analytics, data processing

**Secondary:**
- JavaScript/HTML/CSS - Web UI components in `matrix_commander_web/`, pywebview frontend code
- Batch (.bat) - Build automation scripts

## Runtime

**Environment:**
- Python 3.10+ (requires-python >=3.10)
- .NET Framework (netfx) - pywebview runs on .NET Framework, NOT coreclr/.NET 8

**Package Manager:**
- pip (PyPA)
- Lockfile: No explicit lockfile (requirements managed via `pyproject.toml`)

## Frameworks

**Core:**
- `tkinter` (bundled with Python) - Desktop GUI for GelPackCalculator, InventoryReorder (3200+ line single-file apps)
- `pywebview>=6.1` - Embedded web UI for fulfillment dashboard (`InventoryReorder/fulfillment_web/`)
- `flask>=3.1.2` - Web server for fulfillment dashboard
- `mcp>=1.0.0` - Model Context Protocol server for Claude Desktop integration

**Data & Serialization:**
- `openpyxl>=3.1.5` - Excel import/export for inventory snapshots, weekly production queries
- `pyyaml>=6.0.3` - Configuration and data serialization
- `pydantic>=2.12.4` - Data validation and type safety for MCP inputs/outputs

**API & Networking:**
- `requests>=2.32.5` - HTTP client for Shopify Admin API, Recharge API, OpenWeatherMap, Gorgias, NWS alerts
- `aiohttp>=3.13.2` - Async HTTP (optional, currently unused - all calls use synchronous requests)

**PDF & Reporting:**
- `fpdf2>=2.8.7` - PDF generation for shipping reports and analytics

**Testing:**
- `pytest>=8.0` - Test runner
- `pytest-cov>=5.0` - Code coverage measurement

**Code Quality:**
- `ruff>=0.9.0` - Fast linter and formatter (replaces Black + flake8 + isort)
- `pyright>=1.1.390` - Static type checker

## Key Dependencies

**Critical (Core Functionality):**
- `requests` - HTTP client for all external API integration (Shopify, Recharge, weather, helpdesk)
- `openpyxl` - Excel I/O for inventory management and production forecasting
- `pydantic` - Type validation for MCP tool inputs (prevents runtime errors in Claude Desktop)

**Infrastructure (Desktop/Web):**
- `pywebview` - Embedded web view with Python-JavaScript bridge for desktop UIs
- `flask` - WSGI server for fulfillment web dashboard
- `mcp` - MCP stdio transport for Claude Desktop integration

**Data Processing:**
- `pyyaml` - Settings/config serialization
- `fpdf2` - Shipping report PDF generation

## Configuration

**Environment:**
- Settings stored as JSON files persisted next to executables:
  - `gel_calc_shopify_settings.json` - GelPackCalculator settings (Shopify OAuth, OpenWeatherMap key, thermal params, state routing)
  - `inventory_reorder_settings.json` - InventoryReorder settings (Recharge token, bundle recipes, cohorts, forecasts)
- Also reads from `%APPDATA%/AppyHour/` directory on Windows
- Credentials for Google Sheets, Gorgias, and other services read from settings JSON or fallback locations

**Build:**
- `pyproject.toml` - PEP 517 project metadata with optional dependency groups (fulfillment, shipping, mcp, dev)
- `.ruff.toml` - Linter config: Python 3.10 target, 120 char line length, security checks enabled (flake8-bandit)
- `pyright` config embedded in `pyproject.toml` - Type checking mode "basic", Python 3.10

**Type Checking:**
```toml
[tool.pyright]
pythonVersion = "3.10"
typeCheckingMode = "basic"
reportMissingImports = "warning"
reportMissingTypeStubs = false
```

**Testing:**
```bash
pytest                     # Run all tests (pythonpath includes ".")
pytest --cov               # Coverage report
ruff check .               # Lint
pyright                    # Type check
```

## Platform Requirements

**Development:**
- Windows 11 Pro (primary development environment)
- Python 3.10+ via Anaconda (`/c/Users/Work/anaconda3/python.exe`)
- Tcl/Tk DLLs for tkinter (bundled with Anaconda, explicitly included in PyInstaller spec)
- Git for version control

**Production (Desktop Apps):**
- Windows 7+ (via PyInstaller one-file standalone exes)
- .NET Framework runtime (for pywebview netfx backend) — NOT .NET Core/8
- No external dependencies required when distributed as exe (all DLLs bundled)

**Web/MCP Server:**
- Python 3.10+ runtime
- HTTP server (flask for web UI, MCP stdio for Claude Desktop)
- Can run on any OS (Windows, macOS, Linux)

## Build & Distribution

**Desktop Apps (PyInstaller):**
- Single-file windowed exes via `PyInstaller --onefile --windowed`
- PyInstaller spec explicitly includes Tcl/Tk DLLs from Anaconda distribution
- Build scripts: `build_exe.bat` in GelPackCalculator directory
- Output: `dist/GelPackCalculator.exe`, `dist/InventoryReorder.exe`

**MCP Server:**
- PEP 440 versioned at `version = "1.0.0"` in pyproject.toml
- Entry point: `AppyHourMCP/server.py` (scripts-based invocation via FastMCP)
- Transport: stdio (subprocess communication)
- Requires: `mcp[cli]>=1.0.0`

---

*Stack analysis: 2025-02-10*
