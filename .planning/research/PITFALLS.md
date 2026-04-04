# Domain Pitfalls: Shopify Fulfillment Automation at Scale

**Domain:** Batch order editing for food subscription fulfillment (2500+ orders)
**Researched:** 2026-04-04
**Confidence:** HIGH for Shopify API behavior (official docs + community reports); MEDIUM for openpyxl specifics

---

## Critical Pitfalls

Mistakes that cause data corruption, duplicate line items, or silent failures affecting real orders.

---

### Pitfall 1: orderEditCommit Returns Error But Still Applies the Edit

**What goes wrong:** `orderEditCommit` can return an error response (network timeout, 5xx) while still having applied the changes to the order in Shopify. Standard retry logic re-runs the commit, adding duplicate line items to the order.

**Why it happens:** The commit is not idempotent by default. Shopify processes the mutation server-side before the HTTP response is returned. If the connection drops mid-response, the client never receives confirmation but the edit has been committed.

**Consequences:** Orders receive double quantity of items. In a 2500-order batch, a flaky network window corrupts potentially hundreds of orders silently. The React tool's CalculatedOrder view will not reflect the duplication until the next fetch.

**Prevention:**
- Before calling `orderEditCommit`, read the current order line items and check whether the target variant is already present at the expected quantity.
- Use a per-order state tracker (dict keyed by order ID) that marks `committed: true` before retrying. Never re-commit an order that was previously committed.
- The codebase already has duplicate protection in `sync-shopify` — verify it covers the commit-after-network-error scenario, not just the begin-edit scenario.
- Shopify tracks idempotency keys for 24 hours; use `idempotencyKey` on mutations where supported (currently inventory mutations; watch for order edit support).

**Warning signs:** Line items on orders with quantities double what was allocated; audit log shows two commit mutations for the same edit session.

**Phase:** sync-shopify implementation (batch edit loop).

---

### Pitfall 2: Two-Pass Sequencing Has No Hard Gate

**What goes wrong:** Pass 1 (PR-CJAM only) writes to Shopify. Pass 2 depends on the React tool having read Pass 1 results before generating the second allocation CSV. If Pass 2 is triggered too early — or if Pass 1 commits are still in-flight when the React tool reads — some PR-CJAM items are missing from orders, causing incorrect second-pass allocation.

**Why it happens:** There is no synchronization primitive between Matrix Commander and the React tool. Matrix Commander finishes issuing commits but Shopify's API may still be processing them (eventual consistency under load). The React tool reads order state immediately.

**Consequences:** Second-pass allocation produces wrong child→parent assignments. If the error is not caught before the RMFG sheet is generated, the wrong items are in the production order for that week.

**Prevention:**
- After all Pass 1 commits, issue a verification read: fetch a sample of orders and confirm PR-CJAM variant appears at the expected quantity before returning control to the operator.
- Provide an explicit "Pass 1 complete — verify before running React tool" checkpoint. Do not auto-chain into Pass 2.
- Log Pass 1 commit count vs. Pass 1 verification read count. Mismatch = hold.

**Warning signs:** PR-CJAM appears on fewer orders than expected in the second-pass allocation CSV.

**Phase:** sync-shopify two-pass flow; finalize handoff to React tool.

---

### Pitfall 3: GraphQL Leaky Bucket Exhaustion During Batch

**What goes wrong:** Each `orderEditBegin` + `addVariant` + `orderEditCommit` cycle costs approximately 30–60 GraphQL cost points. At 2500 orders, that's 75,000–150,000 points total against a 1000-point bucket refilling at 50 points/second. Running at full speed exhausts the bucket in seconds, producing 429 responses.

**Why it happens:** The GraphQL cost model charges per-field complexity, not per-request. Order edit mutations are not cheap. The existing `ShopifyClient` handles 429 with retry-after, but no proactive throttling means the client will spend most of a batch in backoff rather than making progress.

**Consequences:** A 2500-order batch that could complete in ~30 minutes takes 3–5 hours if backoff is naive. On Saturday morning, this delays the entire RMFG email window.

**Prevention:**
- Use proactive throttling: read `X-GraphQL-Cost-Include-Fields` response headers (or enable `Shopify-GraphQL-Cost-Debug: 1` during development) to understand actual mutation cost.
- Target 40 points/second throughput (leave 10/sec headroom) by sleeping `actual_cost / 40` seconds between mutations.
- Process orders in chunks of 20–30 with a sleep between chunks rather than issuing all mutations in a tight loop.
- Reuse the edit session where possible: `beginEdit` → multiple `addVariant` calls → single `commitEdit` per order. One commit per order, not one commit per variant.

**Warning signs:** 429 responses in logs within the first 10% of the batch; increasing average time per order as batch progresses.

**Phase:** sync-shopify rate limiting design; applies to both Pass 1 and Pass 2.

---

### Pitfall 4: Gift Orders Silently Skipped Without XLSX Reconciliation

**What goes wrong:** Gift orders return an error from `orderEditBegin` (Shopify blocks all edits). If the batch loop treats this as a retriable error or logs-and-continues without recording which orders were skipped, those orders are absent from the Shopify line item data that `generate` reads. The RMFG matrix then has incorrect counts for the week.

**Why it happens:** The skip-and-continue pattern loses the gift order's items entirely unless there is an explicit side-channel to record them.

**Consequences:** Gift order children (typically 3–8 items each) are missing from the production sheet. Manufacturer under-produces. Customer complaints.

**Prevention:**
- Maintain a `gift_orders` list populated during the sync pass. Every order that fails `orderEditBegin` with a locked/gift error is added to this list with its allocated items.
- `generate` must merge the `gift_orders` list into the matrix counts before producing the XLSX.
- Log gift order count at the end of sync and require operator confirmation before proceeding to generate.
- Distinguish gift-order errors from genuine API failures: gift orders return a specific `userErrors` code; network failures return HTTP-level errors.

**Warning signs:** Order count in Shopify data is lower than order count in the React tool's allocation output.

**Phase:** sync-shopify error classification; generate gift merge; finalize.

---

### Pitfall 5: Stale Inventory Snapshot Feeding Allocation

**What goes wrong:** `sync-shopify` writes inventory quantities to Shopify (paid variant + $0 remainder variant) at the start of the Saturday flow. If that write happens before the React tool reads it, the allocation is correct. If the inventory snapshot used was itself stale (pulled Friday, not Saturday morning), the allocation is based on wrong quantities — potentially over-allocating.

**Why it happens:** The codebase stores inventory as a mutable Python dict in a JSON settings file with no timestamp or version check. The inventory loaded at Saturday 8am may be the same dict last updated Thursday. No code path verifies the snapshot age before using it.

**Consequences:** Over-allocation against a SKU that sold out Friday evening. Manufacturer receives incorrect quantities. Physical shortage on dispatch day.

**Prevention:**
- Timestamp every inventory snapshot write. Refuse to proceed if the snapshot is older than 18 hours without operator confirmation.
- Pull a live Shopify variant inventory read immediately before sync to validate the snapshot matches what Shopify currently holds. Flag discrepancies > 5 units.
- The `validate` command should include an inventory freshness check, not just structural checks.

**Warning signs:** Shopify variant inventory (paid) differs from the settings dict values by more than rounding; snapshot file mtime is more than 24 hours old.

**Phase:** inventory sync design; validate command.

---

## Moderate Pitfalls

---

### Pitfall 6: CalculatedOrder Reads Active Line Items Incorrectly

**What goes wrong:** After `orderEditBegin`, the `CalculatedOrder` object contains `addedLineItems` and `lineItems` with a `quantity` that includes already-removed items unless filtered. Counting total items without filtering `qty=0` or removed items produces inflated item counts. The React tool's allocation is based on Shopify's actual order state, but Matrix Commander may double-count.

**Prevention:**
- Filter `calculatedLineItem.quantity == 0` and items where `stagedChanges` show a removal before counting.
- Apply the same `active_line_items` filtering used in the MCP tools (`filter qty=0`, check `fulfillableQuantity`) to every CalculatedOrder read.

**Phase:** sync-shopify CalculatedOrder parsing.

---

### Pitfall 7: XLSX Tab Name Assumptions Break RMFG Portal Compatibility

**What goes wrong:** The `finalize` command must produce a tab named exactly `Worksheet` (per RMFG Translator portal format). `openpyxl` creates a default tab named `Sheet` on workbook creation. If any intermediate step renames it or adds additional tabs, the portal import fails silently — it processes zero rows.

**Prevention:**
- Always explicitly set `ws.title = "Worksheet"` immediately after creating the sheet, never rely on the default.
- After writing, re-read the XLSX and assert `wb.sheetnames == ["Worksheet"]` before saving as final output.
- Integration test: parse the finalized file with the same parser the RMFG portal uses (column header match).

**Phase:** finalize XLSX generation.

---

### Pitfall 8: openpyxl Column Styles Apply Only to Explicitly Written Cells

**What goes wrong:** Setting a `ColumnDimension` style or number format in openpyxl applies only to cells that already exist in that column, not to future Excel-added cells. Setting column-level formats programmatically does not guarantee the manufacturer sees correct formatting when they open the file and add data.

**Prevention:**
- Apply number formats cell-by-cell when writing data rows, not via column-level style objects.
- Use `auto_size=False` and set explicit `column_width` values calibrated to the longest expected cell value.
- Validate output by opening in Excel (or checking openpyxl's parsed cell dimensions) before committing to a format spec.

**Phase:** finalize XLSX formatting.

---

### Pitfall 9: beginEdit / commitEdit Session Leaks on Crash

**What goes wrong:** If Matrix Commander crashes between `orderEditBegin` and `orderEditCommit`, an open edit session is left on the order. Shopify holds the order in an intermediate `CalculatedOrder` state. Subsequent calls to `orderEditBegin` on the same order may return an error or produce a nested CalculatedOrder.

**Prevention:**
- Log the `calculatedOrderId` returned by `beginEdit` to a crash-recovery file before proceeding with `addVariant` calls.
- On startup, check the crash-recovery file and issue `orderEditCommit` with `revert: true` (or call `orderEditBegin` again which implicitly closes the previous session) for any orphaned sessions.
- Set a session timeout: if commit is not called within 60 seconds of begin, log a warning and revert.

**Phase:** sync-shopify error recovery design.

---

### Pitfall 10: Recharge Cursor Pagination Silent Loop

**What goes wrong:** This is a known codebase bug (documented in CONCERNS.md). If the Recharge API response format changes or a pagination edge case occurs, page-based iteration loops forever. During a Saturday batch run with 2500 orders, this hangs the entire pipeline.

**Prevention:**
- Enforce a hard maximum page count (e.g., 200 pages) with an explicit error if exceeded.
- Use cursor-based pagination exclusively: `next_cursor` in response metadata, as documented in INTEGRATIONS.md.
- Add a timeout at the data-fetch level, not just the HTTP request level (total operation timeout of 5 minutes for a full Recharge pull).

**Phase:** inventory pull before sync.

---

## Minor Pitfalls

---

### Pitfall 11: ProductionDay Column Missing From Generated Sheet

**What goes wrong:** The RMFG portal download includes `ProductionDay`. Matrix Commander's `generate` command builds from live Shopify data which does not include this column. If `finalize` assumes it is present, it produces a sheet with an offset column structure.

**Prevention:** `generate` must explicitly add a `ProductionDay` column and populate it from the batch config (Saturday date), not from Shopify data.

**Phase:** generate command.

---

### Pitfall 12: Auto-Name Logic Produces Wrong Filename on Month Boundary

**What goes wrong:** `finalize` auto-names the output file using the current date. When run on the first Saturday of a new month, the month number increments but the week-of-year logic may produce a filename that doesn't match the operator's naming convention, causing it to sort incorrectly in the output folder.

**Prevention:** Use explicit ISO week number (`datetime.isocalendar().week`) rather than month-based logic. Expose the filename in the pre-finalize confirmation prompt so the operator can catch it before the file is emailed.

**Phase:** finalize auto-name.

---

### Pitfall 13: `_rc_bundle` Property Check as Only Guard

**What goes wrong:** The swap filter correctly excludes non-`_rc_bundle` items (paid/chosen items). But if a Recharge subscription update changes the property name or the property is missing from the line item due to an API change, items that should be excluded are treated as swappable.

**Prevention:** Log a warning and skip (do not swap) any line item whose Recharge properties cannot be read. Default to exclusion, not inclusion.

**Phase:** swap filtering validation.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| sync-shopify batch loop | orderEditCommit duplicate on retry (#1) | Pre-commit state check; per-order committed flag |
| Two-pass sequencing | Pass 2 triggered before Pass 1 confirmed (#2) | Explicit verification gate between passes |
| Rate limiting | Bucket exhaustion at scale (#3) | Proactive 40 pts/sec throttle; chunk processing |
| Gift order handling | Silent skip drops items from matrix (#4) | gift_orders side-list; merge in generate |
| Inventory snapshot | Stale data feeds wrong allocation (#5) | Timestamp check; live validation before sync |
| XLSX generation | Wrong tab name breaks portal import (#7) | Assert `sheetnames == ["Worksheet"]` |
| Crash recovery | Orphaned edit sessions after crash (#9) | Crash-recovery file; revert on restart |
| Recharge pull | Cursor pagination loop hangs batch (#10) | Hard page cap; total operation timeout |

---

## Sources

- [Shopify API Limits (official)](https://shopify.dev/docs/api/usage/limits) — GraphQL leaky bucket, 1000 points, 50/sec refill
- [Edit existing orders (official)](https://shopify.dev/docs/apps/build/orders-fulfillment/order-management-apps/edit-orders) — beginEdit/commitEdit workflow, locked order restrictions
- [orderEditCommit silent apply bug (community)](https://community.shopify.dev/t/potential-order-edit-api-bug-commit-returns-error-but-still-applies-changes/32225) — MEDIUM confidence, confirmed in community forum
- [Shopify idempotency (official)](https://shopify.dev/docs/api/usage/implementing-idempotency) — 24-hour idempotency key window
- [Shopify inventory oversell (community)](https://community.shopify.dev/t/graphql-admin-api-returns-negative-inventory-quantity/22024) — negative inventory / stale read risk
- [openpyxl styles (official)](https://openpyxl.readthedocs.io/en/stable/styles.html) — column-level style restriction
- `.planning/codebase/CONCERNS.md` — Recharge pagination bug, duplicate protection status, bare exception handlers
- `.planning/PROJECT.md` — Gift order constraint, two-pass sequencing, 400–2500 order range, PR-CJAM first-pass requirement
