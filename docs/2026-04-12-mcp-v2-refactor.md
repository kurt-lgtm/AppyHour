# AppyHour MCP v2 Refactor — April 12, 2026

## Summary

Major overhaul of the AppyHour MCP server: 6-phase refactor reducing codebase from ~7.6K LOC to ~6.1K LOC, consolidating 45 tools down to 42, adding performance caches, and extracting the weather module from the GelPackCalculator monolith. One new feature added.

**14 commits | -1,500 LOC net | 6 phases completed**

---

## Phase 1: Dead Code & DRY Cleanup

- Archived 4 dead scripts to `scripts/archive/`:
  - `rebuild_ops_formulas`
  - `run_enrich_v2`
  - `test_gorgias_lookup`
  - `_tmp_carrier_stats`
- Deleted duplicate `_graphql()` from `shopify.py` (identical to `utils.shopify_graphql`)
- Centralized constants in `utils.py`:
  - `APPDATA_SETTINGS` path (was duplicated in 4 modules)
  - `OPS_SHEET_ID` (was hardcoded in 2 modules)
  - `SHOPIFY_API_VERSION` (was inline string)

## Phase 2: Gorgias Deduplication & Error Standardization

- **New module:** `tools/_gorgias_internal.py`
  - Cached auth loader
  - Shared `gorgias_get()` and `gorgias_paginate()` helpers
- Removed ~60 LOC of duplicated auth/HTTP/pagination from `gorgias.py` and `gorgias_sheets_sync.py`
- Standardized error handling in `google_sheets.py` (6 sites) and `gorgias.py` (5 sites) → all use `format_error()`

## Phase 3: Shipping Tool Consolidation

- **6 tools → 2:**
  - 5 read-only tools (`costs`, `transit`, `misroutes`, `chronic_zips`, `overrides`) merged into `appyhour_shipping_analysis(report_type)`
  - Write tool `appyhour_apply_zip_routing_tags` kept separate
- Misroute config extracted to helper function
- **Net: -91 LOC**

## Phase 4: DRY Pagination

- Extracted `shopify_paginate(url, headers, params, key, timeout, sleep)` to `utils.py`
- Replaced 5 identical Shopify REST pagination loops across modules
- Cleaned orphaned imports (`requests`, `re`, `time`) from 5 modules

## Phase 5: Performance Caches

- **Variant GID cache** (`order_edit.py`): Module-level cache so repeated swap runs for same SKUs skip GraphQL lookups
- **Weather cache** (`gelcalc.py`): 1-hour TTL — 50 orders in same zip code = 1 API call instead of 50

## Phase 6: New Feature — Order Search

- **New tool:** `appyhour_search_orders`
  - Search by order number, email, or customer name
  - REST API for number/email lookups
  - GraphQL for name-based search
  - Returns order details with line items

---

## Weather Module Extraction

Separate from the 6-phase refactor, extracted weather functionality from the 3,200-line `gel_pack_shopify.py` monolith:

- **New module:** `appyhour/weather.py`
  - `fetch_weather_by_zip()` — single zip forecast
  - `fetch_weather_batch()` — batch zip forecasts
  - `fetch_nws_alerts()` — NWS weather alerts
- `gelcalc.py` now imports from `appyhour.weather`
- **Bug fix:** `fetch_nws_alerts` returns `(alerts, error)` tuple but caller was ignoring the error value — now properly handled

---

## Code Review Fixes

Post-refactor review caught 4 issues:

| File | Issue | Fix |
|------|-------|-----|
| `order_edit.py` | Missing `import time` | Restored — would've been NameError on variant lookup |
| `shipping.py` | `format_error` called with string, not Exception | Fixed argument type |
| `shopify.py` | GraphQL name search failures silently swallowed | Added logging |
| `utils.py` | Dead `import requests` in `get_shopify_auth` | Removed |

Additionally restored `import requests` in `product_catalog.py`, `shipping.py`, and `order_tags.py` — Phase 4 cleanup accidentally removed them from modules still using `requests` directly.

---

## Before / After

| Metric | Before | After |
|--------|--------|-------|
| Total tools | 45 | 42 |
| Lines of code | ~7,600 | ~6,100 |
| Shipping tools | 6 | 2 |
| Gorgias auth implementations | 3 | 1 (shared) |
| Pagination implementations | 5+ | 1 (shared) |
| Hardcoded constants | 8+ sites | Centralized in utils |

## Next Steps

- Shipping module decoupling (extract from monolith like weather)
- Continue GelPackCalculator monolith decomposition
- Performance benchmarking on caches
