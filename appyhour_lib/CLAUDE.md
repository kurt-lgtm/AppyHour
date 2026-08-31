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
│                     #   + write_lock_holder()/assert_write_lock_free() (lock-release proof)
├── cancel.py         # CancelToken/StageCancelled/checkpoint() — cooperative stage cancel
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
- **🔴 A long stage that can be ABANDONED must be CANCELLABLE — `cancel.py` (2026-08-31).** A watchdog that stamps `fail:Timeout` and moves on does not stop anything: Python cannot kill a thread, so the abandoned stage kept writing (~11,900 upserts) holding `<db>.writelock` forever and killed `daily_shipping_sync` three runs running. `CancelToken.check()` / `checkpoint()` belong **only at committed boundaries — after `commit()` AND after `close()`**; a check inside a transaction abandons a partial write, and a check with the connection open leaves the lock held (a louder orphan, not a fixed one). A loop with no reachable committed boundary does NOT get a token — say so and leave it. Callers must treat "still alive after the grace join" as a named hard alarm, never a quieter move-on. Full rules: `HEARTBEAT_RULES.md` rule 14. Tests: `tests/test_stage_cancel.py` (scratch DB only).
- **🔴 CANONICAL-PATH GUARD (2026-08-31) — `connect()` HARD-REFUSES a second name for `shipping.db`.**
  The three corruptions were caused by **two NAMES for one file**, not by concurrent writers: measured,
  4 writers on ONE path with no `busy_timeout` ran ~2,900 txns clean, while two names for one image
  (NTFS hardlink) *with* `busy_timeout=10000` corrupted 5/5 with `database disk image is malformed`.
  Two names = two `-shm` lock namespaces = two independent checkpointers folding into one image. The
  advisory writelock cannot catch it — it is keyed `str(target) + ".writelock"`, i.e. per-NAME, and
  two `connect()` calls on two names BOTH acquired a lock (same-name control correctly refused). So
  `connect()` calls `paths.assert_canonical_db()` and raises **`NonCanonicalDBPath`** (a
  `RuntimeError`) when the target is named `shipping.db` and is not under `DATA_ROOT`. Promoted from
  `sync_logon._resolve_db_guarded`; `shipping_invoice_db.init_db` enforces it too.
  - **Allowed with NO env var:** any other file name; anything under `%TEMP%` (tests, scratch copies —
    pytest `tmp_path` just works); a pre-migration machine with no `C:\AppyHourData`. An
    `APPYHOUR_DB_PATH`/`AH_DB_OVERRIDE` naming exactly that file is honored and prints a warning.
    **`%APPDATA%\AppyHour\shipping.db` has NO escape hatch** — it is the virtualized second name.
  - **`connect_ro()` is deliberately NOT guarded.** `mode=ro` cannot take a write lock or checkpoint,
    so it cannot join the race; guarding it would block read-only forensics on a backup or snapshot,
    which is the SAFE path.
  - **A refusal RAISES; it does NOT Slack.** `notify` is opt-in (`AH_UNATTENDED=1`, or `notify=True`
    as `sync_logon` passes) and deduped per path per process. Keeping the promoted function's
    `notify(level="critical")` as a library default posted **7 CRITICALs to Kurt in 90 s from one test
    run** — an alarm that fires on developer typos gets muted, which is worse than no alarm.
  - 🔴 **Do NOT report this as the fix for 2026-07-03.** That day the canonical path *was* the
    MSIX-virtualized `%APPDATA%` one, and MSIX splits packaged from unpackaged writers at the same
    name string. The **07-08 move to `C:\AppyHourData`** is what stopped the corruptions. This guard
    prevents a REGRESSION onto a second name. Full rules: `HEARTBEAT_RULES.md` rule 15.
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
