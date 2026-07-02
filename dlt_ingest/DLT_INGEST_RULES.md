# DLT_INGEST_RULES.md — constraints SSOT for the dlt ingest pipeline

🔴 **PRE-CHANGE GATE:** read this before touching anything in `AppyHour/dlt_ingest/`.
Change rules HERE first, same commit. Single source of truth for the isolated dlt pipeline.

## What it is

`shopify_orders_pipeline.py` is an **isolated, additive** [dlt](https://dlthub.com) pipeline
skeleton that *would* pull Shopify orders (Admin REST, Link-header pagination) into its **OWN**
sqlite database at `AppyHour/dlt_ingest/dlt_ingest.db`. It is a scaffold — import-clean and a
no-op unless run with an explicit `--run` flag.

## Gotchas / failure-modes (negatives-first)

- 🔴 **NEVER touch `shipping.db`.** This pipeline writes to its OWN sqlite `dlt_ingest.db` inside
  `dlt_ingest/`. It must NEVER read, write, connect, checkpoint, or name the live
  `shipping.db` (MSIX LocalCache / `%APPDATA%/AppyHour/shipping.db`) or its `-wal`/`-shm`.
  MSIX-sandboxed writes to that WAL-mode DB corrupt it (corrupted 6/27 + 7/1). Different file,
  different directory, no overlap.
- 🔴 **Import must NOT hit the API.** Importing this module, or running it without `--run`, must
  make ZERO network calls. The API pull lives behind the `--run` flag so tests / imports never
  touch Shopify. Guard = `if "--run" not in sys.argv: print instructions; return`.
- 🔴 **Read-only against Shopify.** GET orders only. This pipeline must NEVER mutate Shopify
  (no order edits, tags, POST/PUT/DELETE). It is an ingest, not a writer.
- 🔴 **Creds from the canonical source ONLY.** Use `appyhour_lib.credentials.get_shopify_auth()`
  (single source of truth; reads env `SHOPIFY_STORE_URL`/`SHOPIFY_ACCESS_TOKEN` or the settings
  JSON at `%APPDATA%/AppyHour/inventory_reorder_settings.json`). NEVER hardcode a token/store or
  duplicate auth. If creds are missing, `get_shopify_auth()` raises loud — do not silent-default.
- ⚠️ **`workers=1` + `dev_mode`.** Keep the pipeline single-worker (no parallel writers to the
  sqlite file) and `dev_mode=True` in the skeleton so schema iterations get isolated datasets.
  Raising workers risks concurrent sqlite writers — the exact class of bug that corrupts WAL DBs.
- ⚠️ **Link-header pagination, not page=N.** Shopify Admin REST uses `Link: <...>; rel="next"`.
  Do not use page-number pagination (silently loops / caps). Follow the `next` URL until absent.
- 📝 dlt creates `dlt_ingest.db` + a `_dlt_*` state/schema tables namespace. That is dlt's own
  bookkeeping and is fine — it is inside `dlt_ingest/`, isolated from all AppyHour DBs.

## I/O contract

- Input: Shopify Admin REST `/orders.json` (GET, paginated). Creds via `get_shopify_auth()`.
- Output: `dlt_ingest/dlt_ingest.db`, dataset `shopify`, table `orders`.
- Invocation: `python dlt_ingest/shopify_orders_pipeline.py --run` (only path that hits the API).

## Non-goals

- Not the live shipping ingest (that's `auto_import.py` / the MCP sync → shipping.db). Additive,
  parallel, experimental. Does not replace or feed any live path.
