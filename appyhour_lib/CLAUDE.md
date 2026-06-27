# appyhour_lib

Shared Python library — pure utilities used by both MCP servers and desktop apps. **NOT** the AppyHour repo (that's the parent dir). Renamed from `appyhour/` 2026-05-09 to disambiguate.

## Layout

```
appyhour_lib/
├── __init__.py
├── credentials.py    # get_shopify_auth() — single source of truth
├── paths.py          # db_path() — canonical shipping.db location
├── db.py             # connect()/connect_ro() — MANDATORY shipping.db opener
└── weather.py        # OpenWeatherMap, NWS alerts
```

## Task Routing

| Task | Read | Skip | Notes |
|------|------|------|-------|
| Shopify auth change | `credentials.py` + every consumer (grep `get_shopify_auth`) | weather | AppyHourMCP re-exports — propagate carefully |
| Weather/alerts | `weather.py` | credentials | Used by gel-pack thermal risk + shipping ops |
| Add new shared util | here, with stdlib-only deps | — | Pure functions only — NO API/UI deps |

## Rules

- **Pure-only.** stdlib + `requests` for weather. No GUI, no MCP, no Flask.
- **`get_shopify_auth()` is the SINGLE source.** AppyHourMCP re-exports it. Never duplicate auth elsewhere.
- **`db.connect()`/`db.connect_ro()` are the ONLY sanctioned way to open `shipping.db`.** NEVER `sqlite3.connect()` it raw. The helper enforces `journal_mode=WAL` + `busy_timeout=10000` + `synchronous=NORMAL`; `busy_timeout` is per-connection (not persisted), and its absence let concurrent writers race a checkpoint and corrupt the DB on 2026-06-27. Writers → `connect()`, readers → `connect_ro()` (`mode=ro`, can't take a write lock or trigger a checkpoint).
- **Backward compatibility.** This lib is a leaf — every consumer depends on it. Breaking changes ripple across 4+ apps.

## Consumers

- `AppyHourMCP/tools/shopify.py` (re-exports auth)
- `AppyHourMCP/tools/shipping.py`
- `AppyHourShippingMCP/`
- `GelPackCalculator/`
- `InventoryReorder/`
- `ShippingReports/`

## History

Renamed `appyhour/` → `appyhour_lib/` on 2026-05-09 because the lowercase package name collided visually with the parent `AppyHour/` repo and broke discovery. 12 imports updated across 5 files at rename time.
