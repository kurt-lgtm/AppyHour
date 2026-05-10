# AppyHourShippingMCP

Shipping-only MCP server. Subset of AppyHourMCP scoped to shipping analysis — separate process so shipping queries don't load full AppyHour toolset.

## Layout

```
AppyHourShippingMCP/
├── server.py        # entry: stdio MCP, registers shipping tools only
├── utils.py         # helpers
└── pyproject.toml   # standalone packaging
```

## Task Routing

| Task | Read | Skip | Notes |
|------|------|------|-------|
| Add shipping query | `server.py`, `../AppyHourMCP/tools/shipping.py` (canonical logic) | full AppyHourMCP | Mirror logic from main MCP if it lives in both |
| Debug zone/transit calc | `server.py`, `~/.knowledge/ops/transit*` | inventory/gelcalc | TNT = final-mile pickup→delivery only, never carrier API transit |
| Veho-specific | `../ShippingReports/` data, `~/.knowledge/shipping_db_path.md` | non-Veho carriers | shipments.db = canonical with Veho |

## Why two MCP servers?

Main `AppyHourMCP` includes ~15 tools. When Claude only needs shipping, the extra tool surface wastes tokens and slows enumeration. This server registers only shipping tools.

**Rule:** if a shipping tool gets added/changed in `AppyHourMCP/tools/shipping.py`, mirror the change here. Diverging logic = bugs.

## Run

```bash
PY=/c/Users/Work/anaconda3/python.exe
$PY AppyHourShippingMCP/server.py  # stdio, launched by Claude Desktop
```

## Critical

- **HARD RULE:** transit = final-mile pickup → final-mile delivery only. Never use carrier API `transit_time`.
- **Veho:** use ParcelPanel `pickup_date`, NOT `Tendered`.
- **shipments.db** at `ShippingReports/output/shipments.db` is canonical (with Veho). `Kori-shipping.db` is runtime-only, no Veho.
