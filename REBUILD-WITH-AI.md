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
- Veho lanes = Indianapolis + Nashville ONLY; two-gate rule for any new carrier×hub.
- Dedup `(invoice_id, tracking)` at cohort rollup, one physical row per tracking at storage.
- FedEx 2Day Express = last resort ($25); carrier cost order Veho $6 → OnTrac $8 → UPS $11 → FedEx HD $15.

## 5. Refresh cadence for this backup
Logic zip + DB snapshot to Drive **weekly** (goal: automated in M2 `pipeline_run.py`). Repos: push on every work session. THIS FILE lives in the logic zip + all three repos' awareness docs.
