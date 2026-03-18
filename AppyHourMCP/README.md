# AppyHour Unified MCP Server

MCP server that unifies GelPackCalculator, InventoryReorder, and ShippingReports into a single tool interface for Claude Desktop and other MCP clients.

## Setup

### 1. Install dependencies

```bash
pip install mcp[cli] pydantic requests openpyxl pyyaml
```

### 2. Configure Claude Desktop

Add to your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "appyhour": {
      "command": "python",
      "args": ["C:/Users/Work/AppyHour/AppyHourMCP/server.py"],
      "env": {
        "PYTHONPATH": "C:/Users/Work/AppyHour/GelPackCalculator;C:/Users/Work/AppyHour/InventoryReorder;C:/Users/Work/AppyHour/ShippingReports"
      }
    }
  }
}
```

Adjust paths to match your actual `AppyHour` folder location.

### 3. Restart Claude Desktop

The server runs via stdio as a subprocess. Restart Claude Desktop to pick up the new config.

## Available Tools (16)

### Gel Pack Calculator
- **appyhour_analyze_shipment** — Run thermal analysis for a single shipment
- **appyhour_get_weather** — Fetch weather forecast for a zip code
- **appyhour_get_weather_alerts** — Fetch NWS severe weather alerts

### Shopify Orders
- **appyhour_fetch_orders** — Fetch unfulfilled orders by tag filters
- **appyhour_analyze_orders** — Fetch orders and run thermal analysis on each
- **appyhour_update_order_tags** — Add/remove tags on a Shopify order (write)

### Inventory & Forecasting
- **appyhour_get_subscription_demand** — Current weekly SKU demand from subscriptions
- **appyhour_get_upcoming_charges** — Queued charges by month from ReCharge
- **appyhour_forecast_demand** — Multi-month cohort-based demand forecast
- **appyhour_get_reorder_alerts** — Reorder alerts (CRITICAL/WARNING/PLAN)

### Shipping Reports
- **appyhour_analyze_shipping_costs** — Cost breakdown by state/carrier/hub/zone
- **appyhour_analyze_transit** — Transit time performance metrics
- **appyhour_detect_misroutes** — Detect wrong-hub shipments
- **appyhour_get_chronic_3day_zips** — Find zips with chronic 3+ day transit
- **appyhour_get_zip_overrides** — Generate complete routing override rules
- **appyhour_apply_zip_routing_tags** — Apply FedEx 2Day routing tags to orders in override zips (checks conflicts, removes stale tags, adds new)

## Zip Routing Overrides

The GelPackCalculator maintains per-zip routing overrides in `gel_calc_shopify_settings.json` under the key `zip_routing_overrides`. These override state-level transit assignments for specific zip prefixes.

### Data Structure

```json
{
  "zip_routing_overrides": {
    "329": {
      "action": "force_2day",
      "transit_override": "2-Day",
      "reason": "FL Gulf Coast — OnTrac 91% 3-day rate. Route FedEx 2Day."
    },
    "150": {
      "action": "transit_warning",
      "transit_override": "2-Day",
      "reason": "Pittsburgh metro — Nashville OnTrac 100% 2-day"
    }
  }
}
```

### Actions

| Action | Effect | When to use |
|--------|--------|-------------|
| `force_2day` | Force FedEx 2Day routing (apply `!NO OnTrac` tag) | Zip consistently exceeds transit limit on OnTrac, hot state risk |
| `block_hub` | Block a specific hub for this zip | Hub/zip combination has chronic issues |
| `transit_warning` | Flag for review, adjust gel packs | Borderline transit, may need extra insulation |

### FL Gulf Coast Override (March 2026)

OnTrac zone 5 from Dallas to FL Gulf Coast/West consistently hits 3 days (91% failure rate on 2-day assignment). The following zip prefixes route to FedEx 2Day:

| Prefix | Area | Reason |
|--------|------|--------|
| 329xx | Melbourne/Sebastian | 100% 3-day |
| 335xx | Tampa/Brandon/Riverview | ~100% 3-day |
| 336xx | St Petersburg/Clearwater | ~100% 3-day |
| 337xx | Port Charlotte/Englewood | 100% 3-day |
| 338xx | Lakeland/Winter Haven | ~83% 3-day |
| 339xx | Fort Myers/Cape Coral/Naples | ~82% 3-day |
| 341xx | Naples/Bonita Springs | 100% 3-day |
| 342xx | Sarasota/Bradenton | ~83% 3-day |
| 346xx | Brooksville/Spring Hill | 100% 3-day |

**Not overridden** (OnTrac delivers in 1-2 days):
- North FL 320-326xx (Jacksonville, St Augustine) — 0% failure
- SE FL 330-334xx, 347xx, 349xx (Miami, Orlando, Boca, Ft Lauderdale) — 0% failure

### State Transit Assignments (Texas/Dallas Hub)

| State | Assignment | Notes |
|-------|-----------|-------|
| TX, NV, TN | 1-Day | Core hub proximity |
| AZ, CA, GA | 2-Day | Upgraded from 1-Day (March 2026) — actual avg 1.2-1.8d |
| KY | 2-Day | Louisville zone 5 consistently 2-3d |
| FL, CO, ID, IL, IN, MI, MN, NC, OH, OR, SC, WA | 2-Day | Standard |
| AL, MS | 3-Day | Upgraded from 2-Day (March 2026) — 33-50% hit 3d, hot states |
| Northeast (CT, DE, MA, MD, NJ, NY, PA, VA) | 3-Day | Standard |

### Shopify Routing Tags

| Tag | Effect |
|-----|--------|
| `!NO OnTrac - Dallas_AHB!` | Block OnTrac, force FedEx from Dallas hub |
| `!NO UPS - Dallas_AHB!` | Block UPS from Dallas hub |
| `!ANY - Dallas_AHB!` | Use any carrier (exclusive — can't combine) |

Apply `!NO OnTrac - Dallas_AHB!` to orders matching `force_2day` zip overrides.

## Testing

```bash
# Verify syntax
python -m py_compile server.py

# Test with MCP Inspector
npx @modelcontextprotocol/inspector python server.py
```

## Architecture

The MCP server imports Python modules directly from the three sibling projects (same pattern as the existing FastAPI layer in `GelPackCalculator/app/`). No HTTP calls are needed — everything runs in-process via stdio transport.
