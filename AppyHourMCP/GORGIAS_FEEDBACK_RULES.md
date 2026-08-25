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

## Files

| Path | Role |
|------|------|
| `AppyHourMCP/tools/gorgias_sheets_sync.py` | sync + extraction + SQLite tee |
| `AppyHourMCP/run_gorgias_update.py` | CLI entry (`gorgias_update.bat`, scheduled task) |
| `appyhour_lib/feedback_completeness.py` | field-level completeness assert + threshold derivation |
| `tests/test_feedback_completeness.py` | production-shape tests (real wk0608 / wk0727 / wk0817) |
| `tests/test_gorgias_order_extraction.py` | extraction + hydration + dry_run guards |
| `_outputs/scripts/freshness_sweep.py` | wires the assert into the weekly sweep |
| `_outputs/scripts/backfill_feedback_order_numbers.py` | gated, dry-by-default historical repair |
