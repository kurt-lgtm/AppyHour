# Automation Handoff Plan — Kurt + Jessa

**Date:** 2026-04-05
**Purpose:** Make the Director Transition real by automating everything that currently requires Kurt's hands-on involvement. Two tracks: Kurt's ops automation (inventory, demand, POs) and Jessa's CS automation (reporting, tagging, escalation).

**Principle from the Director Transition Playbook:** "If someone can do a task 70% as well as you, delegate it. Your 100% on one reship approval is worth less than your 100% on a system that handles all reship approvals."

---

## The Current State (Why You're Stuck)

| Task | Who Does It Now | Time/Week | Should Be |
|------|----------------|-----------|-----------|
| Demand calculation | Kurt (manual) | 2-3 hrs | System (auto-sync + curation floor) |
| Cut order generation | Kurt (manual XLSX) | 2 hrs | System (one-click approve) |
| Shortage detection | Kurt (Slack DMs) | 1-2 hrs reactive | System (2-week alerts) |
| PO decisions | Kurt + Dan (Slack) | 1-2 hrs | Tommy (auto-drafted POs) |
| Reship decisions | Kurt (tagged in Slack) | 3-5 hrs | Jessa's team (Decision Guide) |
| Swap decisions | Kurt (manual calc) | 1-2 hrs | System (suggested swaps) |
| CS reporting | Jessa (manual spreadsheet) | 2-3 hrs | System (auto-generated) |
| Food safety tracking | Jessa (manual) | 1 hr | System (Gorgias sync) |
| Recipe rotation | Kurt (manual settings) | 1 hr/month | Tommy (recipe diff + auto-ramp) |

**Total Kurt ops time today: ~15-20 hrs/week**
**Total after automation: ~2-3 hrs/week (review + approve)**

---

## Track 1: Kurt's Automation (Inventory + Demand + POs)

### Already Built (This Session)

| Tool | Endpoint | What It Replaces |
|------|----------|-----------------|
| Curation Floor | `/api/curation_floor` | Manual shortage detection |
| EWMA Forecast | `compute_ewma()` in shopify_sync | Flat 8-week average |
| Growth Multiplier | `compute_growth_multiplier()` | No acquisition signal |
| Final Demand | `/api/demand_final` | Manual demand calculation |
| PO Drafts | `/api/po_draft` | Kurt+Dan Slack PO discussions |
| PO Approve | `/api/po_approve` | Manual PO tracking |
| Recipe Diff | `/api/recipe_diff` | Manual demand impact guessing |
| Recipe Apply | `/api/recipe_apply` | Manual settings update |
| Auto-Depletion Journal | journal entries in `auto_deplete` | Dual inventory truth |

### What Kurt Does Now (Director Mode)

| Day | Action | Time | Tool |
|-----|--------|------|------|
| Monday | Scan PO draft — Tommy already approved? Confirm. | 5 min | `/api/po_draft` dashboard |
| Monday | Check shortage alerts — anything red? | 5 min | `/api/curation_floor` dashboard |
| Wednesday | Approve cut order — one click | 10 min | Auto-generated XLSX |
| Wednesday | Review swap suggestions if shortage | 10 min | `/api/suggest_fixes` |
| Friday | Glance at weekly ops summary | 10 min | Auto-report |
| **Total** | | **~40 min/week** | |

### What Kurt Does NOT Do
- Calculate demand (system does it)
- Build cut orders (system generates)
- Chase Tommy for POs (system alerts him)
- Approve individual reships (Jessa's team decides)
- Monitor Slack for shortages (system alerts 2 weeks ahead)
- Manually track inventory (journal-based ledger)

---

## Track 2: Jessa's Automation (CS Reporting + Tagging)

### Tools Already Built

| Tool | What It Does | Trigger |
|------|-------------|---------|
| `gorgias_sync_operational_issues` | Pulls tagged tickets → Google Sheet | MCP / scheduled |
| `gorgias_sync_food_safety` | Pulls spoiled/quality tickets → Sheet | MCP / scheduled |
| `enrich_operational_issues` | Fills carrier, state, FC from Shopify | MCP / scheduled |
| `rebuild_ops_summary` | Weekly rollup report | MCP / scheduled |
| CS Decision Guide (HTML) | Agents make reship decisions autonomously | Browser access |

### What Jessa Does (3 Steps to Automation)

#### Step 1: Fix the Input (This Week)
Jessa enforces Gorgias tagging. Without this, no automation works.

**Start with the Gap Analysis:** Send Jessa the `gorgias-gap-analysis.md` (already built, in `Cowork/` folder). This document shows her team that only 6% of tickets are being tagged — 94% are invisible to reporting. It has the hard numbers: 1,321 tickets scanned, only 43 properly captured. $1,500-2,500/week in unreported resolution costs.

**Jessa's job:** Review the gap analysis with her team, identify WHERE the tagging breaks down (which agents, which ticket types, which step in the workflow), and come back with a plan to close the gap. She knows her team better than anyone — let her figure out the "how."

**Jessa's action items:**
1. Read the Gorgias Gap Analysis — understand the 94% miss rate and what it costs
2. Identify the gap: Is it training? Is it the workflow? Is it the Gorgias UI? Is it specific agents? Report findings to Kurt.
3. Brief her team (Lawrence, Adel, Eli, Mark): "Every ticket needs Contact Reason + Resolution before close. Starting now."
4. Spot-check 5 tickets/day for 2 weeks — track who tags and who doesn't
5. Merge duplicate tags (cancel/refund + Cancel Sub + Cancel order → Cancellation)
6. Confirm agents can access CS Decision Guide in their browser
7. Report tagging rate to Kurt weekly (target: 50% week 1, 90% week 3)

**What Jessa does NOT need to do:**
- Build reports manually
- Count tickets
- Track reship costs by hand
- Reconcile Slack vs Gorgias
- Escalate standard decisions to Kurt

#### Step 2: Turn On Auto-Sync (Week 2 — Kurt/AI Assistant)

Once tagging hits 50%+, Kurt's AI assistant configures:

| Automation | Schedule | Output |
|------------|----------|--------|
| Operational issues sync | Daily 6 AM ET | Google Sheet updated |
| Food safety sync | Daily 6 AM ET | Separate tab updated |
| Enrichment (carrier, state) | Daily 6:30 AM ET | Missing fields filled |
| Weekly ops report | Monday 7 AM ET | Summary sheet + Slack post |

**Jessa sees:** Open Google Sheet Monday morning → full report is there. No manual work.

#### Step 3: Live Dashboard (Week 3-4 — Kurt/AI Assistant)

Weekly auto-report in this format:

```
ELEVATE FOODS WEEKLY CS REPORT — Week of April 7, 2026
Orders shipped: 2,700

ISSUE RATES (normalized to order volume)
  Operational: 46 / 2,700 = 0.85%    (target: <2%)    [OK]
  Food Safety: 25 / 2,700 = 0.46%    (target: <1%)    [OK]
  Arrived Warm: 12 / 2,700 = 0.22%   (trending up)    [WATCH]

CS COST
  Total: $976 = $0.36/order           (target: <$0.50) [OK]

AGENT PERFORMANCE
  Tagging rate: 88% (↑ from 6%)
  Avg response: 3.1 hrs
```

---

## What Makes Automation Super Easy

### The Unlock: Everything Feeds From 2 Sources

```
RECHARGE API ──→ Subscription data ──→ Curation Floor ──→ Demand
                                   ──→ Growth Multiplier
                                   ──→ PO Drafts
                                   ──→ Cut Order

GORGIAS API  ──→ Ticket data       ──→ Ops Report
                                   ──→ Food Safety Report
                                   ──→ CS Cost Tracking
                                   ──→ Agent Performance
```

Both APIs are already connected. The MCP tools already work. The only manual step left is **Jessa's team tagging tickets**.

### The Automation Stack (What's Running)

| Layer | What | Status |
|-------|------|--------|
| **Data In** | Recharge sync (subscriptions, charges) | Working — auto-syncs with cache |
| **Data In** | Shopify sync (orders, fulfillment) | Working — EWMA added |
| **Data In** | Gorgias sync (tickets, issues) | Working — needs tagging enforcement |
| **Calculation** | Curation floor (recipe × subs) | Built this session |
| **Calculation** | EWMA + growth multiplier | Built this session |
| **Calculation** | Recipe diff + auto-ramp | Built this session |
| **Output** | PO drafts | Built — `/api/po_draft` |
| **Output** | Cut order XLSX | Existing — needs one-click approve |
| **Output** | Ops report | Tools exist — needs scheduling |
| **Output** | Food safety report | Tools exist — needs scheduling |
| **Decision** | CS Decision Guide | Built — needs team deployment |
| **Decision** | Swap suggestions | Existing — `/api/suggest_fixes` |

### What's NOT Automated Yet (Backlog)

| Item | Effort | Impact | Assigned |
|------|--------|--------|----------|
| Schedule daily Gorgias sync (cron/Mechanic) | Small | High — enables auto-reporting | AI Assistant |
| Monday Slack post with PO draft + ops summary | Small | High — Tommy + Jessa get alerts | AI Assistant |
| Host CS Decision Guide on Flask app | Small | Medium — browser access for Peak Support | Anik (when available) |
| Fix Mechanic inventory alert loop | Small | Medium — stops 10+/day spam alerts | AI Assistant |
| Anomaly detection (demand deviation >20%) | Medium | Medium — catches surprises | Future sprint |
| Churn detection (Recharge cancellation rate) | Medium | Medium — adjusts forecast | Future sprint |

---

## The Handoff Sequence

### Week 1: Foundation
| Who | Action |
|-----|--------|
| **Jessa** | Brief team on tagging. Spot-check 5/day. Merge duplicate tags. |
| **Kurt** | Share CS Decision Guide with Jessa's team. Confirm browser access works. |
| **Kurt** | Stop answering individual reship tags in Slack. Redirect to Decision Guide. |
| **Tommy** | Start filling vendor catalog (XLSX provided). Define tray recipe SKUs. |

### Week 2: Automation On
| Who | Action |
|-----|--------|
| **Jessa** | Report tagging rate (target: 50%+). Continue spot-checks. |
| **AI Assistant** | Turn on daily Gorgias sync. Configure Monday Slack posts. |
| **Kurt** | Review first auto-generated ops report. Provide feedback on format. |
| **Tommy** | Review first PO draft from dashboard. Approve or adjust. |

### Week 3: Trust Building
| Who | Action |
|-----|--------|
| **Jessa** | Tagging at 90%+. Ops report is accurate. Stop manual spreadsheet. |
| **Kurt** | Cut order is auto-generated. Approve with one click. |
| **Tommy** | PO drafts are reliable. Submit to vendors directly from dashboard. |
| **Kurt** | Leave #reship-and-order-requests channel. Jessa handles it. |

### Week 4: Director Mode
| Who | Action |
|-----|--------|
| **Kurt** | 40 min/week on ops oversight. Rest of time on strategy + systems. |
| **Jessa** | 15 min Monday report review. Rest of time managing her team. |
| **Tommy** | 2 hrs/week procurement. Rest on product planning + cheese rotation. |
| **Test** | Kurt takes a 3-day weekend. Nothing breaks. |

---

## Success Metrics

| Metric | Current | Week 4 Target |
|--------|---------|---------------|
| Kurt's ops time | 15-20 hrs/week | <3 hrs/week |
| Jessa's reporting time | 2-3 hrs/week manual | 15 min review |
| Gorgias tagging rate | 6% | 90%+ |
| Shortage lead time | 0-3 days (reactive) | 14+ days (proactive) |
| PO turnaround | Days of Slack DMs | Same-day auto-draft + approve |
| CS cost visibility | Invisible ($1,500-2,500/week estimated) | Tracked to the dollar weekly |
| Shortage incidents | 2-4/week | 0/week |
| Recipe rotation chaos | First-week stockouts | Auto-ramp, zero shortage |

---

## For Dan (if he asks)

The playbook says: "Don't tell Dan to slow down — show him the system that absorbs his speed."

What Dan sees:
- Shortages go to zero (system prevents them)
- CS costs become visible and trending down
- Cut orders happen on time every week
- New products (trays) don't break inventory
- Kurt has time for strategic projects Dan actually cares about

What Dan doesn't need to know:
- The specific automation details (he'll try to redesign them)
- That Kurt's role changed (he'll see results, not process)
- The delegation framework (it just works)
