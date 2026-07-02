# PLAN — Harden the Ice/Weather "Arrived Warm" Post-Mortem

- **Date:** 2026-07-02 · **Mode:** /forge STANDARD (new canonical tool + constraints doc + box-physics resolve)
- **Status:** PLANNED — not built (DB sync running at plan time; discovery was read-only, and shipping.db access stays READ-ONLY always per `shipping-db-msix-wal-corruption`).
- **Source of truth:** `_outputs/reports/HANDOFF-2026-07-02-ice-weather-postmortem-hardening.md` (taxonomy + negatives), this plan (build spec).

## Premise check (read-only, 2026-07-02)
- Prototype `_outputs/scripts/ice_weather_postmortem.py` EXISTS → harden/replace it, don't start from zero.
- **No** canonical warm-postmortem tool/skill/**constraints-doc** exists → all new (build them).
- `box_simulation.py` (AppyHour root) provides DistVol→`Small Box`/`Large Box` + `has_tray` — the real-box resolve the handoff requires.
- 🔑 **The handoff's `appyhour_search_orders` (MCP) dependency is a trap for a weekly scheduled run** — MCP tools disappear in unattended sessions (proven by the 2026-07-02 ops-issues failure). Pull live Shopify via the **direct REST client** (`appyhour_lib.credentials.get_shopify_auth`) over Bash instead. (Same lesson: scheduled tools use Bash+script, never MCP tools.)

## 🔴 Constraints doc FIRST (forge ordering gate)
Author `WARM_POSTMORTEM_RULES.md` (or a CONSTRAINTS section) BEFORE the tool — gotchas/negatives-first — gated from `shipping-rules` skill + `ROUTING_RULES.md`. It encodes the taxonomy + every negative below. Plan-critic blocks tool-before-doc.

### The taxonomy (SSOT) — every warm ticket = exactly ONE
Arrived-warm is **on-time by definition** (delivered ≤ 2-day) → **never a speed/routing problem; no "needs faster lane" bucket.**
1. **PACK** — prescribed ice not physically packed (RMFG exec). *Sig:* live Shopify has `!ExtraGel48oz!/24oz` but box shipped with less.
2. **FORECAST** — forecast under-called dest heat → ice sized too low. *Sig:* build's forecast temp ≪ actual delivery-day peak.
3. **ASSIGNMENT** — wrong config vs physics. *Sig:* assigned gel < physics-required at a correct forecast, or wrong box-type/carrier-hub.
- **PERCEPTION** (flagged separately, excluded from failure rate) — adequate ice + moderate temp + on-time + "no ice/melted" wording = thawed-cool CS-messaging issue.
- **PHYSICS-CEILING** (noted separately) — `TR-` tray at 2×96 oz max + genuinely hot = gel can't fix (packaging lever).

### Negatives (each burned us — encode as hard rules)
- **Ice attribution from LIVE Shopify tags ONLY** — NOT `fulfillments.tags` (only ~53% coverage on `_SHIP_2026-06-29`; gave a false shipped-baseline for #154799) and NOT routing snapshots.
- **No "needs faster lane"/"heat-breach" bucket** — on-time+warm ≠ speed failure.
- **No blanket "gel floor"** — a seasonal min-gel floor broke the physics node before. Heat margin lives INSIDE the thermal model (temp/confidence input), never a post-hoc floor.
- **`AHB-LGE`/`AHB-SML` = food serving tier, NOT box dimension.** Grade physics against the **assigned shipping box's REAL dimensions** (DistVol→box via `box_simulation.py`, existing box-dim dataset) — never inferred from the food-tier label. Resolve tray-vs-box per order; tray gel ceiling = 2×96 oz.
- **Freshness gate** — assert `delivery_status` max ≥ cohort ship date AND `fulfillments` has the `_SHIP_` cohort; else STOP ("stale — run sync first"). Stale tables → false "0 warm / denom 0".
- **Immutable reads** — read shipping.db `?mode=ro&immutable=1` (ignores WAL); a mid-checkpoint plain read returns transient "disk image is malformed" (NOT corruption). Only conclude corruption if an immutable read also fails. **Claude never writes shipping.db.**

## Build tasks
0. **Constraints doc** (`WARM_POSTMORTEM_RULES.md`) — taxonomy + negatives, gated from shipping-rules/ROUTING_RULES.
1. **Real-box-dimension resolve** (correctness prerequisite): per order, DistVol→`Small`/`Large Box` (or tray if `has_tray`) via `box_simulation.py`; feed the box's REAL dims into the thermal check; confirm `TR-` tray = 2×96 gel ceiling. Must be right BEFORE gel-adequacy grading is trusted.
2. **Canonical tool** (skill + script, e.g. `/ice-warm-postmortem`) implementing the per-ticket detection logic: live-Shopify tags (via get_shopify_auth REST, not MCP) → assigned box + dims → delivery (transit ≤2 confirm) → actual dest peak (`weather_history`, backfill newest-first) → forecast temp+precision the build used (cohort forecast cache/snapshot) → physics-required gel (thermal model) → classify 1/2/3/PERCEPTION/PHYSICS-CEILING. Freshness gate + immutable reads baked in.
3. **Rate + report:** denom = full `_SHIP_<Mon>` cohort from `fulfillments` (both RMFG sub-cohorts), volume-normalized; per-ticket cause table + cause histogram + carrier×cause + WoW rate; reconcile ticket count vs Slack `#reship-and-order-requests` (feedback tee lags). PERCEPTION excluded from rate.
4. **Freshness gate wired to the sync pipeline** (fulfillments→delivery→weather) so a weekly run self-checks staleness before grading.

## Forecast-timing context (attribute causes to the right cohort)
- Cohorts before `_SHIP_2026-07-06` used the OLD forecast horizon (couldn't reach Wed delivery → under-size). The 8-day/Friday-zip5 fix (`e1d50d2`) went live 2026-07-01; `_SHIP_2026-07-06` is the first covered cohort. → A **pre-fix** FORECAST label is expected; a **post-fix** one is a real regression. Record each cohort's forecast build-date + precision so this is visible.

## Verification
- Re-grade a KNOWN case: #154799 (max ice, 60°F, "No Ice") must classify **PERCEPTION**, not a failure. #155441 (TN 370 lane) forecast reachability.
- Freshness gate: with stale `delivery_status` → tool STOPS with "stale," does NOT report 0/0.
- Box resolve: an `AHB-LGE` order whose DistVol → `Small Box` grades against SMALL dims, not large.
- Tray: a `TR-` at 2×96 warm → PHYSICS-CEILING, not ASSIGNMENT.

## Open decisions (rec in parens)
1. Delivery form — **skill + backing script** (like `appyhour-shipping-data`) so it's callable both interactively and by a weekly scheduled task (Bash), no MCP dependency. (Recommend.)
2. Physics-required-gel source — reuse `gel_pack_shopify.analyze_order` / `appyhour_lib` thermal directly vs a thin re-implementation. (Recommend: reuse the canonical thermal model; never re-derive physics.)
3. Schedule it? A weekly `ice-warm-postmortem` scheduled task (post-cohort, after sync + freshness gate passes) → Slack Kurt anomaly-first. (Recommend yes, once built + the sync-freshness gate is reliable.)
