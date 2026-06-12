# EPIC — Unified Cold-Chain Data + Cost-Aware Routing Pipeline

**Date:** 2026-06-11 · `/forge epic` · Owner: Kurt
**North Star:** complete order data → Kori assigns right ice + right carrier/hub/lane → **on-time, good condition, cost-controlled.** Carrier preference: **Veho → OnTrac → FedEx Home Delivery / UPS Ground → (last) FedEx 2Day Express**, always within TNT≤2.

**Single canonical store:** `%APPDATA%/AppyHour/shipping.db` (Kori reads via `appyhour_lib.paths.db_path`). Attribution = `tracking_order_link` pointer (tracking→order#→_SHIP), NEVER `cohort_key` raw ship_date.

**Key reality (verified this session):**
- Cost-aware routing already exists = **ShipRouting `lib/engine.py compute_routing()`**, SHADOW mode, imported by build.py + Kori (`compute_v2_routing`). Not missing — needs go-live.
- Data substrate exists but: 2 DBs, broken cohort_key, FedEx invoice lag, weather actuals unsynced, ice gate unenforceable (no box-upgrade capability + Shopify dup-tag limit).

---

## M1 — ONE source of truth (data-integrity foundation) ⭐ FIRST
- Retire `ShippingReports/output/shipments.db`; migrate its reads (`enrich_*`, `build_wallet_share`) to `db_path()`. All ingest → APPDATA via `sync_all_carriers` (extended to all carriers, not just FedEx).
- **Backfill `shipments.cohort_key` from `tracking_order_link`→`fulfillments._SHIP_`.** Then cohort_key is trustworthy; all reports auto-correct (05-04 → 100%).
- Ingest the 5 attached invoices (4× UPS `C411H4` CSV + 1 FedEx xlsx) + email sweep for any un-downloaded.
- Coverage-% logged per source × cohort (flag FedEx <70% = don't-trust).

## M2 — Complete the ingest (all 6 sources, one orchestrated run)
- **UPS billing CSV parser** for the `C411H4` format — carries order# + tracking(1Z) + cost(Net Charge) + service(Ground Res/Comm) + hub(Dallas_AHB) + dims(13x10x10). Rich; map straight into shipments + pointer.
- Weather **actuals** nightly (Open-Meteo archive) → closes forecast-vs-actual in-DB (no more live one-offs).
- 17track ground-truth (browser batch ≤40, `fc=` locked) wired for ParcelPanel-lag correction (PP undelivered ≠ late).
- One idempotent command: email · FedEx · UPS · Shopify · ParcelPanel · Gorgias · 17track → APPDATA.

## M3 — Weekly post-mortem loop (folds into existing recurring task)
- Last-week Arrived-Warm + Delayed: delivery data + Gorgias **vs snapshot** (`cohort_health.py` + `routing_postmortem.py` + warm-forecast-vs-actual). Already partly built — wire into the Wed weekly report so it runs automatically.

## M4 — Ice enforcement (the OPEN capability Kurt flagged)
- **Box upgrade for negative-margin orders** — bump to a larger box so more ice physically fits (today there's no capability → gate can't enforce).
- **Reship gel** — add 2× 48oz gel tags to a reship that fits (~7 refrigerated items in the small box).
- **Solve Shopify duplicate-tag limit** — xlsx output takes duplicate tags, Shopify rejects them. Need a quantity-encoded single tag (e.g. `!ExtraGel48oz_x2!`) or box-size tag the packer reads. Decide encoding.
- Wire to lock-gate: negative-margin → upgrade box/ice or HOLD (the apply-gels/lock-ship gate from the 2026-06-04 spec).

## M5 — Go live: cost-aware routing (ShipRouting SHADOW → APPLY)
- Engine already encodes the North Star + TNT≤2 survivor-invariant. Go-live blockers (from ENGINE_GUIDE §10):
  - Resolve **Bree Hrechka (MD) + PAM DEMORE (FL)** Fixed_Route `!NO FedEx` (blocks `--apply`).
  - 6/15 cohort rebuild on commit `03609c1` after ~500 more orders → confirm blanks≈0, Veho fenced off AZ/CO/FL, Express ~11% → `upload_cohort.py` → `apply.py` dry-run → `--apply` → Kori restart.
- Cost guardrail at lock: flag cohort if FedEx share >55% or Express > expected ~11%.

---

## Sequencing
M1 (data integrity) unblocks everything — do first. M2 + M3 parallel after M1. M4 (ice capability) + M5 (routing go-live) are the closed-loop payoff; M5's engine is built so it's mostly go-live ops, M4 is net-new build.

## Immediate next (M1 first actions)
1. Stage + ingest the 5 attached invoices into APPDATA (NOT output/shipments.db).
2. Backfill `cohort_key` via `tracking_order_link` (one UPDATE) → re-run weekly report off corrected data.
3. Confirm `sync_all_carriers` covers all 4 carriers; if not, that's the M2 UPS-parser gap.

## Refs
- ENGINE_GUIDE: `ShipRouting/ENGINE_GUIDE.md` · pointer rule: `memory/feedback_tracking_order_link_pointer.md` · ice spec: `_outputs/artifacts/2026-06-04-applygels-forecast-drift-spec.md` · gap analysis: `2026-06-11-unified-pipeline-gap-analysis.md`
