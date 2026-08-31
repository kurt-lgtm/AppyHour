# GORGIAS → `feedback` SYNC — CONSTRAINTS (single source of truth)

> 🔴 **PRE-CHANGE GATE.** Read this before touching `tools/gorgias_sheets_sync.py`,
> `run_gorgias_update.py`, or anything that reads `shipping.db.feedback`. Change the rules
> HERE first, in the same commit as the code. A change that satisfies every rule below but
> makes a failure quieter is still wrong — see the north star.

> 🧭 **NORTH STAR.** Every customer-reported shipping/fulfillment issue lands in
> `shipping.db.feedback` **attributable to the order that actually failed**, within one sync
> cycle, and **any gap in that says so loudly**. This table is the denominator of every
> ticket-rate, carrier-failure and warm-arrival number the operation acts on; a row that
> cannot be joined to an order is not a data point, it is a silent subtraction from the
> measurement. Serves AppyHour's north star: fast, autonomous, **loud failures, never silent
> ones**.

## What it is

`sync_gorgias_to_sheet()` (and its sibling `sync_food_safety_to_sheet()`) pull tickets from
Gorgias, append them to the `UPDATE_Operational Issues` tab, and **tee** each new row into
`shipping.db.feedback`.

- **Input:** Gorgias `GET /tickets` (cursor-paginated, newest-first, `days_back` window),
  filtered to valid shipping/fulfillment issue types (custom field 13282, or tag inference).
- **Output:** one sheet row per ticket (col H comma-joins multiple issue types) + one
  `feedback` row per `(gorgias_link, issue_type)`.
- **Scheduled owner:** `\AppyHour\GorgiasUpdate` — weekly, Wed 09:00, `StartWhenAvailable`
  (catches up after a missed window), running `C:\AppyHourProd\...\gorgias_update.bat`.
- **Freshness/completeness assert:** `_outputs/scripts/freshness_sweep.py` — a recency row
  AND a field-level completeness row (`appyhour_lib/feedback_completeness.py`).

---

## 🔴 GOTCHAS — the failures these rules exist to stop

### 1. The order number is NOT reliably in the ticket. Do not treat text as the primary source.
The customer usually never types it. The authority is the **Gorgias Shopify panel**
(`customer.integrations` → most recent NON-reship order created **before** the ticket).
Text is a fallback, and a dangerous one — see rule 3.

### 2. `customer.integrations` is NOT in the `GET /tickets` LIST payload. (2026-08-17 outage)
It used to be, and the sync read it straight off the list ticket. Gorgias stopped embedding
it; `customer` now carries only `email/firstname/id/lastname/meta/name`. The primary
extraction path returned `""` for every ticket and **34 of 55 wk0817 rows landed orphaned
(61.8%, against a 24-week baseline of 0.0–8.9%)** while the job reported success.
`GET /tickets/{id}` and `GET /customers/{id}` still carry the field, so
`_hydrate_customer_integrations()` re-fetches it per customer (cached per run).
**Never assume a nested object present on a detail endpoint is present on the list endpoint.**
There is no `include=`/`expand=` param — unknown query params return HTTP 400.

### 3. Never take an order number out of a CS reply. It is the REPLACEMENT order.
Agents close these tickets with *"Your new order number is #177002."* Thirteen of the 34
wk0817 orphans contain that sentence and no other number. Writing it attributes the failure
to the **replacement's** carrier — the exact misattribution `_resolve_original_order()` was
built to undo. `_extract_order_from_text` rejects any match preceded by replacement language.
`from_agent` alone is insufficient: customers quote the agent's sentence back in their reply
(ticket 288715629). **Rejecting costs a MISSING; accepting corrupts carrier attribution.**

### 4. A fallback that never runs is not a fallback.
`_shopify_latest_order` called `ShopifyClient._get(...)` — a method that has **never existed**
in that class — inside a bare `except: pass`. It returned `""` on every call for its entire
life, and nobody noticed because the panel path always answered first. When the panel path
died there was no net. **Any except-pass around a lookup hides exactly this.** If a path is
load-bearing, it needs a test that proves it returns a value, not just that it does not raise.

### 5. `dry_run` must mean dry_run — check EVERY writer, not the obvious ones.
The SQLite tee ran unconditionally while the Sheet writes were gated, so `--dry-run` — the
flag you reach for when unsure — still wrote production rows. Pinned by
`test_every_sqlite_tee_call_is_gated_on_not_dry_run`.

### 6. The tee is INSERT-ONLY. A first-pass miss is permanent.
`INSERT OR IGNORE` on the unique index `(gorgias_link, issue_type)`. The sheet-side upsert and
the enrich passes patch the **sheet**; nothing propagates them to `feedback`. So a row written
blank stays blank forever, even after the sheet is enriched, and even after a re-sync.
Repairing history requires `_outputs/scripts/backfill_feedback_order_numbers.py`.
**Consequence to weigh before any change: whatever the sync gets wrong on first contact is
what the analytics see forever.**

### 7. `date_reported` carries TWO formats. Parse both.
`'2026-06-10'` (older rows) and `'08/19/2026'` (current). A parser handling one silently
empties the recent window — which reads as "no rows", not as a bug. See
`parse_report_date()`; `test_both_production_date_formats_parse` pins it.

### 8. Recency freshness cannot see this class of failure.
The `feedback.synced_at` 14-day row in the sweep was **green throughout** the outage. A writer
that runs on time and writes rows with a blank field is worse than one that stops. The
completeness assert (orphan rate per completed report-week, limit 15%) is the one that catches
it. **Keep both — they fail independently.**

### 9. Gorgias tag counts are NOT valid as metrics (rule 81603). Read ticket bodies.

### 9b. 🔴 `feedback` IS APPEND-ONLY. A "full refresh from sheet" wiped `gorgias_link` on 1,121 rows.
`shipping_invoice_db.store_feedback()` was `DELETE FROM feedback` followed by a reinsert —
docstring "Replace feedback entries (full refresh from sheet)" — wired to a live Kori button
(`sync_feedback_sheet`, and the same block inside the full sync). `feedback` has **several
writers with different column coverage**: the Gorgias tee writes `gorgias_link` /
`raw_issue_type`, the Kori sheet payload carries neither. So each "refresh" rewrote the whole
table from the narrowest view of it, and **every column the current sync does not populate was
blanked on rows that already had it**. Measured on the live table (3,595 rows, 2026-08-31):
replaying one sync through the old path destroys **2,474 `gorgias_link` values and 3,379
`raw_issue_type` values**; the 1,121 rows sitting there today with no link are what a previous
pass already cost us. `gorgias_link` is the join key of the completeness assert (gotcha 8) and
the dedup index — losing it silently shrinks the denominator every ticket rate is computed on.

Second loss on this same table by a different mechanism (gotcha 2 orphaned 34 of 55 wk0817
rows), which is why the write path is now **structurally incapable of loss** rather than
carefully correct. Kurt's ruling 2026-08-31: *"I don't see a reason why we should delete
anything there."* A ticket happened; that fact does not stop being true.

**The rules now enforced in `store_feedback()`:**
- **No DELETE in any sync/import path.** The only function that removes rows is
  `delete_feedback_rows(conn, ids, confirm=True, reason=...)` — id-addressed (no widenable
  WHERE), gated on an explicit confirm **and** a written reason, and never called by a sync.
- **An empty incoming value never overwrites a populated column.** Every column update is
  `CASE WHEN <incoming> <> '' THEN <incoming> ELSE <col> END`. A sync that stops emitting a
  field can no longer erase it.
- **Upsert key = `(gorgias_link, issue_type)`** — measured on the live table: 2,474 rows,
  **0 duplicate groups**, already backed by the partial UNIQUE index
  `idx_feedback_dedup_link_issue`.
- **There is NO proven key for the 1,121 link-less historical rows.**
  `(order_number, issue_type, date_reported)` has 8 duplicate groups / 202 excess rows among
  them, so upserting on it would MERGE genuinely distinct events. Link-less rows are therefore
  **insert-only**, deduped on the full natural tuple with multiplicity preserved (insert the
  shortfall, never the whole batch) — idempotent without ever touching an existing row.
- 🔴 **"Rows that vanished from the sheet should vanish here" is NOT what this path does any
  more.** Sheet rows do get deleted occasionally (audit sweeps —
  `scripts/audits/apply_trawl_and_mark_nosignal.py` removed 51). Under the old code that
  silently deleted DB rows as a side effect of the next sync. If that removal is genuinely
  wanted, express it as an explicit `delete_feedback_rows()` call driven by the audit that
  decided it — absence from a sheet is not evidence that the ticket did not happen.

Pinned by `GelPackCalculator/tests/test_feedback_append_only.py` — **production-shape**: a
read-only slice of the live table copied to scratch sqlite, not an invented fixture. All six
behavioural cases were verified RED against the old `DELETE`-and-reinsert implementation before
being made green (three of four guards this codebase wrote for earlier data-loss bugs were green
against injected shapes and still failed).

### 10. Prod runs from a SEPARATE COPY: `C:\AppyHourProd\AppyHour\...`
Editing the working tree does not change what the scheduled task runs. As of 2026-08-25 the
prod copy predates several working-tree fixes. **A fix is not deployed until that copy is
synced** — verify the prod file, not the repo file.

---

## Invariants (must hold after any change)

1. An order number is written only from an **authoritative** source: the Gorgias Shopify
   panel, or Shopify Admin by the ticket's customer email. Never inferred from a name, a
   date proximity, or a CS reply. Not resolvable → **MISSING**, never a guess.
2. Historical backfills require two independent sources to **agree**; disagreement → MISSING.
3. The tee never runs under `dry_run`.
4. The tee never overwrites a non-blank `order_number`.
5. The completeness assert's threshold is changed only with a fresh measurement in the commit
   message. Raising it to silence a flag is prohibited — the flag means the join population
   shrank and every rate off it is a floor.
6. Any new extraction path ships with a test that proves it **returns a value** on real data.
7. **No sync, import, or refresh path may DELETE from `feedback`** (gotcha 9b). Removal happens
   only through `delete_feedback_rows(..., confirm=True, reason=...)`.
8. **No write may replace a populated column with an empty value**, whatever the writer's column
   coverage. Adding a writer that covers fewer columns must be safe by construction.
9. An upsert key is adopted only after its **duplicate count is measured against the live
   table** and reported. Unproven key → insert-only, never upsert (an upsert on a non-unique key
   merges distinct events, which reads as a clean table and is a silent subtraction).

## Files

| Path | Role |
|------|------|
| `GelPackCalculator/shipping_invoice_db.py` | `store_feedback` (append-only merge) + `delete_feedback_rows` (the only deleter) |
| `GelPackCalculator/kori/gel_pack_webview.py` | Kori sheet sync — the button; supplies `gorgias_link` so rows land on the proven key |
| `GelPackCalculator/tests/test_feedback_append_only.py` | production-shape append-only + idempotency tests |
| `ShipRouting/server/shipping_invoice_db.py` | vendored copy for the DO deploy — keep `store_feedback` identical |
| `AppyHourMCP/tools/gorgias_sheets_sync.py` | sync + extraction + SQLite tee |
| `AppyHourMCP/run_gorgias_update.py` | CLI entry (`gorgias_update.bat`, scheduled task) |
| `appyhour_lib/feedback_completeness.py` | field-level completeness assert + threshold derivation |
| `tests/test_feedback_completeness.py` | production-shape tests (real wk0608 / wk0727 / wk0817) |
| `tests/test_gorgias_order_extraction.py` | extraction + hydration + dry_run guards |
| `_outputs/scripts/freshness_sweep.py` | wires the assert into the weekly sweep |
| `_outputs/scripts/backfill_feedback_order_numbers.py` | gated, dry-by-default historical repair |
