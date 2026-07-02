# PLAN — Single-writer lock for shipping.db + raw-writer migration

- **Date:** 2026-07-02 · **Mode:** /forge STANDARD→EPIC (leaf-lib change + multi-file migration + tests)
- **Status:** PLANNED — not built (a DB sync was running at plan time; all discovery was read-only).
- **Source of truth:** `_outputs/reports/HANDOFF-2026-07-02-db-single-writer-lock.md` (direction), this plan (build spec).
- **Constraints doc:** `appyhour_lib/CLAUDE.md` already documents the WAL/busy_timeout invariant — EXTEND it with the writelock semantics in the SAME commit as Part 1 (feature-constraints-doc gate).

## Premise check (forge step 0 — done 2026-07-02, read-only)
- ✅ **No lock exists** — `writelock` / `DBWriterBusy` / `filelock` / `single-writer` → zero hits. Part 1 is new, not reinventing.
- ✅ **Handoff inventory is ACCURATE** (initial broad `rg sqlite3.connect` gave a FALSE-NEGATIVE list — it silently omitted `shipping_invoice_db.py`; a targeted grep confirmed `shipping_invoice_db.py:129` still does raw `sqlite3.connect(path)` in `init_db()`). **Lesson for the build: do NOT trust one broad grep — verify each writer per-file.**
- ✅ **auto_import opener RESOLVED:** `auto_import.py` opens the DB via `sidb.init_db()` → `shipping_invoice_db.py:126 init_db()` → raw `sqlite3.connect(path)` (line 129). `init_db()` is the CENTRAL opener: migrating it alone routes `auto_import` + every `init_db` caller through the lock. **This is the single highest-leverage change.**

## Part 1 — advisory single-writer lock in `appyhour_lib/db.py` (stdlib only)
`appyhour_lib` is pure-stdlib (+requests) — no `psutil`/`filelock`. Use `os`, `ctypes`, `json`, `atexit`.

**Lock file:** `<real_db>.writelock` beside the LIVE db (via `db_path()`/`APPYHOUR_DB_PATH` — must resolve to MSIX LocalCache, not empty Roaming). Atomic create `os.open(O_CREAT|O_EXCL|O_WRONLY)`; contents JSON `{pid, create_time, script=sys.argv[0], started_at, host}`.

**Crash + PID-reuse recovery** (the hard part):
- Liveness: `ctypes` → `kernel32.OpenProcess(SYNCHRONIZE, False, pid)` + `GetExitCodeProcess == STILL_ACTIVE(259)`. No handle / exited → dead → break lock.
- PID-reuse guard: store holder `create_time` (`GetProcessTimes` on the handle at acquire); a live PID whose create_time ≠ stored value = reused PID → treat as dead → break.
- Belt-and-suspenders: `AH_WRITE_LOCK_MAX_AGE` (default 1800s) — any lock older than this breaks (covers SIGKILL where atexit never ran).

**Wait/fail:** `AH_WRITE_LOCK_WAIT` (default **90s**) — poll, then raise `DBWriterBusy` naming the holder ("PID 6480 run_gorgias_update.py since 14:31"). Manual users set `=0` (instant fail); scheduled tasks set higher. Escape hatch `AH_WRITE_LOCK_DISABLE=1`.

**API (drop-in):** `connect()` acquires then returns a `LockedConnection` (sqlite3 `factory=`) whose `.close()` releases then `super().close()`. **Process-local refcount** so nested `connect()` in one process re-enters (increment/decrement; release the file at 0). `atexit` safety net. `connect_ro()` **never** touches the lock — readers must never block (health check, monitors, analysis).

## Part 2 — route raw shipping.db WRITERS through `connect()`
Per-file confirm target is shipping.db (NOT box_distvol / `AppyHourMCP/tools/cache.py` / a cache) before migrating. **Verify each raw `sqlite3.connect` individually** (broad grep proved unreliable).

Order (highest leverage first):
1. **`shipping_invoice_db.py:129 init_db()`** → `connect()`. Covers `auto_import.py` + all callers. (Keep `connect_ro` for the read paths.)
2. `daily_shipping_sync.py`, `sync_shopify_orders.py`, `pp_backfill_aged_out.py` — the daily orchestrator + fulfillment/PP writers.
3. Feedback writers: `reclassify_feedback.py`, `reconcile_lost_in_transit.py`, `import_feedback_csv.py`, `scripts/incident-fixes/normalize_feedback_dates.py`.
4. `cohort_attribution.py`, `build_tracking_link.py` (enrich/writeback), `kori/db_snapshots.py` (snapshot).
5. `AppyHourMCP/wednesday_ops_run.py` — mixed (raw + helper); fix the raw leg.
6. CONFIRM-then-skip (likely readers / other-DB — migrate reads to `connect_ro` only if they touch shipping.db): `ShippingReports/{build_wallet_share,melt_efficiency_calibrator,postmortem_runner,reports/box_size_report,safety_factor_sweep,parsers/veho}.py`, `AppyHourMCP/tools/cache.py`, `backup_offsite.py` (snapshot), `_retired/*` (dead — ignore).

## Test matrix
1. acquire → write → release (lock file gone after close).
2. 2nd writer while held → raises `DBWriterBusy` naming the holder.
3. dead-PID stale lock → auto-breaks + acquires.
4. **reused-PID** (same pid#, different create_time) → treated stale → breaks.
5. **reentrant** nested `connect()` in one process → no self-deadlock; releases only at outer close.
6. `connect_ro()` while a writer holds the lock → never blocks, reads committed data.
7. SIGKILL mid-write → next writer breaks stale lock (atexit didn't run) → no permanent lock.
8. `AH_WRITE_LOCK_DISABLE=1` → lock fully bypassed (legacy behavior).

## Rollout
1. Part 1 in `db.py` (lock **on** by default; `AH_WRITE_LOCK_DISABLE` escape) + extend `appyhour_lib/CLAUDE.md` + `REBUILD-WITH-AI.md §5.1` (same commit).
2. **2-terminal collision test** on the LIVE box (Kurt): terminal A holds a write conn; terminal B `connect()` must `DBWriterBusy`, not corrupt.
3. Part 2 migration incrementally, starting with `init_db()` (step 1) — run each migrated script once to verify it still writes.
4. Atomic commit per file; `git revert` (not reset --hard) to roll back.

## Open decisions (recommendation in parens)
1. Default `AH_WRITE_LOCK_WAIT` = **90s** — long enough to ride out a sync leg, short enough to fail a stuck one. OK?
2. Liveness via **ctypes** (keep stdlib, honor pure-lib rule) vs adding psutil. Recommend ctypes.
3. Part-2 scope THIS build: **(a) Part 1 + `init_db()` only** (smallest safe slice — protects auto_import + most writers via the central opener) vs **(b) all ~8 high-confidence writers**. Recommend (a) first, then (b) as a fast follow — smaller blast radius per commit.

## Guardrails
- Never lock readers (`connect_ro`/`immutable=1`).
- Lock file MUST sit beside the REAL db (LocalCache) — resolve via `db_path()`/`APPYHOUR_DB_PATH`; unpackaged writers otherwise hit empty Roaming.
- The lock is the BACKSTOP, not the plan — still avoid launching a manual ingest during the noon sync window.
