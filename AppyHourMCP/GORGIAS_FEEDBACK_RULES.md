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

> 🔴 **CUSTOMER SENDS (Kurt 2026-09-02).** Anything that posts a PUBLIC message to a Gorgias
> ticket (API `POST /tickets/{id}/messages` with `public: true`, or any reply/email to a
> customer) waits for Kurt's EXPLICIT "send" / "post". Draft tweaks ("yes", "say X") are
> feedback on the draft, not a go — re-show and wait. Burn: ticket 290607848, reply posted off
> "yes, say rest assured…" and Kurt said it was not a go. Sent messages cannot be recalled.
> Global rule: `~/.claude/rules/live-writes.md` §"Customer-facing messages".

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

### 7. `date_reported` carries TWO formats. Parse both. 🔴 NEVER `MAX()` it in SQL.
`'2026-06-10'` (older rows) and `'08/19/2026'` (the 2026-06-19..2026-09-07 band). A parser
handling one silently empties the recent window — which reads as "no rows", not as a bug. See
`parse_report_date()`; `test_both_production_date_formats_parse` pins it.

**🔴 The eleven-week burn this rule used to under-state (2026-09-07).** Dual format is not merely
a parsing inconvenience — it makes **`MAX(date_reported)` and `ORDER BY date_reported` LIE**, because
SQLite compares TEXT lexically and `'2026-06-19' > '09/04/2026'` (the `'2'` beats the `'0'`). One
ISO row therefore MASKS every newer US-format row. From 2026-06-19 to 2026-09-04 `MAX(date_reported)`
read **2026-06-19** while `MAX(synced_at)` read **2026-09-04** and the tee ran every week: the
synced-but-frozen shape (`ENGINEERING_GOTCHAS` A4/C4). **692 rows, 223 of them `Arrived Warm`**, sat
behind that mask, and warm arrival is one of the two floors never for sale in the north star.

Root cause: `SHIPPING_PIPELINE.md`'s 2026-06-18 entry claims the chokepoint was hardened so the tee
"now `_normalize_date_reported()`s every insert to ISO". **That fix was never landed** — no commit
touched `gorgias_sheets_sync.py` between 2026-06-12 and 2026-06-27, and the symbol existed nowhere in
the codebase. Only the ONE-TIME backfill ran, which rewrote history and made the table look fixed for
exactly one day. **Repairing the data instead of the writer bought three days of green.**

Rules now in force:
- **EVERY writer writes ISO — see rule 7d. The tee is not the only one.**
- **The tee writes ISO.** `_normalize_date_reported()` in `gorgias_sheets_sync.py` canonicalises the
  DB value; **the SHEET keeps `%m/%d/%Y`** (Ops Summary formulas compare column A against date cells).
  Never "simplify" this by changing `date_str` at its construction site — that breaks the sheet.
- **Never guess.** An unparseable value is written verbatim, never dropped and never year-inferred;
  year inference is what made 2025 tickets masquerade as 2026 in the original 2026-06-18 incident.
- **Consumers still parse BOTH** — the pre-cutover legacy band is untouched (see rule 7b).
- **Never `MAX(date_reported)`/`ORDER BY date_reported` in SQL.** Parse, then take the max
  (`max_event_date()`). `test_lexical_max_masks_newer_rows_but_the_assert_does_not` pins the burn.

### 7d. 🔴 THERE ARE THREE `date_reported` WRITERS. All call ONE shared function. A new one MUST too.
**Near-miss, 2026-09-07 (the reason this rule exists).** Rule 7's fix landed at the Gorgias tee
*only*. A sweep hours later — while the Kurt-approved rule-7b backfill was queued — found a SECOND
writer with no equivalent: `GelPackCalculator/shipping_invoice_db.py` `store_feedback` /
`_feedback_value`, which wrote `entry.get("date_reported", entry.get("date",""))` through verbatim,
stripped only. It is reachable from **Kori's feedback save** (`kori/gel_pack_webview.py`) and from
`import_feedback_csv.py`. **One Kori save or one CSV import after the backfill would have re-mixed
the column and brought the lexical-`MAX` mask straight back** — the same defect, on repaired data.
A third, vendored copy (`ShipRouting/server/shipping_invoice_db.py`, no caller today, live-shaped and
one import from being one) had the same gap.

This is the workspace's *"an authority governs EVERY consumer — enumerate the write-paths, not the
one in front of you"* class. **Fixing one of two writers is what produced the original bug**: the
2026-06-18 entry hardened the data and left the writer; 2026-09-07 hardened one writer and left two.

Rules:
- **The rule lives in ONE place:** `appyhour_lib.feedback_completeness.normalize_report_date`. It
  sits beside `parse_report_date` (the read side) so the write and read halves of the same format
  contract cannot drift apart.
- **Every writer CALLS it. Never re-implement, never copy it into a vendored module.** Two divergent
  copies of a date rule is this exact bug one level up. `_normalize_date_reported` in
  `gorgias_sheets_sync.py` is now a thin alias, kept only because it is the documented symbol.
- **A new `feedback` writer that does not call it is a defect**, whatever it looks like in review.
  Before landing one, run `rg --no-ignore --hidden -n "date_reported"` over `AppyHour`, `ShipRouting`
  and `_outputs/scripts` — the plain repo grep is near-blind here (root `.gitignore` is `*`).
- **Semantics are identical at every call site** and are not a writer's choice: `%m/%d/%Y` and ISO
  (date or timestamp) in, ISO out; blank/None → None; **anything else returned VERBATIM** — never
  dropped, never year-inferred. Month-first only (rule 7bb has the proof: 389 rows with day >12,
  zero with a first component >12); do not add day-first handling.
- **Pinned by `tests/test_feedback_completeness.py`**, including
  `test_both_writers_agree_on_every_input` — the equality test is what stops a future divergence,
  so if it fails, DELETE the divergent implementation rather than patching both.

### 7b. The 692-row legacy `%m/%d/%Y` band is NOT repaired. A backfill is Kurt's decision.
Rows synced 2026-06-19..2026-09-07 remain US-format on purpose. Eleven weeks of warm data touches
published reports and the reship/warm-cohort numbers, so normalising them is a business decision, not
a repair a monitor or an agent makes. `ISO_CUTOVER` in `appyhour_lib/feedback_completeness.py` marks
the boundary; only rows synced **on or after** it are format-graded. Do not advance that date to
silence a flag — a flag there means the writer stopped canonicalising.

### 7bb. The backfill tool is `scripts/incident-fixes/normalize_feedback_dates.py`. Do not hand-roll one.
When Kurt does give the go for rule 7b's backfill, that script is the canonical path — it already
handles every yeared and year-less shape, dry-runs by default, snapshots to
`feedback_backup_<UTCstamp>`, and (2026-09-07) VERIFIES zero non-ISO rows remain **inside the same
transaction** and rolls back if not. A backfill that reports success while skipping a shape
recreates the mixed column and the lexical-`MAX` mask, so the verify is not optional decoration.
- 🔴 **Connection discipline.** Until 2026-09-07 this script opened the DB with raw
  `sqlite3.connect()`, bypassing BOTH the single-writer advisory lock and the canonical-path guard —
  a surplus write handle racing the MCP servers' checkpointer is the direct cause of all three
  `shipping.db` WAL corruptions. It now reads through `connect_ro()` (so a dry-run is structurally
  incapable of taking a write lock) and writes through `connect()`. `DBWriterBusy` means a sync is
  mid-flight: wait and re-run. **Never** set `AH_WRITE_LOCK_DISABLE` to get past it.
- The transform needs no year inference — the year is present in `%m/%d/%Y` — and the band is
  provably month-first: of the 692 rows, 389 have a day component >12 (impossible under `%d/%m/%Y`),
  none have a first component >12, and month-first puts every row 0-14 days before its `synced_at`
  while day-first would date 92 of them in the future.

### 7c. Recency-of-EVENT is a THIRD independent assert. Do not collapse it into the other two.
This table has now failed in three ways that no single check can see:
`synced_at` recency (did the task run) · field completeness (rule 8 — it ran and wrote blanks) ·
**event recency (it ran, wrote good rows, and the newest EVENT still went nowhere)**.
`check_feedback_event_freshness()` grades the newest **parsed** `date_reported` at a 12-day limit
(weekly writer + `StartWhenAvailable` catch-up; HEARTBEAT_RULES rule 4 — a weekly writer gets ~10
days, never 7). It **fails closed** on zero rows, zero parseable rows, or a read error: absence of a
row is not absence of an event, and a zero is a claim.

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
10. **Every writer of `date_reported` canonicalises through
    `appyhour_lib.feedback_completeness.normalize_report_date` — there is exactly one
    implementation of that rule** (rule 7d). A writer with its own date handling is a defect even
    if its output happens to match today.

## Files

| Path | Role |
|------|------|
| `GelPackCalculator/shipping_invoice_db.py` | `store_feedback` (append-only merge) + `delete_feedback_rows` (the only deleter). **`date_reported` WRITER #2** (rule 7d) |
| `GelPackCalculator/kori/gel_pack_webview.py` | Kori sheet sync — the button; supplies `gorgias_link` so rows land on the proven key |
| `GelPackCalculator/tests/test_feedback_append_only.py` | production-shape append-only + idempotency tests |
| `ShipRouting/server/shipping_invoice_db.py` | vendored copy for the DO deploy — keep `store_feedback` identical. **`date_reported` WRITER #3** (no caller today; fixed anyway, rule 7d) |
| `AppyHourMCP/tools/gorgias_sheets_sync.py` | sync + extraction + SQLite tee. **`date_reported` WRITER #1** — `_normalize_date_reported` is now a thin alias |
| `appyhour_lib/feedback_completeness.py` → `normalize_report_date` | 🔴 **THE ONE `date_reported` write-side rule** — every writer calls it (rule 7d) |
| `AppyHourMCP/run_gorgias_update.py` | CLI entry (`gorgias_update.bat`, scheduled task) |
| `appyhour_lib/feedback_completeness.py` | field-level completeness assert + threshold derivation |
| `tests/test_feedback_completeness.py` | production-shape tests (real wk0608 / wk0727 / wk0817) |
| `tests/test_gorgias_order_extraction.py` | extraction + hydration + dry_run guards |
| `_outputs/scripts/freshness_sweep.py` | wires the assert into the weekly sweep |
| `_outputs/scripts/backfill_feedback_order_numbers.py` | gated, dry-by-default historical repair |
