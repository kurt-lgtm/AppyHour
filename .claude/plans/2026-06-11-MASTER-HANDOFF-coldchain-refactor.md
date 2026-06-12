> ⛔ **SUPERSEDED 2026-06-11 by `2026-06-11-MASTER-HANDOFF-v2-coldchain-POST-PHASE0.md`.** Phase 0 audits corrected this file's scope (M5 mostly-built, M1 subtler, dead-DB safe). Read v2 first; this v1 is kept for the deep component inventory only.

# MASTER HANDOFF — Cold-Chain Pipeline (fresh epic: brainstorm → plan → execute)

**Date:** 2026-06-11 · Owner: Kurt (Head of Ops, AppyHour) · **This is the single entry point for a fresh `/forge epic` session. Expect a BIG REFACTOR.**

**Read order:** this file → `SHIPPING_PIPELINE.md` (system-of-record, plain-English) → `ShipRouting/ENGINE_GUIDE.md` (routing brain) → then `/forge epic`.
**Companion docs (don't re-derive):** `SHIPPING_PIPELINE.md` (repo root, living master) · `.claude/plans/2026-06-11-EPIC-unified-coldchain-pipeline.md` (milestones) · `2026-06-11-BUILD-HANDOFF-unified-coldchain-pipeline.md` (full table schema §2) · `2026-06-11-unified-pipeline-gap-analysis.md`.

---

## 0. MISSION (North Star)
Complete order data → **Kori assigns the right ICE + the right CARRIER/HUB/LANE** → every box arrives **on-time, cold, cost-controlled.** Carrier cost order within TNT≤2: **Veho ($6) → OnTrac ($8) → FedEx Home Delivery ($15) / UPS Ground ($11) → LAST: FedEx 2Day Express ($25).** Cost must become a routing INPUT, not a post-hoc report.

---

## 1. WHY THIS IS A BIG REFACTOR (the thesis)
The pipeline works but is **fragmented across 3 repos with massive duplication**:
- **~11 overlapping invoice importers** in GelPackCalculator (auto_import, sync_all_carriers, sync_carrier_invoices, import_missing_fedex, import_other_data, invoice_scanner, pull_rmfg_invoices, gmail_fedex_sync, backfill_sync, daily_shipping_sync, sync_logon) — nobody knows which is canonical (it's `auto_import.py`; `sync_all_carriers` is misleadingly FedEx-only).
- **TWO databases:** `%APPDATA%/AppyHour/shipping.db` (canonical, Kori) vs `ShippingReports/output/shipments.db` (deprecated build artifact, still rebuilt) — 15k rows divergent.
- **TWO parser sets:** `ShippingReports/parsers/{fedex,ups,ontrac,veho}.py` (feed the dead DB) vs the GelPackCalculator importers (feed APPDATA).
- **Attribution ambiguity:** `cohort_key` (physical-week) vs `tracking_order_link` pointer (order-coverage) — both valid, but conflated for years.
- **Routing brain is separate + shadow:** `ShipRouting/lib/engine.py` (good, cost-aware) imported by Kori but apply-gated.

**Refactor goal:** ONE database, ONE importer, ONE parser set, ONE attribution convention, the routing engine LIVE. Collapse 3 repos' overlap into a clean layered pipeline.

---

## 2. COMPONENT INVENTORY (every moving part + status)
Status: 🟢 KEEP/canonical · 🟡 CONSOLIDATE (overlaps, fold into canonical) · 🔴 RETIRE (deprecated) · ⚙️ engine

### GelPackCalculator/ — importers & sync (the sprawl)
| Script | Does | Status |
|---|---|---|
| `auto_import.py` | watches Downloads+Invoices+fedex_invoices, dispatches by filename → APPDATA, all carriers | 🟢 **canonical importer** |
| `sync_all_carriers.py` | "all" but FedEx-RMFG-Gmail ONLY + Shopify + cohort backfill | 🟡 misnamed; merge into auto_import |
| `sync_carrier_invoices.py` | scan Invoices/ + Gmail RMFG (FedEx/OnTrac/Veho) + box-type | 🟡 overlaps auto_import |
| `import_missing_fedex.py` | FedEx breakdown XLSX → APPDATA (Gmail or folder) | 🟡 FedEx-only subset |
| `import_other_data.py` | one-time historical "other data" import | 🔴 one-off |
| `invoice_scanner.py` | download from Google Drive + parse | 🟡 alt source path |
| `pull_rmfg_invoices.py` | RMFG invoice pull | 🟡 overlaps |
| `gmail_fedex_sync.py` | Gmail FedEx sync | 🟡 overlaps |
| `backfill_sync.py`, `daily_shipping_sync.py`, `sync_logon.py` | scheduled wrappers | 🟡 audit cron usage |
| `download_{fedex,ontrac,veho,shipping_pdfs}_imap.py` | IMAP attachment download → folder | 🟢 keep (feed auto_import) |
| `cohort_attribution.py` | `cohort_for()` carrier ship_date→cohort_key + Veho offset; `backfill_db()` | 🟢 keep (wallet-share attribution) |
| `shipping_invoice_db.py` | parse_*_bytes + store_invoice/store_shipments (the APPDATA writer) | 🟢 keep (core) |
| `sync_shopify_orders.py` | Shopify → fulfillments/shopify_orders/tracking_order_link | 🟢 keep |
| `parcel_panel.py` | ParcelPanel → delivery_status | 🟢 keep |
| `import_feedback_csv.py` | feedback import | 🟡 check vs gorgias_sheets_sync |
| `gel_pack_shopify.py` | Tk app + shared constants/thermal (library for Kori) | 🟢 keep constants; 🔴 dead Tk UI |
| `kori/gel_pack_webview.py` | **Kori** — ice + routing + Lock&Ship record | 🟢 the live app |

### ShippingReports/ — the deprecated parallel pipeline
| Script | Does | Status |
|---|---|---|
| `ingest_all.py`, `ingest.py`, `build_db.py`, `merge_jsons.py` | build `output/shipments.db` + `.json` | 🔴 RETIRE (dead DB) |
| `parsers/{fedex,fedex_csv,ups,ontrac,veho,issues,common}.py` | invoice parsers → Shipment dataclass | 🟡 migrate the GOOD ones to feed APPDATA, retire dupes |
| `enrich_{shipment_orders,ups_delivery,veho_delivery,veho_v2_pickup,veho_via_parcelpanel}.py` | post-ingest enrichment | 🟡 fold into canonical importer |
| `build_wallet_share.py` | wallet-share report off output/shipments | 🟡 repoint to APPDATA |
| `weather_sync_cron.py` | weather pull | 🟡 → weather_history actuals (M2) |
| `AppyHourMCP/tools/gorgias_sheets_sync.py` | Gorgias → feedback | 🟢 keep |

### ShipRouting/ — the routing brain (mostly KEEP)
| Script | Does | Status |
|---|---|---|
| `lib/engine.py` `compute_routing()` | ⚙️ cost-aware lane+ice per order (North Star, TNT≤2 survivor) — imported by build.py + Kori | 🟢 canonical engine, **SHADOW** |
| `lib/{optimizer,thermal,features,hist_risk,origin,tags,zip_loaders}.py` | engine internals | 🟢 keep |
| `build.py` | assignment sheet from engine | 🟢 keep |
| `apply.py` | apply tags to Shopify (dry-run default, gated) | 🟢 keep |
| `upload_cohort.py`, `cohort_health.py`, `routing_postmortem.py`, `postmortem.py`, `shadow_report.py` | reporting/ops | 🟢 keep |
| `phase0_origin_backfill.py`, `learn_origin_hub_map.py`, `sweep_conflicts.py`, `scan_production_tags.py`, `normalize_tag_names.py`, `compare_chooser.py`, `ice_shadow.py`, `fix_export_xlsx.py` | one-offs/experiments | 🟡 archive after refactor |

---

## 3. CANONICAL DATA LAYER
**ONE DB: `%APPDATA%/AppyHour/shipping.db`** via `appyhour_lib/paths.py::db_path()`. Full table schema in BUILD-HANDOFF §2. Core tables: `fulfillments` (cohort authority, tags), `tracking_order_link` (pointer), `shipments` (cost), `delivery_status` (TNT), `feedback` (CS), `kori_snapshots`/`_orders` (predictions), `shopify_orders`, `weather_history`, `gel_apply_log`, `invoices`.

**Attribution — TWO valid, pick by question (don't compare across):**
- **Pointer** `shipments.tracking → tracking_order_link → order# → fulfillments._SHIP_` → COVERAGE + order joins + per-cohort cost report.
- **`cohort_key`** (`cohort_attribution.py`, carrier ship_date−dow, Veho tender-offset) → WALLET-SHARE / physical carrier-week.
- ⚠️ Dedup invoice rows by `(invoice_id, tracking)` before cohort_key rollup — same invoice in RMFG XLSX + account CSV double-counts (the "152%").

---

## 4. SOURCES → TABLES (6 feeds)
email (RMFG + FedEx/UPS account) · FedEx/UPS/OnTrac/Veho invoice download (portal) → `shipments`/`invoices` via `auto_import` · Shopify → `fulfillments`/`shopify_orders`/`tracking_order_link` · ParcelPanel → `delivery_status` · Gorgias → `feedback` · 17track (manual ground-truth). Hubs: TX→Dallas·TN→Nashville·CA→Anaheim·IN→Indianapolis (NOT MA).

---

## 5. DECISION COMPONENTS
- **Kori** (`GelPackCalculator/kori/gel_pack_webview.py`) — assigns ice (`recommend_config`+`min_gel_states`+zip overrides) + routing; Lock&Ship RECORDS to `kori_snapshots`. Reads APPDATA. Launch `run_webview.bat`.
- **ShipRouting engine** (`lib/engine.py`) — cost-aware lane+ice, North Star encoded, SHADOW. Go-live = M5.
- **Apply-Gels gate** (`gel_apply_log` + `gels_*` cols exist) — records apply time/margin; lock-time drift notify (partly built).

---

## 6. REPORTS
Weekly Carrier Report (`weekly_carrier_report.py --auto` — mix+spikes+cost by carrier×service, `Order::` excluded) · Weekly Shipping Issue Report (`~/.knowledge/ops/Weekly Shipping Issue Report.md` — Gorgias `Shipping::%` vs Slack) · Derived transit/post-mortem (`cohort_health.py`, `routing_postmortem.py`; business-day TNT, on-time = delivered≤2 ÷ FULL cohort).

---

## 7. RULES (must hold)
Cohort = `_SHIP_<Mon>` tag (never scan date). Sub-cohorts: **A** Fri/Sat→Mon ~70% multi-hub TN/TX/IN/CA all-carriers-incl-Veho; **B** Tue/Wed→Tue ~20% Dallas-only no-Veho zones5-7→Express. **Fixed_Route = sacred** (anecdotal customer reason; never cost/TNT-override; Bree MD + Pam FL block `--apply`). **Fulfillment ≠ shipping** (`Order::%` separate from `Shipping::%`). Ice physics-first, history upgrades only. ParcelPanel lags → 17track verify. OnTrac≡LaserShip. `is_internal=1` excluded. FedEx cost always by service (2Day≠Ground/HD). Shopify rejects duplicate tags (blocks multi-gel reships — M4).

---

## 8. KNOWN ISSUES / COURSE CORRECTIONS
Full list: `SHIPPING_PIPELINE.md` §7 + `BUILD-HANDOFF` §7 + ENGINE_GUIDE §8. Headlines: risk-label bug (Python rates margin<0 MEDIUM; port JS→Python margin<0→HIGH; audit `2026-06-04-kori-3critic-audit.md`); ice gate unenforceable (no box-upgrade + Shopify dup-tag); weather actuals not synced; FedEx invoices lag ~3wk + double-source dedup needed; tray ice out-of-scope routing in-scope.

---

## 9. THE EPIC (milestones — refactor-shaped)
**M1 — ONE source of truth ⭐:** retire `output/shipments.db` + ShippingReports ingest; designate `auto_import` the sole importer (absorb the ~10 overlapping ones); backfill `cohort_key` via pointer; coverage-% logging. **M2 — Complete ingest:** one orchestrated run (all 6 sources), weather actuals nightly, 17track wired, OnTrac/UPS IMAP folded in. **M3 — Weekly post-mortem** into the recurring Wed task. **M4 — Ice enforcement (net-new):** box-upgrade for neg-margin, reship 2×48oz, solve Shopify dup-tag (`!ExtraGel48oz_x2!`), lock-gate. **M5 — Routing GO-LIVE:** ShipRouting SHADOW→APPLY (resolve Bree/Pam, 6/15 cohort, cost guardrail). Sequence: M1 unblocks all.

---

## 10. CURRENT STATE (as of 2026-06-11, for the fresh agent)
- Ingested: FedEx & UPS to ship-date **06-02**, OnTrac **05-28** (6/1 in email, needs `download_ontrac_imap`→`auto_import`), Veho **06-05**. Shopify refreshed 8 cohorts (19,481). Gorgias current incl tonight's wave.
- `auto_import` last run: +23 invoices / 6,679 rows / 28,683 enriched · **900 unknown + 1 parse_error** (un-triaged).
- 2 attached account-direct invoices ingested (FedEx 6/11 xlsx + UPS C411H4236).
- Open: OnTrac 6/1 pull; 900-unknown triage; cohort_key dedup; the whole M1-M5.

---

## 11. PATH INDEX
DB `%APPDATA%/AppyHour/shipping.db` · helper `AppyHour/appyhour_lib/paths.py` · importer `GelPackCalculator/auto_import.py` · writer `shipping_invoice_db.py` · Kori `GelPackCalculator/kori/` (`run_webview.bat`) · engine `ShipRouting/lib/engine.py` (+`ENGINE_GUIDE.md`) · weekly report `~/.claude/skills/appyhour-shipping-data/queries/weekly_carrier_report.py` · query tool `~/.claude/skills/appyhour-shipping-data/query.py` · vault index `~/.knowledge/ops/Shipping Data Pipeline.md` · memory `feedback_tracking_order_link_pointer.md`, `feedback_shipping_pipeline_system_of_record.md`, `feedback_ship_tag_cohort_pairing.md`.

---
*Fresh agent: start with `/forge epic` using this map. M1 (one DB + one importer) is the foundation — everything else depends on collapsing the import sprawl + retiring `output/shipments.db`.*
