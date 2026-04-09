# Jessa's CS Reporting Plan — Getting to Automated Ops Reporting

**Date:** 2026-04-05
**For:** Jessa Enriquez (Peak Support CS Supervisor)
**From:** Kurt (Head of Operations)

---

## The Goal

Replace manual spreadsheet tracking with automated weekly reports. Jessa should never have to manually count tickets, track reships, or build reports. The system does it.

---

## What Exists Today (Tools Already Built)

| Tool | What It Does | How to Trigger |
|------|-------------|----------------|
| `gorgias_sync_operational_issues` | Pulls shipping/order tickets → writes to Google Sheet (UPDATE_Operational Issues tab) | MCP tool or scheduled |
| `gorgias_sync_food_safety` | Pulls spoiled/quality tickets → writes to UPDATE_Food Safety tab | MCP tool or scheduled |
| `enrich_operational_issues` | Fills missing fields (carrier, state, FC tag) from Shopify/Gorgias | MCP tool |
| `gorgias_list_tickets` | Pull raw tickets with filters | MCP tool |
| `gorgias_satisfaction_stats` | CSAT survey breakdown | MCP tool |
| `rebuild_ops_summary` | Rebuild the full ops summary sheet | MCP tool |

**These tools exist and work.** The problem is they're not running automatically and the input data (Gorgias tagging) is incomplete.

---

## Current State (Last 14 Days — ~5,400 orders shipped)

**Order volume:** ~2,700 orders/week (Sat ~2,300 + Tue ~400) from Shopify + Recharge

| Metric | Count | Rate (per 5,400 orders) |
|--------|-------|------------------------|
| Operational issues | 46 | 0.85% |
| Food safety issues | 25 | 0.46% |
| Total real CS tickets | ~71 | 1.31% |
| Spam/auto-reply tickets | ~51% of raw volume | — |
| Cancellation tickets | ~60% of human tickets | — |

**Context:** 0.46% food safety rate is solid for perishable cold chain. All metrics should be tracked as rates against order volume, not raw counts — raw counts rise with growth even if quality improves.

### Top Operational Issues (Last 14 Days)
| Issue Type | Count | Rate | Trend |
|------------|-------|------|-------|
| Arrived Warm / Heat Sensitive | ~12 | 0.22% | Trending up (summer) |
| Missing Item (cheese, meat, accompaniment) | ~10 | 0.19% | Stable |
| Substitute Complaint | ~8 | 0.15% | Stable |
| Quality Complaint (cheese) | ~8 | 0.15% | Stable |
| Wrong Order | ~4 | 0.07% | Stable |
| Mold (sealed package) | ~3 | 0.06% | Monitor |
| Off smell/odor | ~2 | 0.04% | Low |

### Resolutions Applied
| Resolution | Examples |
|------------|----------|
| Full Reship | Arrived warm, major missing items |
| Refund $20 | Missing items, wrong order |
| Credit Next Box $15 | Missing accompaniment |
| Comp Item (extra cheese/accompaniment) | Substitute complaints |
| Comp + Credit $10 | Substitute + partial compensation |

---

## The 3-Step Plan

### Step 1: Fix Gorgias Tagging (Jessa's Team — This Week)

**This is the foundation. Nothing else works without it.**

Every ticket MUST have these fields filled BEFORE closing:

| Field | Required? | Options |
|-------|-----------|---------|
| **Contact Reason** (Issue Type) | YES | Shipping::Arrived Warm, Order::Missing Item, Order::Substitute Complaint, Order::Wrong Order, Order::Quality Complaint, Order::Spoiled Item, etc. |
| **Resolution** | YES | Full Reship, Partial Reship, Refund::$Amount, Credit Next Box::$Amount, Comp Item, No Action |
| **Order Number** | YES (if applicable) | Shopify order # |

**What Jessa needs to do:**
1. Send this to her team (Lawrence, Adel, Eli, Mark): "Starting immediately, every ticket must have Contact Reason + Resolution filled before you close it. No exceptions."
2. Spot-check 5 tickets per day for the first 2 weeks — if a ticket is closed without tags, reopen it and tag it, then message the agent
3. Track tagging rate weekly — goal is 90%+ within 2 weeks

**Tag taxonomy cleanup needed:**
- Merge "cancel/refund" + "Cancel Sub" + "Cancel order" → single "Cancellation" tag
- Ensure Gorgias has these as dropdown options, not freeform text

### Step 2: Automate the Sync (Kurt/AI Assistant — Week 2)

Once tagging is at 50%+, turn on automated syncing:

| Automation | Schedule | What It Does |
|------------|----------|-------------|
| `gorgias_sync_operational_issues` | Daily 6 AM ET | Pulls tagged tickets → Google Sheet |
| `gorgias_sync_food_safety` | Daily 6 AM ET | Pulls food safety tickets → separate tab |
| `enrich_operational_issues` | Daily 6:30 AM ET | Fills carrier, state, FC from Shopify |
| `rebuild_ops_summary` | Monday 7 AM ET | Weekly rollup for Jessa + Kurt |

**How it works:** A cron job or Mechanic automation triggers these MCP tools daily. The Google Sheet becomes the live ops dashboard — Jessa opens it and sees current data, not yesterday's manual entry.

### Step 3: Weekly Report Auto-Generation (Week 3-4)

**The report Jessa wants, auto-generated every Monday:**

```
ELEVATE FOODS — WEEKLY CS OPS REPORT
Week of April 7-13, 2026

TICKET VOLUME
  Total tickets: 245 (↓8% vs last week)
  Real CS tickets: 71 (after spam filter)
  Cancellation requests: 42

ORDER VOLUME: 2,700 orders shipped this week

OPERATIONAL ISSUES (46 issues = 0.85% of orders)
  Arrived Warm: 12 / 2,700 = 0.44% (↑ — entering summer)
  Missing Item: 10 / 2,700 = 0.37% (stable)
  Substitute Complaint: 8 / 2,700 = 0.30% (stable)
  Wrong Order: 4 / 2,700 = 0.15% (↓)
  
FOOD SAFETY (25 issues = 0.46% of orders — target: <1%)
  Quality Complaints: 8 (0.15%)
  Mold Reports: 3 (0.06% — monitor RMFG storage)
  Off Smell: 2 (0.04%)

RESOLUTION COST
  Full Reships: 8 × $65 avg = $520
  Partial Reships: 5 × $30 avg = $150
  Refunds: 12 × $18 avg = $216
  Credits: 6 × $15 avg = $90
  TOTAL CS COST: $976 / 2,700 orders = $0.36/order (↓12% vs last week)

TOP ACTION ITEMS
  🔴 Arrived Warm trending up — summer gel pack adjustment needed (→ Tommy)
  🟡 Mold on sealed cheese — 3 reports this week (→ Tommy for RMFG review)
  🟢 Substitute complaints stable — Decision Guide reducing escalations

AGENT PERFORMANCE
  Adel: 28 tickets, 95% tagging rate, 2.1hr avg response
  Lawrence: 18 tickets, 88% tagging rate, 3.4hr avg response
  Eli: 12 tickets, 72% tagging rate, 4.8hr avg response
  Mark: 13 tickets, 91% tagging rate, 2.8hr avg response
```

**This replaces:** Jessa's manual spreadsheet, the Slack reconciliation, the "I can't keep the file up to date" problem.

---

## What Jessa Gets

| Before | After |
|--------|-------|
| "I badly need help with reporting" | Auto-generated Monday report in Google Sheet |
| Manual ticket counting | Live ops dashboard updated daily |
| "We can't keep the file up to date" | System keeps it up to date |
| Guessing "70% shipping issues" | Exact breakdown with trends |
| Invisible reship costs ($1,500-2,500/week) | Weekly cost tracked to the dollar |
| No agent performance data | Tagging rate + response time per agent |

## What Jessa Does

| Task | Frequency | Time |
|------|-----------|------|
| Enforce tagging (spot-check 5 tickets/day) | Daily, first 2 weeks | 15 min |
| Review weekly ops report | Monday | 15 min |
| Flag patterns to Tommy (quality) or Kurt (systemic) | Weekly | 10 min |
| Send tag taxonomy cleanup to Kurt (one-time) | This week | 30 min |

## What Jessa Does NOT Do
- Manually count tickets
- Build reports in spreadsheets
- Track reship costs by hand
- Reconcile Slack vs Gorgias
- Answer to Kurt for individual ticket decisions (Decision Guide handles)

---

## Timeline

| Week | Milestone | Who |
|------|-----------|-----|
| 1 | Tagging enforcement starts. Team briefed. | Jessa |
| 1 | Tag taxonomy cleanup (merge duplicate tags) | Kurt + Jessa |
| 2 | Tagging rate hits 50%+. Auto-sync turned on. | Kurt |
| 2 | Enrichment running daily (carrier, state, FC) | System |
| 3 | Tagging rate hits 90%+. Weekly report auto-generated. | System |
| 4 | First full automated weekly report delivered Monday AM. | System |
| 4 | Jessa reviews report in 15 min instead of building it in 2 hours. | Jessa |

---

## Appendix: Issue Type Taxonomy (for Gorgias Dropdown)

### Shipping Issues
- Shipping::Damaged in transit::Arrived Warm
- Shipping::Damaged in transit::Crushed/Broken
- Shipping::Late delivery::1-2 days
- Shipping::Late delivery::3+ days
- Shipping::Lost package
- Shipping::Wrong address

### Order Issues
- Order::Missing item::1+ cheeses
- Order::Missing item::All Meats
- Order::Missing item::1+ accompaniment
- Order::Missing item::Entire category
- Order::Wrong Order
- Order::Substitute complaint
- Order::Duplicate charge

### Quality Issues
- Product::Quality Complaint::Cheese
- Product::Quality Complaint::Meat
- Product::Quality Complaint::Accompaniment
- Product::Spoiled Item::Mold
- Product::Spoiled Item::Off smell/odor
- Product::Packaging Heat Sensitivity

### Account Issues
- Account::Cancellation request
- Account::Subscription change
- Account::Billing dispute
- Account::Login/access issue
