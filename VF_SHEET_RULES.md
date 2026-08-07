# vF / Access_LIVE Production Sheet — Structure, Names, PO Boxes, Syntax (SSOT)

🔴 **PRE-CHANGE GATE.** Single source of truth for the weekly production upload sheet
(`AHB_WeeklyProductionQuery_*_vF.xlsx`, tab `Access_LIVE`). **Every rule here is extracted from
the authority that already enforces it — do NOT invent or "close-enough" any value:**

- **Sheet QC / syntax authority:** Kori — `AppyHour/GelPackCalculator/kori/gel_pack_webview.py`
  QC pass (`qc_check_sheet`, ~lines 2240-2646). This doc is a readable extract of that code; the
  code wins on any conflict.
- **MFG / product names authority:** `AppyHour/mfg_names_authoritative.csv` (the meal-type export),
  code-guarded by `matrix_commander.validate_mfg_names` (MATRIX_RULES rule 21).
- **Lanes / routing-tag & serviceability authority:** `ShipRouting/ROUTING_RULES.md` + coverage
  CSVs (`ShipRouting/lib/zip_loaders.py`); resolved by `lib/engine.serviceability_gate`.

Related: [[mfg-names-canonical-source]] · [[xlsx-live-edit-discipline]] · [[never-fabricate-lookup-or-ask]].
When editing the sheet by hand, map cells by **header name, never column index** (indices shift on
Excel re-save), and re-read the layout after any lock/save.

---

## 1. Sheet structure

- **Tab name MUST be `Access_LIVE`** (QC "Tab Name" check, ~2548). Any other name fails.
- **Header row = row 1; data from row 2.** Orders keyed by `OrderID` (col A).
- **Fixed left columns, in order (A-N):** `OrderID, Name, Distribution Type, Total, Phone Number,
  Email, Address, Address 2, City, State, Zip, Tags, Notes, ProductionDay`.
  - **`ProductionDay` header is at Col N** (QC "ProductionDay Header (Col N)", ~2556). Do not move it.
- **Product columns (O onward):** one column per SKU, header = **`AHB (S_REG): <MFG name>`**. A cell
  holds the per-order quantity (`1`, blank/`0`/`None` = not in box). Values counted with the
  blank/`0`/`None` guard (~2345).
- **OrderIDs MUST be sorted ascending** (QC "Sort Order", ~2601) and **unique** — a repeated OrderID
  fails "Duplicate Orders" (~2595).

## 2. Names (columns)

- Every `AHB (S_REG): <name>` header MUST match a name in `mfg_names_authoritative.csv` exactly.
  **Never derive a name from a Shopify product title** (that fabrication reached a sent vF on 234
  rows once, and again 2026-08-04 as "Farmstead Smoked Cumin Gouda" vs the real "Farmstead Cumin
  Gouda"). SKU not in the authority → STOP and ask; onboard via the RMFG Translator, re-export.

## 3. PO Boxes (+ address syntax)

Kori "Address (PO Box / Slash)" check (~2518-2533, 2613):
- **No PO Box in `Address` or `Address 2`.** Detector (~2255):
  `\b(?:p\.?\s*o\.?\s*box|pobox)\b`, case-insensitive — matches `PO Box`, `P.O. Box`, `P O Box`,
  `P.O.Box`, `POBOX`, etc. A carrier can't deliver a cold box to a PO Box → the row is flagged and
  must be corrected (real street address) before ship.
- **No forward slash `/` in `Address` or `Address 2`** (breaks downstream parsing).

## 4. Syntax rules (all QC checks, negatives-first)

- **Zip (~2458):** must be TEXT, 5-digit. Fails if stored as a number (leading zeros lost) or a
  4-digit string missing its leading zero (`1234` → should be `01234`).
- **ProductionDay values (~2506):** every row must equal the ship day — `TUE` on a Tuesday run,
  `SAT` otherwise. Any other value fails.
- **Routing / `!bang` tags (in `Tags`, ~2244-2455):**
  - Only **approved** bang-tags may carry a leading `!` (canonical `ROUTING_TAG_SET` + gel/weather
    tags `!ExtraGel24oz!`,`!ExtraGel48oz!`,`!WeatherHold!`,`!WeatherHold_Origin!` + active
    `routing_tag_configs`). An unapproved `!tag` fails; a well-formed-but-unlisted lane is flagged
    "add to ROUTING_TAGS if valid [info]" — **never hand-mint a lane tag; add it to the authority.**
  - **No `!!` (double bang)** anywhere in a tag (~2422) — malformed by definition.
  - **Combo rules** (`validate_routing_tag_combo`, ~2377): multiple routing tags must be a legal
    combination (`!ANY` is solo-only; exclusive carriers can't combine; `!NO` tags may stack).
  - **Tuesday = Dallas-only** (~2391): a routing tag naming a hub not shipping Tuesday fails; and
    **CA / FL on Tuesday require `!FedEx 2Day OneRate` (~2404).**
  - **force-2Day override** (prior-failure reship, ~2358): routing must resolve to
    `!FedEx 2Day OneRate - Dallas_AHB!`; any other carrier left in is an error. `Fixed_Route`
    pins routing and exempts route-selection checks (physical Tuesday-hub checks still run).
- **Gift Redemption (~2316):** orders tagged `gift redemption` are hard-locked (un-editable via
  import) → removed/excluded from the sheet, not shipped through it.
- **Low item count (~2482):** `Total` < 10 items fails unless the order is a `reship` or contains a
  tray (`TR-`) — else it's a suspected short box.

## 5. Asymmetric generation — LEDGER truth + async verify (Kurt A/A/A, 2026-08-06)

Sheet generation reads the **apply-time LEDGER**, never a blocking live-Shopify fetch. Rules,
negatives-first:

- 🔴 **The wk0703 burn stays fixed by the VERIFIER, not by live-fetching.** The old rule "col L
  from live tags at export" existed because a stale tag column shipped; asymmetric generation
  replaces it: col L comes from the ledger (what apply ACTUALLY wrote), and the async verifier
  diffs ledger vs live AFTER generation, flagging any divergence loudly (Q1=A: alarm+patch — the
  automated wk0803 rev-2..7 workflow). A vF whose verifier pass hasn't run/completed is marked
  UNVERIFIED on its Summary line; presend_check refuses an unverified sheet older than its cohort's
  last apply.
- 🔴 **The ledger is written AT APPLY TIME by apply.py, full item snapshot** (Q2=A): every order's
  tags (as-written), address (as-shipped — MASS/COG boxes keep the COG address even after Shopify
  restore), items (fulfillable lines at apply moment), box/tray class, ProductionDay inputs. One
  JSON per cohort under `_outputs/cache/vf_ledger_<ship-date>.json`; re-apply OVERWRITES with a
  diff line (rule-17 style — one file per cohort, never forked parallels).
- 🔴 **Ledger ≠ authority for names/lanes** — headers still validate against
  `mfg_names_authoritative.csv` (rule 21) and tags against the routing authority; the ledger only
  answers "what did apply write", never "what is a valid name/lane".
- 🔴 **Mid-window mutations (CS address edits, cancels, swaps) do NOT invalidate the sheet
  silently.** The verifier's diff classes: address changed → patch row + re-check lane
  serviceability per (hub, zip5); cancelled/fulfilled → remove row; tags diverged → the LIVE tag
  wins only if a human wrote it (the ledger records apply's write; any later live change is either
  a hand-fix to honor or an anomaly to flag — verifier distinguishes by diffing against the
  ledger's post-apply read-back).
- 🔴 **Verifier Shopify access is READ-ONLY GraphQL** — no sync writers (cloud ownership matrix,
  DATA_CANON). It must never write tags, orders, or the retired sync tables.
- **Gift orders**: the vFGR merge (`merge_gift_xlsx`, MATRIX_RULES 20) still overrides ledger item
  cells — vFGR remains item-truth for uneditable gift orders, and it must be swap-translated or
  re-exported post-swap ([[vfgr-gift-order-replace]]).
- **Offline regen** (Q3=A: iteration speed): any rev-N patch loop reads ledger + local caches only;
  zero Shopify round-trips between verifier passes.

## 6. Editing discipline

Hand-editing the vF is allowed, but: pull names from `mfg_names_authoritative.csv`, lanes/tags from
the ROUTING authority, keep PO-box/zip/sort/tag syntax above, and **run Kori's QC (or an equivalent
header/name validator) on the file after any edit** — the QC is the guard; a raw openpyxl edit that
skips it is how an invented name or bad lane reaches a sent sheet.
