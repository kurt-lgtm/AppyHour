# Feature Landscape: Shopify Subscription Fulfillment Automation

**Domain:** Shopify-based food subscription box fulfillment pipeline
**Researched:** 2026-04-04
**Confidence:** HIGH (domain well-understood from PROJECT.md + ARCHITECTURE.md; Shopify API confirmed via official docs)

---

## Table Stakes

Features the pipeline cannot function without. Missing any of these = the pipeline breaks or requires manual intervention.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Direct Shopify order editing via GraphQL | Replaces Matrixify; must write line items to 2500+ orders | High | `beginEdit → addVariant/setQuantity → commitEdit`; duplicate protection required |
| Two-pass sequencing (PR-CJAM first) | React tool reads live order state between passes; wrong sequence = incorrect second-pass output | Medium | Pass 1 completes + confirms before Pass 2 begins; blocking gate required |
| Rate-limit-aware API client | Shopify GraphQL uses cost-based throttling (1000 pts/60s per app); 2500 orders × N variants = thousands of mutations | High | Exponential backoff on 429; log throttle events; do not hammer blindly |
| Inventory sync: calculated → Shopify variants | Paid variant gets full allocation first; $0 variant gets remainder | Medium | Two variant IDs per child SKU; order matters; atomic per-SKU update |
| Shortage detection | Identify SKUs where demand > available inventory before any sync | Medium | Run before Pass 1; block sync if unresolved critical shortages |
| Automated swap resolution | Replace shorted SKU with approved substitute from SUBSTITUTION_FAMILIES | Medium | Dietary exclusions (NNRS/CORS/NCRS) must be skipped; only `_rc_bundle` items swappable |
| Gift order handling at matrix level | Shopify blocks ALL edits on gift orders; cannot use order edit API | High | Detect gift orders pre-sync; assign child SKUs in XLSX only; merge into final sheet |
| RMFG production sheet generation | Replaces manual RMFG Translator portal download | Medium | Tab rename to "Worksheet"; add ProductionDay column; correct sort; XLSX output |
| MFG name validation | RMFG portal rejects unrecognized names; must map internal SKUs to MFG names | Low | Validation against RMFG Translator data; block finalize on unmapped SKUs |
| Audit trail for swap decisions | Each substitution must be traceable (original SKU, replacement, reason, count) | Low | `SwapDecision` dataclass pattern already in place |
| XLSX validation before sync | Numeric order IDs, valid SKU mappings, ProductionDay present | Low | `CheckResult` pattern already in place; run before any API call |
| Idempotent sync | Re-running sync on already-edited orders must not double-apply variants | High | Check existing line items before `beginEdit`; skip orders already correct |

---

## Differentiators

Features that save significant time or reduce error risk beyond baseline pipeline function.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Webhook trigger from React tool | React tool POSTs after allocation; Matrix Commander auto-starts post-processing without manual launch | Medium | Removes human handoff step; reduces Saturday window from 60+ min to 15 min |
| Sync progress dashboard | Real-time per-order sync status (success / skipped / failed) visible in web UI | Medium | Vanilla JS polling against Flask `/status` endpoint; reduces "is it done?" anxiety |
| Pre-sync dry-run mode | Simulate full sync against live data, report what would change, without committing edits | Medium | High confidence before irreversible API mutations on 2500 orders |
| Shortage report with swap suggestions | When shortage detected, auto-surface approved substitutes ranked by inventory availability | Medium | Saves manual lookup time; operator confirms, system applies |
| Gift order auto-detection | Identify locked orders from Shopify `tags` or order attributes without manual flagging | Low | Reduces gift-order merge errors |
| Finalize auto-naming | Output XLSX named with production week + date stamp automatically | Low | Removes rename step; consistent file naming for email attachment |
| Per-pass sync confirmation gate | Operator explicitly confirms "Pass 1 complete" before Pass 2 begins | Low | Prevents accidental out-of-order execution; explicit over implicit |
| Swap exclusion enforcement | NNRS/CORS/NCRS orders flagged and excluded from all auto-swap logic, with report | Low | Already partially implemented; make violations visible in UI |
| End-to-end timing log | Timestamps for each pipeline stage (inventory load → Pass 1 sync → Pass 2 sync → generate → finalize) | Low | Identifies bottlenecks; builds confidence in the 15-min target |
| React-compatible spec output | Each Matrix Commander module documented as a spec for future React absorption | Low | Not a user-facing feature; critical for long-term architecture |

---

## Anti-Features

Things to deliberately NOT build in this milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Charge detection / Saturday 3am polling | Out of scope per PROJECT.md; adds complexity without proven Saturday flow | Defer until end-to-end flow is battle-tested |
| Pre-flight Friday night automation | Same — defer until Saturday path is stable | Manual Friday steps continue as-is |
| Tuesday cycle automation | Same pipeline, different cadence; proves nothing new until Saturday is proven | Reuse Saturday modules when ready |
| React tool allocation logic | Owned by external developer; wrong boundary to cross | React tool POSTs to webhook; Matrix Commander consumes output only |
| Full operator dashboard / single-screen view | "Option 1" architecture requires React to absorb all logic first | Build Matrix Commander as backend; React dev absorbs later |
| Staging / sandbox environment | No staging environment available; live data only per PROJECT.md constraints | Use dry-run mode + idempotency instead |
| Recharge bundle sync for swaps | Recharge bundle selection updates are a separate concern with their own complexity | Shopify-level swaps via order edit API are sufficient for production sheet; Recharge sync is future |
| Custom error classification UI | 24 existing error detection scripts are separate domain; do not merge into Matrix Commander web | Keep error detection in `InventoryReorder/Errors/` |
| Carrier / shipping label generation | Different domain (ShippingReports); not part of production matrix pipeline | Keep in GelPackCalculator / ShippingReports |
| Generic WMS / 3PL integration layer | This is a bespoke RMFG relationship with a fixed XLSX format; generalization adds no value | Hard-code RMFG format; extract only if second manufacturer appears |

---

## Feature Dependencies

```
Inventory sync (paid variant → $0 variant)
  └── Shortage detection (must know allocations before checking gaps)
       └── Swap resolution (must have shortages identified first)
            └── XLSX update with swaps applied
                 └── Pass 1 sync to Shopify (PR-CJAM only)
                      └── [React tool re-runs second-pass allocation]
                           └── Pass 2 sync to Shopify (all other parents)
                                └── Gift order detection + matrix merge
                                     └── RMFG sheet generation (generate)
                                          └── MFG name validation
                                               └── Finalize (tab rename, ProductionDay, sort, auto-name)
                                                    └── Email-ready XLSX

Webhook trigger (React → Matrix Commander)
  └── Requires Pass 1 sync to be complete before webhook fires
```

Key blocking dependencies:
- Pass 2 sync MUST NOT start until Pass 1 is confirmed complete and React has re-run
- Finalize MUST NOT run if MFG name validation fails
- Swap resolution MUST respect dietary exclusions before applying any swap
- Idempotency check MUST run before every `beginEdit` call

---

## MVP Recommendation

The Saturday flow is the north star. MVP = everything required to go from inventory CSV to email-ready RMFG XLSX in one Saturday morning session without touching Matrixify or the RMFG portal manually.

**Prioritize (blocking):**
1. Idempotent Shopify sync (`sync-shopify`) with rate limiting and duplicate protection
2. Two-pass sequencing with explicit confirmation gate between passes
3. Gift order detection and matrix-level merge
4. RMFG sheet generation (`generate`) with correct tab/column transforms
5. MFG name validation (blocks `finalize` on bad names)
6. `finalize` with auto-naming

**Prioritize (high value, not blocking):**
7. Shortage detection + swap resolution with dietary exclusion enforcement
8. Inventory sync: calculated → Shopify paid + $0 variants
9. Pre-sync dry-run mode (confidence before committing 2500 mutations)

**Defer:**
- Webhook trigger from React tool (manual trigger acceptable until Saturday flow proven)
- Sync progress dashboard (stderr logs acceptable for now)
- Tuesday cycle (same modules, different cadence — falls out naturally once Saturday works)

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Table stakes completeness | HIGH | Derived directly from PROJECT.md validated/active requirements and known Shopify API constraints |
| Differentiator value | MEDIUM | Based on domain knowledge of manual steps being automated; no external benchmark |
| Anti-feature rationale | HIGH | Each anti-feature maps to an explicit PROJECT.md "Out of Scope" entry or known architectural boundary |
| Rate limit specifics | HIGH | Shopify official docs confirm cost-based throttling (1000 pts/60s); bulk operations exempt but not applicable here (order edit mutations are not bulk ops) |
| Gift order API constraint | HIGH | Shopify truly blocks order edit API on gift orders — confirmed in PROJECT.md as a discovered constraint, not an assumption |

---

## Sources

- PROJECT.md — Requirements (Validated + Active + Out of Scope sections)
- ARCHITECTURE.md — Existing abstractions (CheckResult, SwapDecision, SyncResult)
- [Shopify GraphQL Admin API — API limits](https://shopify.dev/docs/api/usage/limits) — Rate limiting details (HIGH confidence)
- [Shopify bulk operations](https://shopify.dev/docs/api/usage/bulk-operations/queries) — Bulk ops exempt from cost limits but only for queries, not mutations (HIGH confidence)
- [Shopify automated order fulfillment](https://www.shopify.com/blog/automated-order-fulfillment) — General fulfillment patterns (LOW confidence, marketing content)
- [Subscription box fulfillment operations guide](https://getproductiv.com/blog/subscription-box-fulfillment-guide) — Two-phase allocation patterns (MEDIUM confidence)
