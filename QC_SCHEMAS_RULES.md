# QC_SCHEMAS_RULES.md — constraints SSOT for `qc_schemas.py`

🔴 **PRE-CHANGE GATE:** read this before touching `AppyHour/qc_schemas.py`. Change rules HERE
first, in the same commit. Single source of truth for what the pandera QC schemas guarantee.

## What it is

`qc_schemas.py` is an **additive** pandera validation layer for AppyHour ops artifacts.
It exposes `DataFrameSchema` definitions plus a `validate(df, schema) -> (ok, failures_df)`
helper that runs **lazy** validation and returns *all* failing rows as a DataFrame instead
of raising on the first failure.

**One live caller (2026-07-02):** `ShipRouting/scripts/qc_audit.py` `schema_gate()` runs
`ROUTING_TAB5_SCHEMA` against the newest `_outputs/cache/routing_tab5_rows.json` as a
**LOG-ONLY** step — failures go to `_outputs/logs/qc_schema_failures_<tag>.jsonl`, and it
**never** changes qc_audit's PASS/FAIL findings or exit code.

## Lifecycle — log-only → promote (do NOT skip the quiet cycle)

1. **LOG-ONLY (current):** failures are written to the jsonl log + a one-line summary.
   Exit codes, PASS/FAIL reports, and live routing behavior are UNCHANGED.
2. **Quiet cycle:** at least one full ship-week with zero false positives on a clean cohort.
3. **Promote:** only then may a schema failure become a real gate (FAIL / halt). Promotion is
   a deliberate edit to `qc_audit.py` + this doc in the same commit — never implicit.

🔴 **Do NOT promote early.** Known false-positive candidate: the `tnt_le_2` check flags
winter cold-state 3-day ground (legal per ROUTING_RULES §0 seasonal rule). Review the log
against season before promoting that check.

## Gotchas / failure-modes (negatives-first)

- 🔴 **NEVER raise on first failure.** Lazy validation (`lazy=True`) must collect ALL failing
  cases. A first-fail raise hides the other 200 bad rows and defeats the purpose of a QC audit.
  `validate()` catches `SchemaErrors` and returns `err.failure_cases` — it must NOT re-raise.
- 🔴 **The wired gate is LOG-ONLY. Do NOT make `schema_gate()` affect qc_audit's exit code,
  findings, or any live path** until the promote step above. A missing pandera install must
  degrade to a printed warning — never crash the audit.
- 🔴 **Column names are load-bearing and copied from real builders — do not rename to guesses.**
  - `ROUTING_TAB1_SCHEMA` mirrors `ShipRouting/build.py` tab1 header exactly:
    `["Order Number", "State", "Zip Code", "OnTrac", "Veho"]` (build.py ~L164).
  - `ROUTING_TAB5_SCHEMA` mirrors build.py tab5 header exactly (~L375), **VERIFIED 2026-07-02**
    against a live 1331-row `routing_tab5_rows.json`:
    `["Order Number", "State", "Zip Code", "Final Routing Tag", "TNT (effective)",
    "Lane (or rate-battle lanes)", "Ice Config", "Extra Gel"]`.
  - If a build.py header changes, update the schema in the SAME commit or it silently
    passes garbage / fails good data.
- ⚠️ **Do not add `coerce=True` blindly.** Coercion masks type bugs we WANT the QC gate to catch
  (e.g. a numeric zip that lost its leading zero). Validate as-is; coerce only where the upstream
  contract genuinely allows it.
- ⚠️ **`failure_cases` is empty on success.** Callers must branch on the returned `ok` bool, not
  on `len(failures_df)` alone across pandera versions.
- ⚠️ **`_CARRIER_HUBS` is a deliberate duplicate of ROUTING_RULES §0** (this module must stay
  import-side-effect-free; it cannot import ShipRouting). If the legality table changes there,
  change it here in the SAME commit or the schema flags legal lanes / passes illegal ones.

## ROUTING_TAB5_SCHEMA — each check names the incident it guards

| Check | Incident / bug class it guards |
|---|---|
| `Zip Code` = 5-char string w/ leading zeros (`coerce=False`) | **zip-leading-zero class** — a numeric coercion turns NJ `07001` into `7001` and every zip-keyed lookup (serviceability, TNT, ice) silently misses. |
| `tag_grammar` (`!(NO )<Carrier Service> - <Hub>_AHB!` or empty) | malformed `!!`/stray tags reaching Shopify — apply path is `_AHB!`-grammar-gated; a bad tag = order silently unrouted. |
| `legal_lane` (Veho=Nash/Indy · UPS=Dallas · OnTrac=Anaheim/Nash/Dallas · FedEx=all 4) | engine once proposed **physically impossible Veho-Dallas** lanes; also the 475-row Veho@Dallas ingest mis-attribution (2026-06-24). |
| `lastmile_zip` (positive Veho/OnTrac tag ⇒ real 5-digit zip) | **serviceability class — 358–391 live Veho/OnTrac orders routed to UNSERVICED zips** on `_SHIP_2026-06-29` (HISTORY_SERVICEABILITY STATE layer minted coverage). Shape-level guard; the full coverage-file check lives in qc_audit SERVICEABILITY. |
| `tnt_le_2` (TNT effective ∈ {"", 1, 2}) | 3-day-on-final-sheet = late/warm class; also surfaces **FedEx-Ground over-assignment spikes** (6/15 incident) as TNT drift. Seasonal caveat above. |

## Cut-order schema — DEFERRED (do not wire)

`CUT_ORDER_LINE_SCHEMA` remains a **GENERIC EXAMPLE, not a contract**.
`build_cut_order_xlsx_v2.py` writes a styled worksheet cell-by-cell (openpyxl), NOT one flat
DataFrame — there is no single canonical cut-order column list to pin. **Deliberately skipped
in the 2026-07-02 wiring pass.** Do NOT wire it anywhere until the real per-line contract is
derived from the v2 builder (or the builder grows a flat-frame emit point).

## I/O contract

- `validate(df: pd.DataFrame, schema: pandera.DataFrameSchema) -> tuple[bool, pd.DataFrame]`
- Returns `(True, empty_df)` when valid; `(False, failure_cases_df)` when not.
- `failure_cases_df` columns follow pandera: `schema_context, column, check, check_number,
  failure_case, index`.
- `schema_gate()` log line format (one JSON object per failing case):
  `{"tag", "schema", "order", "column", "check", "failure_case", "row_index"}`.

## Non-goals

- Not a replacement for `qc_audit.py` (routing/ice legality + live coverage gate). This is
  dataframe-shape QC on the emitted artifact only.
- Does not fetch data, hit Shopify, or open shipping.db. Importing it must have zero side
  effects (no I/O, no API, no DB).
