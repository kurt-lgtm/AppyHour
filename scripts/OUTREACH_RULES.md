# OUTREACH_RULES.md — Customer Outreach List + Notice Drafts (SSOT)

> 🔴 **PRE-CHANGE GATE** — single source of truth for `scripts/outreach.py`. Read this BEFORE touching
> the code; change rules HERE first, same commit as the code change.

## 🧭 NORTH STAR

Every affected customer hears from us **once, accurately, in a human voice — and nothing sends without
an explicit go.** The tool builds the list and the drafts; a human (or an explicitly approved session
step) does the sending.

## 🔴 GOTCHAS / NEGATIVES FIRST

1. **THE TOOL NEVER SENDS. Hard non-goal.** `outreach.py` builds lists + drafts ONLY. No Gorgias
   ticket creation, no email dispatch, no SMTP, no MCP send calls — ever, under any flag. Sending is a
   separate explicit human/session action in Gorgias. (Standing rule: messages on Kurt's behalf require
   explicit approval per message batch. Do not add a `--send` flag; a PR adding one violates this doc.)
2. **Prior-contact check is mandatory, not optional.** Before a customer appears on the list, query
   Gorgias for existing open/recent tickets on their email (documented REST pattern:
   `_gorgias_internal.gorgias_paginate("tickets", ...)` — basic auth from AppData settings; raw REST
   needs a User-Agent header or Cloudflare 1010 blocks it; memory: gorgias-api-access). Rows with hits
   are marked `prior_contact=YES` with `gorgias_ticket_refs` — they stay ON the list (visibility) but
   flagged so a human decides. The wk0810 4-customer list was blocked on exactly this check. A Gorgias
   API failure marks the row `prior_contact=UNKNOWN` — never silently `NO`.
3. **Drafts are NOT final wording.** Every draft is stamped `DRAFT-NEEDS-HUMANIZER` (memory:
   cs-replies-use-humanizer). Final customer-facing wording is a session/skill step through the
   humanizer convention. Never paste a raw template draft into Gorgias.
4. **Refund amounts = actual paid, NEVER list price.** Amounts in refund notices come from the order's
   actual refund records (`refunds[].transactions` / refund_line_items subtotal+tax), net of discounts,
   including tax share. NEVER recompute from catalog/list price (standing burn: refund staged at LIST
   price). If no refund record exists on the order yet, the amount is `MISSING` — flag, don't compute.
5. **Never fabricate product names.** Item names in notices come from the order line items VERBATIM
   (`active_line_items()` titles). No MFG-name derivation, no prettifying, no catalog lookups to
   "improve" a name (never-fabricate rule, 2026-08-04 burn class).
6. **One email per customer.** Dedupe by customer email across item pairs/reasons — a customer with 3
   affected items gets ONE row and ONE draft covering all of them, never 3.
7. **Removed/refunded lines don't count as shipped items.** Line items come through
   `active_line_items()` (AppyHourMCP/utils.py) — never raw `line_items` (removed-lines burn class).
   Exception: refund-type notices reference the refund records themselves.
8. **Read-only everywhere.** Shopify: GET only (cached via `shopify_paginate`). Gorgias: GET only.
   Writes limited to `_outputs/artifacts/outreach-<tag>-<type>/`. Never overwrite a prior run's dated
   output dir — if it exists, version alongside (`-2`, `-3`).

## What it is

`outreach.py SHIP_TAG --type sub|refund|short --items items.csv [--out dir]`

Inputs:
- `SHIP_TAG` — Shopify tag scoping the cohort (fetched via `shopify_paginate`, fields incl.
  `line_items,refunds,email,customer,name,tags`).
- `--type` — notice class: `sub` (substitution), `refund`, `short` (shorted item).
- `--items items.csv` — the affected items. Columns: `sku` (required); optional `new_sku` (sub type),
  `note`. Only orders containing an affected SKU (per active_line_items) make the list.

Outputs (`_outputs/artifacts/outreach-<tag>-<type>/`):
- `contacts.csv` — email, order_number, items, new_items (sub), amount (refund type; actual-paid or
  MISSING), prior_contact (YES/NO/UNKNOWN), gorgias_ticket_refs.
- `draft-<order_number>.md` per customer — per-type template, header `DRAFT-NEEDS-HUMANIZER`.
- `run.json` — args, counts, timing (audit trail).

Performance: order fetch cached (shopify_paginate TTL), Gorgias lookups in a ThreadPool (pacing
respected by `_gorgias_internal` module lock is per-process; workers capped at 3 to stay under the
2 req/s cap). Target < 60s for a 50-customer list.

## Non-goals

- Sending anything (see gotcha 1).
- Final wording (humanizer step owns that).
- Refund EXECUTION (that's `InventoryReorder/Errors/_template_bulk_refund.py`).
- Deciding refund vs reship vs credit (that's `Cowork/cs-decision-guide.html`).
