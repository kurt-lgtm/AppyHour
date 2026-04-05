# Elevate Foods — Operations Reorganization

**Date:** 2026-04-05
**Premise:** You are the architect, not the operator. Every process that currently touches you should route to someone else or to a system. Your time goes to strategy, systems design, and growth — never to tickets, swaps, or manual data entry.

---

## The Problem: You're the Bottleneck

Right now, everything flows through you:

```
Customer complaint → Slack → YOU decide → CS executes
Shortage discovered → Slack → YOU calculate swap → RMFG executes  
Cut order needed → YOU pull data → YOU build XLSX → RMFG receives
PO needed → Dan Slacks YOU → YOU confirm → Dan orders
Inventory question → Anyone → YOU look it up → YOU respond
New cheese rotation → Michelle proposes → YOU figure out demand impact
Reship decision → Gorgias → YOU approve → CS ships
Ad budget change → Michelle does it → YOU discover demand spike after the fact
```

**You are a single point of failure for a 13,489-subscriber business.** If you take a vacation, the business stops. That's not sustainable, and it's keeping you from the work you're actually good at.

---

## The New Org: Four Pillars

```
                         ┌──────────────┐
                         │     YOU      │
                         │  Architect   │
                         │  Strategist  │
                         └──────┬───────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
     ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
     │   PRODUCT   │    │ OPERATIONS  │    │    TECH     │
     │   Tommy     │    │  (System)   │    │   Anik +    │
     │             │    │  + Lawrence │    │   AI Asst   │
     └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
            │                   │                   │
     ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
     │ Procurement │    │ Fulfillment │    │  Platform   │
     │ Rotation    │    │ CS          │    │  AppyHour   │
     │ Suppliers   │    │ Shipping    │    │  MCP tools  │
     │ Quality     │    │ Inventory   │    │  Automation │
     └─────────────┘    └─────────────┘    └─────────────┘
```

---

## Pillar 1: PRODUCT — Tommy

**Tommy owns everything cheese.** Not just procurement — the full product lifecycle.

### Tommy's Domain
| Area | What He Owns | What the System Gives Him |
|------|-------------|--------------------------|
| **Cheese Selection** | Which cheeses enter/exit rotation | Recipe planner with demand impact preview |
| **Procurement** | Vendor relationships, POs, pricing | Auto-PO drafts every Monday, one-click approve |
| **Quality** | Shelf life, storage, cut specs | Quality complaint data from Gorgias (once tagged) |
| **Rotation Calendar** | 2-3 month lookahead | Auto-ramp calculator, lead time warnings |
| **Supplier Management** | Negotiate, maintain relationships | Vendor catalog with lead times, MOQ, cost tracking |
| **New Product Development** | Trays, specialty boxes, seasonal | Demand modeling tool — "if we launch X, here's the inventory impact" |

### What Tommy Does NOT Do
- Answer customer tickets (that's Lawrence)
- Build cut orders (that's the system)
- Calculate demand (that's the system)
- Monitor daily inventory (that's the system — he reviews weekly)

### Tommy's Weekly
| Day | Activity | Time |
|-----|----------|------|
| Monday | Approve POs from dashboard. Check supplier calendar. | 30 min |
| Tuesday | — | — |
| Wednesday | Review cut order accuracy. Monthly: update next month recipes. | 30 min |
| Thursday | Confirm RMFG received materials. Supplier calls if needed. | 30 min |
| Friday | Review weekly quality report (Gorgias complaint types by cheese). | 20 min |
| **Total** | | **~2 hrs/week** + monthly planning sessions |

### Tommy's KPIs
- Zero shortage incidents per week (target: 4 consecutive clean weeks)
- PO lead time accuracy (ordered early enough to arrive before stockout)
- New cheese launch success rate (no stockout in first 2 weeks of rotation)
- Supplier cost per unit trending down or stable

---

## Pillar 2: OPERATIONS — The System + Lawrence

**Operations runs itself.** The system handles inventory, demand, cut orders, and alerts. Lawrence handles customers. You handle neither.

### The System Owns
| Process | How It Works Now | How It Should Work |
|---------|-----------------|-------------------|
| **Demand calculation** | You pull Recharge + Shopify manually | Auto-sync daily. Curation floor + EWMA + growth multiplier. |
| **Cut order** | You build XLSX on Wednesday | System auto-generates. You approve with one click. |
| **Inventory tracking** | You check dashboard, interpret numbers | Journal-based ledger. Single source of truth. Red/amber/green per SKU. |
| **Shortage detection** | Dan/Kurt discover via Slack DMs | System alerts 2 weeks ahead. PO auto-drafted. |
| **Depletion** | Semi-manual XLSX upload | Auto-detect shipment files, auto-deplete, write journal entry. |
| **Swap suggestions** | You calculate manually | System suggests swaps based on surplus/shortage + dietary rules. |
| **Reship decisions** | Gorgias → you → CS executes | CS decision guide (already built!). Lawrence decides. You never see it. |

### Jessa Owns CS Operations (Peak Support Supervisor)

Jessa Enriquez is the CS supervisor via Peak Support. She manages the agent team (Lawrence, Adel, Eli, Mark Jason). She is currently overloaded — handling tickets, social media, and manual reporting with no automated tools.

**Jessa's Current Pain Points (from Slack conversations):**
- Ticket volume overwhelming (~70% shipping/delivery issues, rising into summer)
- Manual ops reporting unsustainable — "I badly need help with the reporting"
- Gorgias tagging rate at 6% — agents close tickets without filling required fields
- Staffing fragility — absences cascade directly onto Jessa with no buffer
- Social media management was added to her scope but never staffed properly
- Escalation bottleneck — everything non-standard routes through you

**What Jessa Needs (and what we're building):**

| Need | Solution | Status |
|------|----------|--------|
| Automated Gorgias tagging | Food safety automation + enforce required fields before close | In progress |
| Live ops reporting | Auto-generated weekly report from Gorgias API data | AI assistant building |
| CS Decision Guide accessible | Host on Flask app (browser-based, no download) | Built, needs deployment |
| Ticket volume dashboard | Gorgias API → live view of volume, types, trends | To build |
| Spoilage/mold as distinct category | Add Gorgias tag + reporting filter | To configure |
| Social media off her plate | Org decision — reassign to Michelle's team | Needs your decision |
| Escalation rules in writing | CS Decision Guide covers this — validate with Jessa | Built |

**CS Team Authority (via Decision Guide):**

| Authority Level | No Approval Needed |
|---------------------|-------------------|
| Reship warm/spoiled/delayed orders | Up to box value |
| Issue $20 credit | Standard resolution |
| Replace missing items on next box | Standard |
| Cancel/refund single box | Standard |
| **Escalate to Tommy** | Product quality (taste, texture, sourcing) |
| **Escalate to Jessa** | Staffing, coverage, agent performance |
| **Escalate to you** | Legal threats, PR risks, systemic patterns only |

**Jessa's new responsibilities:**
1. Enforce Gorgias tagging — every ticket MUST have Contact Reason + Resolution before close
2. Validate the CS Decision Guide with her team — confirm agents can use it autonomously
3. Weekly: review auto-generated ops report for patterns, flag to Tommy (quality) or you (systemic)

**Jessa does NOT escalate to you for:**
- Individual reship decisions (Decision Guide handles)
- Standard credits/refunds (agent authority)
- Product quality questions (→ Tommy)
- Coverage/staffing (she manages her team)

### CS Team KPIs
- Ticket tagging rate: >90% (currently 6%)
- First response time: <4 hours
- Resolution without escalation: >85%
- Customer satisfaction score (Gorgias): track trend
- Reship cost per week: track and trend (currently invisible — $1,500-2,500/week estimated)

---

## Pillar 3: TECH — Anik + AI Assistant

**They build and maintain the platform.** You spec it, they build it.

### Anik's Domain
- AppyHour fulfillment web app (Flask + pywebview)
- New dashboard features (7 sprints from operations-plan.md)
- Bug fixes and pipeline maintenance
- Tray integration, legacy variant ID resolution

### AI Assistant's Domain
- MCP tools and automation scripts
- Gorgias reporting pipeline
- Slack integrations (PO alerts, anomaly detection)
- Data analysis and reconciliation scripts

### Your Relationship with Tech
You are the **product owner**, not the developer. You:
1. Define what needs to be built (specs, like in operations-plan.md)
2. Prioritize the backlog
3. Review the output
4. Never write production code yourself (unless you want to)

---

## Pillar 4: YOU — Architect & Strategist

### What You Actually Do

**This is your new role. Everything else is delegated.**

| Category | Activity | Frequency | Time |
|----------|----------|-----------|------|
| **Strategy** | Review business metrics (sub growth, churn, revenue, costs) | Weekly | 1 hr |
| **Strategy** | Plan next quarter's product/growth initiatives with Tommy + Michelle | Monthly | 2 hrs |
| **Strategy** | Evaluate new box types, markets, channels | Ad-hoc | — |
| **Systems** | Design new automation (spec for Anik) | Weekly | 2-3 hrs |
| **Systems** | Review and improve existing workflows | Weekly | 1 hr |
| **Systems** | Build MCP tools and prototypes (your creative outlet) | Ad-hoc | — |
| **Oversight** | Monday: scan PO approvals, shortage alerts (approve/reject) | Monday | 15 min |
| **Oversight** | Wednesday: approve cut order (one click) | Wednesday | 15 min |
| **Oversight** | Friday: glance at weekly ops summary | Friday | 15 min |
| **Oversight** | Review Gorgias reports for systemic patterns | Weekly | 30 min |
| **Total** | | | **~5-8 hrs/week on operations, rest on strategy** |

### What You Never Do Again
- ❌ Read customer tickets
- ❌ Calculate demand or build cut orders manually
- ❌ Decide individual reships or credits
- ❌ Chase Dan/Tommy for PO status
- ❌ Manually check inventory levels
- ❌ Build swap lists
- ❌ Respond to shortage DMs (system alerts Tommy, not you)
- ❌ Monitor Slack for operational issues in real-time

### What Only You Can Do
- ✅ Set the strategic direction (which curations, which markets, when to scale)
- ✅ Design the systems (spec the software, define the workflows)
- ✅ Make big bets (new product lines, pricing changes, partnerships)
- ✅ Hire and develop the team
- ✅ Review patterns and trends (not individual data points)
- ✅ Be the architect of a business that runs without you

---

## The Transition: 4-Week Handoff

### Week 1: Foundation
- [ ] Anik fixes Sprint 1 bugs (MONG, CEX-EC, variant IDs, tray)
- [ ] Tommy starts filling vendor catalog (his cheese knowledge → system data)
- [ ] Lawrence starts tagging 100% of Gorgias tickets (enforce before close)
- [ ] You: stop answering Slack shortage DMs. Redirect to Tommy.

### Week 2: Dashboard
- [ ] Anik delivers curation-floor dashboard (Sprint 2)
- [ ] Tommy reviews dashboard daily for one week (calibration)
- [ ] You: stop building cut orders manually. Review system-generated one instead.
- [ ] Lawrence: assistant delivers first weekly Gorgias report

### Week 3: PO Automation
- [ ] Tommy's vendor catalog complete
- [ ] Anik delivers EWMA + PO draft (Sprint 3-4)
- [ ] Tommy approves first auto-generated PO
- [ ] You: stop approving individual POs. Tommy owns this now.

### Week 4: Go-Live
- [ ] All operational Slack channels go to Tommy (product/procurement) or Lawrence (CS)
- [ ] You leave #reship-and-order-requests channel
- [ ] Weekly ops summary auto-posted to you (read-only, 15 min review)
- [ ] You spend the week on strategy and systems design

### The Test
**Can you take a week off and nothing breaks?** If yes, the reorg worked. If Tommy can approve POs, Lawrence can handle CS, and the system generates cut orders — you're free.

---

## The Dashboards You'll Actually Use

### 1. Weekly Strategy Dashboard (Friday review, 15 min)
```
SUBSCRIBER HEALTH
  Active: 13,489 (+2.1% wow)    Churn: 1.8%/month    New: 214 this week
  
FINANCIAL
  Revenue/sub: $48.50    Shipping cost/box: $9.12    CS cost/box: $1.85
  
OPERATIONAL HEALTH  
  Shortage incidents: 0 (target: 0)    On-time ship rate: 97.2%
  Gorgias tag rate: 92%    Avg resolution: 3.2 hrs
  
INVENTORY
  SKUs critical: 0    SKUs amber: 3    Weeks of inventory avg: 2.8
  
PRODUCT
  Next rotation: May 1    New cheeses: 3    Auto-POs drafted: 5
```

### 2. Monday Operations Check (15 min)
```
PO DRAFTS (Tommy to approve)
  CH-EBRIE  | 500 units | Forever Cheese | $2,400 | Runway: 0.8 → 3.2 weeks
  MT-LONZ   | 150 units | VT Salumi     | $900   | Runway: 1.3 → 4.0 weeks
  [All approved by Tommy ✓]

ALERTS
  🟢 No critical shortages
  🟡 AC-DTCH arriving Thursday (PO confirmed)
  
CUT ORDER (Wednesday)
  Auto-generated. 47 SKUs. Total units: 18,400. Ready for your approval.
```

---

## Michelle's Role (unchanged, one addition)

Michelle continues owning marketing and seasonal box planning. **One new process:**

When scaling ad spend >2x: post in #ads-team with spend level and expected duration. System picks up the signal and bumps safety buffer on inventory.

When planning seasonal/limited-release boxes: enter the box recipe in the system 3+ weeks before launch. System auto-calculates demand impact and triggers PO drafts.

---

## Summary: Before vs After

| | Before (now) | After (4 weeks) |
|--|-------------|-----------------|
| **Your time** | 30-40 hrs/week ops | 5-8 hrs/week oversight + unlimited strategy |
| **Shortages** | 2-4/week, discovered reactively | 0/week, prevented 2 weeks ahead |
| **Cut order** | Manual, 2-3 hrs Wednesday | One-click approve, 15 min |
| **POs** | DM-based, reactive | Auto-drafted Monday, Tommy approves |
| **CS tickets** | Route through you | Lawrence decides, you never see them |
| **Reporting** | 94% of tickets invisible | 90%+ tagged, weekly summary |
| **New cheese launch** | Chaos, first week stockouts | Auto-ramp, zero shortage |
| **Vacation test** | Business stops | Business runs for weeks |

**The goal isn't to work less. It's to work on the right things.** You're a planner — go plan. Let the system and the team handle the rest.
