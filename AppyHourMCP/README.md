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

## Available Tools (15)

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

## Testing

```bash
# Verify syntax
python -m py_compile server.py

# Test with MCP Inspector
npx @modelcontextprotocol/inspector python server.py
```

## Architecture

The MCP server imports Python modules directly from the three sibling projects (same pattern as the existing FastAPI layer in `GelPackCalculator/app/`). No HTTP calls are needed — everything runs in-process via stdio transport.
