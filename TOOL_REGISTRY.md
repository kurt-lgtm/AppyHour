# AppyHour Canonical Tool Registry

**Single source of truth for "which tool do I call for capability X."** Before writing ANY new script/helper, check here. If a capability is listed, **call the canonical owner — do NOT recreate or hallucinate one.** Duplicates fragment behavior and drift out of sync.

**Discovery order** when a capability isn't listed: codegraph (`codegraph_search`/`codegraph_context`) → this registry → `appyhour_lib` → grep — BEFORE creating anything. Generic build discipline + small-model-operability principle: `~/.claude/skills/forge/references/canonical-tools.md`. Reimplementation guard: `~/.claude/instincts/canonical-tools.md`.

## How to dispatch (small-model operating layer)
Find the capability → call the listed tool. The logic lives IN the tool, so a small model only needs to pick + call the right one. Legend:
- **✅** = small-model-safe: deterministic, just call it.
- **🔒** = needs judgment or a human go (live writes, SKU-identity safety, multi-source analysis). Use a capable model / confirm first.
- **✍️** = WRITES/mutates (Shopify, Sheets, DB, Slack) — `dry_run`/preview first, verify target state after.

Client namespacing: MCP tools are called as `mcp__appyhour__<name>` (always-on server) or `mcp__appyhour-shipping__<name>` (load-on-demand). Scripts run via `C:\Users\Work\anaconda3\python.exe <path>`.

---

## 1. MCP TOOLS (40 — the primary dispatch layer)

> ⚠️ **Naming gotcha:** tools decorated `@mcp.tool(name="appyhour_…")` expose that exact name; **bare** `@mcp.tool()` tools expose the **python function name with NO `appyhour_` prefix** (e.g. `get_inventory_snapshot`, `sheets_read`, `gorgias_list_tickets`, `update_operational_issues`). Both are namespaced `mcp__<server>__<name>` at the client.

### Gel-pack / thermal · demand · inventory (AppyHourMCP, always-on)
| Capability | Tool | Disp |
|---|---|---|
| FedEx/UPS forward TNT / rate quotes (ShipStation) — batch quoting, prewarm, hypothetical-origin analysis | `shipstation-quotes` skill → `ShipRouting/lib/carrier_tnt.py` `build_carrier_tnt` (BATCHED, never per-lane) | 🔒 |
| Thermal/gel analysis for ONE shipment (dest state+temps → config, BTU, risk, gel tags) | `appyhour_analyze_shipment` | ✅ |
| Current weekly per-SKU demand (active Recharge subs) | `appyhour_get_subscription_demand` | ✅ |
| Upcoming QUEUED Recharge charges → per-month SKU demand | `appyhour_get_upcoming_charges` | ✅ |
| Multi-month cohort demand forecast (retention + curations) | `appyhour_forecast_demand` (months 1-12) | ✅ |
| Reorder alerts (forecast vs on-hand+PO+wheel → CRITICAL/WARN/OK) | `appyhour_get_reorder_alerts` | ✅ |
| Inventory from settings (filter by SKU prefix) | `get_inventory_snapshot` | ✅ |
| **Source-of-truth** inventory (journal replay; use for fulfillment) | `get_calculated_inventory` | ✅ |
| Cut-order config (curation recipes, assignments, wheel inv, box counts) | `get_cut_order_config` | ✅ |
| Newest error-scan CSV as rows | `get_recent_errors` | ✅ |
| Recent depletion history | `get_depletion_history` | ✅ |

### Shopify (AppyHourMCP)
| Capability | Tool | Disp |
|---|---|---|
| Fetch unfulfilled orders by tags | `appyhour_fetch_orders` | ✅ |
| Fetch tagged orders **+ per-order thermal analysis** | `appyhour_analyze_orders` | ✅ |
| Search orders by #/email/name (returns line items+SKUs) | `appyhour_search_orders` | ✅ |
| List / get / search products; list collections | `appyhour_list_products` · `appyhour_get_product` · `appyhour_search_products` (🔴 BROKEN: crashes on null-title products; use Shopify REST directly) · `appyhour_list_collections` | ✅ |
| BULK read (>1000 rows; bulkOperationRunQuery+poll) | `appyhour_bulk_query` | 🔒 |
| Validate production matrix (xlsx) vs Shopify by RMFG tag | `appyhour_validate_production_matrix` | ✅ |
| Generate per-order swap LIST/preview (no writes) | `appyhour_generate_swap_list` | ✅ |
| **EXECUTE SKU swaps** on a ship_tag cohort | `appyhour_swap_order_skus` (dry_run default True). 🔴 Balance invariant enforced 2026-07-10 (#157930 shipped short): every swap path aborts if qty removed ≠ qty added; duplicate-SKU lines all swapped; audits to `swap_audit.jsonl`. NEVER hand-roll an order edit around this. | 🔒✍️ |
| **WEEKLY shorts→swap pass** (whole pairs list, one command: plan → preview → apply → verify) | `scripts/shorts_pass.py SHIP_TAG --pairs pairs.csv [--apply]` — constraints SSOT `scripts/SHORTS_PASS_RULES.md`. Plan via `find_swap_targets` + login-OR-customize exclusion; execution ONLY via `order_edit._swap_order_skus`; verify re-counts `fulfillableQuantity` vs plan (nonzero exit on mismatch — success is NEVER call-count). Dry-run default; JSONL log per run. | 🔒✍️ |
| **REMOVE a SKU's line item(s) from a cohort** (last resort after swaps) | `scripts/remove_line_items.py SHIP_TAG --sku SKU [--only-if-tagged T] [--allow-paid] [--apply]` — constraints SSOT `scripts/REMOVE_ITEMS_RULES.md`. Targets via `active_line_items` only; edits via order_edit's begin/setQty(restock)/verify/commit guards; PAID lines refused without `--allow-paid` (prints actual-paid — pair with `refund_batch.py`, wk0720 CH-IPRW); verify = per-order REST read-back + control-order probe; delta JSONL + swap_audit.jsonl. NEVER write a dated remove_* one-shot instead. | 🔒✍️ |
| **BATCH/count-limited swaps in code** (shorts pass, per-order batches) | `fulfillment_web/shopify_swap.py::find_swap_targets` + `execute_bulk_swap` — per-order accounting `{success, failed, locked, transient}`, `fulfillableQuantity>0` filter, REST pagination. 🔴 `execute_swap` is a LOW-LEVEL primitive, NOT an entry point — wk0810 hand-rolled loop around it reported **34 phantom successes** (`success:False` doesn't raise). Wrong-result shape refs archived: `_archive/handrolled-shape-refs/wk0810-swaps/` | 🔒✍️ |
| **ADD free ($0) variant(s)** to orders (pure add, idempotent; tag/box/explicit-order targeting) | `appyhour_add_order_variants` (dry_run default True) | 🔒✍️ |
| **Add/remove tags** on ONE order (gel/routing/hold) | `appyhour_update_order_tags` | 🔒✍️ |
| **Bulk tag by predicate on a cohort** ("tag every order on SHIP_TAG that has/lacks SKU X / is (not) tagged Y") | `scripts/tag_where.py SHIP_TAG [--has/--lacks SKU] [--tagged/--not-tagged T] --add/--remove T` — dry-run DEFAULT, `--apply` to write, JSONL delta log. Constraints SSOT: `scripts/TAG_WHERE_RULES.md` (read FIRST — ice tags add-only, tags-are-a-SET, active_line_items). | 🔒✍️ |
| **Customer outreach list + notice drafts** (sub/refund/short notices for a cohort) | `scripts/outreach.py SHIP_TAG --type sub\|refund\|short --items items.csv` — SSOT `scripts/OUTREACH_RULES.md` (read FIRST). Builds contacts.csv (prior-contact Gorgias flag) + per-customer `DRAFT-NEEDS-HUMANIZER` drafts in `_outputs/artifacts/`. 🔴 NEVER SENDS — sending is a separate explicit human/session step in Gorgias; refund amounts from actual refund records only; item names verbatim from line items. | 🔒 |

### Google Sheets (AppyHourMCP)
| Capability | Tool | Disp |
|---|---|---|
| Read range → JSON | `sheets_read` | ✅ |
| Append rows | `sheets_append` | ✅ |
| Create sheet / add tab / list tabs | `sheets_create` · `sheets_add_tab` · `sheets_list_tabs` | ✅ |
| **Overwrite** a tab from A1 | `sheets_write` | 🔒✍️ |

### Gorgias + Ops-summary (AppyHourMCP)
| Capability | Tool | Disp |
|---|---|---|
| Connection test · list tickets · get ticket · ticket stats · CSAT | `gorgias_test_connection` · `gorgias_list_tickets` · `gorgias_get_ticket` · `gorgias_ticket_stats` · `gorgias_satisfaction_stats` | ✅ |
| **Sync** Gorgias→Operational Issues sheet + enrich rows | `update_operational_issues` (dry_run first) | 🔒✍️ |
| **Sync** food-safety tickets → Food Safety tab | `gorgias_sync_food_safety` | 🔒✍️ |
| **Rebuild** Ops Summary pivots+charts | `rebuild_ops_summary` | 🔒✍️ |
| **Build** Shipments tab from fulfilled orders (FC counts) | `build_shipments_tab` | 🔒✍️ |

### Shipping + weather (AppyHourShippingMCP, load-on-demand)
| Capability | Tool | Disp |
|---|---|---|
| Shipping analytics (costs/transit/misroutes/chronic-zips/overrides) | `appyhour_shipping_analysis` | ✅ |
| OpenWeatherMap forecast for a zip (→ lat/lon) | `appyhour_get_weather` | ✅ |
| NWS alerts for lat/lon | `appyhour_get_weather_alerts` | ✅ |
| **Apply** force-2day zip routing tags | `appyhour_apply_zip_routing_tags` (dry_run default True) | 🔒✍️ |

> `appyhour_shipping_analysis` + `appyhour_apply_zip_routing_tags` are served ONLY by the Shipping MCP — the copy in `AppyHourMCP/tools/shipping.py` is intentionally NOT registered (server.py:40-44).

---

## 2. APPYHOUR OPERATIONAL SCRIPTS (weekly/daily)
Run via `C:\Users\Work\anaconda3\python.exe <path>`.

| Capability | Canonical tool | Disp | Never instead |
|---|---|---|---|
| Weekly RMFG cut order (3-tab xlsx) | `InventoryReorder/build_cut_order_xlsx_v2.py` (or `/cut-order`) | 🔒✍️ | v1 `build_cut_order_xlsx.py`; `agents/tuesday_cut_order.py` (empty demand) |
| Weekly RMFG cheese portion yield audit (Before/After xlsx, oz/slice vs spec) | `_outputs/artifacts/2026-06-17-rmfg-production-invoices/` pipeline (or `/rmfg-yield-audit`) | 🔒✍️ | hand-rolling from the PDF; confusing w/ `inventory_reorder.py` wheel→slice yield |
| Carrier-invoice ingest → shipping.db | `GelPackCalculator/auto_import.py [--dir]` | ✅✍️ | hand SQL inserts; double-import via Kori "Sync All" |
| **Manual carrier download → auto-ingest** (UPS/FedEx working path) | `GelPackCalculator/ingest_downloads.py [--watch]` | ✅✍️ | `portal_pull.py` (PARKED) |
| **UPS invoice pull** (billing.ups.com, captcha-free via logged-in Chrome session) | claude-in-chrome real-session → My Invoices → Download **CSV:Full(250)** → `ingest_downloads.py --no-import` (stage) → **human** runs `auto_import.py` in real terminal. Rules: `GelPackCalculator/UPS_INVOICE_PULL_RULES.md` | 🔒✍️ | 🔴 Claude NEVER runs auto_import (MSIX corrupts shipping.db); NEVER fresh-login/captcha-solve (portal_pull PARKED); download CSV flat-file NOT PDF; never enter creds |
| Carrier-billing portal LOGIN automation | `GelPackCalculator/portal_pull.py` | 🔒 | **PARKED 2026-06-24** (anti-bot dead-end) — use `ingest_downloads.py` |
| Fetch ALL carrier invoice emails (FedEx+OnTrac+Veho) | `GelPackCalculator/sync_all_carriers.py` | ✅✍️ | `download_fedex_imap.py` alone (lags OnTrac/Veho) |
| Per-carrier IMAP fetch (one carrier) | `GelPackCalculator/download_{fedex,ontrac,veho,shipping_pdfs}_imap.py` | ✅✍️ | — (prefer `sync_all_carriers` for weekly) |
| Daily shipping sync + aged-out sweep | `GelPackCalculator/daily_shipping_sync.py [--aged-out-sweep --apply]` | ✅✍️ | ad-hoc SQL to delivery_status |
| Logon freshness sync (Task Scheduler) | `GelPackCalculator/sync_logon.py` | ✅ | duplicating its chain by hand |
| Gorgias field-completion gate (audit) | `AppyHourMCP/tools/gorgias_field_gate.py [--days --tag]` | ✅ | confuse w/ ops-sheet sync |
| Gorgias→ops-sheet sync (CLI wrapper) | `AppyHourMCP/run_gorgias_update.py [--days --dry-run]` | ✅✍️ | raw gspread/Gorgias REST |
| Wrong-address inbox automation | `scripts/automations/wrong_address_automation.py [--apply-untag --apply-fix --days]` | 🔒✍️ | `scripts/incident-fixes/fix_*` (one-shots) |
| Ship-week postmortem / warm-cohort report | `ShippingReports/postmortem_runner.py` (or `appyhour-shipping-data` skill) | 🔒 | hand-rolled SQL join |
| **Weekly shipping vendor×issue matrix + box-type breakdown → Google Sheet (1 tab/week)** | `python -m ingest.slack_reship.sync --week <Mon> --report --push` (add `--box-types` for box breakdown w/o sheet; `--denom <N>` overrides auto-count; `--dump-file <blob>` for no-token path). Weekly wrapper: `python -m ingest.slack_reship.weekly_task` | 🔒✍️ | hand-counting Slack OR `feedback.issue_type` (UNDERCOUNTS — Gorgias Contact Reason empty on ~all shipping tickets). Deterministic: same week+denom→same table. Counts = Slack `#reship-and-order-requests` parsed→joined to `fulfillments` for carrier. **Window = ticket RECEIPT date, %=count/denom, denom auto = `fulfillments _SHIP_<Mon>`** (per `~/.knowledge/ops/Weekly Shipping Issue Report.md` GATE). **Box type = Shopify line-item SKU: MCUST→Medium Tray, LCUST→Large Tray, else Regular Box** (`boxtype.py`; NOT `shipments.box_type`). Sheet = Kurt-owned, shared to SA `appyhour-shipping@…` (SA has no Drive quota — can't create; seed id once via `--sheet-id`). LIVE Slack needs `AH_SLACK_BOT_TOKEN`. Readers use `connect_ro` (MSIX+WAL guard). |
| **Reship tracking report (HEADLESS — Dan's durable "how are reships doing" sheet)** | **Canonical = PIVOT sheet `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`**, bound Apps Script `ShippingReports/appsscript/Code.gs` (hourly trigger, Kurt's account). Manual = the **Reship Report** custom menu on the sheet (Refresh now / Product Mix / Triage / Daily / Backfill). Immediate local mirror = `ShippingReports/scratchpad/rebuild_mix_triage.py`. Legacy `reship_report_refresh.py` = reference/backfill only (schtask DISABLED). | 🔒✍️ | Gorgias tag counts (rule 81603 spam — NEVER a reship metric), hand-edited script cols, `fulfillments` as denominator, Shopify `tracking_company` for carrier (use **Parcel Panel** — Script Property `PARCELPANEL_API_KEY`). **Constraints SSOT: `ShippingReports/RESHIP_REPORT_RULES.md` — read BEFORE any change.** Tabs: Raw Data · Triage · Product Mix (Reship/Unresolved/Potential/Actual) · Product Mix (T) (transpose + By Issue/Carrier/State %+discrete) · Daily. Unit = deduped reship ORDERS; attribution = original cohort (ship-Monday-precedes-complaint guard); denom = live Shopify `tag:'_SHIP_<Mon>' -status:cancelled -tag:'Reship'`. Overrides: expedite-request guard + late(>2d PP transit)-supersedes-warm (both post-classification; `parse.py` untouched). Deploy: REST `updateContent`; clasp token dies weekly `invalid_rapt` → `appsscript/clasp_login_py.py`. |
| Zero-shot TS forecast of ANY univariate series (CSV col → point + q10/median/q90) | `forecast_ts.py input.csv --horizon N` (TimesFM 2.5, pinned HF rev) | 🔒 | hand-rolled timesfm call (un-pinned, no OMP/UTF8 guard). **NOT** domain demand — that's `appyhour_forecast_demand`/`/cut-order`. Use for new/un-modeled series, safety-stock quantile bands, trading vol ranges. Needs `pip install "timesfm[torch]"` |
| Auto-doc a system → browsable OKF markdown bundle (handoff/onboarding/restore) | `docgen/okf_docgen.py schema` (shipping.db schema, READ-ONLY via connect_ro → `_outputs/artifacts/okf-shipping-schema/`) | ✅ | 🔴 READ-ONLY, NEVER writes shipping.db; NO fabrication (facts only, prose from vault not invented). Rules: `docgen/DOCGEN_RULES.md`. Add Sources (SKU/engine-flags) via the `Source` ABC — descriptions human/vault-supplied. |
| Box simulation (DistVol + box size for cohort) | `box_simulation.py [SHIP_TAG]` | ✅ | inline DistVol regex |
| DistVol drift audit | `audit_distvol_drift.py` | 🔒 | re-derive the lookup |
| SKU lifecycle scan (discontinued/seasonal/onetime) | `InventoryReorder/Errors/sku_lifecycle_scan.py [months]` | ✅ | hardcode SKU lists |
| **Refund EXECUTION (batch partial refunds)** | `scripts/refund_batch.py (--orders file.xlsx \| --ship-tag TAG --sku SKU) --note "..." [--commit]` — 🔴✍️ moves money. Constraints SSOT: `scripts/REFUND_BATCH_RULES.md` (read FIRST). Dry-run DEFAULT; amount = actual-paid (discounted + tax share, NEVER list); idempotent by note; per-run log + moved-xlsx; run `detect_double_refunds_v2.py` after every commit. Fallback reference: `InventoryReorder/Errors/_template_bulk_refund.py` (no longer copy-per-incident). | 🔒✍️ | copying the template into new dated `refund_*` one-shots |
| Double-refund detection | `InventoryReorder/Errors/detect_double_refunds_v2.py` | ✅ | v1 `detect_double_refunds.py` |

> `InventoryReorder/Errors/` (~120 files: `check_*`/`fix_*`/`swap_*`/`marc_*`) are overwhelmingly **one-shot dated remediations — NOT recurring tools.** Only `sku_lifecycle_scan.py` + `detect_double_refunds_v2.py` recur.

---

## 3. SHIPROUTING (build → engine → apply)
Canonical pipeline: `build.py` (I/O driver) → `lib/engine.compute_routing` (the shared brain, also used by Kori) → `apply.py`/`apply_tuesday.py` (Shopify writes). Cohort `TAG` is a top-of-file constant.

| Capability | Tool | Disp | Never instead |
|---|---|---|---|
| **🔴 RUN THE WHOLE FRIDAY FLOW — lock → vF sheet (MONDAY/main cohort). ⚠️ SEE THE ICE-GATE BLOCKER ROW BELOW BEFORE A LIVE RUN** | `ShipRouting/scripts/weekly_flow.py --async-apply [--apply-tags] [--inventory <HAVE.csv>] [--gift <vFGR.xlsx>]` — ONE process: stage1 SKU/matrix → stage2 build + `apply.py --queue` (guards run, **zero Shopify writes**) → stage3 sheet from `VF_FROM_QUEUE=1` intent → background `apply_runner.py`. **Measured 4m21s dry** end-to-end on 2,254 orders (~7–8m with quotes on + inventory) | 🔒✍️ | 🔴 **Driving the console job API by hand / two jobs (`apply=false` then `apply=true`) with an agent relaying between them.** That is how wk0810 took 27–30 min instead of ~8: a 13m45s seam where the dry build finished at 15:53:47 and nobody noticed until Kurt asked at 16:06, plus a duplicate full `compute_routing` because the second job reuses nothing from the first. One process has no seam and computes once. Also never: generating the sheet AFTER a live apply (RMFG prints from the SHEET; nothing fulfills until ship-day 05:00 ET) |
| Build routing assignment/cohort (→ tab1-5 JSON) | `build.py` (set TAG; opt `INCLUDE_RECHARGE=1`) — **a STAGE, not the entry point; prefer `weekly_flow.py`** | 🔒 | `_archive/routing_test_*.py` (stale) |
| Apply routing tags — main/Monday cohort | `apply.py` (dry-run; `--apply`) | 🔒✍️ | **Kori Apply button** (ignores engine) |
| Apply — Tuesday Dallas-only (max-gel + tag check) | `apply_tuesday.py` (dry-run; `--apply`) | 🔒✍️ | `apply.py` (lacks dallas_only) / Kori button |
| **🔴🔴 ICE-GATE BLOCKER — do NOT swap Friday to the raw script yet (measured 2026-08-09)** | The **job path** (`server/flow_jobs.py`) makes the vF structurally unreachable without its in-process ICE pass — `IcePassResult` → `IceGateError`. That gate exists because of **wk0713 (432-order ice overcap)** and **wk0720 (552 boxes under-iced)**. 🔴 **`weekly_flow.py` has NO equivalent gate**; the `weekly-shipping-run` skill relies on running `ice_distvol_workflow.py --write` SEPARATELY afterward, and warns `gen_rmfg_sheet`'s own 3×48 counter is unreliable. So running the raw script live trades ~22 min for the under-icing class. 🔴🔴 **AND `MAX_ICE_PLUS` IS DELIVERED BY THAT SEPARATE PASS, NOT BY THE BUILD** — verified 2026-08-09: it exists ONLY in `scripts/ice_distvol_workflow.py` (`:207` `_flag("MAX_ICE_PLUS","max_ice_plus",True)`, `:240` `INCLUDED: MAX_ICE_PLUS (toggle)`), with **zero hits in `lib/flags.py`**. Skip the pass and you get NO max-ice-plus at all, at legacy ice levels — in summer. Kurt 2026-08-09: *"we're doing the max ice plus until fall."* The pass self-guards (`:280` ICE ABORT if MAX_ICE_PLUS is ON with live orders but ZERO targets) but only if it is RUN. **THE CORRECT FIX IS `async_apply` INSIDE `flow_jobs`, keeping the ice gate — not bypassing the server.** Resolve ice before any live raw-script Friday | 🔒 | 🔴 Running `weekly_flow.py` on a live Friday without a verified 3×48 ice pass |
| **🔴 FridayFlow GUI CANNOT reach the async path — fix the JOB API, not the script** | `FridayFlow.bat` → `launch_friday_flow.py` → uvicorn `server/api.py` → `flow_jobs.run_flow(...)`. Verified 2026-08-09: **zero hits** for `async_apply\|--queue\|from_queue\|VF_FROM_QUEUE\|apply_runner` across `flow_jobs.py` + `api.py`. `run_flow()` has **no `async_apply` param**, and `STAGE_ORDER = ("matrix","allocate","build","apply","ice","vf")` hardcodes **apply BEFORE vf** — the sheet structurally waits on apply. The job path cannot express the async shape at all, so the GUI reproduces wk0810's two-job/one-seam run no matter how good the script is. Token story already works: `apply.py --queue` writes nothing so needs no token, and `friday.py`'s `/friday/apply_runner` is separately two-call gated — so adding `async_apply` lets the whole pre-sheet chain run UNGATED and moves the single gate onto the runner, AFTER the sheet. That removes the 13m45s seam by construction rather than by trusting anyone to click sooner | 🔒 | assuming the GUI runs `weekly_flow.py` (it does not) |
| **🔴 TUESDAY cohort — the async path does NOT apply** | Production side (matrix → allocate → ice → vF) still runs `weekly_flow.py`; **ROUTING/APPLY goes through `apply_tuesday.py` separately**. 🔴 `cohort.json` MUST set `ship_week` explicitly (a Tue batch's ship_date is its departure day but orders carry the MONDAY `_SHIP_` tag — building the tag from ship_date matches 0 orders). No `TR-` trays in a Tue cohort; cutoff Fri 3pm EST | 🔒✍️ | 🔴 **`weekly_flow.py --async-apply` on a Tuesday.** Three blockers: stage2 hardcodes `apply.py` (`weekly_flow.py:159`) so it routes MULTI-HUB instead of `dallas_only=True`; `apply_tuesday.py` has NO `--queue` (plain `sys.argv` `--apply` check, no argparse); and with no queue artifact, stage3's `VF_FROM_QUEUE=1` has nothing to read. Making Tuesday async = add `--queue` to `apply_tuesday.py` + a flow switch — NOT done as of 2026-08-09 |
| **🔴 GEOGRAPHY QC — run BEFORE the sheet goes out** (Dan's map, as a gate) | `_outputs/scripts/geography_qc.py <SHIP_TAG> --run <ts>` — for every NON-EXPRESS order, flags where the chosen hub is not the nearest SERVICEABLE hub, classifies each against the engine's own `lane_audit` (🔴 nearer+proven+faster-or-equal · UNMEASURED · OK-slower · OK-judged-bad) and prints the engine's `reasons` beside it so cap-spill/fences self-explain. Origin: Dan caught 110 wk0810 misroutes with map + hub annotation + express filter and no engine access; wk0810 12.2% flagged, wk0803 12.9% — a standing rate, not a new-hub artifact | ✅ | 🔴 Eyeballing a map after the sheet has SHIPPED (that is the wk0810 sequence — his 17:30 feedback never reached RMFG). Never assert a hub can serve a zip from DISTANCE alone: serviceability comes from the order's own `lane_audit`, and inventing one is the invented-route reject class. Never skip `--run`: a cohort is re-run 10–15× and the last record is often a weekend dry run |
| **🔴 PER-ORDER ROUTING EVIDENCE — Dan's "why doesn't this need express?" table** | `_outputs/scripts/routing_evidence.py <COHORT_TAG> [--decision-tag <ship_tag>] --run <ts> [--since YYYY-MM-DD] [--examples N]` — one xlsx (Summary tab first, Evidence second) with, per order: **zip5 lane history and zip3 history as SEPARATE columns** (zip3 labelled "the neighborhood, NOT this address"), lates as **"X of N arrived within 2 days"**, 3–5 **real order numbers that succeeded on that exact lane** with their transit days, **label quote (ShipStation rate cache, an ESTIMATE) beside historical INVOICED cost** with the delta, **`ShipStation quoted FedEx` and `ShipStation quoted UPS` as SEPARATE columns** (a lane is `(carrier, hub, dest zip5)` — side by side makes a same-road substitution visible), chosen hub/carrier/service, miles, the **live lever values** (`near_hub_premium_usd` etc., read from `lib/levers.py` at runtime + flag state) on the Summary tab, and a plain-English rationale carrying the numbers inline. Cohort membership is read **LIVE** (`box_simulation.fetch_all_orders(live=True)`, unfulfilled + open, never cancelled); `shipping.db` opened `mode=ro`; output versioned under `~/Downloads` and it REFUSES to overwrite | ✅ | 🔴 Hand-building another `Dan_ground_rationale_*.xlsx` one-off (three were built on 2026-08-11 alone). Never merge zip5 and zip3 into one "history" number. Never cite an AVERAGE transit — an average of 2.05 hides 3-day arrivals and was used once to justify keeping a box on ground. Never show a FedEx number on a UPS row, and never quote OnTrac/Veho at all (coverage files are their authority). Never omit `--run`: a cohort is re-run 10–15× and "last record per order" silently scores a later dry run. Never fill an empty cell with a plausible number — no history says so. Never hardcode the $3.00 distance premium — it is the `near_hub_premium_usd` LEVER and is read at runtime. 🔴 Never add a second reader of the decision log: this tool and `routing_map.py` both import **`_outputs/scripts/routing_run.py`** (one load, run pinning by run_id, all record kinds, the `fence`-is-not-a-carrier guard, applied-tag-wins lane identity, coverage accounting) so the map and the sheet cannot disagree about an order's lane |
| Full-cohort dry-run review sheet (no writes) | `scripts/full_cohort_dryrun.py --ship _SHIP_<date>` | 🔒 | per-order calls (miss Indy cap) |
| Indianapolis 6-pallet gate | `lib/engine._indy_pallet_gate` (runs inside `compute_routing`) | 🔒 | there is NO `pallet_gate.py` |
| **AUDIT the routing tags on a built/sent vF sheet** | `scripts/vf_tags.py validate <sheet.xlsx>` | ✅ | eyeballing col L; a grep for `_AHB!` (row presence in the OnTrac master is NOT a lane) |
| **EDIT routing tags on a vF sheet** (retag / bulk flip / revert / fence / unfence) | `scripts/vf_tags.py {retag,flip,revert,fence,unfence}` — dry-run default, `--write` emits `_r2.xlsx` | 🔒✍️ | 🔴 Excel find-and-replace (how 3 invented OnTrac lanes reached a SUBMITTED sheet), or a hand-rolled openpyxl script (mangles the workbook, drops ice tags, no ledger). Constraints SSOT: `AppyHour/VF_SHEET_RULES.md` §7 |
| **ROW/ITEM edits on a vF sheet post-lock** (per-order 1:1 item swap csv / shorts substitution / late add-order from live / drop / set-route / set-item / set-address / bulk retag csv) | `ShipRouting/scripts/vf_edit.py {swap,sub,add-order,drop,set-route,set-item,set-address,retag-csv} --sheet <vF.xlsx>` — in-place atomic write, jsonl ledger `_outputs/logs/vf_edit_<shipdate>.jsonl`, auto-runs presend_check | 🔒✍️ | 🔴 chat-driven scratch openpyxl one-shots (the wk0817 ~5h seam: check7_swap/add11/tray_swap/closer_hub/fix_totals — this replaces them); Excel hand-edits; rewriting bare `!ANY` rows in bulk; inventing an MFG header. Constraints SSOT: `ShipRouting/VF_EDIT_RULES.md`. Tag-only edits stay with `vf_tags.py` |
| **RUN EVERY STANDING CHECK on a vF sheet** (MFG headers, zip5 text, ice/gel, guides, totals, routing legality+coverage, live Shopify cross-check for cancelled/_HOLD/drift-in/SKU diff, name chars, dupe list — then presend_check) | `ShipRouting/scripts/vf_checks.py <vF.xlsx> [--fix] [--skip-shopify]` — report-only default; `--fix` = safe class only (zip/guides/totals/names); exit 1 on FLAG; report to `_outputs/reports/vf_checks_*.md`. Skill: `/vf-checks` | 🔒 (✍️ only with `--fix`) | 🔴 hand-rolled scratch check scripts (the wk0817 ~5h/~10-run seam: dan_checks/full_sweep/cancel_check — this replaces them); writing gel or routing tags from this path (ice → `ice_distvol_workflow --write`, tags → `vf_tags.py`); rewriting bare `!ANY` rows; "(Tray)" header-substring tray detection (24-vs-321 undercount). Constraints SSOT: `ShipRouting/VF_CHECKS_RULES.md` |
| **RESOLVE Matrixify import dupes** (detect live+in-sheet dupes on an add-line-item export, pick history-aware CH-/MT- replacements, emit corrected import CSV + decision log) | `AppyHour/scripts/resolve_import_dupes.py --export <csv> --ship-tag <TAG> [--warnings <txt>] [--apply]` — dry-run default; READ-ONLY vs Shopify, never overwrites the input | 🔒 | copying another dated `scripts/utilities/resolve_dupes_2026_*.py` one-shot (14 of those exist — this replaces them); fabricating a replacement SKU (pool = sheet only, else MISSING). Constraints SSOT: `AppyHour/scripts/RESOLVE_DUPES_RULES.md`; process: `matrixify-import-dupe-check` skill |
| **AUDIT the ITEM matrix on a built/sent vF sheet** (MFG names, header shape, duplicate items, Total drift) | `AppyHour/scripts/vf_items.py validate <sheet.xlsx>` | ✅ | eyeballing headers in Excel; `validate_vf_sheet.py` alone (it fails OPEN on a missing authority) |
| **EDIT the ITEM matrix on a vF sheet** (swath SKU swap / qty / add-drop-rename column / find-replace / gift rows / revert) | `AppyHour/scripts/vf_items.py {swap,qty,add-column,drop-column,rename-column,replace-name,gift,revert}` — dry-run default, `--write` emits `_r2.xlsx` | 🔒✍️ | 🔴 Excel hand-editing (how "Farmstead Smoked Cumin Gouda" was invented off a Shopify title 2026-08-04, and how a comma'd walnut header shipped to RMFG, 545 units). Never a hand-rolled openpyxl script (no authority check, no dupe guard, no ledger). Gift rows go through `merge_gift_xlsx` only. Constraints SSOT: `AppyHour/MATRIX_RULES.md` rule 24 |

> ✅ RESOLVED (2026-07-29): the **feasible-hub-fence/ice-floor** line (`lib/zone_floor.py`, `fedex_tnt.py`, `invariants.py`) was **ARCHIVED 2026-06-25** (`ShipRouting/_archive/shiprouting-feasible-hub-fence-2026-06/`); the static FedEx map's vouch role is replaced by live ShipStation committed quotes (`SHIPSTATION_LANE_VOUCH`, ROUTING_RULES §2 source 4). Do not port.

---

## 4. SKILLS (user-invocable — capability entry points)
| Capability | Skill | Disp |
|---|---|---|
| Build the weekly cut order | `/cut-order` | 🔒✍️ |
| Weekly RMFG cheese portion yield audit (oz/slice vs spec, over/under-portion) | `/rmfg-yield-audit` | 🔒✍️ |
| Weekly MT-FS bulk-meat throughput (demand = trays × spec oz, NOT inventory deltas) | `/mtfs-throughput` | 🔒✍️ |
| Automation health / dead-man-switch check (absent heartbeats, failed schtasks, stale ingest, db integrity — silent-green, Slack-on-red) | `scripts/automation_health.py --verbose` (daily task `automation-health-daily` 12:15pm; SSOT `HEARTBEAT_RULES.md`; beats via `appyhour_lib.heartbeat.beat`) | ✅ |
| Weekly routing loop-closure scorecard (reship-recovery check, Indy pins, MILP A/B, postmortem link — matured cohort) | `ShipRouting/scripts/loop_scorecard.py [_SHIP_tag]` (weekly task `loop-scorecard-weekly` Mon 1:15pm; read-only) | ✅ |
| Weekly corrections-mining digest (Kurt-corrections -> instinct CANDIDATES for confirmation; never auto-saved) | `_outputs/scripts/corrections_digest.py` (weekly task `corrections-mining-weekly` Sun 1:30pm; candidates -> `~/.claude/instincts/candidates/`) | ✅ |
| Execute a SKU swap on a cohort (audited, $0-variant) | `/swap OLD NEW SHIP_TAG` | 🔒✍️ |
| Build carrier-routing assignment sheet for a cohort | `ship-routing-assignment` | 🔒 |
| Daily Command Center brief | `/today` | 🔒 |
| Per-carrier failure rates for a ticket cohort | `ticket-carrier-analysis` | 🔒 |
| Order lookup by #/tag + SKU check | `shopify-order-lookup` | ✅ |
| SKU taxonomy / box / curation reference | `product-rules` (read before parsing SKUs) | ✅ |
| Recharge API rules (v2021-11, cursor pagination) | `recharge-api` (MANDATORY for Recharge code/tools) | 🔒 |
| NL→SQL shipping analytics (orders⨝deliveries⨝feedback⨝weather) | `appyhour-shipping-data` (MANDATORY for shipping Qs) | 🔒 |
| Shopify line-item handling rules | `shopify-line-items` / `shopify-api` | 🔒 |

## 5. SCHEDULED ROUTINES (run automatically; see `~/.claude/scheduled-tasks/`)
Registered via the scheduled-tasks MCP (NOT manually slash-invoked). Each: capability → routine.
| Routine | Cadence | What |
|---|---|---|
| `shipping-cost-sheet` | weekly Mon | refresh CEO shipping-cost sheet (IMAP→ingest→cost report --push) |
| `warm-cohort-report` | weekly Mon | arrived-warm report + Slack reconciliation |
| `gorgias-field-gate-daily` | daily | field-completion gate + Slack nudge to Jessa |
| `wrong-address-handler-daily` | daily | triage invalid_address + learned fixes (needs `scripts/automations/` — recovered) |
| `sku-lifecycle-scan-weekly` | weekly Mon | upcoming-charge lifecycle flags |
| `ops-issues-weekly-update` | Wed 4pm | sync+enrich Operational Issues sheet |
| `truffle-watch-christine-farley` | daily | auto-swap meat truffle off one customer + Slack Kurt |
| `forge-learn-weekly` · `observability-weekly` · `rulestore-dedup-audit` · `vault-bm25-refresh` | weekly/daily | the learning/observability layer |

---

## 6. Core lib canonicals (call THESE; never reimplement)
| Capability | Canonical owner | Call via |
|---|---|---|
| Shopify auth | `appyhour_lib/credentials.py` → `get_shopify_auth()` | import (MCP re-exports) — never hand-built `X-Shopify-Access-Token` |
| Recharge client | `cut_order_server/app/recharge_client.py` | v2021-11 + cursor pagination — never ad-hoc requests |
| Weather + NWS | `appyhour_lib/weather.py` | import — never new OWM/NWS callers |
| Box / SKU classify | `appyhour_lib/box_classify.py`, `internal_classify.py` | import — never inline prefix regex (use `product-rules`) |
| Paths / app dirs | `appyhour_lib/paths.py` | import — never hardcoded `%APPDATA%` |
| User uploads | `appyhour_lib/user_data.save_user_file()` | never write to `.claude/` |
| Gel-pack lookup keys | `OPENWEATHER_API_KEY` (env) read by `appyhour_lib/credentials.get_openweather_key()` | — |

`appyhour_lib/` (credentials, weather, paths, box_classify, internal_classify, user_data, notify) = pure-util single source. Every consumer imports from it; never copy a util into an app.

---

## 7. Canon-vs-dup decisions (2026-06-14 sweep) + drift
Archive snapshot: `Claude Projects/_archive/scripts-canon-snapshot-2026-06-14/` (41 scripts). Newest ≠ strictly better — preserve flags first.

| Capability | CANONICAL | Superseded | Preserve before retiring |
|---|---|---|---|
| Recharge repeat-order dup-verdict | `Errors/check_repeat_subs_v4.py` | subs/v2/v3/class/class_v2 | re-add v3 CSV-cohort input + MONG/SS double-sub + class_v2 box_contents qty-parse |
| Cut-order xlsx | `build_cut_order_xlsx_v2.py` | v1 | v1 held two-week (WK2) logic |
| Double-refund | `detect_double_refunds_v2.py` | v1 | v1 had urllib3 Retry/HTTPAdapter |
| RC shortage fix | `fix_rc_shortages_v2.py` | v1 | v1 docstring per-SKU action map |
| One-shot SKU swaps (×28) | `/swap` + MCP swap tools; conditional → `shopify_swap.execute_conditional_swap` | 26 of 28 archived | protected-swap NOT built (premise false) |

**Known drift (opportunistic migration):** several `Errors/check_*.py` make ad-hoc Recharge calls (should use `recharge_client.py`). Rule: migrate a flagged file when you touch it AND its API version already matches canonical (else preserve its version).

🔴 **Security receipt:** 5 scripts had a hardcoded committed Recharge **write** token → scrubbed to `settings["recharge_api_token_write"]` (gitignored), commit `b4cad88`, rotated + tested 2026-06-14.

## 8. Maintenance
- Tool swapped/retired → update its row + log `~/.knowledge/decisions/deprecations.md`.
- New canonical → add a row here + the matching trigger in `~/.claude/instincts/canonical-tools.md`.
- New billing tools (`ingest_downloads.py` live, `portal_pull.py` parked) added 2026-06-25 from this-week's work.

## 9. Routing-run reader location (2026-08-11)

`ShipRouting/lib/routing_run.py` is the canonical implementation so the same reader ships in the
Digital Ocean image. `_outputs/scripts/routing_run.py` is a compatibility re-export for the existing
local evidence and map tools. Never put parsing logic in the shim and never add another decision-log
reader.
