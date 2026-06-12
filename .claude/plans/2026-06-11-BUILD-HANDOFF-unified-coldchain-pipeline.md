# BUILD HANDOFF — Unified Cold-Chain Data + Cost-Aware Routing Pipeline

**Date:** 2026-06-11 · Owner: Kurt (Head of Ops, AppyHour) · Status: scoped, ready to build
**Companion docs:** `2026-06-11-EPIC-unified-coldchain-pipeline.md` (milestones) · `2026-06-11-unified-pipeline-gap-analysis.md` (target-vs-reality) · `ShipRouting/ENGINE_GUIDE.md` (routing brain)

> Read this cold and you have everything: the goal, every dataset we hold, where it lives, how it joins, what's broken, and the build order.

---

## 0. NORTH STAR (the whole point)
Complete order data → **Kori assigns the right ICE + the right CARRIER/HUB/LANE** so each order **delivers on-time, in good condition, without blowing up cost.**
Carrier cost preference (cheapest-first, always within **TNT ≤ 2 business days**):
**Veho ($6.18) → OnTrac ($7.81) → FedEx Home Delivery ($15) / UPS Ground ($11) → LAST: FedEx 2Day Express ($25).**
Open-loop today: cost is reported after the fact, not enforced in the assignment. Closing this loop is the epic.

---

## 1. CANONICAL STORE — read this first
**ONE database is authoritative: `%APPDATA%/AppyHour/shipping.db`** (~109 MB).
- Resolved by `appyhour_lib/paths.py` → `db_path()`. **Every script MUST import from there. No hardcoded paths.**
- Kori (`GelPackCalculator/kori/gel_pack_webview.py`), the analysis skill (`appyhour-shipping-data`), and `sync_all_carriers.py` all read/write THIS db.
- Read-only query tool: `~/.claude/skills/appyhour-shipping-data/query.py --sql "..."` (run with `/c/Users/Work/anaconda3/python.exe`).

🔴 **DUPLICATE TO RETIRE: `ShippingReports/output/shipments.db`** (~20 MB, shipments-only). Built by `ingest_all.py`/`build_db.py`; **Kori never reads it**; ~15k rows divergent. `paths.py` already marks it DEPRECATED. M1 retires it. The 0-byte `GelPackCalculator/shipping.db` stub is already gone.

⚠️ **Two invoice pipelines exist** — use the right one:
- ✅ `GelPackCalculator/sync_all_carriers.py` → imports Gmail invoices INTO `%APPDATA%` + cohort backfill. **THE keeper.**
- ❌ `ShippingReports/ingest_all.py` → builds `output/shipments.db` (dead artifact). Don't use for live data.

---

## 2. DATA MAP — every table in the canonical DB

| Table | Rows | What it is | Source | Key columns | Freshness |
|---|---|---|---|---|---|
| **fulfillments** | 93,107 | One row per shipped order-line. The **cohort authority** (`tags` holds `_SHIP_<Mon>` + `RMFG_<YYYYMMDD>`). | Shopify sync | order_number, order_id, **tags**, tracking_number, tracking_company, fulfilled_at, dest_state, dest_zip, ship_week | live, complete to last week |
| **tracking_order_link** | 91,093 | 🔑 **THE POINTER.** tracking → order#. Backbone of attribution. | derived from fulfillments | tracking (PK), order_number, src | live |
| **shipments** | 71,149 | One row per invoice line item (carrier cost). | carrier invoices (Gmail) | invoice_id, **tracking**, carrier, **service**, hub, state, zip_code, zone, **cost**, weight, ship_date, delivery_date, box_type, **cohort_key**⚠, subcohort, acct, **is_internal**, cohort_ab | FedEx ~3wk lag; Veho/OnTrac fast |
| **delivery_status** | 93,186 | Final-mile tracking (ParcelPanel). | ParcelPanel API | tracking_number, carrier, status, **pickup_date**, **delivery_date**, transit_days, last_event, order_number, origin_hub | live; LaserShip/OnTrac final-scan gaps |
| **feedback** | 2,902 | Gorgias CS issues (Arrived Warm, Delayed, Lost…). | Gorgias sync | order_number, **issue_type**, date_reported, carrier, state, resolution, gorgias_link, raw_issue_type | ~25-60% capture gap (improving) |
| **shopify_orders** | 32,057 | Order header (price, status, tags). | Shopify | order_name, ship_tag, fulfillment_status, financial_status, customer_email, ship_state, ship_zip, total_price, tags_csv | cache starts Dec-2025 |
| **kori_snapshots** | 6 | Per-cohort Lock&Ship RECORD (forecast + box config). | Kori | snapshot_id, ship_week, **ship_tag**, locked_at, target_temp_f, hub_temp_f, **fulfilled_at**(=canonical), box_settings_json, **gels_applied_at, gels_stale_hours, forecast_drift_count** | per ship-week |
| **kori_snapshot_orders** | 8,046 | Per-order Kori prediction at lock. | Kori | order_number, state, **predicted_config**, predicted_packs_48/24, **predicted_risk**, dest_peak_temp_f, total_q_safe, effective_btu, **margin_btu**, transit_type, origin_state/zip | per snapshot |
| **gel_apply_log** | 2,918 | When ice tags were applied + margin at apply (forecast-drift audit). | Kori Apply-Gels | ship_tag, order_number, applied_at, applied_config, apply_margin_btu, apply_peak_temp_f, forecast_hash | live (already built) |
| **invoices** | 214 | Invoice-email metadata (balance, msg id). | Gmail | carrier, invoice_week, total_balance, email_msg_id, email_date, source, filename | live |
| **weather_history** | 724 | Per-zip daily temps. | weather pull | zip_prefix(=5-digit!), date, avg_temp, peak_temp, lat, lon | 🔴 ACTUALS NOT synced for recent dates |

---

## 3. THE ATTRIBUTION CHAIN (how a cost line → its cohort) 🔑
```
shipments.tracking
  → tracking_order_link.tracking → .order_number
  → fulfillments.order_number → fulfillments.tags
      → _SHIP_<Mon>      = COHORT (full week)
      → RMFG_<YYYYMMDD>  = SUB-COHORT (A=Fri/Sat→Mon ship · B=Tue/Wed→Tue ship, Dallas-only no-Veho)
```
🔴 **NEVER use `shipments.cohort_key`** for per-cohort numbers — it buckets by raw `ship_date`, and FedEx weekly invoices straddle the Mon boundary → garbage (saw 05-04=56%, 05-11=152%). The pointer chain gives the truth (05-04=100%, 05-11=100%). **M1 backfills `cohort_key` FROM this chain so the column becomes trustworthy.**

Other canonical joins:
- order → CS issue: `feedback.order_number = fulfillments.order_number` (strip `#`, drop 5-digit pre-cache <103873).
- order → delivery timing: `delivery_status.order_number` (pickup→delivery = real TNT, business days).
- order → Kori plan: `kori_snapshot_orders.order_number` pinned to the canonical snapshot (`kori_snapshots.fulfilled_at IS NOT NULL`).
- order → weather actual: `weather_history.zip_prefix = substr(dest_zip,1,5)` (LEFT JOIN; sparse).

---

## 4. SOURCES → TABLES (the 6 feeds + how)
| Source | Brings | Lands in | Script |
|---|---|---|---|
| **Email (Gmail)** RMFG `accounting@robbinsmfginc.com` + FedEx/UPS account | invoices (cost, service, hub, dims) | `shipments` + `invoices` | `sync_all_carriers.py` (canonical) / `download_{fedex,ontrac,veho}_imap.py` |
| **FedEx + UPS invoice download** (account portals) | same, account-direct | `shipments` | parsers `parsers/{fedex,fedex_csv,ups,ontrac,veho}.py` |
| **Shopify** | order#, tracking, _SHIP/RMFG tags, dest | `fulfillments`, `shopify_orders`, feeds `tracking_order_link` | `sync_shopify_orders.py` |
| **ParcelPanel** | pickup/delivery dates, status | `delivery_status` | `pull_parcelpanel.py` |
| **Gorgias** | CS issues (warm/delayed/lost) | `feedback` | `gorgias_sheets_sync.py` |
| **17track** (sometimes, manual) | ground-truth delivery vs PP lag | (manual) | browser batch ≤40, `fc=` locked (FedEx=100003) |

---

## 5. THE DECISION COMPONENTS
- **ShipRouting `lib/engine.py compute_routing()`** — the cost-aware ROUTING brain. One function, imported by `ShipRouting/build.py` (assignment sheet) AND Kori (`compute_v2_routing`). **SHADOW mode** (writes tags, apply-gated). Encodes the North Star: partition lanes GOOD(eff-TNT≤2)/BAD, fence BAD with `!NO`, positively tag only Veho/OnTrac, Express-Dallas floor when no good lane. Ice = physics-first (`calibrated_ice`), history only upgrades. See ENGINE_GUIDE §2-3.
- **Kori** (`GelPackCalculator/kori/gel_pack_webview.py`) — assigns ice config (`recommend_config` + `min_gel_states` + zip overrides) and routing; Lock&Ship RECORDS to `kori_snapshots`. Reads `box_settings_json` (13x10x10, 1.5" foam).
- **Apply-Gels gate** (partly built — `gel_apply_log` + `gels_*` cols exist) — records apply time/margin; lock-time forecast-drift notify.

---

## 6. BUILD SCOPE — milestones (full detail in EPIC doc)
- **M1 — One source of truth ⭐FIRST:** retire `output/shipments.db`; **backfill `cohort_key` from `tracking_order_link`**; ingest the 5 attached invoices + email sweep; log coverage% per source×cohort.
- **M2 — Complete ingest:** one orchestrated idempotent run over all 6 sources → APPDATA; **weather ACTUALS nightly** (Open-Meteo archive); wire 17track ground-truth. (UPS `C411H4` parser already exists.)
- **M3 — Weekly post-mortem:** last-week Warm+Delayed delivery+Gorgias **vs snapshot** (`cohort_health.py`, `routing_postmortem.py`, warm-forecast-vs-actual) folded into the existing Wed recurring task.
- **M4 — Ice ENFORCEMENT (net-new capability):** (a) **box-upgrade** for negative-margin orders (bigger box → fits more ice); (b) **reship gel** add 2×48oz to reships that fit (~7 refrigerated items in small box); (c) **solve Shopify duplicate-tag limit** — xlsx takes dup tags, Shopify rejects → need quantity-encoded single tag (e.g. `!ExtraGel48oz_x2!`) or box-size tag the packer reads; (d) wire to lock-gate: neg-margin → upgrade or HOLD.
- **M5 — Cost routing GO-LIVE:** flip ShipRouting SHADOW→APPLY. **Fixed_Route locks are honored absolutely (see §7.11) — never cost/TNT-override a customer's carrier preference.** Blockers: **manually** resolve **Bree Hrechka (MD) + PAM DEMORE (FL)** `!NO FedEx`-with-no-≤2-lane (Express reship / accept slower / contact cust — NOT force back to FedEx); 6/15 cohort rebuild on commit `03609c1`; `apply.py` dry-run → `--apply` → Kori restart (`run_webview.bat`). Add lock guardrail: flag FedEx>55% or Express>~11%. **Add-on:** capture a reason note per Fixed_Route so locks are auditable.

Sequence: M1 unblocks all. M2+M3 parallel after M1. M4 net-new. M5 mostly go-live ops.

---

## 7. KNOWN ISSUES / COURSE CORRECTIONS (don't repeat — from ENGINE_GUIDE §8 + this session)
1. **Cohort = `_SHIP_<Mon>` tag, never carrier pickup/scan date.** Sub-cohort A/B = `RMFG_` tag, never scan weekday.
2. **ParcelPanel lags 1-3 days** — PP "undelivered" ≠ late; ground-truth via 17track.
3. **On-time = delivered≤2 / FULL cohort**, not delivered-only (fake 100%).
4. **OnTrac ≡ LaserShip** — `normalize_carrier` collapses them.
5. **Invoices straddle cohort boundary** → use the pointer, not cohort_key.
6. **5-digit feedback order#s** = real 2024-25 orders pre-cache → drop from current-week (<103873).
7. **`is_internal=1`** = staff/test (82 rows) → exclude from customer cost/rate.
8. **FedEx cost MUST be split by service** — 2Day Express $25 ≠ Ground/Home Delivery $15 (combine HD+Ground). Blended FedEx avg lies.
9. **Risk-label bug:** Python `analyze_order` rates margin<0 as MEDIUM; JS already correct (margin<0→HIGH). Port JS formula. (audit: `_outputs/artifacts/2026-06-04-kori-3critic-audit.md`)
10. **TR- tray boxes:** ice out of scope (fixed 2×96oz), routing in scope.
11. **Fixed_Route is SACRED — honor it absolutely.** Fixed_Route customers carry an **anecdotal reason** behind the lock (a past complaint, a stated carrier preference, a delivery-access quirk). The engine must **never** override a Fixed_Route for cost or TNT — it is the highest-priority branch (`choose_lane` priority: Fixed_Route → reship → force_2day → choose_lane). When honoring a `!NO <carrier>` lock leaves **no ≤2 lane** (e.g. Bree Hrechka MD, PAM DEMORE FL → `!NO FedEx` with no non-FedEx ≤2 option), the resolution is a **manual ops decision** (Express reship, or accept the slower lane, or contact the customer) — **NOT** auto-routing them back onto the carrier they asked us to avoid. This is why those two **block `--apply`** until decided. Each Fixed_Route should ideally carry its reason note so the lock is auditable.

---

## 8. ATTACHED INVOICES (ingest in M1) + IMMEDIATE ACTIONS
Files in `C:\Users\Work\Downloads\`:
- `FedEx_invoice_2026-06-11_15_59.XLSX` (FedEx account)
- `Invoice_000000C411H4206_051626.csv`, `...216_052326.csv`, `...226_053026.csv`, `...236_060626.csv` (UPS account `C411H4` — order#, 1Z tracking, cost, service, hub Dallas_AHB, dims 13x10x10). Extend UPS past current 05-26.

**First 3 M1 actions (safe, high-value):**
1. Ingest the 5 attached into **APPDATA** via `sync_all_carriers` path (UPS parser `parsers/ups.py` ready) — NOT `ingest_all`.
2. `UPDATE shipments SET cohort_key` from `tracking_order_link → fulfillments._SHIP_` (one statement) → fixes 05-04→100% + every report after.
3. Confirm `sync_all_carriers` covers all 4 carriers; if UPS/Veho not wired there, that's the M2 orchestration gap.

---

## 9. PATH INDEX
- Canonical DB: `%APPDATA%/AppyHour/shipping.db` · helper: `AppyHour/appyhour_lib/paths.py::db_path()`
- Invoice importer (live): `AppyHour/GelPackCalculator/sync_all_carriers.py`
- Parsers: `AppyHour/ShippingReports/parsers/{fedex,fedex_csv,ups,ontrac,veho}.py`
- Kori: `AppyHour/GelPackCalculator/kori/gel_pack_webview.py` · launch `run_webview.bat`
- Routing engine: `ShipRouting/lib/engine.py` · guide `ShipRouting/ENGINE_GUIDE.md` · health `ShipRouting/cohort_health.py` · postmortem `ShipRouting/routing_postmortem.py`
- Weekly report: `~/.claude/skills/appyhour-shipping-data/queries/weekly_carrier_report.py --auto`
- Query tool: `~/.claude/skills/appyhour-shipping-data/query.py`
- Memory: `feedback_tracking_order_link_pointer.md`, `feedback_ship_tag_cohort_pairing.md`, `feedback_5digit_orders_are_old_shopify.md`
- Vault: `~/.knowledge/codebase/{shipping-db-schema.md, ShipRouting Expected-Cost Engine.md, Wallet-share + carrier analytics pipeline.md}`
