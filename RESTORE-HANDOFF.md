# AppyHour Restore — Handoff (2026-06-22)

New-machine restore after the old PC died. This is the live status. Source of truth for the
original plan is `RESTORE.md`; this file records what's actually been done on the new box.

## Machine / paths (canonical — do NOT move)
- New machine: `MEIMEI_KURT\Work` (old was `HotRichie`). Username is still `Work`, so hardcoded
  `C:\Users\Work\...` paths Just Work — §6 path sweep is a no-op.
- Repos (kept at the path the code hardcodes — moving them breaks `.mcp.json`, `kori/routing_v2.py`
  sys.path, the `.bat` task scripts, OAuth token paths):
  - `C:\Users\Work\Claude Projects\AppyHour`  (this repo)
  - `C:\Users\Work\Claude Projects\ShipRouting`  (side-by-side)
  - `C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator`  (nested)
- Python: `C:\Users\Work\anaconda3\python.exe` (3.13). Deps installed (`pip install -e .[dev,fulfillment,shipping,mcp]` + `npm i -g @shopify/dev-mcp`).

## Restored & verified
- `restore_check.py` → **0 required items missing.**
- `%APPDATA%\AppyHour\`: `shipping.db` (live, 125MB), `gel_calc_shopify_settings.json`,
  `portal_creds.json`, `inventory_reorder_settings.json`.
- `~/.knowledge` (Obsidian vault, full, with `.git`), `~/.claude/skills` (42 skills).
- Cut-order gitignored creds (were never on GitHub; recovered from SSD):
  `InventoryReorder/dist/drive_oauth_token.json`, `InventoryReorder/dist/inventory_reorder_settings.json`,
  root `shipping-perfomance-review-accd39ac4b78.json` (Google service account).
- `pytest` green (161 pass, excluding one stale module — see below).
- `appyhour` MCP server verified connecting (smoke_test_mcp.py).

## Cut order specifics
- The `inventory_reorder_settings.json` `shopify_access_token` (shpat_) is valid. Tested against
  store `504ac4.myshopify.com`: **200** on `orders.json` (REST), GraphQL `orders`, `products.json`,
  `shop.json`. **403** on `locations.json` and `inventory_levels.json` (app lacks read_locations /
  read_inventory). The cut order references `locations` 2×; if it hits one, add those scopes to the
  InventoryReorder custom app in Shopify admin, then re-test.
- `cut_order_server/.env` is NOT local (only `.env.example`); its real `.env` lives on the
  DigitalOcean droplet. Not needed for the local cut order.

## Restore gap found 2026-06-22 (cut order)
- The fulfillment web app (`InventoryReorder/fulfillment_web/app.py`, port 5187) imports
  `command_center`, which is a SHIM that re-exports `cc.engine` from a **separate repo**:
  `C:\Users\Work\Claude Projects\CommandCenter`. That repo was NOT cloned during restore and was
  missing from this handoff → server crashed `ModuleNotFoundError: No module named 'cc'`. No GitHub
  remote exists (`kurt-lgtm/CommandCenter` 404). Recovered by copying from SSD:
  `E:\Users\Work\Claude Projects\CommandCenter` → canonical path (`.pytest_cache` blocked by ACL,
  irrelevant). Server then starts; both `/api/calculated_inventory` + `/api/tuesday_projection`
  return 200. Launch: `python app.py --browser` (Flask on 5187; native pywebview window otherwise).
  `cc` shares state via `~/.cc/command_center.db`. **Push CommandCenter to GitHub so the next restore can clone it.**

## Restore gap #2 found 2026-06-22 (cut order, cont.)
- `claude_agent_sdk` was NOT installed (the cut-order agents `agents/tuesday_cut_order.py`,
  `agents/monday_swap_planner.py` import it) and it is NOT declared in `pyproject.toml`, so the
  `pip install -e .[...]` restore step never pulled it. Fixed: `pip install claude-agent-sdk`
  (0.2.106). Nested `query()` spawns the `claude` CLI (`C:\Users\Work\.local\bin\claude`, on PATH)
  which uses its own logged-in auth — no `ANTHROPIC_API_KEY` needed. **Add `claude-agent-sdk` to
  pyproject deps so the next restore installs it.**

## Uncommitted local changes worth committing (made during restore)
- `pyproject.toml` — added `[build-system]` + `[tool.setuptools] packages=["appyhour_lib"]`
  (modern setuptools rejected flat-layout auto-discovery; editable install failed without this).
- `scripts/backup_offsite.py` — now encrypts `%APPDATA%\AppyHour\*.json` + `cut_order_server/.env`
  into `coldchain-creds-backup-<date>.zip.enc` (scrypt+Fernet, in-memory). New `scripts/decrypt_creds.py`
  restores it. Needs env `AH_BACKUP_PASSPHRASE`; unset = skip-with-warning (never plaintext).
- `scripts/utilities/smoke_test_mcp.py` — `command="python"` → `sys.executable` (Windows Store stub).

## Known pre-existing issue (not a restore gap)
- `tests/test_cut_order_helpers.py` imports `InventoryReorder/cut_order_generator.py`, deliberately
  deleted in commit 6e71f31. Stale test — delete or `@pytest.mark.skip`. (Why pytest shows 1 collection
  error; the other 161 pass.)
- `smoke_test_mcp.py` expects `appyhour_shipping_analysis` (moved to AppyHourShippingMCP 2026-06-01,
  see server.py:40-44) and a standalone `appyhour_get_weather` (folded into gelcalc). 4 "failures"
  there are stale expectations, not server faults.

## Still pending / optional
- **Scheduled tasks NOT yet registered.** 13 task XMLs recovered to `C:\Users\Work\restore_survey\tasks\`
  (8 loose + 5 under `AppyHour\`). Register elevated with `schtasks /Create /XML <file> /TN <name> /RU Work /F`
  (InteractiveToken → no password). The registration + a worktrees/archives recovery pass were launched
  but stalled on **unapproved UAC prompts** — re-run when someone can click Yes.
- **Worktrees + archives** not yet pulled (same stuck UAC pass). Old worktrees were under
  `E:\Users\Work\Claude Projects\.claude\worktrees\`. Transcript archives already staged in
  `C:\Users\Work\.claude-from-ssd\transcript-archive\`.
- **Backup task** needs `AH_BACKUP_PASSPHRASE` in its environment to back up creds (else it skips them).
- **Edge tabs**: clickable index at `C:\Users\Work\restore_survey\edge\old_tabs.html` (228 pages from the
  last session). Passwords are NOT recoverable (App-Bound Encryption confirmed). For exact open-tab
  fidelity, native session restore from `restore_survey\edge\Sessions\` into the live Edge profile is an
  option (Edge closed; can overwrite new session).

## SSD access (for anything still on the old disk)
- Old SSD mounted at `E:` (Disk 1, USB enclosure). Profile folders (`E:\Users\Work\...`) are ACL-locked
  to the old SID → reads/copies need an **elevated** process. Use robocopy `/B` (backup mode, reads
  without changing ownership). Elevate via `Start-Process powershell -Verb RunAs -EncodedCommand <base64>`
  — NOT `-File` (execution policy Restricted) and NOT `-ExecutionPolicy Bypass` (blocked).

## Staging locations
- `C:\Users\Work\.claude-from-ssd\` — full old `~/.claude` (only `skills/` merged into live so far).
- `C:\Users\Work\restore_survey\` — task XMLs, Edge session files + `old_tabs.html`, survey logs.
- `C:\Users\Work\restore_archive\` — (intended) worktrees/archives/uncommitted patches once the
  stuck elevated pass is approved.
