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
| **ADD free ($0) variant(s)** to orders (pure add, idempotent; tag/box/explicit-order targeting) | `appyhour_add_order_variants` (dry_run default True) | 🔒✍️ |
| **Add/remove tags** on ONE order (gel/routing/hold) | `appyhour_update_order_tags` | 🔒✍️ |

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
| Double-refund detection | `InventoryReorder/Errors/detect_double_refunds_v2.py` | ✅ | v1 `detect_double_refunds.py` |

> `InventoryReorder/Errors/` (~120 files: `check_*`/`fix_*`/`swap_*`/`marc_*`) are overwhelmingly **one-shot dated remediations — NOT recurring tools.** Only `sku_lifecycle_scan.py` + `detect_double_refunds_v2.py` recur.

---

## 3. SHIPROUTING (build → engine → apply)
Canonical pipeline: `build.py` (I/O driver) → `lib/engine.compute_routing` (the shared brain, also used by Kori) → `apply.py`/`apply_tuesday.py` (Shopify writes). Cohort `TAG` is a top-of-file constant.

| Capability | Tool | Disp | Never instead |
|---|---|---|---|
| Build routing assignment/cohort (→ tab1-5 JSON) | `build.py` (set TAG; opt `INCLUDE_RECHARGE=1`) | 🔒 | `_archive/routing_test_*.py` (stale) |
| Apply routing tags — main/Monday cohort | `apply.py` (dry-run; `--apply`) | 🔒✍️ | **Kori Apply button** (ignores engine) |
| Apply — Tuesday Dallas-only (max-gel + tag check) | `apply_tuesday.py` (dry-run; `--apply`) | 🔒✍️ | `apply.py` (lacks dallas_only) / Kori button |
| Full-cohort dry-run review sheet (no writes) | `scripts/full_cohort_dryrun.py --ship _SHIP_<date>` | 🔒 | per-order calls (miss Indy cap) |
| Indianapolis 6-pallet gate | `lib/engine._indy_pallet_gate` (runs inside `compute_routing`) | 🔒 | there is NO `pallet_gate.py` |

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
