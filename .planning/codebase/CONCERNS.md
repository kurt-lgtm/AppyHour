# Codebase Concerns

**Analysis Date:** 2026-04-04

## Monolithic Files (Architectural Debt)

**Primary blocker for testability and maintainability:**
- `InventoryReorder/inventory_reorder.py` — **14,350 lines**
  - Single tkinter app containing: UI layout, business logic, API clients, data persistence, reporting, forecasting engine, OAuth handlers
  - Files: `InventoryReorder/inventory_reorder.py`
  - Impact: Impossible to unit test, single-file deployments, tight UI-logic coupling
  - Fix approach: Extract into layers: models (domain logic) → services (API clients, forecasting) → repositories (data access) → UI handlers

- `InventoryReorder/fulfillment_web/app.py` — **9,753 lines**
  - Single Flask app: all API endpoints, business calculations, CSV parsing, PDF generation, file I/O in one file
  - Files: `InventoryReorder/fulfillment_web/app.py`
  - Impact: No route isolation, calculation logic untestable, circular dependencies risk
  - Fix approach: Separate concerns into `routes/`, `services/`, `parsers/`, `calculators/` modules

- `GelPackCalculator/gel_pack_shopify.py` — **4,931 lines**
  - Monolithic tkinter app: thermal calculations + API calls + UI + database
  - Files: `GelPackCalculator/gel_pack_shopify.py`
  - Impact: Cannot reuse thermal calculation engine in other contexts
  - Fix approach: Extract `GelPackEngine` class to separate module

## Temporary/Scratch Files (Cleanup Debt)

**Do not commit to production:**
- `AppyHourMCP/_tmp_carrier_stats.py` — Ad-hoc carrier analysis script
- `compare_matrix.py` — One-off Excel-vs-Shopify comparison (hardcoded file paths, hardcoded SKU maps)
- `tmp_cexec_counts.py` — Temporary cohort count extraction
- `tmp_cexec_combined.py` — Temporary data combination script
- `_gen_swap_csv.py` — Generated CSV export (should be parameterized)

**Fix approach:** Move to `scripts/archive/` or `scripts/legacy/` with clear documentation of purpose and deprecation status.

## Error Handling Gaps

**Bare exception handlers obscure real errors:**
- Pattern: `except Exception:` with no logging (26+ instances)
- Files affected:
  - `AppyHourMCP/utils.py:55` — Shopify auth failure silently fails
  - `AppyHourMCP/server.py:58` — MCP tool failures swallowed
  - `AppyHourMCP/tools/gorgias_sheets_sync.py` — 10+ bare except blocks with no logging
  - `InventoryReorder/inventory_reorder.py` — 8+ bare exception handlers
  - `InventoryReorder/fulfillment_web/app.py:125` — API request failures
  - `InventoryReorder/fulfillment_web/depletion_finder.py` — Multiple file I/O failures hidden

**Impact:** No visibility into failures, impossible to debug in production, error swallowing enables cascading failures

**Fix approach:**
```python
# WRONG: Silently fails
except Exception:
    pass

# CORRECT: Log and context
except ValueError as e:
    logger.exception("Failed to parse value", exc_info=e)
    raise ValueError("Input validation failed") from e
except FileNotFoundError as e:
    logger.error("Config file missing: %s", path)
    raise
```

## Security Concerns

**API credentials accessed from settings JSON:**
- Files: `compare_matrix.py`, `InventoryReorder/inventory_reorder.py`, multiple MCP tools
- Risk: Settings file contains `shopify_access_token`, `recharge_api_token`, OAuth tokens
- Current state: Settings stored in `dist/inventory_reorder_settings.json` (inside app directory)
- Impact: Tokens exposed if file is backed up, synced, or accidentally committed

**Fix approach:**
- Separate secrets from settings (use environment variables or secure keyring)
- Never store tokens in JSON files in version control
- Use `python-dotenv` for development, `AZURE_KEYVAULT` or `AWS_SECRETS` for production

**Example hardcoded API mappings:**
- `compare_matrix.py` — Hardcoded SKU→product name map (85 entries, maintenance burden)
- `AppyHour/InventoryReorder/fulfillment_web/app.py:44-92` — Hardcoded curation box slot definitions
- Impact: Any product rename requires code changes

## Performance Bottlenecks

**Synchronous API calls blocking UI:**
- Pattern: All API calls in `inventory_reorder.py` run on daemon threads but main thread polls for results
- Files: `InventoryReorder/inventory_reorder.py` (threading throughout)
- Impact: Recharge API with cursor pagination + 100+ cohorts = 5-10s delays per refresh
- Current state: User sees frozen UI during `_refresh_recharge_demand()`, `_fetch_shopify_trends()`

**Fix approach:**
- Move to async/await with `asyncio` instead of threads
- Implement cancellation tokens for long-running operations
- Add progress callbacks for large data pulls

**N+1 API queries:**
- Recharge cohort forecast loops over every cohort, curation, month without batching
- Files: `InventoryReorder/inventory_reorder.py:4000-5000` (estimated, cohort forecast loop)
- Impact: Forecast with 50 cohorts × 7 curations × 3 months = repeated calculations

## Data Consistency Gaps

**Multiple sources of truth for demand:**
- Recharge API pull → `recharge_demand` key
- Queued charges → `recharge_queued` key by month
- Shopify API trends → `shopify_api_demand` key
- Manual forecast → `shopify_forecast` key
- Cohort projection → calculated separately in forecast loop

**Issue:** No single source of truth; reconciliation logic is scattered, no audit trail

**Files affected:**
- `InventoryReorder/inventory_reorder.py` — Multiple demand aggregation points
- `InventoryReorder/fulfillment_web/app.py` — Separate aggregation logic duplicated

**Fix approach:** Create `DemandSource` enum and unified `AggregatedDemand` model with source attribution

**Inventory mutation without versioning:**
- `inventory` dict in settings mutated directly without change tracking
- Files: `InventoryReorder/inventory_reorder.py`
- Impact: No undo/redo, no audit trail for cost discrepancies, warehouse splits not properly tracked

## Testing Gaps

**No unit tests for critical business logic:**
- `InventoryReorder/inventory_reorder.py` — 14k lines, zero isolated unit tests
- `GelPackCalculator/gel_pack_shopify.py` — Thermal calculations untestable
- `InventoryReorder/fulfillment_web/app.py` — 9.7k lines, endpoints not tested

**Files with test coverage:**
- `tests/test_error_detection.py` — 393 lines (only error detection module has tests)
- `conftest.py` — Minimal fixtures

**Current coverage:** Estimated <10% (only error detection has tests)

**Fix approach:** 
- Extract calculation engines to pure functions
- Write unit tests for: cohort forecasting, demand aggregation, reorder point calculation, price breakdowns
- Target: 80%+ coverage minimum for new code

## Fragile Areas (High Risk of Breakage)

**Curation name resolution is fragile:**
- Pattern: `resolve_curation_from_box_sku()` uses string parsing on SKU names
- Files: `InventoryReorder/cut_order_generator.py`, mirrored logic in `fulfillment_web/app.py`
- Risk: Any box SKU naming change breaks demand resolution
- Example: `AHB-MCUST-MONG` → MONG (string extraction after `-`)

**Wheel-to-slice conversion has no validation:**
- Files: `InventoryReorder/inventory_reorder.py`, `fulfillment_web/app.py`
- Default: `WHEEL_TO_SLICE_FACTOR = 2.67`
- Risk: Hardcoded constant used without quality checks; actual wheel sizes vary 5-15%
- Current state: Manual adjustments tracked in `adjusted_conversion_factors` but no alerts for outliers

**Recharge pagination silent failure:**
- Files: `AppyHourMCP/tools/inventory.py`, `InventoryReorder/inventory_reorder.py`
- Issue: Pagination uses `?page=1,2,3...` but cursor pagination is required
- Risk: If response format changes, app silently loops forever (no timeout)

**Hardcoded date/month logic:**
- Forecast assumes 30.4 days/month average without date math
- Files: `InventoryReorder/inventory_reorder.py` (cohort forecast)
- Risk: February demand calculations off by 8%, month boundary transitions buggy

## Dependency Management

**No requirement pinning:**
- Files: `InventoryReorder/pyproject.toml`, `pyproject.toml` (if exists)
- Risk: `openpyxl`, `requests`, `flask` versions not locked; minor updates break parsing

**Deprecated/abandoned dependencies:**
- `pywebview` — Uses .NET Framework (`netfx`), not .NET 8+; limited maintenance
- Files: `InventoryReorder/fulfillment_web/app.py`
- Impact: If running on future Windows, netfx may not be available

## Credential Exposure Risk

**OAuth tokens stored in JSON:**
- `gcal_refresh_token`, `dropbox_refresh_token`, `clickup_api_token` in settings
- Files: `InventoryReorder/inventory_reorder.py:3464-3472`
- Risk: High if settings file synced to cloud storage (OneDrive, Dropbox)

**SMTP password plaintext:**
- `smtp_password` stored in settings JSON
- Files: `InventoryReorder/inventory_reorder.py`
- Risk: Used for email alerts; if leaked, attacker can impersonate email sender

## Process Orchestration Gaps

**Cut order generation relies on manual folder naming:**
- Files: `InventoryReorder/fulfillment_web/app.py:load_rmfg()`
- Pattern: Looks for `RMFG_*` folders with hardcoded file naming patterns
- Risk: Misnaming a folder breaks the entire pipeline silently

**Missing validation for box slot assignments:**
- Box assignment algorithm has no duplicate detection for PR-CJAM cheese
- Files: `InventoryReorder/fulfillment_web/app.py` (assignment logic)
- Risk: Same cheese assigned to two curations, violating uniqueness constraint

## Scaling Limits

**In-memory inventory snapshot:**
- Entire inventory loaded into memory as Python dict
- Impact: 1000+ SKUs with warehouse splits = manageable; 10k SKUs = memory pressure

**No database backend:**
- All data persisted in single JSON file
- Risk: Concurrent access (two users saving simultaneously) causes data loss
- Current state: No file locking, no transactions

**Fix approach:** Migrate to SQLite for multi-user safety, or implement file-level write locks

## Known Bugs/Issues

**Orphaned CEX-EC assignment:**
- Symptom: CEX-EC appears in fulfillment after box removal
- Files: Recharge bundle edit logic (not directly in AppyHour, but affects order structure)
- Workaround: Manual CEX-EC removal in matrix

**Recharge charge timing inconsistency:**
- Queued charges pulled on demand but not cached; old demand persists
- Files: `InventoryReorder/inventory_reorder.py`
- Impact: Forecast lags behind actual Recharge schedule by 1+ day

**AC- SKU packet oz mapping incomplete:**
- `DEFAULT_BULK_CONVERSIONS` has 13 entries but 20+ AC- SKUs exist
- Files: `InventoryReorder/inventory_reorder.py:66-79`
- Impact: Missing bulk-to-packet mappings default to 0, causing false "OUT OF STOCK"

---

*Concerns audit: 2026-04-04*
