# RESTORE — AppyHour New-Machine Setup Checklist

**Derived from `REBUILD-WITH-AI.md` (2026-06-11).** That file may be stale; this is the
runnable version. Work top-to-bottom. Run `python scripts/restore_check.py` at any point
to see what's still missing.

> ⚠️ **Biggest risk: the username changed.** The old machine was `C:\Users\Work\`. A LOT of
> config hardcodes `C:\Users\Work\` and `Claude Projects\`. If your new username is not `Work`,
> see **§6 Path Sweep** — nothing routing-related will work until those are fixed.

---

## 0a. ✅ PREFERRED: you have the old SSD — copy these first
If the dead machine's SSD is readable (USB-SATA adapter / enclosure), it is a **better source
than the Drive backups** — it has the creds, the full vault, and the live DB that were never
backed up. Copy these from the old disk (mounted as e.g. `E:\`) before falling back to Drive:

| From old disk (`Users\Work\...`) | To new machine | Why it matters |
|---|---|---|
| `AppData\Roaming\AppyHour\` (whole folder) | `%APPDATA%\AppyHour\` | **Creds + settings + live `shipping.db`** — in NO backup. Skips §3 and §5 entirely. |
| `.knowledge\` (whole folder) | `~/.knowledge\` | Full vault, **no 06-11 gap** — better than the knowledge zip. Skips §4 vault. |
| `.claude\skills\` | `~/.claude\skills\` | Skills, current. |
| `.claude\hooks\` and `.claude\settings.json` | `~/.claude\` | Stop-hook, permissions, MCP prefs. |
| `AppData\Roaming\Claude\claude_desktop_config.json` | same path | MCP server registration (patch paths per §6). |
| `Claude Projects\AppyHour`, `ShipRouting`, `GelPackCalculator` | your projects root | **Run `git status` in each** — recover any uncommitted local changes the repos don't have. |
| Scheduled-task exports / `.bat` files | — | Reference for §7 (Task Scheduler entries live in the registry, not files — recreate via §7). |

After copying, run `python scripts/restore_check.py` — most rows should already be `[ OK ]`.
Then skip to §1 (runtime) and §6 (path sweep) — §3/§4/§5 are mostly satisfied by the disk copy.

## 0. Decide your paths first
Old machine used:
- Home: `C:\Users\Work\`
- Projects root: `C:\Users\Work\Claude Projects\`
- Python: `C:\Users\Work\anaconda3\python.exe`

If you can recreate the **same username `Work`**, do it — it makes every hardcoded path Just Work
and you can skip §6 almost entirely. If not, note your new paths; you'll patch them in §6.

## 1. Install the runtime
- [ ] **Anaconda Python 3.10+** (gives you Python + Tcl/Tk for tkinter)
- [ ] **.NET Framework** runtime — pywebview MUST use the **netfx** backend, NOT coreclr/.NET 8 (Kori desktop)
- [ ] **git**, **gh CLI**, **Node.js + npm**, **Claude Code**, **Claude Desktop**
- [ ] `pip install -e ".[dev,fulfillment,shipping,mcp]"` from the AppyHour repo root
- [ ] `npm i -g @shopify/dev-mcp` (the shopify-dev MCP server in `.mcp.json`)

## 2. Clone the repos (path-coupled — keep folder names exact)
Into your projects root (`...\Claude Projects\`):
- [ ] `github.com/kurt-lgtm/AppyHour`  (this repo)
- [ ] `github.com/kurt-lgtm/ShipRouting`  → **side-by-side** with AppyHour
- [ ] `github.com/kurt-lgtm/GelPackCalculator` → **INSIDE** `AppyHour/` (it's gitignored in this repo)

`kori/routing_v2.py` and ShipRouting hardcode `Claude Projects\AppyHour` + `Claude Projects\ShipRouting`
on `sys.path`. Keep these names or fix the sys.path blocks (§6).

## 3. Restore the database
> ✅ **Latest verified on Drive (checked 2026-06-22):** weekly auto-backups are running.
> Use the newest, NOT the stale `pre-cutover` snapshot the old recovery doc named.
- [ ] Download **`shipping.weekly-2026-06-21.db`** — id `1wcYREgMgGw339GYmDmzTBxhQsWDzhE1c` (130 MB, yesterday)
- [ ] Copy to `%APPDATA%\AppyHour\shipping.db`
- [ ] Catch up the gap since 06-21: re-run importers (carrier emails re-downloadable from carrier portals + RMFG emails)
- [ ] Fallback if the weekly is corrupt: `shipping.weekly-2026-06-19.db` (id `1sTPOsEvR4FKo3_bcSYyOtHI5qVWVkGjZ`) or the original `shipping.pre-cutover-2026-06-11.db` (id `14Wjf8EOPnSqh_kwBPQSK9403AIAnq-4l`)

## 4. Restore vault + skills
- [ ] **`coldchain-logic-backup-2026-06-21.zip`** — id `1BHVQuZPs93tqKMKI_C_26Hm1ihmUtsE9` (latest; docs/handoffs/contracts/this file)
- [ ] ⚠️ **`coldchain-knowledge-backup-2026-06-11.zip`** — id `1zwKaRnmzchrKEsLNlgaU3A4s7OPydiUH` — extract to `~/.knowledge/` + `~/.claude/skills/`.
      **This is the ONLY knowledge backup on Drive and it's 11 days stale** — the weekly job backs up logic+DB but NOT the vault/skills. Expect to lose vault notes written since 06-11; reconstruct from the logic zip + git history. (See §5a — fix the backup job.)

## 5. Recreate creds + settings (NOT in any backup — re-enter by hand)
Create `%APPDATA%\AppyHour\` if missing, then:
- [ ] `gel_calc_shopify_settings.json` — Shopify creds, OpenWeatherMap key, per-state transit config, zip overrides
- [ ] `inventory_reorder_settings.json` — Shopify store URL + access token (used by `appyhour_lib/credentials.py`)
- [ ] `portal_creds.json` — FedEx/UPS billing portal logins
- [ ] For `cut_order_server/`: copy `.env.example` → `.env`, fill real values (FLASK_SECRET_KEY, RECHARGE_TOKEN, GOOGLE_SVC_ACCOUNT_JSON, DO Spaces keys, DATABASE_URL)
- [ ] Gmail/IMAP app passwords, Google OAuth service account (8 scopes), Gorgias API key, GitHub auth (`gh auth login`)

Alternatively set env vars instead of the JSON: `SHOPIFY_STORE_URL`, `SHOPIFY_ACCESS_TOKEN`,
`SHOPIFY_API_VERSION`, `OPENWEATHER_API_KEY` (see `appyhour_lib/credentials.py`).

## 5a. Backup gap — FIXED 2026-06-22
`scripts/backup_offsite.py` now also bundles `~/.knowledge/` + `~/.claude/skills/` into
`coldchain-knowledge-backup-<date>.zip` and uploads it weekly. So from the next backup run
onward the vault/skills are covered. **But the newest knowledge zip on Drive *before* that fix
is still 2026-06-11** — until a fresh weekly run completes, restore from the dead disk's
`~/.knowledge` if recoverable.

## 6. Path sweep (ONLY if username/paths differ from `C:\Users\Work\Claude Projects`)
Files that hardcode the old path — patch each to your new path:
- [ ] `.mcp.json` — `command` + `args` python/server paths
- [ ] `AppyHourMCP/claude_desktop_config_snippet.json` and your live `%APPDATA%\Claude\claude_desktop_config.json`
- [ ] `AppyHourMCP/install.bat` (note: uses `C:\Users\Work\AppyHour`, no "Claude Projects" — old drift)
- [ ] `AppyHourMCP/register_wednesday_task.bat` → `BAT=` path
- [ ] `ShippingReports/*.bat` (`weather_sync_cron.bat`, `safety_factor_sweep.bat`, `melt_efficiency_calibrator.bat`, `postmortem_runner.bat`)
- [ ] `kori/routing_v2.py` + ShipRouting `lib/` sys.path blocks
- [ ] Any `command_center.py` / `postmortem_runner.py` absolute paths

`python scripts/restore_check.py --grep-paths` lists every file still containing `C:\Users\Work`.

## 7. Re-register scheduled tasks (Windows Task Scheduler)
> 💾 **Authoritative record is on the old SSD:** Windows stores every registered task as XML in
> `C:\Windows\System32\Tasks\` (look for ones named `AppyHour*` / `appyhour*`). Copy those out and
> re-import on the new box with `schtasks /Create /XML "task.xml" /TN "<name>"` — that captures the
> EXACT trigger/args, including any task not listed below. The list here is reconstructed from the
> repo and may be incomplete.

| Task name | Schedule | Target | Registration script |
|---|---|---|---|
| `appyhour_sync_on_logon` | At logon | `GelPackCalculator/sync_logon.py` (orchestrates sync_all_carriers + backfill_sync + auto_import) | none in repo — recreate from SSD XML |
| `AppyHour Wednesday Ops Run` | Weekly Wed 14:00 | `AppyHourMCP/wednesday_ops_run.bat` (incl. routing post-mortem) | ✅ `AppyHourMCP/register_wednesday_task.bat` |
| `AppyHour Weekly Offsite Backup` | Weekly Sun 02:00 | `scripts/backup_offsite.py` | ✅ `scripts/register_backup_task.bat` |
| `AppyHour Weather Actuals` | Daily 03:00 | `ShippingReports/weather_sync_cron.bat` | none — `schtasks /Create` one-liner in `HANDOFF.md` |
| Postmortem runner (name TBC) | Mondays 09:00 | `ShippingReports/postmortem_runner.py` | none — schedule was a "suggestion"; confirm against SSD XML whether it was ever registered |

- [ ] Recover `C:\Windows\System32\Tasks\AppyHour*` XML from the SSD → re-import (authoritative)
- [ ] For the two with `register_*.bat`: just run the .bat (after the §6 path sweep)

## 8. Verify
- [ ] `pytest` (from repo root) — core logic green
- [ ] `python scripts/validate_refactor_db.py --copy <restored.db>` vs expectations
- [ ] `python GelPackCalculator/auto_import.py` — expect clean totals
- [ ] Launch Kori: `GelPackCalculator/kori/run_webview.bat`
- [ ] `python ShipRouting/build.py` on the current `_SHIP_` cohort
- [ ] In Claude Desktop, confirm the `appyhour` MCP server connects

---

### Non-negotiable domain rules (sanity check after restore)
- Cohort = `_SHIP_<Monday>` TAG, never scan date. On-time = delivered≤2 ÷ FULL cohort.
- Canonical DB columns: `zip_code` (never `zip`), `weather_history.zip_prefix` = 5-digit.
- Veho lanes = Indianapolis + Nashville ONLY. OnTrac ≡ LaserShip.
- Carrier cost order: Veho $6 → OnTrac $8 → UPS $11 → FedEx HD $15 → FedEx 2Day $25 (last resort).
