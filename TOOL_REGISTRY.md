# AppyHour Canonical Tool Registry

**Single source of truth for "which tool/script/function do I call for capability X" in the AppyHour domain** (Shopify, Recharge, Gorgias, shipping, fulfillment, swaps, cut orders). Before writing ANY new script/helper, check here. If a capability is listed, **call the canonical owner — do NOT recreate it.** Duplicates fragment behavior and drift out of sync.

Discovery order when a capability isn't listed: codegraph (`codegraph_search`/`codegraph_context`) → this registry → `appyhour_lib` → grep — BEFORE creating anything. The generic build discipline (and the small-model-operability principle behind it) lives in forge: `~/.claude/skills/forge/references/canonical-tools.md`. Reimplementation-signature guard: `~/.claude/instincts/canonical-tools.md`.

## Core canonicals (call THESE)

| Capability | Canonical owner | Call it via | Never instead |
|---|---|---|---|
| **Shopify auth** | `appyhour_lib/credentials.py` → `get_shopify_auth()` | `from appyhour_lib.credentials import get_shopify_auth` (AppyHourMCP re-exports) | hand-built `X-Shopify-Access-Token` headers |
| **Recharge API client** | `cut_order_server/app/recharge_client.py` | import the client; v2021-11 header, cursor pagination | ad-hoc `requests` to recharge with inline headers |
| **Gorgias outbound send** | `AppyHourMCP/tools/` Gorgias REST path | POST `/tickets?should_skip_rules=true` + `from_agent` + `hi@appyhourbox.com`, then PUT `assignee_user:null` | MCP read-only tools (they can't send) |
| **Google Sheets / GWS** | `AppyHourMCP/tools/google_sheets.py` | the sheets tool / `gws` CLI (authed) | new gspread/google-api glue |
| **Weather + NWS alerts** | `appyhour_lib/weather.py` | `from appyhour_lib.weather import ...` | new OpenWeatherMap/NWS callers |
| **SKU swap** | `/forge-swap` skill + appyhour MCP swap tools (`appyhour_swap_order_skus`); count-limited → `fulfillment_web/shopify_swap.py` | per `/forge-swap` SKILL.md | new per-SKU one-shot swap scripts |
| **Gmail attachment download** | `GelPackCalculator/download_*_imap.py` (per-carrier: fedex/ontrac/veho/shipping_pdfs) | run the carrier's IMAP script | MCP/gws for Gmail *attachments* (rule: dedicated script > IMAP > gws > MCP) |
| **Box / SKU classification** | `appyhour_lib/box_classify.py`, `internal_classify.py` | import the classifier | inline SKU prefix regex (use `product-rules` taxonomy) |
| **Paths / app dirs** | `appyhour_lib/paths.py` | `from appyhour_lib.paths import ...` | hardcoded `%APPDATA%`/profile paths |
| **User uploads persistence** | `appyhour_lib/user_data.save_user_file()` | per `feedback_user_data_persistence` | writing to `.claude/` |

## Shared library rule

`appyhour_lib/` (credentials, weather, paths, box_classify, internal_classify, user_data, notify) = **pure-util single source**. Every consumer imports from it. Adding a shared util → add it HERE in the lib, register it above. Never copy a util into an app.

## Known drift (consolidate — deferred full-sweep)

Flagged 2026-06-14 audit; not yet fixed (core-canonicals scope only):
- **Inline Shopify auth** (should import `get_shopify_auth`): `AppyHourMCP/tools/shipping.py`, `GelPackCalculator/{gel_pack_shopify,revert_losc,swap_spn_leon_srhub,sync_shopify_orders,_lookup_losc}.py`.
- **Ad-hoc Recharge calls** (should use `recharge_client.py`): several `InventoryReorder/Errors/check_*.py`.
- When you touch any flagged file for another reason, migrate it to the canonical while you're there (preparatory refactor).

## Canon decisions — 2026-06-14 dup-script sweep

Archive snapshot (non-destructive, recoverable): `Claude Projects/_archive/scripts-canon-snapshot-2026-06-14/` (41 scripts, 488KB). Live dupes NOT deleted — migration is a separate gated step. "Later weighted more" held, but **newest ≠ strictly better** — each canonical has missing-functionality flags to preserve first.

| Capability | CANONICAL | Archive (superseded) | ⚠️ Preserve before retiring |
|---|---|---|---|
| Recharge repeat-order **dup-verdict** (per-order) | `InventoryReorder/Errors/check_repeat_subs_v4.py` | subs, subs_v2, subs_v3, class, class_v2 | v4 is a one-shot (hardcoded 19 emails) — re-add v3's **CSV-cohort input** + **MONG/SS double-sub check** + class_v2's **box_contents qty-parse** before v3/class_v2 truly dead |
| Recharge repeat **per-SKU classification** | `InventoryReorder/Errors/check_repeat_sources.py` — **DISTINCT, keep** | — | not a dup of subs; different question |
| Cut-order xlsx build | `InventoryReorder/build_cut_order_xlsx_v2.py` (4 fix commits) | build_cut_order_xlsx.py | v1 held **two-week (WK2)** logic — low risk, current ops single-cohort |
| Double-refund detection | `InventoryReorder/Errors/detect_double_refunds_v2.py` | detect_double_refunds.py | v1 had **urllib3 Retry/HTTPAdapter** resilience |
| RC shortage fix | `InventoryReorder/Errors/fix_rc_shortages_v2.py` | fix_rc_shortages.py | v1 docstring held full **per-SKU action map** — keep as comment |
| One-shot SKU swaps (`swap_*.py` ×28) | **`/forge-swap` + appyhour MCP swap tools** | 26 of 28 | 2 patterns ported as documented flows into forge-swap SKILL.md (Protected-swap, Conditional-target-swap); callable-fn promotion deferred (live-order test). 3 source files stay archived as reference |

🔴 Security receipt: 5 scripts had a hardcoded committed Recharge **write** token → scrubbed to `settings["recharge_api_token_write"]` (gitignored), AppyHour commit `b4cad88`; rotated + tested working 2026-06-14. History-scrub declined (token revoked). See `~/.knowledge/decisions/deprecations.md`.

## Maintenance

- Tool swapped/retired → update the row + log `~/.knowledge/decisions/deprecations.md`.
- New canonical → add a row here + the matching trigger in `~/.claude/instincts/canonical-tools.md`.
