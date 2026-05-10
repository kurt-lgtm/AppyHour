# AppyHourMCP

Main MCP server exposing AppyHour domain tools to Claude Code. FastMCP, stdio transport.

## Layout

```
AppyHourMCP/
├── server.py              # entry: load tools, register(mcp), stdio loop
├── context.py             # shared context / auth bridges
├── constants.py           # NAME_TO_SKU, FOOD_PREFIXES, curations
├── tools/
│   ├── shopify.py         # GraphQL order ops (auth via appyhour_lib.credentials.get_shopify_auth)
│   ├── order_edit.py      # beginEdit/addVariant/setQuantity/commitEdit
│   ├── inventory.py       # demand, reorder, cut order
│   ├── shipping.py        # cost, transit, hub analysis
│   ├── gelcalc.py         # thermal sizing
│   ├── matrix_qc.py       # CheckResult-based validation
│   ├── ops_summary_builder.py
│   ├── product_catalog.py
│   ├── google_sheets.py   # OAuth refresh, append/read tabs
│   ├── gorgias.py + _gorgias_internal.py + gorgias_sheets_sync.py
│   └── ...
└── data/                  # build_sku_database.py + cached lookups
```

## Task Routing

| Task | Read | Skip | Skills/Scripts |
|------|------|------|----------------|
| Add new tool | `server.py`, an existing `tools/*.py` as template | desktop apps | — |
| Order-edit bug | `tools/order_edit.py`, `tools/shopify.py` | inventory/, shipping/ | shopify-dev MCP for GraphQL validation |
| Cut-order tool | `tools/inventory.py`, `InventoryReorder/cut_order_generator.py` | shipping/, gelcalc/ | — |
| Gorgias sync | `tools/gorgias*.py`, `~/.knowledge/ops/gorgias*` | rest | — |
| Sheets reporting | `tools/google_sheets.py` | rest | — |

## Conventions

- Each tool exports `register(mcp)` (FastMCP-compatible)
- Pydantic models for input validation: `class XxxInput(BaseModel)`
- Errors return as strings: `return format_error(str(e))`
- Auth: NEVER hardcode — pull via `appyhour_lib.credentials.get_shopify_auth()` (single source) or env vars
- DEFAULT_API_VERSION = `2026-04`

## Run

```bash
# Claude Desktop config: stdio transport via mcp[cli]>=1.0.0
PY=/c/Users/Work/anaconda3/python.exe
$PY AppyHourMCP/server.py  # rarely run directly; usually launched by Claude
```

## Critical

- **Cursor pagination required** (Recharge); page-based loops silently
- **Filter qty=0** from CalculatedOrder before counting
- **`_rc_bundle` property** = Recharge curation, removable; no props = paid extras, keep
- **shopify-dev MCP** available for GraphQL/component validation — use it before shipping new GraphQL
