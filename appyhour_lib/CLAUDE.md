# appyhour_lib

Shared Python library — pure utilities used by both MCP servers and desktop apps. **NOT** the AppyHour repo (that's the parent dir). Renamed from `appyhour/` 2026-05-09 to disambiguate.

## Layout

```
appyhour_lib/
├── __init__.py
├── bootstrap.py      # init()/require_env() — MANDATORY first call in scheduled/CLI mains
│                     #   (UTF-8 stdio + canonical .env; reuses notify's loader)
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
- **ONE writer at a time — busy_timeout does NOT prevent two independent checkpointers from corrupting the WAL.** Corrupted again 2026-07-01 when a manual `weather_sync_cron.py` raced the live MCP servers. **Claude/agents stay READ-ONLY** (`connect_ro`); manual writers run only when the MCP servers aren't mid-sync. Recovery + the canonical DB path (**`C:\AppyHourData\shipping.db` as of 2026-07-08 — moved OUT of `%APPDATA%` because MSIX virtualizes it; never symlink the legacy path, never restore to Roaming/LocalCache**) live in `REBUILD-WITH-AI.md` §5.1 + memory `shipping-db-msix-wal-corruption`. Health check: `_outputs/scripts/shipping_db_healthcheck.py`.
- **🔴 SINGLE-WRITER LOCK (2026-07-02) — `connect()` enforces one writer *process* at a time.** It takes an advisory `<db>.writelock` (atomic `O_CREAT|O_EXCL`, JSON `{pid, create_time, script, started_at, host}`) beside the *real* DB. A 2nd `connect()` waits `AH_WRITE_LOCK_WAIT` (default **90s**) then raises **`DBWriterBusy`** naming the holder — it does NOT corrupt. Crash-safe: a dead-PID / reused-PID (create_time mismatch) / over-`AH_WRITE_LOCK_MAX_AGE` (default 1800s) lock is auto-broken; refcount makes nested `connect()` reentrant; `atexit` releases on exit. Escape hatch `AH_WRITE_LOCK_DISABLE=1`. **`connect_ro()` NEVER touches the lock** (readers must never block). The lock only protects code that opens via the helper — raw `sqlite3.connect(shipping.db)` still bypasses it, so ALL writers MUST route through `connect()` (Part 2 migration in progress; `shipping_invoice_db.init_db` migrated 2026-07-02). Tests: `tests/test_db_writelock.py` (temp DB only — NEVER the live file). Manual 2-terminal collision test is a human step.
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
