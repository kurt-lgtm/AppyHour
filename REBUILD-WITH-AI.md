# REBUILD-WITH-AI — AppyHour Cold-Chain System Disaster Recovery

**Audience:** a fresh Claude (or any AI agent) on a NEW machine, with Kurt present for logins.
**Written:** 2026-06-11 (M1 cut-over day). **You are reading this because Kurt's PC died.**
Hand this whole file to the AI as the first prompt, with the backup zip extracted alongside.

---

## 0. What this system is
AppyHour/Elevate Foods cold-chain shipping ops: ingest carrier invoices + Shopify + ParcelPanel +
Gorgias + weather into ONE SQLite DB, route every `_SHIP_<Monday>` cohort to the cheapest carrier
lane that holds the 2-day promise, size gel ice physics-first, post-mortem weekly. Owner: Kurt
(kurt@elevatefoods.co), Head of Ops. The system was refactored 2026-06-11 to: one DB, one importer,
cost-aware routing engine (shadow), exception-only escalation.

**2026-06-27 engine additions (LIVE in `ShipRouting`):**
- **Carrier ZIP-SERVICEABILITY gate** (`build.py`, post-routing) — coverage files (`load_veho` active / `load_ontrac`) are the AUTHORITY for last-mile carriers. Any Veho/OnTrac rec whose dest zip isn't covered is auto-rerouted to **FedEx Home Delivery (same hub)** + hard-asserted (mirrors the Indy pallet gate); `_svc_rerouted` overrides `resolve_apply` keep-existing so it reaches Shopify. Post-hoc mirror: `qc_audit.py` SERVICEABILITY check. Fixed 391 Veho/OnTrac→unserviced-zip mis-routes on _SHIP_2026-06-29.
- **`HISTORY_SERVICEABILITY` STATE-proof layer DROPPED** (`lib/features.build_history_lanes` returns empty `state`) — whole-state crediting from metro history was inventing rural coverage (268 of those mis-routes). z3-proof retained; history sets TNT, never final serviceability.
- **Indy pallet over-cap fix** — gate-spilled orders now actually move off Indy at apply (`_indy_spilled` overrides keep-existing) + `apply.py` live-state Indy ≤6 guard.
- **`load_veho()` is format-aware** — parses BOTH the old single-sheet file AND the new multi-sheet `Veho_GroundPlusSuite_*.xlsx` (tier *Ground Plus Zero*, per-hub IND+Nashville serviceability). Coverage exports = serviceability authority; keep current (OnTrac via `GelPackCalculator/download_ontrac_imap.py`).

**2026-06-24 engine additions (LIVE in `ShipRouting/build.py`, env-gated):**
- `HISTORY_SERVICEABILITY=1` — proven-ground lanes from delivery actuals (SE→Nashville visibility); **STATE layer dropped 2026-06-27 (above)**.
- `CLOSEST_HUB_DEFAULT=1` — route to the geographically closest proven hub (breaks cost-ties off distance).
- `CARRIER_TNT_TRUST=1` — rescue AIR-bound orders onto a FedEx/UPS GROUND lane the carrier itself quotes
  ≤2 (via owned **ShipStation/ShipEngine**, `lib/carrier_tnt.py`), +1 ice. Needs `%APPDATA%/AppyHour/shipengine_api_key.txt`. Degrades to no-op without the key. Kill: `=0`.
- **Carrier-hub legality guard** (`lib/features.legal_lane`/`CARRIER_HUBS`) — rejects impossible lanes
  (Veho=Nashville+Indy only, UPS=Dallas-only) so dirty data can't route. Mirrored in the ingest path.
- **MILP Indy-capacity solver** (`ShipRouting/milp/`) — a global HiGHS solve that replaces the greedy
  6-pallet Indy gate's keep/spill choice with the globally cheapest set. **MILP makes the Indy decision,
  surfaced as a human-reviewed draft** (`milp/draft_sheet.py` runs the cohort through carrier-TNT + the MILP
  and emits a DRAFT routing xlsx for human review before apply). It is **HUMAN-GATED, not autonomous** —
  live `compute_routing` still calls the greedy `_indy_pallet_gate` as the shipped path until the draft
  workflow is adopted. Validated A/B: saves ~$627/wk hard carrier+ice cash but +$234/wk warm-arrival risk →
  quote the **~$393/wk NET** (a cash-vs-spoilage trade), not the ~$627 gross.

## 1. Where everything lives offsite
| What | Where | Notes |
|---|---|---|
| **Code: AppyHour monorepo** | github.com/kurt-lgtm/AppyHour (private) | ShippingReports, AppyHourMCP, appyhour_lib, scripts. GelPackCalculator is gitignored — separate repo below |
| **Code: GelPackCalculator** | github.com/kurt-lgtm/GelPackCalculator (private) | THE production app: Kori (kori/), auto_import (sole importer), shipping_invoice_db (writer) |
| **Code: ShipRouting** | github.com/kurt-lgtm/ShipRouting (private) | The routing engine (lib/engine.py, lib/optimizer.py) + build/apply pipeline |
| **DB snapshot** | Google Drive: `shipping.pre-cutover-2026-06-11.db` (file id 14Wjf8EOPnSqh_kwBPQSK9403AIAnq-4l) | 110MB SQLite. RESTORE TO: `%APPDATA%/AppyHour/shipping.db` |
| **Logic/docs bundle** | Google Drive: `coldchain-logic-backup-<date>.zip` (2026-06-11 id 1HY0k9cXfbglFsYP1F8qcGEK3KcioEHSu) | SHIPPING_PIPELINE.md, ENGINE_GUIDE, handoffs, contract, audits, vault notes, this file |
| **Vault + skills bundle** | Google Drive: `coldchain-knowledge-backup-<date>.zip` | `~/.knowledge/` (Obsidian vault) + key `~/.claude/skills/` |

## 2. Rebuild order (do in sequence)
1. **Install:** Anaconda Python (system uses `C:\Users\Work\anaconda3\python.exe`), git, gh CLI, Claude Code. `pip install requests openpyxl pywebview` (Kori needs pywebview **netfx** backend — .NET Framework, NOT coreclr).
2. **Clone** the three repos into `C:\Users\<user>\Claude Projects\` — AppyHour and ShipRouting side-by-side, GelPackCalculator INSIDE AppyHour/ (path-coupled: `kori/routing_v2.py` and ShipRouting lib hardcode `Claude Projects\AppyHour` + `Claude Projects\ShipRouting` on sys.path — keep these exact folder names or fix the sys.path blocks).
3. **Restore DB** from Drive snapshot → `%APPDATA%/AppyHour/shipping.db`. Then run the importers to catch up the gap since snapshot (sources: Gmail/IMAP carrier emails — history re-downloadable from carrier portals + RMFG emails).
4. **Restore vault + skills** from the knowledge zip → `~/.knowledge/`, `~/.claude/skills/`.
5. **Recreate settings + creds** (NOT in any backup — Kurt re-enters):
   - `%APPDATA%/AppyHour/gel_calc_shopify_settings.json` — Shopify creds, OpenWeatherMap key, per-state transit config, zip overrides (a copy may exist in the knowledge zip; verify freshness).
   - `%APPDATA%/AppyHour/portal_creds.json` — FedEx/UPS billing portal logins (template in GelPack docs).
   - `%APPDATA%/AppyHour/shipengine_api_key.txt` — ShipStation/**ShipEngine v2** key (header `API-Key`) for carrier-TNT trust. Missing key → `CARRIER_TNT_TRUST` safely no-ops.
   - Gmail/IMAP app passwords, Google OAuth (gws CLI: Internal app, 8 scopes), Gorgias API key, GitHub auth.
6. **Re-register scheduled tasks:**
   - `appyhour_sync_on_logon` → `GelPackCalculator/sync_logon.py` (logon trigger)
   - `AppyHour Wednesday Ops Run` → `AppyHourMCP/register_wednesday_task.bat` (Wed 14:00; includes routing post-mortem)
   - Weather actuals daily 3:00 → `ShippingReports/weather_sync_cron.bat`
7. **Verify:** `python AppyHour/scripts/validate_refactor_db.py --copy <restored>` vs expectations; run `GelPackCalculator/auto_import.py` (expect clean totals); launch Kori via `GelPackCalculator/kori/run_webview.bat`; run `ShipRouting/build.py` on the current `_SHIP_` cohort.

## 3. The documents that ARE the system (read order for the AI)
1. `SHIPPING_PIPELINE.md` (AppyHour repo root) — system of record, plain-English §1-3.
2. `ShipRouting/ENGINE_GUIDE.md` — routing brain: survivor-invariant, probation tier, ice rules, course corrections.
3. `.claude/plans/2026-06-11-MASTER-HANDOFF-v2-coldchain-POST-PHASO0.md` + `2026-06-11-ORCHESTRATION-claude-codex-coldchain.md` (frozen schema contract) — in the AppyHour repo.
4. Vault: `~/.knowledge/ops/Shipping Data Pipeline.md` (index) + `codebase/ShipRouting Expected-Cost Engine.md` (rules R1-R8).
5. Skill rulebook: `~/.claude/skills/ship-routing-assignment/SKILL.md`.

## 4. Non-negotiable domain rules (survive any rebuild)
- Cohort = `_SHIP_<Monday>` TAG, never scan date. On-time = delivered≤2 ÷ FULL cohort.
- Fixed_Route = sacred. Ice physics-first (history upgrades only). OnTrac≡LaserShip.
- Canonical DB columns: `zip_code` (never `zip`), `weather_history.zip_prefix` = 5-digit.
- Veho lanes = Indianapolis + Nashville ONLY; two-gate rule for any new carrier×hub. Enforced in code by
  `lib/features.legal_lane`/`CARRIER_HUBS` (UPS=Dallas-only too) at lane-build AND at invoice ingest — an
  impossible carrier@hub is a data bug (the 475 Veho@Dallas mis-attribution), not a routable lane.
- **Carrier coverage exports are the serviceability AUTHORITY.** A last-mile carrier delivers a zip ONLY if that zip is in its coverage export (`load_veho` active / `load_ontrac`) — legality(carrier@hub) ≠ coverage(serves the *zip*). The `build.py` serviceability gate enforces it (uncovered Veho/OnTrac → FedEx Home Delivery, same hub). `HISTORY_SERVICEABILITY` may set TNT/speed but **never creates serviceability** (its STATE layer did → 391 mis-routes, dropped 2026-06-27). Keep exports current: OnTrac via `GelPackCalculator/download_ontrac_imap.py`; Veho via the GroundPlusSuite export (`load_veho` parses the multi-sheet format).
- Dedup `(invoice_id, tracking)` at cohort rollup, one physical row per tracking at storage.
- FedEx 2Day Express = last resort ($25); carrier cost order Veho $6 → OnTrac $8 → UPS $11 → FedEx HD $15.
- **The Indy 6-pallet cap is enforced LIVE by the greedy `_indy_pallet_gate`** (engine.py) — this is the
  shipped path. The `milp/` solver **makes the Indy keep/spill decision, surfaced as a human-reviewed draft**
  (`milp/draft_sheet.py` emits a DRAFT routing xlsx); it is human-gated, not autonomous, and does not set a
  live tag on its own. Live `compute_routing` still calls the greedy gate until the draft workflow is adopted.

## 5. Refresh cadence for this backup
Logic zip + DB snapshot to Drive **weekly** (goal: automated in M2 `pipeline_run.py`). Repos: push on every work session. THIS FILE lives in the logic zip + all three repos' awareness docs.

✅ **2026-06-27 — OFFSITE UPLOAD WORKS** (gws dependency removed). `scripts/backup_offsite.py` now uploads
via the **`drive.file` OAuth token** (same one `upload_sheet.py` uses; resumable, gws-independent) — verified
uploading `shipping.weekly-<date>.db` (132MB) + logic/knowledge/reference zips to Drive. Run it with the
**`appyhour-backup` skill** (repeatable: refresh THIS doc → `backup_offsite.py` → verify the `OFFSITE:` lines).
⚠️ One gap remains: **`AH_BACKUP_PASSPHRASE` is unset** → the encrypted creds bundle is SKIPPED (refuses to
upload plaintext secrets), so the Drive backup has the DB + docs + knowledge but NOT the API keys/tokens —
set the passphrase (user env) to include creds. Code is offsite via the three GitHub repos regardless.
