# /forge brainstorm — Unified Shipping/Kori Pipeline: Target vs Reality

**Date:** 2026-06-11 · Phase 1 UNDERSTAND · grounded in this session's verified findings

**Verdict: NO — we have ~80% of the *parts*, but it is NOT one consistent unified pipeline, and the cost-aware closed loop is incomplete.** The raw fields and sources mostly exist and flow; the unification, attribution integrity, and enforcement do not.

---

## Target-state → reality map

### (2) Fields ingested — MOSTLY PRESENT
| Field | Status | Where / gap |
|---|---|---|
| order # | ✅ | `fulfillments`, `shopify_orders` |
| tracking | ✅ | `delivery_status`, `shipments`, `tracking_order_link` |
| _SHIP week + full cohort | ✅ | `fulfillments.tags` |
| RMFG tags + sub-cohort (Fri/Tue) | ✅ | `fulfillments.tags` RMFG_<Fri>/<Tue> |
| final-mile pickup date | ✅ | `delivery_status.pickup_date` |
| final-mile delivery date | 🟡 | `delivery_status.delivery_date` — **LaserShip/OnTrac stuck `in_transit`**, final scan missing |
| Gorgias "arrived warm" etc | 🟡 | `feedback` — but ~25-60% capture gap; order# extraction fixed this session |
| cost | 🟡 | `shipments.cost` — FedEx ~3wk lag + **cohort mis-attribution** |
| service | ✅ | `shipments.service` (Ground/HD now combined) |
| distribution hub | 🟡 | `kori_snapshot_orders.origin_state/zip` + `shipments.hub` — not uniformly populated per delivered order |

### (3) Sources — ALL PRESENT, some manual
email ✅ · FedEx/UPS/OnTrac/Veho invoice download ✅ · Shopify ✅ · ParcelPanel ✅ · Gorgias ✅ · **17track 🟡 manual** (public-browser batch, not in pipeline).

### (1)(4) ONE consistent unified pipeline — 🔴 **NO**
The core gap:
- **TWO databases.** `%APPDATA%/shipping.db` (canonical, Kori reads) vs `ShippingReports/output/shipments.db` (deprecated per `paths.py`, still rebuilt every `ingest_all`). 15k-row divergence.
- **TWO invoice pipelines.** `sync_all_carriers.py`→APPDATA vs `ingest_all.py`→output. I ran the wrong one earlier.
- **Broken attribution.** Reporting uses `shipments.cohort_key` (raw ship_date → 56%/152% lies) instead of the **`tracking_order_link` pointer** (tracking→order#→_SHIP → 100%). Pointer exists, reporting doesn't use it.
- **Weather actuals not synced** — forecast-vs-actual needs a live Open-Meteo pull; `weather_history` empty for recent dates.
- Not one orchestrated run — many scripts, two targets.

### (5) Kori assigns ice + carrier/hub/lane, on-time + good-condition + cost-controlled — 🟡 PARTIAL
- ✅ Ice assignment (`recommend_config` + `min_gel_states` + zip overrides).
- ✅ Carrier/hub/lane routing (`suggested_routing`, carrier blocks, force_2day).
- 🔴 **Ice enforcement gap** — CRITICAL/negative-margin orders ship anyway (apply-gels/lock-ship gate spec'd this session, not built). Risk-label bug (margin<0→MEDIUM, Python vs JS divergence).
- 🔴 **Cost is NOT in the assignment loop** — Kori picks by serviceability/TNT; cost is post-hoc + lagged. The 06-01 FedEx overload (61.7%, +$4.2k) happened because nothing enforced cost-aware carrier mix. "Without blowing up cost" is open-loop.

---

## What "unified" actually requires (the build targets)
1. **ONE DB.** Retire `output/shipments.db`; all carriers + enrichment land in `%APPDATA%` via one importer (`sync_all_carriers` extended). Migrate ShippingReports reads to `db_path()`.
2. **Pointer-based attribution everywhere.** Backfill `shipments.cohort_key` from `tracking_order_link`→_SHIP; reporting joins the pointer, never raw ship_date.
3. **One orchestrated ingest.** Single command pulls all 6 sources → APPDATA, idempotent, logs coverage % per source/cohort (flags FedEx <70% don't-trust).
4. **Weather actuals synced** nightly (Open-Meteo archive) → closes forecast-vs-actual in-DB.
5. **Close the Kori loop:** enforcement gate (negative-margin → HOLD/upgrade) + **cost as a routing input** (carrier mix guardrail: flag/deny >55% FedEx at lock; prefer regional $6 over FedEx-2Day $33 when TNT allows).

---

## Honest one-liner
We have a rich data *substrate* and Kori *does* assign ice + carrier — but it's **two databases, mis-attributed cohorts, a manual source, no weather-actuals, an unenforced ice gate, and cost outside the decision loop.** It is a collection of working parts, not yet the single closed-loop pipeline described.
