# DO_READ_CONTRACT.md — the reporting side's read contract against DigitalOcean

🔴 **PRE-CHANGE GATE.** This is the **consumer-side SSOT** for how the reporting / tracking surface
reads shipment data out of DigitalOcean. Read it before changing any reporting consumer's data
source, before adding a freshness assert, and before retiring a local ingest. Change the rule HERE
first, in the same commit as the code.

**Status: SPEC — authored 2026-08-27, not implemented.** Routing Coordinator owns the cloud-side
implementation. This document is the contract the reporting side needs honored; it is written so
the cloud side can build against it without asking us questions. Nothing in it is a claim about
work already done.

**Scope boundary.** This doc does NOT own: which store is truth
(`ShipRouting/server/DATA_CANON_RULES.md`), how a status reaches us
(`ShipRouting/server/STATUS_INGEST_RULES.md`), the pivot sheet's own rules
(`RESHIP_REPORT_RULES.md`), or the exceptions sweep (`EXCEPTIONS_ALERT_RULES.md`). It points at
them and must never contradict them. It owns exactly one thing: **what a reporting consumer is
allowed to assume about cloud data, and what proves it.**

---

## 🧭 NORTH STAR — Kurt's acceptance criteria, verbatim (2026-08-27)

> **1. No duplicate work / API calls.**
> **2. Most up-to-date information.**
> **3. DO makes sense just to have it because it's already kind of there.**

Every section below answers to these three. How to read them, because two of them are routinely
over-read:

- **(3) is a SCOPE INSTRUCTION, not an architecture commission.** Kurt is not asking for a cloud
  re-architecture. He is saying the data already lives in DO, so use it. The bias of this entire
  spec is therefore toward **the smallest change that reads what is already there** — no new cloud
  services, no new tables, no schema redesign, nothing the cloud side must stand up before we can
  read a row. Where a shape requires Routing Coordinator to build infrastructure first, that is a
  cost counted against it, not a neutral.
- **(1) and (2) CONFLICT, and this doc says exactly where.** Fewer API calls means one ingest path;
  most-up-to-date means the freshest available. They agree wherever the cloud is fresher than local
  (`delivery_status`: cloud writer runs **hourly** vs a local path that only moves when Kurt's
  machine does). They **disagree wherever the cloud copy is STALER than the local one** — retiring a
  local path in favor of a lagging cloud writer satisfies (1) and violates (2).
  🔴 **Resolution rule, standing: (2) WINS. A table whose cloud copy is behind its local copy stays
  OUT OF SCOPE until its cloud writer is fixed.** Flag it; never quietly prefer the tidier answer.

---

# 1. Table-by-table verdict — the answer to (1) and (2)

🔴 **Read this section first.** A column list without a verdict is how a migration "completes" onto
a dead writer. Every table we consume gets an explicit verdict on **both** criteria before anyone
looks at its schema.

| table | cloud writer + cadence | (1) no duplicate calls | (2) most up-to-date | **verdict** |
|---|---|---|---|---|
| `delivery_status` | `sync_delivery_status.run()`, ingest-worker timer `delivery_status_sync`, **1h**, `DELIVERY_SYNC=1` **LIVE** | ✅ cloud is the single PP/Shopify-events caller — but see §5 blocker B3, the webhook is a *second* live path with no consumer | ✅ **cloud is a STRICT SUPERSET, measured.** Local-only rows = **0** in all three of `_SHIP_2026-08-10/-17/-24`; cloud-only = +32/+16/+178; "in NEITHER" = 0. Cloud `synced_at` max **2026-08-27 17:59 ET** vs local **2026-08-25 21:41**. Every one of 2,213 status disagreements runs local-behind; not one runs the other way | **IN SCOPE — migrate first.** 🔴 But see B5: the table is HALF-FLIPPED — cloud owns it and **nothing writes local**. Retiring the local pull without a read path is a silent freeze, not a cutover |
| `shopify_orders` | `sync_shopify_orders.run()`, timer `shopify_orders_sync`, **4h**; cloud is already the declared PRIMARY (DATA_CANON matrix) | ✅ already single-writer cloud-owned; local sqlite is a *replica*, not a source | ✅ cloud 60,084 vs local 60,072, both current to today | **IN SCOPE — already cloud-primary; we just stop reading the weekly replica** |
| `fulfillments` | 🔴 **NONE.** No `fulfillments` timer exists in `server/ingest_worker.py` `REGISTRY`. Owner is still the Windows/Kori writer per the DATA_CANON matrix | ✅ would satisfy (1) | ❌ **VIOLATES (2), measured.** Cloud `MAX(updated_at)` **2026-08-12 05:12** vs local **2026-08-27 16:17**. 🔴 **LOCAL is the strict superset: 4,911 local-only orders, ZERO cloud-only.** Cloud is missing ship weeks `2026-08-17` and `2026-08-24` **entirely** | 🔴 **OUT OF SCOPE.** Keep reading local. This is exactly the (1)-vs-(2) conflict the north star names, and it is the clearest case in the system: migrating here would *lose two ship weeks* |
| `shipments` | `sync_invoices.run()` + `imap_invoices.fetch()`, timer `invoice_ingest`, **12h**, `INVOICE_SYNC=1` + `INVOICE_IMAP=1` **LIVE** | ✅ cloud does its own IMAP acquisition — but ⚠️ this DUPLICATES the local `sync_all_carriers.py` IMAP pull against the same mailbox (B4), and 🔴 three OnTrac invoices are double-ingested with **$21,319 of duplicate cost** (B2b) | ⚠️ **NOT STALE — CORRUPT.** Cloud max ISO `ship_date` is **2026-08-19**, same as local. The "stale to March" reading was a **two-format lexical-sort artifact** (B2), now measured and confirmed: 8,940 rows in `YYYYMMDD`, 109,377 in `YYYY-MM-DD` | 🔴 **BLOCKED on B2 + B2b.** Freshness was never the problem; the column cannot be range-filtered or `MAX()`-ed until it is one format. Do not migrate a cost number onto it |
| `weather_history` | `sync_weather.run()`, timer `weather_fetch`, **24h**; cloud-owned PRIMARY | ✅ | ✅ | **IN SCOPE** (low priority — reporting barely reads it; ice sizing does) |
| `feedback` | 🔴 **NONE.** Windows Gorgias tee, no cloud timer | ✅ would satisfy (1) | ❌ no cloud writer at all | 🔴 **OUT OF SCOPE** |
| `pp_webhook_events` | flow-api route `POST /webhooks/parcelpanel`, raw landing, append-only | n/a (push, costs zero PP quota) | n/a — raw layer, not derived state | 🔴 **WE DO NOT READ IT.** It is the ingest's raw evidence layer, not a consumer surface. Reporting reads `delivery_status`. See §4 non-goal N4 |
| `pp_webhook_missed` / `pp_reconcile_runs` | 🔴 `pp_reconcile()` is **defined but NOT in `REGISTRY`** — no timer, and `PP_RECONCILE` is absent from the live app env | — | — | 🔴 **NOT A DATA SOURCE.** Empty here means *nobody is auditing*, not *webhooks are healthy* |

**The one-line summary for Routing Coordinator:** we want `delivery_status` and `shopify_orders`
now; `weather_history` whenever; `shipments` after B2; `fulfillments` and `feedback` **not until
they have a cloud writer** — and we would rather keep a local read than take a dead cloud one.

## 1.1 🔴 The number that makes criterion (2) concrete

For the **live cohort** `_SHIP_2026-08-24`, measured 2026-08-27:

| | delivered | of roster | rate |
|---|---:|---:|---:|
| **LOCAL** (what every report reads today) | 160 | 2,409 | **6.6%** |
| **CLOUD** (what DO already has) | 2,301 | 2,577 | **89.3%** |

**Any on-time or late-rate number pulled right now for this week's cohort is wrong by ~83
percentage points.** DO already holds the right answer and nothing reads it. Settled cohorts
(08-10, 08-17) agree to within a handful of rows — **the divergence is entirely in the
still-moving week, which is precisely the week anyone asks about.**

🔴 That is the whole case for this migration, and it is worth doing on its own merits **this week,
independent of any retire.**

## 1.2 🔴 What the migration does NOT fix — say it before someone assumes otherwise

**Ship routing's ParcelPanel input is one month stale, in BOTH stores, and moving to DO changes
nothing about it.**

The engine's PP-fed historical-TNT correction (`build.py` → `lib/hist_risk.py`) is gated on
`delivery_status.origin_hub IS NOT NULL`. That column is populated only by
`ShipRouting/phase0_origin_backfill.py`, which is **fenced off by default**.

| measurement | LOCAL | CLOUD |
|---|---|---|
| rows with `origin_hub NOT NULL` | 79,266 | **79,266** |
| `MAX(pickup_date)` among them | 2026-07-27 | **2026-07-27** |
| `hist_risk`-eligible rows after the full join | 53,969 | **53,969** |

Byte-identical. `origin_hub` is a **frozen historical backfill, not a live feed**, and 8,766–9,608
rows since 2026-08-01 carry no `origin_hub` at all, so they are invisible to `hist_risk`.

🔴 **The freshness lever for routing is the `origin_hub` backfill and its missing scheduled owner —
not this migration.** Do not let "we moved to DO" be recorded as having fixed routing freshness. It
did not. (Reports are the half DO genuinely fixes — §1.1.)

---

# 2. The read contract

## 2.0 🔴 Gotchas first — the four ways this join has already produced wrong numbers

1. **`#132940` vs `132940` join-zeroes SILENTLY.** `shopify_orders.order_name` is `'#'`-PREFIXED;
   `delivery_status.order_number` and `fulfillments.order_number` are BARE. A join that forgets the
   strip returns 0 rows and looks like a legitimate finding. It has produced confident zeros three
   times in this operation. **A zero is a claim — prove a known-present key survives the join before
   reporting it.**
2. **NEVER key on tracking.** FedEx REUSES tracking numbers across time
   (`382947925124` resolved to a May shipment). The join key is **order_number**, always.
3. **These tables are not one-row-per-order.** `delivery_status` is one shipment **LEG** (159 orders
   carry several rows); `fulfillments` is one **FULFILLMENT** (19 orders have two); `shipments` is
   one **INVOICE LINE**. Aggregate to one row per order *before* any per-order statistic. Reading
   `delivery_status` as one-row-per-order is the grain bug that inflated the wk0803 scorecard
   41-vs-40 and its dollars by 15%.
4. **zip5 is TEXT, and leading zeros are load-bearing.** `shopify_orders.ship_zip` is TEXT holding
   **either** `zip5` **or** `zip5-zip4` (measured today: `37122-4278`, `15367-1055`, `10025-3900`).
   Any consumer keying on zip5 must slice `[:5]`, never cast to int. `02879` read as a number is
   `2879` and matches nothing.

## 2.1 Join key — one format, stated once

> **The canonical join key across every table in this contract is `order_number`: BARE DIGITS, NO
> `'#'`, as TEXT.** Regex `[0-9]+`.

- `delivery_status.order_number` — already bare. ✅ authoritative form.
- `fulfillments.order_number` — already bare. ✅
- `shopify_orders.order_name` — `'#'`-prefixed. **Consumers strip it:**
  `REPLACE(order_name, '#', '')`. We are NOT asking the cloud side to change this column; the
  prefix is Shopify's and the DATA_CANON declaration pins it.
- `feedback.order_number` — "may carry `'#'`; strip before joining."
- `shipments` has **no order_number at all** — it keys on `(invoice_id, tracking)`. Order attribution
  goes through `tracking_order_link` / `fulfillments`, never a direct join.

🔴 **Known blind spot, do not "fix" it:** 19 `order_name` values carry a LETTER SUFFIX (`#164878A`
— Shopify exchange/split orders). ZERO of them match through the standard strip, so they are
invisible to every per-order delivery join. Stripping the letter would COLLIDE with the unsuffixed
order of the same number. This is a declared, un-triaged blind spot, not a bug to patch in a report.

🔴 **This is not a theoretical rule — the exact bug is LIVE in the engine right now.**
`ShipRouting/lib/engine.py:338-345`, inside `derive_failed_carriers` (the `RESHIP_RECOVERY`
fallback), joins:

```sql
JOIN shopify_orders o ON o.order_name = f.order_number
```

`shopify_orders.order_name` is `'#172607'`; `fulfillments.order_number` is `'172607'` (zero rows
`#`-prefixed on either side). **The join returns 0 rows, always.** With `'#'||f.order_number` it
returns **57,427**. Consequence: every reship lacking an explicit `_RESHIP_FAILED_<carrier>` tag
silently falls through to FedEx 2Day **AIR** instead of a proven cheaper ground lane.

Found by `ParityAudit` 2026-08-27. **Pre-existing, unrelated to this migration, and not ours to fix
here** — flagged because it is the canonical instance of the rule above, and because a migration
must not be recorded as having introduced or fixed it. Needs its own fix plus a replay-diff.

🔴 **Two more grain traps that silently drop rows on an exact-match tracking join:**
- **Comma-joined multi-package rows.** Both stores collapse a multi-package order into ONE row with
  a comma-joined tracking value (`'1LSDBVC00171ABW,1LSDBVC00171A8J'`) — cloud 361, local 348. Such a
  value can never equal a `fulfillments.tracking_number`, so an exact-match tracking join drops them
  on both sides. Another reason the key is **order_number**.
- **Cloud carries 2,135 NULL-tracking rows; local carries 0.** All are `info_received` (1,813) or
  `pending` (322) — orders PP knows about before a tracking number exists. This is genuine *extra*
  early-visibility coverage, not duplication (0 of the 2,135 also have a real tracking row), but it
  **inflates a naive `COUNT(*)` on the cloud table by 2,135.** Count orders, not rows.

## 2.2 Columns we consume, per table

Column names below are the **existing** ones in both stores. 🔴 **We are asking for no new columns
and no renames** — criterion (3).

### `delivery_status` — the primary ask
| column | type | notes |
|---|---|---|
| `order_number` | TEXT | **the join key.** bare digits |
| `tracking_number` | TEXT | identity within an order's legs; NEVER a join key |
| `carrier` | TEXT | 🔴 OnTrac and LaserShip are ONE carrier — normalize via `canon.normalize_carrier`, never string-equality |
| `status` | TEXT | measured domain: `delivered`, `in_transit`, `info_received`, `out_for_delivery`, `aged_out`, `exception`, `pending` |
| `delivery_date` | TEXT `YYYY-MM-DD` | date-only, no tz |
| `pickup_date` | TEXT `YYYY-MM-DD` | date-only, no tz. TNT basis |
| `transit_days` | INTEGER | 🔴 never the carrier API's `transit_time`; final-mile pickup→delivery only |
| `fulfilled_at` | TEXT ISO-8601 **with offset** | e.g. `2026-07-27T05:21:05-04:00` |
| `last_event` | TEXT | 🔴 **provenance, do_not_normalize.** `shopify_events%` marks a one-shot BACKFILL. Freshness must measure ORGANIC rows only — a backfill's `synced_at` once masked a dead ingest from the sweep |
| `synced_at` | TEXT | see §2.3 — **UTC**, and this is the freshness column |
| `service`, `origin_hub` | TEXT | sparsely populated (both NULL on the rows sampled today) |

### `shopify_orders`
`id`, `order_name`, `ship_tag`, `fulfillment_status`, `financial_status`, `cancelled_at`,
`created_at`, `customer_email`, `ship_state`, `ship_zip`, `ship_city`, `total_price`,
`subtotal_price`, `line_items_count`, `tags_csv`, `raw_routing_tags` — i.e. exactly the 16 columns
`pull_cloud_replicas.py` already pulls. No additions requested.
🔴 Grain: one order **as of last sync**, REPLACE-keyed on Shopify `id` — an order carrying TWO
`_SHIP_` tags collapses to its NEWEST tag.

### `shipments` (conditional on B2)
`invoice_id`, `tracking`, `carrier`, `service`, `hub`, `state`, `zip_code`, `city`, `zone`, `cost`,
`weight`, `billed_weight`, `ship_date`, `delivery_date`, `source_file`, `acct`.
🔴 `acct` must be the **canonical** key — `-113`/`-911` are LEGACY ALIASES of `203738113`/`206137911`
and splitting on them silently forks per-account cost and hub attribution. Authority:
`AppyHour/appyhour_lib/acct_canon.py`.

### `fulfillments` — **contract stated, but OUT OF SCOPE this migration** (see §1)
`order_number`, `tracking_number`, `tracking_company`, `fulfilled_at`, `ship_date`, `ship_week`,
`dest_city`, `dest_state`, `dest_zip`, `updated_at`.

### `pp_webhook_events` — **we do not consume it.** See §4 non-goal N4.

## 2.3 🔴 Timezone contract — three conventions in one join, measured today

This is the section most likely to be skipped and most likely to produce a wrong number. All three
values below were measured on 2026-08-27 at 18:09 America/New_York.

| column | observed | actual timezone | verified? |
|---|---|---|---|
| `delivery_status.synced_at` | `2026-08-25 21:41:13` (naive) | **UTC** — sqlite column default is `datetime('now')`, which is UTC. Proven: `SELECT datetime('now')` returned `22:09:13` while local wall clock was `18:09:13` | ✅ **verified** |
| `shopify_orders.created_at` | `2026-08-27T15:33:31Z` | **UTC**, explicit `Z` | ✅ verified |
| `*.fulfilled_at` | `2026-07-27T05:21:05-04:00` | **tz-aware ET**, explicit offset (`-04:00` EDT / `-05:00` EST — both observed) | ✅ verified |
| `fulfillments.updated_at` | `2026-08-27 16:17:42` (naive) | ⚠️ **UNVERIFIED.** Schema default is `datetime('now')` (UTC) but the observed value is consistent with a naive LOCAL write by the Python writer. I could not distinguish 16:17 ET from 16:17 UTC from the data alone | ❌ **must be pinned by the cloud side** |
| `delivery_date`, `pickup_date`, `ship_date`, `delivery_status` dates | `2026-08-25` | **date-only, no timezone.** Already a local business date | ✅ |

### 🔴 `synced_at` holds TWO formats too — and it is the freshness column

Measured by `ParityAudit` 2026-08-27:

| store | space-naive (`2026-08-25 21:41:13`) | ISO-with-offset (`2026-08-27T17:59:43-04:00`) |
|---|---:|---:|
| LOCAL | 118,895 | 14 |
| **CLOUD** | 113,762 | **7,414** |

🔴 **Naive and `-04:00`-suffixed values do not sort together correctly, and they do not mean the
same thing** — the naive ones are UTC (§2.3 above), the suffixed ones are ET. So a `MAX(synced_at)`
or a `substr()` on this column mixes two timezones *and* two lexical orderings. **This is the same
class as B2, in the column every freshness assert in §3 depends on.**

**Requirement: `synced_at` is ONE format.** We ask for **ISO-8601 with an explicit offset**
(`2026-08-27T17:59:43-04:00`) rather than naive-UTC, because an explicit offset cannot be
misread — and the naive-UTC form has *already* been misread (see item 3 below). Normalize on write,
backfill, and add the domain assert to `DATA_CANON_RULES.md`. As with B2: **the fix is in the
ingest, never a reader-side `if 'T' in x`.**

**The contract we require:**

1. 🔴 **Every naive timestamp column is declared as UTC**, and the declaration lives in
   `DATA_CANON_RULES.md` beside the table. `fulfillments.updated_at` is the one open item — pin it,
   don't guess it.
2. 🔴 **All date math converts to `America/New_York` BEFORE `.date()`.** A UTC timestamp truncated
   to a date is wrong for every event between 20:00 and 24:00 ET, which is most of an evening's
   deliveries. This has already burned us: a UTC `.date()` bug reported a rate of 6.5% against a
   true 2.8%.
3. 🔴 **A naive-vs-UTC comparison bug is live in our own tooling today.**
   `_outputs/scripts/freshness_sweep.py` compares `datetime.now()` (local ET) against
   `delivery_status.synced_at` (UTC). Ages therefore read **~4 hours YOUNGER than reality**, so a
   table 50h stale grades as 46h and passes a 48h gate. This is ours to fix, not the cloud side's —
   logged here so the migration does not inherit it. Fix = compare UTC-to-UTC, or convert on read.
4. **Report every timestamp to a human in ET.** Raw UTC in code only.

## 2.4 Types — what the mirror must NOT silently change

🔴 **The cloud is MySQL (typed); local is sqlite (typeless). A type that survives the round trip in
one direction can silently break a string comparison in the other.**

- `zip_code` / `ship_zip` — **VARCHAR / TEXT, never numeric.** Cloud `shipments.zip_code` is
  `VARCHAR(16)`; the loader has a dedicated `_zip5()` guard because *"Excel stores Recipient Zip Code
  as a NUMBER, so 02879 arrives as 2879."*
- `ship_date` / `delivery_date` — cloud schema is **`VARCHAR(32)`**, i.e. free-text. The type is not
  the guard; the **value format** is, and it is **currently not enforced and currently violated** —
  8,940 rows hold `YYYYMMDD` against 109,377 holding `YYYY-MM-DD`. See **B2**.
- 🔴 **`'20260817' > '2026-08-19'`** in a string comparison (ASCII `'0'`=48 > `'-'`=45). **That
  single fact turns `MAX(ship_date)` into a lie** and breaks every `BETWEEN` and seasonal window.
- **The fix is in the INGEST, one format on write (B2) — NOT a reader-side `len()` check.** A reader
  workaround spreads the defect to every consumer and the next one written won't have it. Consumers
  may *assert* `^\d{4}-\d{2}-\d{2}$` and fail loudly; they may not *accommodate* the other format.

---

# 3. Freshness — the guarantees we require, per consumer

🔴 **The failure this section prevents:** `shopify_orders` sat **9 days stale behind a 14-day gate**
and nothing said a word; before that it sat dead since 6/09 for a month, and 41 reships defaulted to
avoidable AIR. A freshness contract with no assert is not a contract. **Silence must fail loudly.**

## 3.1 The rule

**Every row in the table below names (a) a max acceptable age, (b) the assert that proves it, and
(c) where that assert LIVES.** A consumer with no assert may not migrate to a cloud read.

🔴 **An assert gated on the same flag as the writer it guards is not a guard.** This is not
hypothetical: `delivery_status_freshness()` no-ops while `DELIVERY_SYNC` is off, which is exactly how
that table went dark for six days while every dashboard read healthy. `invoice_freshness()` and
`pp_derive_freshness()` deliberately assert **even while flag-off**, and that is the correct shape.
**OFF must be distinguishable from HEALTHY.**

## 3.2 Per-consumer freshness table

| # | consumer | reads | max acceptable age | the assert that proves it | assert lives in |
|---|---|---|---|---|---|
| C1 | **ShipRouting live routing decision** — `build.py`, `lib/hist_risk.py`, `lib/features.py`, `lib/movement.py` | `shipments`, `delivery_status`, `fulfillments`, `shopify_orders` | 🔴 **cohort-exact, not age.** A global row floor cannot prove the cohort exists | `histdb.CohortRequirement(table, column, value, min_rows)` — already implemented, already enforced at materialize time | **the READER** (`lib/histdb.py`) — fail-closed, refuses a thinned mirror |
| C2 | **Weekly shipping run / `weekly_flow.py`** (feeds a sheet RMFG acts on) | `shopify_orders`, `delivery_status` | **4h** for `shopify_orders` (= the cloud timer interval); **3h organic** for `delivery_status` | cloud: `shopify_orders_freshness()` (24h) + `delivery_status_freshness()` (<3h organic). Reader-side: cohort requirement as C1 | cloud worker **and** the reader — both, they fail independently |
| C3 | **Current-ship-week late-rate readers** (reship report inputs, cohort scorecard) | `delivery_status` ⨝ `shopify_orders` | **48h** on the CURRENT ship-week window. Existing constant `DELIVERY_COHORT_WINDOW_MAX_H = 48`; the data misled at 19h, 48h is the reader-trust ceiling | current-week joined-window query, ORGANIC rows only (`last_event NOT LIKE 'shopify_events%'`) — 🔴 the whole-table 14d row CANNOT catch a dead current-week ingest hiding behind a fresh backfill of old cohorts | `freshness_sweep.py` (exists today) — 🔴 **and must be re-pointed at the cloud when C3 migrates**, plus the UTC bug in §2.3(3) fixed, or the 48h gate keeps grading 4h light |
| C4 | **Carrier-mix pivot** (`carrier_mix_pivot.py`) | `shipments`, `delivery_status` | **hours to days** — it is a ship-week-columns pivot; a few hours never changes a column. Counts and costs freeze on **two independent clocks** | row-count + `MAX(synced_at)` printed in the report header, so a reader sees the age beside the number | **the READER.** ⚠️ It has **NO scheduled owner today** (RESHIP_REPORT_RULES D35) — that is a dead-cadence gap independent of this migration |
| C5 | **Ship-week postmortem** (`postmortem_runner.py`) | `delivery_status`, `fulfillments`, `shipments`, `feedback` | **cohort maturity, not age** — it only runs on matured cohorts | mature-cohort assertion (an immature cohort is skipped, not reported thin) | the reader |
| C6 | **Thermal / melt calibration** (`melt_efficiency_calibrator.py`, `safety_factor_sweep.py`) | `delivery_status`, `weather_history` | **7d** — it fits parameters over history; a day either way is noise | none today | 🔴 **gap — needs one before it migrates** |
| C7 | **Anything invoice-derived** (cost per box, wallet share, carrier cost tables) | `shipments`, `invoices` | 🔴 **CANNOT PROMISE HOURS — bounded by a HUMAN cadence.** See §4 non-goal N5. Proposed: **9 days**, reusing the existing cloud `invoice_freshness()` number (weekly invoices + slack) rather than inventing one | 🔴 **"no new invoice data in N days" must ALARM**, so a missed portal login surfaces loudly instead of silently aging a cost number. Same dead-cadence class as every writer fixed this month — just with a human in the loop | cloud `invoice_freshness()` (exists, asserts while flag-off ✅) **and** `freshness_sweep.py` `invoices` row (exists, 21d — 🔴 inconsistent with 9d; reconcile) |
| C8 | **`appyhour-shipping-data` skill** (NL→SQL, the mandatory path for shipping questions) | all of the above | inherits whatever it reads | 🔴 must SURFACE the age of every table it touched in its answer, never assert a number without one | the skill |

**Numbers in that table are cited, not invented.** 3h/24h/48h/9d/4h/1h/12h all come from existing
code (`ingest_worker.REGISTRY` intervals, the `*_freshness` docstrings, `freshness_sweep`
constants). The two proposals — C6's 7d and C7's 9d — are marked as proposals and need a nod.

## 3.3 Where the assert lives

- **The reader owns the assert for anything that changes a decision** (C1, C2, C3). A reader that
  trusts an upstream promise has no way to fail when the promise breaks.
- **`freshness_sweep.py` owns the standing weekly sweep** for everything else, and is the
  dead-man-switch for tables no reader ran against this week.
- 🔴 **Both, for the tables that matter.** They must fail independently. The `feedback` precedent is
  exact: the recency row stayed GREEN through the entire 2026-08-17 outage because the writer
  *degraded* rather than stopping — 34/55 rows landed with a blank `order_number`. Recency cannot
  see a degrading writer; a field-level completeness assert can. **Keep both.**

---

# 4. Access shape — what we actually want

## 4.1 🔴 RECOMMENDATION: `lib/histdb.resolve()`, the mirror path that already exists

**One line of reasoning:** `ShipRouting/lib/histdb.py` already materializes exactly the six tables
we consume out of cloud MySQL into an atomic local sqlite file and sets `APPYHOUR_DB_PATH` to it —
which is the env var `appyhour_lib.paths.db_path()` already honors — so **every existing reporting
query runs unchanged, and the cloud side has to build nothing.** That is criterion (3) answered
literally: it is already kind of there.

What it already does, verified by reading the module:

- `TABLES = ("shipments", "delivery_status", "fulfillments", "shopify_orders", "feedback",
  "weather_history")` — our exact consumption set.
- Per-table **row floors** (`delivery_status` 75 000, `shipments` 60 000, …) and it **refuses a
  thinned mirror** rather than serving one. Fail-closed, which is the right direction: *absent is
  safe; shrunken is the dangerous one.*
- **Atomic publish** (`os.replace`) with local row-count verification; a failure leaves the last
  good mirror untouched.
- **Declared dependencies** — a caller requests only the tables it reads, plus optional
  `CohortRequirement` values that prove an exact cohort is present instead of trusting a global row
  floor.
- **TTL** `HISTORY_DB_TTL_MIN`, default **60 minutes**.
- Gated on `ROUTING_HISTORY_DB=1` + `DATABASE_URL`; unset = every existing local path, byte-identical.

**The consumer-side change is therefore:** call `histdb.resolve(tables=[...], requirements=[...])`
at start-up, and keep the SQL. Nothing else.

**Independently confirmed by `ParityAudit` (2026-08-27):** `ShipRouting/lib/dbpath.py:31-34`
already checks `histdb.resolve()` first; the routing switch is **one env var**
(`ROUTING_HISTORY_DB=1`), replay-proven **0-diff** at commit `766c76a`. And because the mirror IS
sqlite, **every `sqlite3.connect(mode=ro)` consumer stays byte-identical** — a once-per-TTL bulk
pull, **not** per-query network. Their cheapest-global-mitigation finding matches this
recommendation exactly: pointing `APPYHOUR_DB_PATH` at the mirror gives ~21 skill query files,
`wednesday_ops_run.py`, `freshness_sweep.py` and the rest cloud-sourced data with **zero code
changes**.

### 🔴 Required mitigation before this shape ships: it hard-fails offline

`histdb.materialize()` **raises** on any MySQL failure and `dbpath.py:33` has **no fall-through to
the local file** when the flag is on. Consequences, both of which this contract requires fixed
before step 4 of §6:

1. 🔴 **An unreachable DO MySQL kills the Friday routing build outright.** Today the local pulls are
   the informal backup that makes this tolerable; retiring them removes it.
2. 🔴 **`freshness_sweep.py` would go BLIND exactly when the network it monitors is down** — and
   would report **"stale"** rather than **"unreachable."** That is the wrong word in the worst
   moment: it sends someone to debug an ingest that is fine. **Unreachable must be its own state,
   never folded into stale.**

**Required:** a fall-through to the last good mirror (it is already atomic and content-verified) or
to the local file, with a **LOUD** degradation notice naming which store answered. Absent is safe;
silently-substituted is not.

### Consumers that need a real edit, not just the env var

- `carrier_mix_pivot.py` — hardcodes `connect_ro()` with no table declaration; needs a real edit,
  and naively rewritten it becomes a full-table transfer per run.
- `postmortem_runner.py` — highest friction: hardcoded path literal at `:24`, bare
  `sqlite3.connect` at `:441`, no resolver, and it exits on a missing DB with no network path.
- ⚠️ Two skill query files are pinned to the **retired `%APPDATA%` path** and honor nothing:
  `air_serviceability_critic_v2.py:23`, `aov_air_cutoff.py:35`.

### Honest costs of this shape

- 🔴 **It IS a second copy.** §4.2 N2 says we don't want those. The distinction that makes this one
  acceptable: it is a **TTL-bounded, floor-guarded, atomically-replaced CACHE regenerated from the
  primary**, not a persistent store with its own write path that can silently diverge. Rot is
  bounded by `HISTORY_DB_TTL_MIN`. A `pull_cloud_replicas.py`-style copy into the canonical
  `shipping.db` is the opposite: persistent, weekly, and *is* the thing that rots.
- **Worst-case staleness is additive:** cloud writer interval + mirror TTL. For `delivery_status`
  that is 1h + 60min ≈ **2h**, before ParcelPanel's own lag. That fits C3's 48h and C2's 3h-organic
  contract only if the TTL stays at 60 min — 🔴 **do not raise `HISTORY_DB_TTL_MIN` without
  re-deriving §3.2.**
- **`_mirror_path()` defaults to `/tmp`**, which on Windows resolves to the current drive root.
  🔴 `FLOW_CACHE_DIR` must be set on any Windows consumer. Related known class: a Windows path
  literal degrading a Linux code path, and vice versa.
- **The mirror DROPS the `id` column** (`cols = [... if r[0] != "id"]`). Any consumer selecting `id`
  breaks. None of ours do, but state it.
- 🔴 **It needs `DATABASE_URL` in the REAL user context.** Claude/MSIX writes to `%APPDATA%` land in
  a sandbox shadow the scheduled task cannot see — **Kurt must create the credential file from a
  real terminal.** This is a prerequisite, not an implementation detail.
- **First materialize is a full table copy** — cost unmeasured on a Windows link. 🔴 Measure it
  before wiring an interactive consumer; do not quote a number nobody timed.

### Rejected alternatives, with why

| shape | why not |
|---|---|
| **Read endpoint on the DO app** | Requires Routing Coordinator to build routes, auth, pagination and serialization **before we can read one row**, and then every reporting query gets rewritten against a REST shape. Directly against criterion (3). ⚠️ **Flagging a conflict rather than resolving it silently:** I was briefed that Kurt already ratified "endpoint over JDBC," but I **could not find that decision record** in `_coordination/decisions.jsonl` or the docs I read, and the codebase's live, working, already-deployed path (`ROUTING_HISTORY_DB=1` + `histdb`) is direct MySQL. **If that ratification exists, it overrides this recommendation and Kurt's word wins** — but it should be produced rather than assumed, because the two answers point opposite ways. |
| **Direct `pymysql` from each local script** | Works, but every consumer's sqlite SQL gets rewritten as MySQL — `carrier_mix_pivot.py` alone is 48 KB of it — and each report pays a network round-trip per query where it currently reads a local file. Large diff, real latency cost, no benefit over the mirror. |
| **Extend `pull_cloud_replicas.py`** (the existing replica path) | 🔴 **Rejected for `delivery_status`.** It writes into the canonical `shipping.db` on a **weekly** Monday-sweep cadence with a 14-day tolerance. That is a persistent second copy that can rot, at a cadence that violates criterion (2) by two orders of magnitude for an hourly table. It remains correct for what it does today (`shopify_orders`, `weather_history` — tables whose consumers tolerate 14d/8d) and this spec does not retire it. |

## 4.2 What we will NOT do — explicit non-goals

**N1. No local ParcelPanel calls from the reporting side.** Not one. If a report needs a carrier
status it reads `delivery_status`; if the status isn't there the answer is "the ingest is behind,"
never a direct PP call. A consumer left with a *reason* to call PP directly is a north-star
violation, not a workaround.

**N2. No second copy of a table DO owns.** No new sqlite table, no new sheet tab, no CSV export that
becomes someone's source. The `histdb` mirror is a TTL-bounded cache and is the single exception,
scoped in §4.1.

**N3. 🔴 The Apps Script exceptions sweep is OUT OF SCOPE and will keep calling ParcelPanel
directly.** State it plainly or someone will "finish" the migration and leave it broken. The bound
Apps Script project **cannot reach either database** — not `shipping.db` (this is already why the
carrier-mix pivot is a Python script and not a sheet tab: the `.gs` project cannot reach the routing
tag token or the carrier-invoice cost) and not managed MySQL. It also runs under a 6-minute
execution ceiling. Its PP usage is governed by `EXCEPTIONS_ALERT_RULES.md` (P1–P9), which this doc
POINTS at and never restates. 🔴 Note for anyone about to "optimize" it: **there is no weekly PP
budget** — "2,500/week" was Kurt's average weekly ORDER count, never an API quota. The real limit is
**120 requests per minute per key**, and a 429 must never be dropped.

**N4. We do not read `pp_webhook_events`.** It is the ingest's raw evidence layer — its whole
purpose is that a dropped payload stays distinguishable from a box that never moved. A reporting
consumer reading it would become a second deriver of state that `delivery_status` already owns.

**N5. 🔴 PERMANENT CARVE-OUT — the FedEx-113 and UPS invoice DOWNLOAD stays manual, forever.**
Kurt, 2026-08-27: *"the only thing we can't do is retire the 113 fedex and ups manual invoices
because i have to log in."* This is a permanent human step, **not an unfinished migration item**,
and must never be listed as a gap someone can close.

🔴 **Get the boundary right, because this is where it will be got wrong:**

> **The manual half is the DOWNLOAD / ACQUISITION only** — Kurt authenticates at the carrier portal
> and the files land. **Everything downstream of the file existing — parsing, ingest, loading to DO,
> freshness asserts — is automatable and STAYS IN SCOPE.** The line is *"getting the file,"* not
> *"using the file."*

**Which carriers, which step — measured, not assumed:**

| carrier | acquisition path | evidence |
|---|---|---|
| OnTrac | **IMAP, automated** | `invoices` table: 82 rows, max `email_date` **2026-08-26** |
| FedEx | **IMAP, automated** — at least in part | `invoices`: 127 rows, max `email_date` **2026-08-25**; `download_fedex_imap.py` / `sync_all_carriers.py` exist and run |
| Veho | IMAP path exists; carrier is dead (Veho GONE, all hubs) | `invoices`: 20 rows, `email_date` blank |
| **UPS** | 🔴 **PORTAL LOGIN ONLY.** No IMAP path exists; there is no public UPS Billing REST API; portal scraping past the CAPTCHA is off-limits and `portal_pull.py` is PARKED (anti-bot dead-end) | `invoices`: 61 UPS rows, **`email_date` entirely blank** — consistent with a non-email acquisition path. Documented flow: notify → Kurt one-click CSV to Downloads → `ingest_downloads.py` |

⚠️ **Honest limit on my verification:** the data confirms **UPS** is portal-only. It does **not**
confirm which FedEx account requires a login — FedEx invoice emails are arriving, and account
`203738113` (the canonical form of `-113`) carries rows through `ship_date` **2026-08-18**. Kurt's
statement is authoritative and is recorded here as given; I could not independently derive the
FedEx-113 half from the data, and I am not going to guess at it. **Routing Coordinator should treat
"FedEx-113 + UPS acquisition is manual" as Kurt's constraint, and not re-derive it.**

**Consequence for §3.2 C7:** the freshness tolerance for anything invoice-derived is a **human
cadence**, and the assert must alarm on *"no new invoice data in N days"* so a missed login is loud.

---

# 5. Blockers we are handing back, not solving

🔴 Each is a **gate on the cloud side**. "Everything reads DO" is not true until each is resolved or
explicitly scoped out. Each names **what evidence would clear it** — so it can be closed with a
measurement, not an opinion.

## B1 — `fulfillments` has no cloud writer at all

**State.** Reported dead in DO since **2026-08-12**. My reading of `server/ingest_worker.py`
`REGISTRY` confirms the underlying cause: **there is no `fulfillments` timer.** Not flag-off — absent.
The DATA_CANON matrix still lists the owner as the Windows/Kori ingest scripts, so the cloud copy
only ever moved via a manual `etl_history --load`.

**Why it is a gate.** `fulfillments` is one of the six tables `histdb` materializes, with a floor of
75 000 rows. Local is at **118 904** rows and moving (`updated_at` max = today). If the cloud copy
is behind, every `histdb` consumer silently reads a stale `tracking_company` — which is the input to
`derive_failed_carriers`, i.e. reship routing.

**Evidence that clears it.** (a) a registered `fulfillments` timer in `REGISTRY` with a freshness
assert that fires while flag-off; (b) cloud row count and `MAX(updated_at)` within one timer
interval of local; (c) one week green.

**Until then:** 🔴 **`fulfillments` stays a LOCAL read** — criterion (2) beats criterion (1).

⚠️ Related pre-existing defect, **not** created by this migration and **not** ours to fix here:
the writer keys on Shopify REST's numeric `order_number`, so a split/exchange pair (`#164878A` and
`#164878`) both serialize to `164878` — one physical shipment per pair is never ingested and the
survivor is attributed to the base order. Declared in DATA_CANON as `known_defect`. **A migration
must not silently inherit this as "fixed."**

## B2 — 🔴 `shipments.ship_date` holds TWO date formats. **It has to be one.**

> **Kurt, 2026-08-27: *"tell routing coordinator about the two formats. it has to be one."***
> This is a **hard requirement on the cloud side**, not a finding for discussion.

### The failure it causes — read this before the numbers

🔴 **`MAX(ship_date)` compares the two formats lexically and returns a WRONG answer.**
`'20260817' > '2026-08-19'` because at position 4, ASCII `'0'` (48) > `'-'` (45). The 8-char rows
therefore beat every correctly-formatted row and the column's maximum is whatever the newest
*compact* row is.

**This is not hypothetical. It already produced a false report:** "shipments is stale to March,"
delivered to Kurt off exactly this comparison, and corrected afterwards. The mis-diagnosis is
expensive in both directions — "the writer is dead" triggers work that isn't needed, and "it's fine"
ships a cost number off a column nobody can range-filter.

**Everything that touches this column is silently wrong for the rows in the other format:** any
freshness gate, any `BETWEEN` range filter, any seasonal window, any ship-week bucketing.

🔴 **And the defect hides from the obvious check.** The split is **clean per carrier** — FedEx writes
the 8-char form, OnTrac/UPS/Veho write the 10-char ISO form. So a **per-carrier** freshness assert
reads perfectly healthy while a **cross-carrier** one lies. Anyone who "verified it per carrier" has
verified nothing.

### Measured on DO `shipments` (read-only, column is `varchar(32)`)

| length | rows | min | max |
|---|---:|---|---|
| 10 (`2026-08-19`) | 109,377 | `2025-05-22` | `2026-08-19` |
| **8 (`20260817`)** | **8,940** | `20251007` | `20260817` |
| NULL | 5,086 | — | — |
| empty string | 6 | — | — |

Corroborating local evidence: local sqlite has **effectively zero** compact values (10-char: 94 760,
NULL: 2 545, empty/blank: 6), so the compact form is **not** mirrored up from local — it originates
in the **cloud** ingest path (`/tmp/invoices/AHB_*.XLSX`). Consistent with `sync_invoices.py`, where
the FedEx path formats via `_dt()` → `%Y-%m-%d` but the UPS path at line 214 writes
`str(s.ship_date)` verbatim with no `_dt()` — i.e. the loader normalizes inconsistently **by
branch**, and `VARCHAR(32)` imposes no guard.

🔴 **"Stale to March" is formally retired as a finding.** Restricted to ISO rows, `MAX(ship_date)`
is **2026-08-19 on BOTH sides** — identical. The true cloud *lexical* max is `20260817`; the
original `20260302` reading did not reproduce and came from some narrower slice. **The table was
never stale. It is CORRUPT, which is worse, because staleness announces itself and this does not.**

### The other half of the damage: duplicate rows

| | LOCAL | CLOUD |
|---|---|---|
| rows / distinct `tracking` | 97,311 / 97,311 — **clean** | 123,409 / 97,614 → 🔴 **25,795 duplicate rows** |
| duplicate groups | 0 | many; top offenders appear **3×** |
| **August cost, `WHERE ship_date LIKE '2026-08%'`** | **$58,176.23** over 5,171 rows | **$48,999.08** over 4,790 rows |

🔴 **The two defects push a cost number in OPPOSITE directions, which is why neither is obvious.**
The standard ISO date filter **misses 381 August shipments and $9,177 — 15.8% of August spend** —
while the duplicate rows simultaneously **over-count** anything not filtered by date, and would
inflate lane observation counts by roughly 26%. Tracking *coverage* is fine (cloud-only 304,
local-only 1): it is the row grain and the date encoding that are broken.

⚠️ **Two duplicate figures are in circulation — they are different slices, do not merge them.**
The `$21,319` in B2b is the duplicate cost on the **three specific double-ingested OnTrac
invoices**; the `25,795 duplicate tracking rows` above is the **whole-table** duplicate count. The
three invoices are a subset. Whoever does the dedupe should reconcile both against the same query
rather than assuming either is the total.

### The requirement

1. 🔴 **ONE format: `YYYY-MM-DD` (ISO, 10-char).** It is already **92%** of rows, it sorts correctly
   as text, and it matches every other date column in this contract.
2. **Normalize on WRITE** — every branch of the cloud loader, not just the one that already does it.
3. **BACKFILL the 8,940 existing rows.**
4. 🔴 **The format check belongs in the INGEST, not in every reader.** A reader-side
   `if len(x) == 8` workaround is the **wrong fix**: it spreads the defect to every consumer, and
   the next consumer written won't have it. This contract will not accept a reader-side patch as
   resolution.
5. Add a `^\d{4}-\d{2}-\d{2}$` domain assert to the `shipments` declaration in
   `DATA_CANON_RULES.md`, so `db_invariants_check` catches the next occurrence instead of a human
   catching it in a wrong report.

**Evidence that clears B2:** the query below returns exactly one non-NULL length bucket, `10`.
```sql
SELECT LENGTH(ship_date) AS n, COUNT(*), MIN(ship_date), MAX(ship_date)
FROM shipments GROUP BY LENGTH(ship_date) ORDER BY n;
```

### B2b — the 5,086 NULL `ship_date` rows are a DOUBLE-INGEST, not missing data

Same table, same handover, found by the same probe:

- Only **2,545 distinct trackings** behind 5,086 rows. **Three OnTrac invoices (Jan 26 / Feb 23 /
  Mar 2) were each ingested TWICE** — once as `AHB_00233_….csv`, once as
  `/tmp/invoices/AHB_00233_….csv`. The path prefix defeated whatever idempotency key was in play.
- 🔴 **$21,319 of duplicate cost is sitting in the table** on those three invoices. **Dedupe is a
  cloud write — theirs, not ours.** Any cost report reading cloud `shipments` today is over-counting
  by that amount on those invoices.
- The NULL `ship_date` has a separate cause: the **early-2026 OnTrac CSV layout was never mapped**
  (`service` = `RD`, no hub, no `cohort_key`). Not a lost file — an unmapped shape.
- **4,582 of the 5,086 match `delivery_status` by tracking**, so `ship_date` / hub / cohort are
  **recoverable** — this is a backfill, not a re-acquisition.

🔴 **Why this is a priority and not tidiness** (Kurt's stated payoff — include it so the ranking is
legible): those rows are **~2,500 WINTER TNT observations with real invoice cost**, and they
currently compute **no transit at all**. Winter is the thin half of the seasonal baseline, and
winter never blends with summer — so this is not cleanup, **it is the sample.**

**Until B2 and B2b are resolved:** 🔴 **no cost, carrier-mix, or seasonal-baseline number moves to
cloud `shipments`.**

## B3 — a live webhook whose derive leg is not wired: (1) and (2) both lose

**State**, from the live App Platform spec read 2026-08-27 21:47 UTC:
- `PP_WEBHOOK_TOKEN` and `PARCELPANEL_API_KEY` are set on both the console and the worker; the
  webhook route is live and `pp_webhook_events` lands raw events.
- `DELIVERY_SYNC=1` — the **polling** sync runs hourly and is what actually fills `delivery_status`.
- 🔴 `pp_derive()` and `pp_reconcile()` are **defined in `ingest_worker.py` but absent from
  `REGISTRY`** — no timer. `PP_DERIVE` and `PP_RECONCILE` are also absent from the app env.

**Why it is a gate on *our* north star specifically.** We are paying for two paths to the same fact:
a free push feed that lands and stops, and a polling loop that spends PP requests. **That is
criterion (1) — "no duplicate work / API calls" — violated inside the cloud ingest itself**, and it
is invisible from the reporting side. Separately, with `PP_RECONCILE` unregistered, an empty
`pp_webhook_missed` means *nobody is auditing*, not *webhooks are healthy* — and the two are
indistinguishable from the table alone.

**Evidence that clears it.** (a) `pp_derive` registered with an interval and its
assert-while-flag-off freshness; (b) `sig_verified=1` rows present, which is what makes the
published HMAC scheme confirmed rather than assumed; (c) `pp_reconcile_runs` non-empty, proving the
divergence audit is alive; (d) a measured statement of which path is now primary and what the other
one costs.

**Note this is Routing Coordinator's call, not ours.** We do not read `pp_webhook_events` (N4). We
raise it because it is the clearest (1)-violation in the system and it sits on the path that feeds
our most-consumed table.

## B5 — 🔴 `delivery_status` is HALF-FLIPPED: cloud owns it, and NOTHING writes local

**This is the blocker that turns a cutover into a silent freeze, and it is the cheapest of them all
to prevent.** Measured by `ParityAudit` 2026-08-27:

- `ShipRouting/server/etl_history.py:577-586` — `cloud_owned = {"shopify_orders",
  "weather_history", "delivery_status"}`, commented *"`delivery_status` JOINED this set 2026-08-20,
  the moment `DELIVERY_SYNC=1` went live."* **Local→cloud publication is already excluded.**
- `_outputs/scripts/pull_cloud_replicas.py:51-57` — `PULLS` carries **only** `shopify_orders` and
  `weather_history`. 🔴 **There is no MySQL→sqlite leg for `delivery_status`.**
- Net: the local copy is kept alive **only** by the local ParcelPanel pulls. Retire them with no
  replacement read path and ~30 consumers freeze at 2026-08-25 **and keep returning answers** —
  the *stale-replica-is-worse-than-absent* class this system already names.

**Evidence that clears it:** either `delivery_status` added to `PULLS`, **or**
`ROUTING_HISTORY_DB=1` / `APPYHOUR_DB_PATH`→histdb mirror live and proven. 🟢 **Ours to fix, not
the cloud side's** — and worth doing this week regardless of any retire, because it is what puts
reports on the fresh data they are currently missing (§1.1).

🔴 **Doc/code contradiction to reconcile in the same pass:** `DATA_CANON_RULES.md:25` still says
`delivery_status` is *"⚠️ FLIP PENDING … sqlite primary"*; the code says the flip happened
2026-08-20. The constraints-doc gate requires the doc to move in the same commit as the code; it did
not. **Cloud side owns that reconciliation** — this contract's §1 verdicts are written against the
CODE, which is the current state.

## B6 — "retire the local PP pulls" ≠ "retire `sync_logon.py`". Be surgical.

🔴 Conflating these deletes writers nothing replaces.

| script | trigger | tables WRITTEN |
|---|---|---|
| `GelPackCalculator/daily_shipping_sync.py` → `run_pp_sync` | `appyhour_daily_{tue,wed,thu,fri}` 12:00 | **`delivery_status` only** |
| `GelPackCalculator/sync_logon.py` → `backfill_sync` | `appyhour_sync_daily_noon` 12:05 | `fulfillments`, `delivery_status` — **and via `sync_all_carriers`, `invoices` + `shipments`** (FedEx IMAP) |

`sync_logon` carries the **FedEx-IMAP invoice leg**, which writes two tables DATA_CANON still lists
as sqlite-primary **with no cloud writer**, and which is flagged as *the one writer with a genuine
local-file dependency* — it cannot move to App Platform as-is.

**The retire, when it happens, is scoped to exactly `run_pp_sync` and
`backfill_sync.sync_parcel_panel`. Nothing else.**

⚠️ **And it may already be partly moot — needs a deliberate diagnostic, not an assumption.**
`delivery_status.synced_at` shows **no rows at all for 8/26 or 8/27** locally (last landing day
2026-08-25, 11,123 rows), yet `appyhour_daily_wed` and `appyhour_daily_thu` both report as having
fired and the same wrapper's `run_cloud_replica_pull` stage stamped success at
`2026-08-27T16:00:18Z`. **The wrapper ran and the PP leg left no trace.** `ParityAudit` did not run
`run_pp_sync` to find out why — it writes to `shipping.db` and that audit was read-only. 🔴 **Do not
conclude the local pull is dead, and do not conclude it is alive.** It needs one deliberate
diagnostic run by someone allowed to write.

## B4 — two IMAP readers on one mailbox (a duplicate-work item we are surfacing, not solving)

`INVOICE_IMAP=1` on the cloud worker means DO now pulls carrier invoice attachments from
`kurt@elevatefoods.co` on a 12h timer. The local `sync_all_carriers.py` / `download_*_imap.py` path
still exists and is still the documented weekly tool. **Two independent readers of one inbox is a
criterion-(1) violation**; whether they collide (both marking messages read, or one starving the
other) is **unverified** — I did not test it. Evidence that clears it: a stated decision on which
side owns invoice acquisition, and the other retired or explicitly kept with its reason.

---

# 6. Migration order + rollback

🔴 **The retire of the local pulls is the LAST step, gated on parity being proven — never the
first.** "It returned 200" is not proof. The standing local-build doctrine applies verbatim: local
stays authoritative until the cloud is proven on a real cohort.

| step | what lands | what proves it | rollback |
|---|---|---|---|
| **0** | This document reviewed by Routing Coordinator; B1/B2/B3 acknowledged as gates | written agreement on the table verdicts in §1 | n/a — doc only |
| **1** | 🔴 **B5 first — give `delivery_status` a local read path** (`ROUTING_HISTORY_DB=1` / `APPYHOUR_DB_PATH`→histdb mirror, or add it to `pull_cloud_replicas.PULLS`). `DATABASE_URL` must exist in the **real** user context — a real terminal, not Claude/MSIX, whose `%APPDATA%` writes land in a shadow the scheduled task cannot see | `histdb.enabled()` true; one successful `materialize()` from a real terminal; `_SHIP_2026-08-24` delivered-rate reads ~89%, not ~7% | delete the file / unset the flag; every path falls back to local, byte-identical |
| **1b** | The offline mitigation in §4.1 — fall-through + a LOUD notice naming which store answered, and **"unreachable" as a distinct state from "stale"** in `freshness_sweep.py` | kill the network, run the sweep: it must say *unreachable*, not *stale*, and the Friday build must not die | revert one commit |
| **2** | **ONE read-only consumer** moved to `histdb.resolve()` — propose the **carrier-mix pivot** (C4): read-only, no live decision depends on it, and it already prints its own row counts | 🔴 **run it BOTH ways on the same ship weeks and diff the output.** Identical counts and costs, or it does not proceed. A row-count match is not a diff | unset `ROUTING_HISTORY_DB`; the script reads local sqlite unchanged |
| **3** | Fix the naive-vs-UTC comparison in `freshness_sweep.py` (§2.3 item 3) and re-point the C3 current-week assert at whichever store C3 reads | the 48h gate grades the same age a human computes by hand from `synced_at` | revert one commit |
| **4** | `delivery_status` + `shopify_orders` consumers migrated behind `ROUTING_HISTORY_DB` | one full ship-week run where cloud-read and local-read produce identical cohort numbers | the flag |
| **5** | **B2 + B2b resolved** → `shipments` consumers migrated | the `LENGTH(ship_date)` query returns exactly one non-NULL bucket (`10`); the three double-ingested OnTrac invoices deduped and the $21,319 gone; a cost report matching local to the cent | the flag |
| **5b** | *(separate from the migration, sequenced after B2b)* backfill the 4,582 recoverable NULL-`ship_date` rows from `delivery_status` by tracking | ~2,500 winter TNT observations computing a real transit where they compute none today | the rows are additive; revert = re-null them |
| **6** | 🔴 **LAST — retire the local PP pulls, scoped to `run_pp_sync` + `backfill_sync.sync_parcel_panel` ONLY** (B6) | 🔴 **parity proven over ≥2 consecutive ship weeks**, plus every §3.2 assert green in `freshness_sweep` across that span, plus the B6 diagnostic answered (is the local pull even alive?) | 🔴 **do not delete anything.** Local writers get DISABLED and their code kept; `pull_cloud_replicas.py` stays as the documented rollback path |

**Never in scope for retirement, at any step:** the FedEx-113/UPS invoice download (N5), the Apps
Script exceptions sweep (N3), `fulfillments` and `feedback` local reads (B1 / §1), and 🔴 **the rest
of `sync_logon.py`** — its FedEx-IMAP leg writes `shipments` + `invoices`, which have no cloud
writer (B6).

## 6.1 Not in this migration, but do not let it get lost

Three items surfaced en route that this contract does **not** own and must not be recorded as
handled by it:

1. 🔴 **The `origin_hub` backfill** (§1.2) — the real routing-freshness lever, fenced off, no
   scheduled owner. Routing keeps optimizing against month-old transit history no matter which
   database it reads.
2. 🔴 **`engine.py:338-345` `derive_failed_carriers` returns 0 rows, always** (§2.1) — reships
   silently fall through to FedEx 2Day AIR. Needs its own fix plus a replay-diff.
3. **The `DATA_CANON_RULES.md:25` doc/code contradiction** (B5).

🔴 **Rollback doctrine.** Every step above is a flag flip, not a deletion. The one irreversible
thing in this migration is deleting a local writer, and step 6 explicitly does not do it — it
disables. A writer we disabled and can re-enable is a rollback; a writer we deleted is an outage.

---

# 7. What is measured, what is cited, what is unverified

Written to `never-fabricate` discipline: every number above is one of these three, and this section
says which.

**Measured directly by this session (read-only, `mode=ro`, 2026-08-27 ~18:09 ET):**
- Local sqlite schemas for `delivery_status`, `fulfillments`, `shipments`, `shopify_orders`; the
  absence of `pp_webhook_events` locally.
- Local row counts / maxima: `delivery_status` 118 909 rows, organic `synced_at` max
  `2026-08-25 21:41:13`; `fulfillments` 118 904, `updated_at` max `2026-08-27 16:17:42`;
  `shipments` 97 311, `ship_date` max `2026-08-19`, length buckets {10: 94 760, NULL: 2 545, 0: 6};
  `shopify_orders` 60 072, `created_at` max `2026-08-27T15:33:31Z`.
- Per-carrier `shipments` maxima: OnTrac `2026-08-19`, FedEx `2026-08-18`, UPS `2026-08-11`,
  Veho `2026-07-28`. `invoices` `email_date` maxima: OnTrac `2026-08-26`, FedEx `2026-08-25`,
  UPS and Veho blank.
- The timezone facts in §2.3: sqlite `datetime('now')` = `22:09:13` UTC vs local `18:09:13`.
- The live App Platform spec for `shipping-console` (`DELIVERY_SYNC=1`, `INVOICE_SYNC=1`,
  `INVOICE_IMAP=1`, `ROUTING_HISTORY_DB=1`, `ROUTING_INPUTS_DB=1`; `PP_DERIVE` and `PP_RECONCILE`
  **absent**).
- Timer intervals and `REGISTRY` membership in `server/ingest_worker.py`.
- `lib/histdb.py` behavior in full; `appyhour_lib/paths.py` `APPYHOUR_DB_PATH` resolution order.

**Cited from existing constraint docs / code comments (not re-derived here):**
DATA_CANON grain, business-key and known-defect declarations; the 3h / 24h / 48h / 9d / 14d / 8d /
3d freshness numbers; the wk0803 scorecard 41-vs-40 and 15% dollars; the 41 air'd reships;
the 2026-08-17 `feedback` completeness outage (34/55 blank `order_number`); the PP 120 req/min limit
and the non-existent weekly budget; `sync_invoices.py`'s 2026-08-20 local-vs-cloud measurement.

**Measured on cloud MySQL by `ParityAudit`** (`_outputs/reports/2026-08-27-cloud-local-parity-audit.md`,
read-only, `pymysql` direct, credentials via `pull_cloud_replicas.database_url()`; join validated
against five known-present `_SHIP_2026-08-17` orders *before* any zero was trusted). Folded into
this document: the per-cohort coverage table and the 0-local-only result; the `_SHIP_2026-08-24`
6.6%-vs-89.3% delivered split; the `fulfillments` 4,911-local-only / 0-cloud-only inversion; the
`shipments` 25,795 duplicate rows and $9,177 August undercount; the `origin_hub` 2026-07-27 freeze
identical on both sides; the mixed `synced_at` format counts; the `etl_history.py:577-586` /
`pull_cloud_replicas.py:51-57` half-flip; the `engine.py:338-345` join bug; the consumer inventory
in §3.2 and §4.1.

**Measured on cloud MySQL by the coordinator's read-only probe (relayed 2026-08-27):** the
`shipments.ship_date` length distribution in B2; the three-double-ingested-OnTrac-invoice /
$21,319 duplicate-cost finding; the 4,582-of-5,086 recoverable-by-tracking count. Together with
`ParityAudit` these supersede the hypothesis this document originally carried, which is why B2 is
now a confirmed requirement rather than an open question.

🔴 **NOT verified — do not treat as fact:**
- **This session issued no query against cloud MySQL.** I had no `DATABASE_URL`. Every cloud-side
  number above is `ParityAudit`'s or the coordinator's, attributed inline.
- `fulfillments.updated_at`'s timezone (§2.3).
- 🔴 **Whether the local ParcelPanel pull is currently alive at all** (B6) — no `synced_at` rows for
  8/26 or 8/27 while the wrapper reports firing. Needs a deliberate write-capable diagnostic; both
  read-only audits correctly declined to run it.
- The `20260302` value from the original brief **did not reproduce**; cloud lexical
  `MAX(shipments.ship_date)` is `20260817`, and which slice produced March is undetermined.
- Whether the cloud and local IMAP invoice pulls collide (B4).
- First-`materialize()` wall time over a real link — unmeasured; measure before wiring an
  interactive consumer.
- The consumer list in §3.2 is grounded in my own grep plus `ParityAudit`'s full reader inventory
  (~30 consumers across ShipRouting, AppyHour, `_outputs/scripts` and the skills). It should still
  be reconciled against `ConsumerSweep`'s inventory before anyone treats it as exhaustive.

---

## Related

- `ShipRouting/server/DATA_CANON_RULES.md` — ownership matrix + table declarations (this doc must
  never contradict it)
- `ShipRouting/server/STATUS_INGEST_RULES.md` — how a status reaches us (DRAFT, unapproved)
- `AppyHour/ShippingReports/RESHIP_REPORT_RULES.md` — the pivot sheet's own rules (D35 carrier mix)
- `AppyHour/ShippingReports/EXCEPTIONS_ALERT_RULES.md` — the exceptions sweep + PP call policy (N3)
- `ShipRouting/INVOICE_INGEST_RULES.md` — invoice ingest constraints (N5, B2)
- `ShipRouting/lib/histdb.py` — the recommended read path
- `_outputs/scripts/freshness_sweep.py` — the standing weekly assert host
- `_outputs/scripts/pull_cloud_replicas.py` — the existing replica path (kept as rollback)
- `_outputs/reports/2026-08-27-cloud-local-parity-audit.md` — 🔴 **the measurement this contract's
  verdicts rest on.** Read it alongside §1 and §5
