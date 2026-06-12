# MASTER HANDOFF v2 — Cold-Chain Pipeline Refactor (POST-PHASE-0)

**Date:** 2026-06-11 · Owner: Kurt (Head of Ops, AppyHour) · **Supersedes** `2026-06-11-MASTER-HANDOFF-coldchain-refactor.md` (v1).
**Status:** Phase 0 (parallel audit) COMPLETE · contract ❄️ FROZEN · **build = HELD pending Kurt's review/go.**
**Model:** Claude (Opus, orchestrator + decision layer) ∥ Codex (`gpt-5.5`, ingest spine). Cross-critique model.

**Read order:** this file → the 3 detail docs below → `SHIPPING_PIPELINE.md` (system-of-record) → `ShipRouting/ENGINE_GUIDE.md` (routing brain) → build.
**Detail docs (don't re-derive — produced this session):**
- `_outputs/reports/2026-06-11-codex-ingest-audit.md` — M1 ingest reality (overlap matrix, dead-DB diff, retirement list, parser sets).
- `_outputs/reports/2026-06-11-claude-decision-layer-audit.md` — M3/M4/M5 reality (engine maturity, ice enforcement gaps, safety bug).
- `.claude/plans/2026-06-11-ORCHESTRATION-claude-codex-coldchain.md` — the Claude∥Codex split + ❄️ FROZEN CONTRACT (build against this).

---

## 0. MISSION (North Star — unchanged from v1)
Complete order data → **Kori assigns the right ICE + the right CARRIER/HUB/LANE** → every box arrives **on-time, cold, cost-controlled.** Carrier cost order within TNT≤2: **Veho $6 → OnTrac $8 → FedEx HD $15 / UPS Ground $11 → LAST FedEx 2Day Express $25.** Cost is a routing INPUT, not a post-hoc report.

**Refactor goal:** ONE database · ONE importer (parsing) · ONE parser set · ONE attribution convention · routing engine LIVE. Collapse 3 repos' overlap into a clean layered pipeline.

---

## 1. ⭐ WHAT PHASE 0 CHANGED (the v1 framing was directionally right but off on scope)
Two independent audits (Claude=decision layer, Codex=ingest spine) cross-critiqued. Verdict: **convergent + complementary.** Key corrections to v1:

1. **M5 decision layer is ~90% BUILT, not greenfield.** v1 framed M5 as "make cost a routing input." It already is — `ShipRouting/lib/optimizer.py::choose_lane()` is a mature expected-cost survivor-invariant chooser (Veho<OnTrac<UPS<FedEx tiebreak, P95 tail-insurance ice, recency + 90d-delay gates, Indy pallet gate). Runs LIVE in Kori as SHADOW. **→ M5 = go-live GATING + validation, not a build.**

2. **M1 is subtler than "auto_import absorbs the 10."** `auto_import.py` is the carrier-file **PARSING** spine, **not an operational superset.** It does NOT: download (IMAP/Gmail/Drive), refresh Shopify fulfillments / ParcelPanel, run cohort backfill, classify `box_type`, parse the legacy "other data" workbooks. **→ Real M1 = consolidate PARSING into auto_import + keep a thin SCHEDULER layer** (downloaders + delivery-status + weather + feedback + postmortem). Don't build a megascript.

3. **Dead DB is migration-SAFE.** `ShippingReports/output/shipments.db` (55,935 rows) — **0 dead-only shipment tracking rows**; every dead `tracking` ∈ canonical. Retirement is blocked only by (a) ShippingReports readers still pointing at it (`build_wallet_share`, `enrich_*`, `build_db`) and (b) the **Veho parser** (ShippingReports-only, imported by `auto_import.py:221-237`) — must be ported into APPDATA first.

4. **M4 ice enforcement is the REAL net-new work.** Kori has the parts (gel tags, `gel_apply_log`, drift audit) but the gate is unenforceable: Shopify rejects duplicate tags so 2×48oz can't be expressed (need `!ExtraGel48oz_x2!`), no box-upgrade for neg-margin, no lock-gate. **Plus a safety bug:** `outside_temp=None` at lock → risk misrated MEDIUM (should be HIGH/CRITICAL) — `gel_pack_webview.py:160`, risk_order map `:855`. Fix first (cheap, high-value).

5. **M3 post-mortem = wiring, not build.** `routing_postmortem.py` + `cohort_health.py` exist and are correctness-fixed (on-time = delivered≤2 ÷ FULL cohort, OnTrac≡LaserShip normalized). Just wire into the recurring Wed task.

6. **Cross-repo reality:** ShipRouting is now its OWN repo at `C:\Users\Work\Claude Projects\ShipRouting` (detached commit `d9ca94a`). Codex's audit looked under `AppyHour\ShipRouting` (gone) and flagged it — my audit covers it. `kori/routing_v2.py:19-24` hard-codes both repo paths on sys.path + imports `box_simulation`. **Any AppyHour dir move breaks Kori's engine bridge.**

---

## 2. ❄️ FROZEN CONTRACT (the integration seam — full detail in ORCHESTRATION doc §"The Contract")
Both audits cross-confirmed. Read-only; any change = edit the doc + notify other agent + re-validate.
- **DB:** `appyhour_lib/paths.py::db_path()` → `%APPDATA%/AppyHour/shipping.db`. Snapshot taken: `…/AppyHour/backups/shipping.snapshot-2026-06-11.db` (110 MB, parity-verified).
- **Row baseline (2026-06-11):** shipments 72,779 · delivery_status 93,186 · fulfillments 93,107 · tracking_order_link 91,093 · shopify_orders 32,057 · weather_history 724 · gel_apply_log 2,918 · invoices 219.
- **Preserve EXACT column names** the engine/Kori read: `shipments.{carrier,service,hub,state,zip_code,city,zone,cost,weight,ship_date,delivery_date,transit_days,invoice_id,box_type,cohort_key,subcohort,acct}` (⚠️ stays `zip_code`, never dead-DB `zip`) · `weather_history.{zip_prefix(=5-digit),peak_temp,avg_temp,date}` · `delivery_status.{tracking_number,carrier,status,pickup_date,delivery_date,transit_days,last_event,service,order_number}` · `gel_apply_log` full schema exact.
- **Writers (Codex owns guts):** `store_invoice` (by id), `store_shipments` (by tracking; on-conflict updates cost/delivery_date/transit_days/hub/box_type only), `store_fulfillments`/`store_delivery_status` (by tracking_number), `parse_{fedex_xlsx,fedex_csv,ontrac_csv,ups_csv}_bytes`.
- **Dedup `(invoice_id, tracking)` at the cohort_key ROLLUP, not storage.** Storage = one physical row per `tracking`. Any drop beyond duplicate `(invoice_id,tracking)` changes per-`(carrier,hub,zip)` eff-TNT → breaks routing.

---

## 3. THE EPIC (milestones — REVISED with Phase 0 reality)
Sequence: **M1 unblocks all.** M3/M4 build against the frozen schema in parallel; M5 apply is the final gate.

- **M1 — ONE source of truth ⭐ (Codex):** consolidate carrier-file PARSING into `auto_import`; keep a thin scheduler layer for downloaders/delivery/weather/feedback/postmortem; port Veho parser into APPDATA; retire `output/shipments.db` + ShippingReports ingest (`ingest_all/ingest/build_db/merge_jsons/enrich_*`) AFTER repointing `build_wallet_share` + remaining readers; **add `shipment_dims` side table** (see §4 DIM decision); enforce `(invoice_id,tracking)` rollup dedup; coverage-% logging. Run against a DB COPY → row-parity validate → cut over.
- **M2 — Complete ingest (Codex):** one orchestrated run (all 6 sources), weather actuals nightly, 17track wired, OnTrac/UPS IMAP folded in; clear the 900-unknown + 1 parse_error triage.
- **M3 — Weekly post-mortem (Claude):** wire `routing_postmortem.py`/`cohort_health.py` into the recurring Wed task (`AppyHourMCP` Wednesday Ops task exists).
- **M4 — Ice enforcement, net-new (Claude):** fix the risk-label safety bug FIRST; then `!ExtraGel48oz_x2!` dup-tag vocabulary + RMFG recipe, box-upgrade for neg-margin (`calibrated_ice` `exceeded`), reship 2×48oz, lock-gate (drift audit detects but doesn't block).
- **M5 — Routing GO-LIVE (Claude):** engine SHADOW→APPLY. Gates: resolve **B1** (does RMFG honor `!NO FedEx/UPS` fences?), shadow-validation (did actual RMFG picks land on survivors? — needs M1-clean `shipments`), resolve **Bree MD + Pam FL**, 6/15 cohort rebuild + validate (blanks≈0, Veho fenced AZ/CO/FL, Express ~11%), `upload_cohort` → `apply.py` dry-run → `--apply` → Kori restart.

---

## 4. OPEN DECISIONS (Kurt-owned)
- **DIM fields → ✅ RESOLVED (Kurt 2026-06-11): preserve via the POINTER pattern, NOT by widening `shipments`.** New side table `shipment_dims(tracking PK → shipments.tracking, actual_weight, dim_l, dim_w, dim_h, dim_factor)`. ⚡ `order_id/order_name/ship_tag` are NOT stored — already reachable via `tracking_order_link → fulfillments`. ADD a table, don't ALTER `shipments` → zero breaking change.
- **B1 (gates M5 apply, not M1):** does RMFG honor `!NO <FedEx|UPS> - <hub>_AHB!` fences, or only OnTrac/Veho? The whole survivor-invariant rests on it. ASK RMFG.
- **Bree Hrechka (MD) + Pam Demore (FL)** Fixed_Route, FedEx-excluded, no non-FedEx ≤2 lane → `choose_lane` returns `kind:"manual_review"`. Needs a human decision; blocks `--apply`.
- **Doc-rot:** `AppyHourShippingMCP/CLAUDE.md:39` still calls the dead DB canonical → fix during M1.
- **B-INGEST-1 (verified M1 blocker, not a decision):** `store_shipments` `ON CONFLICT(tracking)` refreshes only `cost/delivery_date/transit_days/hub/box_type` — NOT `state/zip_code/zone/service`. The engine routes on `state/zip_code/zone` → a bad first insert is frozen, re-imports can't heal it. Fix in M1 (expand on-conflict SET, COALESCE-guarded). `shipping_invoice_db.py:940-945`.
- **Veho weekly TNT (✅ 6.9.2026 file analyzed, design LEFT OPEN):** see `_outputs/reports/2026-06-11-veho-groundplussuite-analysis.md`. Key: ⚠️ **the file FORMAT changed — `load_veho()` breaks on it** (new multi-sheet `GroundPlusSuite`; reads disclaimer → 0 zips → all Veho lanes fenced) → **M1 needs a new Veho parser; don't drop this file into the stable home yet.** Tier = **Ground Plus Zero** (100% match). Veho lanes = **IN+TN only, NO new hubs** ("Inland Empire"=renamed "LA"). Precedence: Veho `shipments` by hub = Indy 2,716 · Nash 2,561 · **Dallas 493 · Anaheim 38** → TX-Veho has precedent (open Kurt call, needs viability audit), CA stays out. Churn: 1,345 active-flips, 146 new ≤2 Veho lanes (the provisional-trust population), 194 "Pending-July" zips. Engine-policy (provisional-trust: shadow-first rec) LEFT OPEN.

---

## 5. ORCHESTRATION — Claude ∥ Codex (Kurt's model: independent + reconvene-and-critique)
- **Codex owns INGEST** (M1/M2): `GelPackCalculator/*.py` importers + `ShippingReports/`. **Claude owns DECISION** (M3/M4/M5): `ShipRouting/` + `kori/` + skills.
- Each produces independently; each then **adversarially critiques the other's output** at every gate. Disagreements surface to Kurt, not silently merged. Claude verifies Codex's claims mechanically (`ls`, row-counts) — Phase 0 proved this necessary (Codex's wrapper twice falsely reported success on failed runs).
- Safety rails: refactor against the DB COPY → row-parity → cut over; no importer retired until cron-audit clears it (`sync_logon.py` = logon-task `appyhour_sync_on_logon`, orchestrates sync_all_carriers+backfill_sync+auto_import — cannot silently retire); engine stays SHADOW till explicit M5 flip; worktrees + branches per agent.
- **Codex tooling state:** `~/.codex/config.toml` fixed this session — removed unsupported `service_tier="flex"`, pinned `model="gpt-5.5"` (ChatGPT account can't use `gpt-5.3-codex`). Codex now runs clean.

---

## 6. RULES (must hold — from v1, unchanged)
Cohort = `_SHIP_<Mon>` tag (never scan date). Sub-cohort A (Fri/Sat→Mon, multi-hub, incl Veho) vs B (Tue/Wed→Tue, Dallas-only, no Veho; engine doesn't run for B). **Fixed_Route = sacred** (never cost/TNT-override). **Fulfillment ≠ shipping** (`Order::%` ≠ `Shipping::%`). Ice physics-first, history upgrades only. ParcelPanel lags → 17track verify. OnTrac≡LaserShip. `is_internal=1` excluded. FedEx cost by service (2Day≠Ground/HD). Shopify rejects duplicate tags (the M4 dup-tag blocker).

---

## 7. CURRENT STATE (2026-06-11)
- Phase 0 done; both audits written; contract frozen; DB snapshotted. **No code written, no DB writes, no Shopify changes.**
- Ingested (per v1): FedEx & UPS to ship-date 06-02, OnTrac 05-28 (6/1 in email, needs pull), Veho 06-05. Shopify 8 cohorts (19,481). Gorgias current.
- Open from v1 still live: OnTrac 6/1 pull; 900-unknown triage; cohort_key dedup; M1–M5.
- **Build is HELD** — Kurt reviewing the 3 detail docs. On go: Codex→M1 (against copy) ∥ Claude→M4 (risk-label bug first) + M3.

---

## 8. PATH INDEX
DB `%APPDATA%/AppyHour/shipping.db` · snapshot `…/AppyHour/backups/shipping.snapshot-2026-06-11.db` · helper `AppyHour/appyhour_lib/paths.py` · importer `GelPackCalculator/auto_import.py` · writer `GelPackCalculator/shipping_invoice_db.py` · Kori `GelPackCalculator/kori/{gel_pack_webview.py,routing_v2.py}` (`run_webview.bat`) · engine `ShipRouting/lib/{engine,optimizer}.py` (+`ENGINE_GUIDE.md`) · scheduler `GelPackCalculator/sync_logon.py` · Veho parser `ShippingReports/parsers/veho.py` (port target) · weekly report `~/.claude/skills/appyhour-shipping-data/queries/weekly_carrier_report.py` · vault `~/.knowledge/ops/Shipping Data Pipeline.md` · memory `feedback_tracking_order_link_pointer.md`, `feedback_shipping_pipeline_system_of_record.md`.

---
*Fresh agent: read this → the 3 detail docs → confirm Kurt's build-go + B1/Bree/Pam. M1 (Codex) is the foundation; M3/M4 (Claude) build against the frozen schema in parallel; M5 apply is the final gate.*
