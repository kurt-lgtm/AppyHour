# REBUILD-WITH-AI — AppyHour Cold-Chain System Disaster Recovery

**Audience:** a fresh Claude (or any AI agent) on a NEW machine, with Kurt present for logins.
**Written:** 2026-06-11 (M1 cut-over day). **Updated:** 2026-08-14 (§5.1 rule 7: carrier TNT cache moved to `C:\AppyHourData` — the `%APPDATA%` MSIX split; §5: creds passphrase now SET. New engine flags `NO_AIR_LAST_GROUND` + `NATIONAL_HIST_OVERRIDES_COMMIT` both default OFF, so §4 non-negotiables are unchanged); prior 2026-08-07 (§0 "2026-08-07 additions": droplet RETIRED → App Platform + managed MySQL, cloud ingest ownership, Swedesboro NJ hub, FridayFlow console; prior 2026-07-31: flags/levers SSOT, Chicago open + Indy CLOSED, MAX_ICE_PLUS, fail_cost rename). DB moved to `C:\AppyHourData\shipping.db` 2026-07-08 — §5.1 v3, READ IT before restoring the DB. **You are reading this because Kurt's PC died.**
Hand this whole file to the AI as the first prompt, with the backup zip extracted alongside.

---

## 0. What this system is
AppyHour/Elevate Foods cold-chain shipping ops: ingest carrier invoices + Shopify + ParcelPanel +
Gorgias + weather into ONE SQLite DB, route every `_SHIP_<Monday>` cohort to the cheapest carrier
lane that holds the 2-day promise, size gel ice physics-first, post-mortem weekly. Owner: Kurt
(kurt@elevatefoods.co), Head of Ops. The system was refactored 2026-06-11 to: one DB, one importer,
cost-aware routing engine (shadow), exception-only escalation.

**2026-08-07 additions — NEWEST, read this block FIRST (the system is no longer single-machine):**
- 🔴 **The DigitalOcean droplet `142.93.188.10` is DESTROYED (2026-08-06). Do NOT rebuild it, do NOT
  restore ssh/jump-box wiring.** Resurrect image if ever needed: NYC3 snapshot `shipping-final-2026-08-06`
  (20.22 GB). Replacements: **builds = GitHub Actions** (push-triggered), **history/inputs = managed MySQL
  `appyhourbox-shipping-db`**, **sweep/pull = direct MySQL** (an ssh fallback firing is a LOUD signal that
  something is missing, not a working path).
- 🔴 **Cloud runtime = DigitalOcean App Platform** (`ShipRouting/server/`: `Dockerfile.appplatform`,
  `appplatform_create.sh`, console app). Two env flags decide where data comes from — **both are `1` in
  the deployed image**: `ROUTING_HISTORY_DB=1` (`lib/histdb.py` materializes the 6-table history mirror
  from MySQL; replay-proven 0-diff) and `ROUTING_INPUTS_DB=1` (`lib/zip_loaders.py` + DistVol/MFG loaders
  read the MySQL replicas). Flag unset ⇒ files. **Circularity guard:** `--snapshot-inputs` forces
  `ROUTING_INPUTS_DB=0` — a snapshot must never be taken from the replica it feeds. SSOT:
  `ShipRouting/server/DATA_CANON_RULES.md` (per-table OWNERSHIP MATRIX).
- 🔴 **Writer ownership moved to the cloud — never re-run the retired Windows writers.**
  `shopify_orders` is OWNED by the cloud ingest-worker (4h timer + 24h freshness assert). The local sqlite
  copy is a **replica**, refreshed by `_outputs/scripts/pull_shopify_orders_replica.py` on the Monday sweep.
  Retired, must NEVER be re-run on a rebuilt box: `GelPackCalculator/sync_shopify_orders.py`,
  `weather_sync_cron.py` (weather worker is cloud-side since 8/06). `etl_history --load` EXCLUDES
  shopify_orders by default (clobber guard). Rule: a data writer isn't shipped without a scheduled owner
  **and** a freshness assert (`_outputs/scripts/freshness_sweep.py`, Mondays).
- **Manual-upload path (phase 2b):** console `/admin/upload`, `/admin/upload/{id}`, `/admin/data-status`
  + `manual_uploads` MySQL table — how coverage/invoice files get in without Kurt's PC (UPS invoices have
  no API). Upload *kind* values are file-format ids (`ontrac_chicago`), NOT city names.
- 🔴 **New hub: Swedesboro NJ (`LTSC`), live for `_SHIP_2026-08-10` — OnTrac-ONLY.** Group cap
  `TN_NJ = {Nashville, Swedesboro} = 24` pallets (`lib/hub_capacity.py`). **New-hub gotcha that silently
  deletes lanes:** a hub's TNT column must be added in **THREE** places — `engine.TNT_COLS`/`_push_tnt`,
  `optimizer.ROUTE_OF_COL`, and the inline dict in `compute_routing`. Tests pin all three.
- **Engine/report changes since 7/31:** MILP tiebreak is deterministic (two-stage lexicographic — a parity
  pin must include `shipengine_api_key.txt`); fence breakout at full carrier-service-hub granularity;
  `routing_report.py` is the canonical **3-section** summary (carrier-service → hub → combo, + fence
  open-set/exposure); build writes run provenance to disk; `FORECAST_CACHE_ONLY=1` reuses a cached
  forecast; `wx_margin_cutoff` = **-800** (Kurt overrule 2026-08-07, seasonal — review October).
- **`VF_FROM_LEDGER` (asymmetric vF ledger) exists but defaults OFF** — `scripts/gen_rmfg_sheet.py`,
  SSOT `AppyHour/VF_SHEET_RULES.md`. wk0810 is a shadow week (generate both ways + diff); the flip is
  Kurt's call. Don't turn it on during a rebuild.
- **`FridayFlow/`** (`FridayFlow.bat` → local console) now wraps the weekly ritual — SSOT
  `FridayFlow/FRIDAYFLOW_RULES.md`. Local-only launcher; it never deploys to cloud.

**2026-07-31 engine additions (LIVE in `ShipRouting`):**
- 🔴 **Flag/lever defaults now live in EXACTLY TWO files — never hand-copy them again.**
  `ShipRouting/lib/flags.py` (`FLAG_DEFAULTS` + `ensure_flag_defaults()`) owns every boolean engine flag;
  `ShipRouting/lib/levers.py` (`REGISTRY`, per `LEVER_RULES.md`) owns every numeric lever with bounds in
  code. The failure that motivated it: per-surface `setdefault` copies (build.py / Kori `routing_v2.py` /
  `qc_audit.py` / droplet env) DRIFTED, so two "identical" engines tagged the same cohort differently.
  **On rebuild: do NOT restore per-surface flag lists.** Call `ensure_flag_defaults()` BEFORE importing
  `lib.engine` / `lib.optimizer` (several flags are read at import time). Live defaults as of 2026-07-31:
  `HISTORY_SERVICEABILITY, CLOSEST_HUB_DEFAULT, CARRIER_TNT_TRUST, USE_LIVE_WEATHER, MILP_LIVE,
  RESHIP_RECOVERY, FENCE_FEDEX_HD, LARGE_BOX_CLOSEST_HUB, HIST_SVC_PROMOTE, RELATIVE_DELAY_CEILING,
  WX_EXPRESS_AUTOFLY, SHIPSTATION_LANE_VOUCH, EXPECTED_COST_LATE` = **1**; `COST_WEIGHTED_CEILING` = **0**
  (off pending a 4-cohort model run — it LOOSENS a fence). An env `=0` kill-switch always wins.
- 🔴 **Hubs: Chicago OPEN, Indianapolis CLOSED** (`lib/hub_capacity.py:HUB_PALLET_CAPS_BASELINE`) —
  `Chicago: 6.0` is a **PLACEHOLDER** (first real run `_SHIP_2026-08-03`; measure then set the truth),
  `Indianapolis: 0.0` = **CLOSED indefinitely 2026-07-30** (restore = config toggle, not a code edit).
  Semantics that must survive a rebuild: **absent = UNCAPPED, 0 = CLOSED**; the `hub_pallet_caps` settings
  block overlays the baseline; an unknown hub key raises `CapacityConfigError` (a typo must never silently
  uncap a live crossdock). Chicago bills at **Nashville** rates (confirmed by Blake). The pallet gate and
  the MILP are now **per-hub**, not Indy-singular.
- **`MAX_ICE_PLUS` (Kurt 2026-07-30, default ON)** — a TOGGLE, not a computation: every non-tray order gets
  the ice bump, **air INCLUDED** (the old "air already solves it" premise is RETIRED and survives only on
  the legacy path). `scripts/ice_distvol_workflow.py`; empty live set/target list is now an ERROR.
  🔴 Seasonal: summer-only — PARK it, never delete it. RMFG accepts ONLY the `_vF.xlsx`; run `presend_check`.
- **Cost model rename + late pricing** — `warm_cost` → **`fail_cost`** (neutral catch-all), `p_churn_warm` →
  `p_churn_fail`; `EXPECTED_COST_LATE` prices `late_rate × late_cost_unit` into lane scoring among GOOD
  survivors only (never admits a fenced lane). North star signed 2026-07-29: lowest **expected total** cost,
  late is priced not forbidden, two floors never for sale (2-day promise, cold arrival).
- **`HIST_SVC_PROMOTE` + `SHIPSTATION_LANE_VOUCH`** — a history-proven zip3 lane satisfies the serviceability
  gate (**never a whole STATE** — the state layer stays dropped); ShipStation committed-ground ≤2 vouches a
  no-history FedEx/UPS lane for **COOL DESTS ONLY** (dormant in summer heat).
- **Replay gate is deterministic now** — freeze carrier TNT with `CARRIER_TNT_CACHE_ONLY`; `gate_attribution.py`
  turns the replay diff into PASS/FAIL. Any pre-2026-07-30 gate result is untrustworthy.
- **`AppyHour`: hourly ParcelPanel exception sweep → private `#exceptions` Slack** (Apps Script hosted in the
  existing Running Reship project; webhooks were investigated and CANNOT replace hourly polling).
- **Code index:** each repo carries its OWN `.codegraph/` (`ShipRouting`, `AppyHour`,
  `AppyHour/GelPackCalculator`, `repos/odysseus`). The watcher daemon is unreliable — run `codegraph sync`
  per repo and verify with a symbol you just added; a bare query with no `projectPath` hits the un-indexed
  workspace root and returns "no results", which reads as "the symbol doesn't exist".

**2026-06-28 engine additions (LIVE in `ShipRouting`):**
- **Veho serviceability is now LIVE, no file needed** — `load_veho()` pulls the PUBLIC no-auth API
  `GET https://api.shipveho.com/v2/serviceable-zips` every build (writes a cache; falls back cache→xlsx on
  failure). It can only DEACTIVATE a zip the xlsx still lists, never inject a live-only zip. The GroundPlusSuite
  xlsx now supplies **per-hub TNT only**, not the serviceable set. On rebuild: Veho serviceability needs no file.
- **OnTrac coverage = manual RMFG `ontrac_master.xlsx`** (we hold NO OnTrac account/API) with a **staleness
  guard** — `load_ontrac` warns when the file is >14d old. No live OnTrac feed without RMFG's account creds.
- **Fixed_Route Indy unevictable** — the Indy pallet gate no longer picks a Fixed_Route package as an eviction
  victim (`resolve_apply` keeps it on Indy at apply → bumping it was phantom relief → live cap breach). Fixed
  packages stay in `keep`; fixed-only overage prints a loud `MANUAL` warning instead of crashing.
- **Ice off FINAL post-gate eff** — gate-bumped orders stamp `_ice_eff`; `build.py` sizes gel off it (was using
  the stale pre-bump eff → tier-1 Indy spills were under-iced one gel pack at 75-85F).
- **Exact-zip proof bar = 3** (`hist_risk.EXACT_ZIP_MIN_N`; zip3/coarser rungs stay 5). Delay fence is 3%
  late-rate / flat `DELAY_MIN_N=20` (NOT reship-rate; doc previously mis-stated 10%).
- **Constraints SSOT discipline:** `ShipRouting/ROUTING_RULES.md` is the single source of truth — read before any
  change, add missing constraints first, gotchas/negatives-first. Global rule `~/.claude/rules/feature-constraints-doc.md`.

**2026-07-24 engine additions (LIVE in `ShipRouting`):**
- **Veho SUSPENDED at Nashville/TN — Veho = Indianapolis ONLY now** (`lib/features.CARRIER_HUBS` is the single
  authority; memory `veho-tn-suspended`). Legal-lane + serviceability now reject Veho@Nashville; `nash_alt`/
  `reship_route` `legal_lane` gaps fixed so a suspended lane can't sneak back in. **On rebuild:** do NOT restore
  Nashville as a Veho hub — CARRIER_HUBS drives it. Revert path = re-add Nashville to Veho in CARRIER_HUBS.
- **`LARGE_BOX_CLOSEST_HUB` flag (default OFF)** — grounds far-dest Large Boxes at their closest hub, FedEx-pinned,
  with a decision-log entry (§0 fix: far Large Boxes were routing NY→Dallas = 5-day warm). Enable only per Kurt.

**2026-07-02 engine additions (LIVE in `ShipRouting`):**
- **`MILP_LIVE=1` is now CANON (default ON)** (`ShipRouting/build.py`, commit e24375f) — the HiGHS solver
  **makes the live Indy keep/spill decision** (superseding the greedy 6-pallet gate). Once upon adoption it
  is autonomous (no human-gate). Kill with `MILP_LIVE=0`. Snapshots `milp_live/` vs `greedy_shadow/` for
  `milp/postmortem_ab.py` warm-rate + cost A/B.
- **`FENCE_FEDEX_HD=1` is now CANON (default ON)** (`ShipRouting/build.py`, commit bb4f716) — FedEx Home
  Delivery orders become **hard fences at non-Indy hubs** (Dallas/Nashville; removes positive-pinning). Indy
  is fence-free (can still spill MILP/greedy). Clarifies that FedEx Home is a carrier-of-last-resort exception,
  not a hub route.

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
  (Veho=Indianapolis only (Nashville SUSPENDED 2026-07-24), UPS=Dallas-only) so dirty data can't route. Mirrored in the ingest path.
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
| **DB snapshot** | Google Drive: newest `shipping.weekly-<date>.db` (fallback `shipping.pre-cutover-2026-06-11.db` id 14Wjf8EOPnSqh_kwBPQSK9403AIAnq-4l) | ~130MB SQLite. RESTORE TO `C:\AppyHourData\shipping.db` (canonical — see §5.1; NEVER `%APPDATA%` or the MSIX LocalCache shadow). |
| **Logic/docs bundle** | Google Drive: `coldchain-logic-backup-<date>.zip` (2026-06-11 id 1HY0k9cXfbglFsYP1F8qcGEK3KcioEHSu) | SHIPPING_PIPELINE.md, ENGINE_GUIDE, handoffs, contract, audits, vault notes, this file |
| **Vault + skills bundle** | Google Drive: `coldchain-knowledge-backup-<date>.zip` | `~/.knowledge/` (Obsidian vault) + key `~/.claude/skills/` |

## 2. Rebuild order (do in sequence)
1. **Install:** Anaconda Python (system uses `C:\Users\Work\anaconda3\python.exe`), git, gh CLI, Claude Code. `pip install requests openpyxl pywebview` (Kori needs pywebview **netfx** backend — .NET Framework, NOT coreclr).
2. **Clone** the three repos into `C:\Users\<user>\Claude Projects\` — AppyHour and ShipRouting side-by-side, GelPackCalculator INSIDE AppyHour/ (path-coupled: `kori/routing_v2.py` and ShipRouting lib hardcode `Claude Projects\AppyHour` + `Claude Projects\ShipRouting` on sys.path — keep these exact folder names or fix the sys.path blocks).
3. **Restore DB** from Drive snapshot → **`C:\AppyHourData\shipping.db` (canonical — see §5.1; NEVER `%APPDATA%` or the MSIX LocalCache shadow)**. Then run the importers to catch up the gap since snapshot (sources: Gmail/IMAP carrier emails — history re-downloadable from carrier portals + RMFG emails). Verify with `_outputs/scripts/shipping_db_healthcheck.py --verbose` (should print OK).
4. **Restore vault + skills** from the knowledge zip → `~/.knowledge/`, `~/.claude/skills/`.
5. **Recreate settings + creds** (NOT in any backup — Kurt re-enters):
   - `%APPDATA%/AppyHour/gel_calc_shopify_settings.json` — Shopify creds, OpenWeatherMap key, per-state transit config, zip overrides (a copy may exist in the knowledge zip; verify freshness).
   - `%APPDATA%/AppyHour/portal_creds.json` — FedEx/UPS billing portal logins (template in GelPack docs).
   - `%APPDATA%/AppyHour/shipengine_api_key.txt` — ShipStation/**ShipEngine v2** key (header `API-Key`) for carrier-TNT trust. Missing key → `CARRIER_TNT_TRUST` safely no-ops.
   - Gmail/IMAP app passwords, Google OAuth (gws CLI: Internal app, 8 scopes), Gorgias API key, GitHub auth.
6. **Re-register scheduled tasks:**
   - `appyhour_sync_on_logon` → `GelPackCalculator/sync_logon.py` (logon trigger)
   - `AppyHour Wednesday Ops Run` → `AppyHourMCP/register_wednesday_task.bat` (Wed 14:00; includes routing post-mortem)
   - Weather actuals daily 3:00 → `ShippingReports/weather_sync_cron.bat` (now resolves DB via `db_path()`/`APPYHOUR_DB_PATH`)
   - `friday-forecast-refresh` (Fri 12:05) → `ShipRouting/scripts/friday_forecast_refresh.py` (8-day→zip5 ice re-size; handoff 2026-07-02)
   - `appyhour-db-healthcheck` (daily ~noon) → `_outputs/scripts/shipping_db_healthcheck.py` (live-DB `quick_check`, Slack-on-fail; §5.1)
7. **Verify:** `python AppyHour/scripts/validate_refactor_db.py --copy <restored>` vs expectations; run `GelPackCalculator/auto_import.py` (expect clean totals); launch Kori via `GelPackCalculator/kori/run_webview.bat`; run `ShipRouting/build.py` on the current `_SHIP_` cohort.

## 5.1 🔴 DB LOCATION & CORRUPTION RECOVERY (v3 2026-07-08 — read before touching the DB)

**Canonical location (approved by Kurt 2026-07-08):**
```
C:\AppyHourData\shipping.db          ← the ONE file. Restore here. Write here.
```
`backups\`, the `.writelock`, and sqlite sidecars live beside it.

**Why here (the failure this prevents):** the DB previously lived in `%APPDATA%\AppyHour` — a folder
MSIX virtualizes. Packaged (Claude/MCP) processes saw a copy-on-write shadow; the 7/07 Claude app
update deleted the shadow image and stranded a stale wal → false MISSING + "malformed" container
reads + a near-miss destructive restore (7/08 incident). `C:\AppyHourData` is not in the MSIX VFS
list: every context — packaged app, scheduled task, bare terminal — sees the one physical file. No
shadow can exist.

**Rules:**
1. Resolution order everywhere: `APPYHOUR_DB_PATH` env → `C:\AppyHourData\shipping.db` if it
   exists → legacy `%APPDATA%\AppyHour\shipping.db` (transition fallback only).
2. NEVER hardcode a shipping.db path. AppyHour code → `appyhour_lib.paths.db_path()`. ShipRouting →
   `lib.dbpath.shipping_db_path()`. Standalone scripts import one of those.
3. NEVER symlink/hardlink/junction a legacy path to the new one — sqlite creates `-wal`/`-shm`
   beside whichever NAME was opened; two names for one image = two wals = corruption machine.
4. A `shipping.db` appearing at any legacy path (Roaming or LocalCache) while the canonical exists =
   **split-brain**: healthcheck alarms CRITICAL → find and fix the straggler writer. Merge nothing
   blindly.
5. Settings JSONs stay in `%APPDATA%\AppyHour` (3-app shared surface, unchanged by this move).
6. `C:\AppyHourData` is in the offsite-backup set + rescue list.
7. **The carrier TNT cache moved here too (2026-08-10)** — same MSIX split-brain failure as the DB:
   `%APPDATA%\AppyHour\routing\` is VIRTUALIZED, so the packaged app and a bare terminal each saw
   their own `carrier_tnt_cache`, and a prewarm run in one context never warmed the other. Canonical
   home is now `C:\AppyHourData` (`ShipRouting/lib/carrier_tnt.py:46-53`). Corollary that burned us:
   two checks landing on the SAME side of a virtualization split prove nothing — verify from a
   packaged AND an unpackaged context. `%APPDATA%\AppyHour\routing` coverage files are likewise
   sandbox-only and are NOT captured by a real-profile backup.

**The MSIX LocalCache path is NOT the live DB** (belief held 7/01–7/08, now disproven):
`...\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\AppyHour\shipping.db` is the Claude
package's **copy-on-write shadow**. Packaged (Claude/MCP) processes get their `%APPDATA%` virtualized:
reads fall through to the real Roaming file when no shadow copy exists, writes fork a diverging copy into
LocalCache. The old "samefile=True / MSIX junction" verification was run from INSIDE the container, where
the VFS makes both names hit one file — an illusion. On 2026-07-08 the shadow's db image vanished (app
servicing) while the real Roaming file stayed live; the LocalCache-pinned healthcheck false-alarmed
MISSING, a stale shadow `-wal` made container `connect_ro()` reads return "malformed", and a bogus restore
handoff nearly overwrote 5 days of data.
- **NEVER restore to, or point `APPYHOUR_DB_PATH` at, the LocalCache path** — that manufactures a
  split-brain (two diverging DBs, packaged vs unpackaged writers).
- If shipping.db exists at BOTH paths as separate files → split-brain: the healthcheck alerts CRITICAL;
  reconcile before any write (the Roaming file + noon-sync freshness is the tiebreaker).
- A stale frames-bearing `shipping.db-wal` in the LocalCache dir with no db image beside it breaks all
  packaged/MCP reads — quarantine it from a real terminal (rename to `shipping.db-wal.orphan-<date>`).

**Corruption cause (2026-06-27 + 2026-07-01):** two processes writing the WAL DB at once (e.g. a manual
`weather_sync_cron.py` racing the live MCP servers) → a checkpoint folds bad pages in → `database disk image
is malformed`. `appyhour_lib/db.py` (WAL+busy_timeout) serializes lock *waits* but does not stop two
independent checkpointers. **Rule: only one writer at a time; Claude/agents stay READ-ONLY on shipping.db
(`connect_ro`).** [[shipping-db-msix-wal-corruption]]

**Enforcement (2026-07-02, commit 7d5e1a5):** `appyhour_lib/db.py` `connect()` now acquires an advisory
single-writer lock — `<real_db>.writelock` beside the canonical DB (atomic `O_CREAT|O_EXCL`; JSON
`{pid, create_time, script, started_at, host}`). A 2nd writer waits `AH_WRITE_LOCK_WAIT` (default 90s)
then raises **`DBWriterBusy`** naming the holder instead of racing a checkpoint. Crash-safe: dead-PID /
reused-PID (create_time mismatch) / over-`AH_WRITE_LOCK_MAX_AGE` (default 1800s) locks auto-break; nested
`connect()` reentrant via refcount; `atexit` releases. **Escape hatch: env var `AH_WRITE_LOCK_DISABLE=1`.**
**`connect_ro()` never touches the lock.** Only protects writers that go through `connect()` — a raw
`sqlite3.connect(shipping.db)` still bypasses it, so ALL writers must route through `connect()` (migration
in progress; `shipping_invoice_db.init_db` done). If a run dies with a stuck lock the file is safe to delete
manually: `rm <db>.writelock`. Tests: `AppyHour/tests/test_db_writelock.py` (temp DB only, never the live file).

**Recovery (main image usually fine — the `-wal` sidecar is the corrupt part):**
1. Read still works via `sqlite3.connect('file:<db>?mode=ro&immutable=1', uri=True)` (ignores the WAL).
2. Newest clean restore point = `%APPDATA%/AppyHour/backups/shipping.after-ingest-*.db` (integrity-gated,
   ~every ingest) or `shipping.weekly-*.db`. Scan candidates with a `PRAGMA quick_check` loop.
3. Rename-guard the live file: `mv shipping.db aside` — on Windows this FAILS if a process holds it (so a
   success proves no live writer is attached). Then `cp <clean-backup> shipping.db; rm -f shipping.db-wal
   shipping.db-shm`. Verify `quick_check` = ok + row counts.

**Recurring guard:** `_outputs/scripts/shipping_db_healthcheck.py` (rewritten 2026-07-08) — dual-path
exactly-one-file rule (separate files at both Roaming + LocalCache = CRITICAL split-brain; neither =
CRITICAL missing; frames-bearing orphan `-wal` beside the empty path = CRITICAL, it breaks packaged/MCP
reads), then read-only immutable `quick_check` + core-table check. Slack-on-failure via
`appyhour_lib/notify.py` (`AH_SLACK_WEBHOOK`). Schedule daily ~noon.

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
- Veho lanes = Indianapolis ONLY (Nashville SUSPENDED 2026-07-24); two-gate rule for any new carrier×hub. Enforced in code by
  `lib/features.legal_lane`/`CARRIER_HUBS` (UPS=Dallas-only too) at lane-build AND at invoice ingest — an
  impossible carrier@hub is a data bug (the 475 Veho@Dallas mis-attribution), not a routable lane.
- **Carrier coverage exports are the serviceability AUTHORITY.** A last-mile carrier delivers a zip ONLY if that zip is in its coverage export (`load_veho` active / `load_ontrac`) — legality(carrier@hub) ≠ coverage(serves the *zip*). The shared `lib/engine.serviceability_gate` enforces it (both build.py AND Kori call it) (uncovered Veho/OnTrac → FedEx Home Delivery, same hub). `HISTORY_SERVICEABILITY` may set TNT/speed but **never creates serviceability** (its STATE layer did → 391 mis-routes, dropped 2026-06-27). Keep exports current: OnTrac via `GelPackCalculator/download_ontrac_imap.py`; Veho via the GroundPlusSuite export (`load_veho` parses the multi-sheet format).
- Dedup `(invoice_id, tracking)` at cohort rollup, one physical row per tracking at storage.
- FedEx 2Day Express = last resort ($25); carrier cost order Veho $6 → OnTrac $8 → UPS $11 → FedEx HD $15.
- **The Indy 6-pallet cap decision is made LIVE by the MILP solver** (`MILP_LIVE=1` CANON in build.py since
  2026-07-02; `engine._indy_milp_gate`, HiGHS). Greedy `_indy_pallet_gate` = the automatic FALLBACK on any
  solve failure and the kill-switch path (`MILP_LIVE=0`). Ice re-sizes off the spilled lane's `_ice_eff`;
  every build snapshots `milp_live_/greedy_shadow_<tag>.json` for `milp/postmortem_ab.py` A/B.
- **Reships are reason-aware** (`RESHIP_RECOVERY=1`, 2026-07-02): carrier-failure reships get a proven
  healthy 1-day ground lane EXCLUDING the failed carrier (derived read-only from fulfillments) else 2Day
  AIR — never generic 2-day ground. Non-failure reasons route normally. Kill `RESHIP_RECOVERY=0`.
- **Dead-man-switch watchdogs** (rebuild these or failures go silent again): `appyhour_lib/heartbeat.py`
  ledger + `scripts/automation_health.py` daily (HEARTBEAT_RULES.md is the SSOT) and
  `ShipRouting/scripts/loop_scorecard.py` weekly (decision quality). Both Slack via `appyhour_lib.notify`.

## 5. Refresh cadence for this backup
Logic zip + DB snapshot to Drive **weekly** (goal: automated in M2 `pipeline_run.py`). Repos: push on every work session. THIS FILE lives in the logic zip + all three repos' awareness docs.

✅ **2026-06-27 — OFFSITE UPLOAD WORKS** (gws dependency removed). `scripts/backup_offsite.py` now uploads
via the **`drive.file` OAuth token** (same one `upload_sheet.py` uses; resumable, gws-independent) — verified
uploading `shipping.weekly-<date>.db` (132MB) + logic/knowledge/reference zips to Drive. Run it with the
**`appyhour-backup` skill** (repeatable: refresh THIS doc → `backup_offsite.py` → verify the `OFFSITE:` lines).

**2026-07-02 security updates:** `backup_offsite.py` now excludes `browser_state`/`browser_profile` dirs
from the knowledge zip (commit fd6790d) — prevents cleartext cookie/login data leaks on Drive. Creds bundle
encryption still required: if **`AH_BACKUP_PASSPHRASE` is unset** the encrypted creds bundle is SKIPPED (refuses
to upload plaintext secrets), leaving the Drive backup with DB + docs + knowledge but NOT the API keys/tokens.
**As of 2026-08-14 the passphrase IS set** (user env), so the creds bundle uploads — to restore it you need that
passphrase from Kurt. Code is offsite via the three GitHub repos regardless.
