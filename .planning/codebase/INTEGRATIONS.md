# External Integrations

**Analysis Date:** 2025-02-10

## APIs & External Services

**Shopify Admin API (2024-10):**
- **What it's used for:** Fetch unfulfilled orders by tag, add/remove order tags, apply gel pack and routing tags
- **SDK/Client:** `requests` HTTP library (REST), custom `ShopifyClient` wrapper
- **Implementation:** `AppyHourMCP/tools/shopify.py` (line ~21-62), `GelPackCalculator/gel_pack_shopify.py` (line ~501-650)
- **Auth:** Client-credentials OAuth2 with 24-hour token auto-refresh
- **Pagination:** Link header-based cursor (REST API endpoint `/orders.json`)
- **Rate limiting:** 2 reqs/sec general quota; graceful handling of 429 responses
- **Timeout:** 30 seconds for all requests
- **Key patterns:**
  - Filter by status=open, fulfillment_status=unfulfilled
  - Fetch all line items and calculate active (non-removed, non-refunded) counts
  - Use `fulfillableQuantity` when available, filter qty=0 items
  - GraphQL order edit API: `beginEdit` → `addVariant`/`setQuantity` → `commitEdit` workflow

**Recharge API (v2021-11):**
- **What it's used for:** Fetch active subscriptions, subscription metadata, bundle selections, queued charges for demand forecasting
- **SDK/Client:** `requests` HTTP library (REST), custom `RechargeClient` wrapper in `InventoryReorder/inventory_reorder.py`
- **Implementation:** `AppyHourMCP/tools/inventory.py` (lazy-loads RechargeClient), `InventoryReorder/inventory_reorder.py` (line ~200+)
- **Auth:** Bearer token (API key) from settings
- **Pagination:** Cursor-based mandatory (page-based leads to infinite loops) — next_cursor in response metadata
- **Rate limiting:** Respect 429 responses with retry-after header
- **Timeout:** 30 seconds for all requests
- **Key patterns:**
  - `v2021-11` header required; variant_id is nested dict, not string
  - Bundle selections PUT needs real collection_id (can't be empty)
  - Skip/unskip must pass purchase_item_ids
  - Fetch subscriptions for demand baseline and cohort analysis
  - Queued charges endpoint for monthly charge forecasting

**OpenWeatherMap API (5-day/3-hour forecast):**
- **What it's used for:** Fetch temperature forecasts by zip code for thermal analysis and gel pack sizing
- **SDK/Client:** `requests` HTTP library
- **Implementation:** `GelPackCalculator/gel_pack_shopify.py` (weather module, line ~1200+)
- **Auth:** API key (stored in settings as `openweather_api_key`)
- **Key patterns:**
  - Zip code geocoding endpoint
  - 3-hour forecast data (not just peak highs)
  - Average temperature across entire transit window for gel pack calculations
  - 90% melt efficiency factor applied to gel pack BTU capacity
  - Safety factor (0-50%) configurable per location

**National Weather Service (NWS) Alerts API:**
- **What it's used for:** Fetch active weather alerts (tornado, blizzard, heat, winter storm) for shipping hold recommendations
- **SDK/Client:** `requests` HTTP library
- **Implementation:** `GelPackCalculator/gel_pack_shopify.py` (NWS module)
- **Auth:** None (public API)
- **Endpoint:** `https://api.weather.gov/alerts` by lat/lon
- **Key patterns:**
  - Checked for both origin and destination zip codes
  - Configurable alert types in Settings (tornado, blizzard, heat, winter storm, etc.)
  - Used for `!WeatherHold!` tag recommendations (not automatic application)
  - Includes event severity and description

**Gorgias Helpdesk API:**
- **What it's used for:** Query support tickets, customer satisfaction metrics, and order lookup for troubleshooting
- **SDK/Client:** `requests` HTTP library with Basic Auth
- **Implementation:** `AppyHourMCP/tools/gorgias.py` (line ~15-60)
- **Auth:** Basic Auth (email, API token) from settings
- **Base URL:** `https://{subdomain}.gorgias.com/api`
- **Pagination:** Cursor-based (`next_cursor` in metadata)
- **Key patterns:**
  - Endpoints: `/customers`, `/tickets`, `/satisfaction`
  - Used for customer/order context without leaving system
  - Configurable subdomain, email, API token in settings JSON
  - Settings path: `%APPDATA%/AppyHour/gel_calc_shopify_settings.json`

**Google Sheets API (v4):**
- **What it's used for:** Read/write inventory snapshots, production queries, analytics reports, fulfillment schedules
- **SDK/Client:** `requests` HTTP library (Google Sheets REST API)
- **Implementation:** `AppyHourMCP/tools/google_sheets.py`, `GelPackCalculator/google_integration.py`
- **Auth:** Service account credentials (JSON key file) with OAuth2 Bearer token
- **Key patterns:**
  - Credentials path from settings `gel_calc_shopify_settings.json` or fallback to bundled JSON
  - Fallback: `shipping-perfomance-review-accd39ac4b78.json` (0 GB quota — OAuth preferred)
  - A1 notation for ranges (e.g., `Sheet1!A1:Z1000`)
  - Read operations: return headers + rows as JSON
  - Write operations: overwrite from A1 with auto-formatting

---

## Data Storage

**Databases:**
- No persistent database (no SQL/NoSQL integration)
- All state stored as JSON files (settings, snapshots, history)

**File Storage:**
- **Local filesystem only** — JSON settings files next to executables or in `%APPDATA%/AppyHour/`
- **Google Sheets** — Used as pseudo-database for shared analytics and inventory snapshots
- **Excel (openpyxl)** — Weekly production query imports (AHB_WeeklyProductionQuery_*.xlsx), bulk inventory snapshots
- **CSV exports** — Fulfillment logs, depletion history, swap records, matrix comparisons

**Caching:**
- In-memory caching via module-level singletons (`_recharge`, `_shopify` clients in MCP tools)
- JSON snapshots saved to `snapshots/` directory for historical comparison
- `gel_pack_history.json` for thermal analysis outcome tracking
- No external cache service (Redis, Memcached)

---

## Authentication & Identity

**Auth Provider:**
- Custom JWT/Bearer token approach per service
- No central auth system (OAuth handled per-API)

**Implementation:**
- **Shopify:** Client-credentials OAuth2 with auto-refresh (tokens valid 24h)
- **Recharge:** Bearer token (API key) from environment/settings
- **OpenWeatherMap:** API key from settings
- **Gorgias:** Basic Auth (email + API token) from settings
- **Google Sheets:** Service account JSON key OR OAuth2 refresh token
- All credentials stored in JSON settings files with no encryption layer

---

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Rollbar, or error tracking service)
- Errors logged to stderr via Python logging module
- MCP server logs to `sys.stderr` for visibility in Claude Desktop debug console

**Logs:**
- **Console logging:** Python `logging` module to stderr
- **Settings-based logging level:** Configurable per module
- **File-based:** Depletion reports emailed as CSV attachments
- **History tracking:** Depletion history persisted in settings JSON with undo support

---

## CI/CD & Deployment

**Hosting:**
- Desktop apps: PyInstaller standalone exes (no hosting required)
- MCP server: Subprocess stdio communication (runs locally with Claude Desktop)
- Web UI (fulfillment_web): Flask dev server or embedded in pywebview

**CI Pipeline:**
- None (no GitHub Actions, GitLab CI, or external CI service detected)
- Manual testing via pytest
- Build automation: `build_exe.bat` batch script

**Build Process:**
```bash
# Desktop apps
python -m PyInstaller --onefile --windowed --name "GelPackCalculator" gel_pack_shopify.py
python -m PyInstaller --onefile --windowed --name "InventoryReorder" inventory_reorder.py

# MCP server
mcp run AppyHourMCP.server
```

---

## Environment Configuration

**Required env vars:**
- `APPDATA` — Windows AppData path (used to locate settings JSON)
- `PYTHONPATH` — May need to include AppyHour directory for imports

**Secrets location:**
- Settings JSON: `%APPDATA%/AppyHour/gel_calc_shopify_settings.json` (primary)
- Fallback: `.env` file in project root (not currently used, but listed in .gitignore)
- Bundled credentials: `shipping-perfomance-review-accd39ac4b78.json` (Google service account, 0 GB quota)

**Settings keys (credentials):**
```json
{
  "shopify_store": "elevatefoods.myshopify.com",
  "shopify_access_token": "...",
  "recharge_api_token": "...",
  "openweather_api_key": "...",
  "gorgias_subdomain": "...",
  "gorgias_email": "...",
  "gorgias_api_token": "...",
  "google_credentials_path": "...",
  "smtp_host": "smtp.gmail.com",
  "smtp_user": "...",
  "smtp_password": "..."
}
```

---

## Webhooks & Callbacks

**Incoming:**
- Shopify webhooks: `order/created`, `order/updated` endpoints (configured in Shopify admin) — not currently active in code
- Recharge webhooks: `order_created`, `subscription_updated` — not currently active in code
- HTTP server for OAuth callbacks: `inventory_reorder.py` includes `HTTPServer` for Shopify OAuth redirect (line ~37-38)

**Outgoing:**
- Google Drive API: Upload inventory snapshots, push production queries, append to analytics sheets
- Email (SMTP): Depletion reports, reorder alerts, expiration warnings via `smtplib`
- Slack webhooks: Optional notifications for critical reorder, expiring inventory, shortfall alerts (webhook URL configurable in settings)

---

## External Service Summary

| Service | Purpose | Auth Type | Status |
|---------|---------|-----------|--------|
| Shopify Admin API | Order management, tagging | OAuth2 client-credentials | Active |
| Recharge API | Subscription demand, charges | Bearer token | Active |
| OpenWeatherMap | Temperature forecasts | API key | Active (optional, fallback hardcoded temps) |
| NWS Alerts | Weather warnings | None (public) | Active |
| Gorgias | Helpdesk, customer context | Basic Auth | Active (optional) |
| Google Sheets | Inventory snapshots, reports | Service account | Active |
| SMTP (Gmail) | Email notifications | Password | Active (optional) |
| Slack | Alert webhooks | Incoming webhook URL | Active (optional) |

---

*Integration audit: 2025-02-10*
