# QC_SCHEMAS_RULES.md — constraints SSOT for `qc_schemas.py`

🔴 **PRE-CHANGE GATE:** read this before touching `AppyHour/qc_schemas.py`. Change rules HERE
first, in the same commit. Single source of truth for what the pandera QC schemas guarantee.

## What it is

`qc_schemas.py` is an **OPT-IN, additive** pandera validation layer for AppyHour ops artifacts
(routing assignment sheet rows, cut-order line rows). It exposes `DataFrameSchema` definitions
plus a `validate(df, schema) -> (ok, failures_df)` helper that runs **lazy** validation and
returns *all* failing rows as a DataFrame instead of raising on the first failure.

Nothing in live ops calls it yet. It is a scaffold for future QC gates.

## Gotchas / failure-modes (negatives-first)

- 🔴 **NEVER raise on first failure.** Lazy validation (`lazy=True`) must collect ALL failing
  cases. A first-fail raise hides the other 200 bad rows and defeats the purpose of a QC audit.
  `validate()` catches `SchemaErrors` and returns `err.failure_cases` — it must NOT re-raise.
- 🔴 **This module is OPT-IN. Do NOT wire it into `qc_audit.py`, `build.py`, `auto_import.py`,
  or any live path** without an explicit ask. Importing it must have zero side effects (no I/O,
  no API, no DB). It only defines schemas + a pure helper.
- 🔴 **Column names are load-bearing and copied from real builders — do not rename to guesses.**
  - `ROUTING_TAB1_SCHEMA` mirrors `ShipRouting/build.py` tab1 header exactly:
    `["Order Number", "State", "Zip Code", "OnTrac", "Veho"]` (build.py ~L164).
    If build.py's header changes, update this schema in the SAME commit or it silently
    passes garbage / fails good data.
- ⚠️ **Do not add `coerce=True` blindly.** Coercion masks type bugs we WANT the QC gate to catch
  (e.g. a numeric zip that lost its leading zero). Validate as-is; coerce only where the upstream
  contract genuinely allows it.
- ⚠️ **`failure_cases` is empty on success.** Callers must branch on the returned `ok` bool, not
  on `len(failures_df)` alone across pandera versions.
- 📝 **TODO (unconfirmed columns):** the cut-order artifact in `build_cut_order_xlsx_v2.py` is
  written cell-by-cell to a styled worksheet (openpyxl `_dark_header_row`), NOT from a single
  flat DataFrame — so there is no one canonical cut-order column list to pin. A generic
  `CUT_ORDER_LINE_SCHEMA` is provided as an EXAMPLE shape (SKU/name/qty) with this TODO: confirm
  the real per-line schema with the builder owner before using it as a live gate.

## I/O contract

- `validate(df: pd.DataFrame, schema: pandera.DataFrameSchema) -> tuple[bool, pd.DataFrame]`
- Returns `(True, empty_df)` when valid; `(False, failure_cases_df)` when not.
- `failure_cases_df` columns follow pandera: `schema_context, column, check, check_number,
  failure_case, index`.

## Non-goals

- Not a replacement for `qc_audit.py` (routing/ice legality gate). This is dataframe-shape QC only.
- Does not fetch data, hit Shopify, or open shipping.db.
