# Pallet Loading Verification — Weekly SOP

**Purpose:** Ensure all items needed for next week's RMFG production are loaded onto the Friday LTL pallet from COG (Woburn). Prevents missed ingredients like the Wasabi peas incident (April 2026).

**When:** Every Thursday afternoon, after Arcade Snacks delivery is confirmed received at COG.

---

## Roles

| Who | Responsibility |
|-----|---------------|
| **Tommy** | Enters all incoming product into the Incoming Product sheet. Confirms COG receipt. Flags any development/PR box ingredients. |
| **Dan** | Provides development box ingredient list for any upcoming PR or pre-launch curations. Confirms if development items need to ride the pallet. |
| **You (Owner)** | Runs the cross-check. Makes final MUST LOAD / CAN WAIT call. Communicates pallet manifest to COG. |

---

## Thursday Process

### Step 1 — Tommy confirms Arcade delivery (Tommy, by 2 PM ET)

- [ ] Arcade Snacks delivery arrived at COG
- [ ] Update **Incoming Product** sheet: fill in "Date Accepted COG" column for all items in this delivery
- [ ] Flag any items that are **development/PR ingredients** (not in regular production yet) in the notes
- [ ] Slack message to group: "Arcade delivery received. [X] SKUs. Sheet updated."

### Step 2 — Dan flags development needs (Dan, by 3 PM ET)

- [ ] Check: Are any PR boxes or development curations going into production next week?
- [ ] If YES: List the specific AC-* ingredients needed and quantities
- [ ] Slack message to group: "Dev/PR needs for next week: [list]" or "No dev needs this week"

### Step 3 — Owner runs the cross-check (You, by 4 PM ET)

**What you need open:**
1. Tommy's Incoming Product sheet → "Incoming Product" tab (filter to this week's Arcade delivery)
2. Current RMFG inventory (fulfillment app → Runway tab, filter AC-* SKUs)
3. Next week's cut order demand (fulfillment app → Cut Order tab, or latest cut_order XLSX)

**For each AC-* SKU in this week's Arcade delivery:**

| Question | Answer | Action |
|----------|--------|--------|
| Is this SKU in next week's cut order? | YES | Go to next question |
| Is this SKU in next week's cut order? | NO | Check Dan's dev list. If not there either → **CAN WAIT** |
| Does RMFG have enough on hand to cover next week's demand? | YES (on_hand ≥ demand) | **CAN WAIT** — but note: if runway < 2 weeks, consider loading anyway |
| Does RMFG have enough on hand to cover next week's demand? | NO (shortage) | **MUST LOAD** — calculate units needed: `demand - on_hand` |
| Is this a development/PR ingredient (from Dan's list)? | YES | **MUST LOAD (DEV)** — even if not in regular cut order |

### Step 4 — Build the pallet manifest (You, by 4:30 PM ET)

Fill out and share:

```
PALLET LOADING CHECKLIST — Week of [date]
=========================================

MUST LOAD (production shortage):
  [ ] AC-XXXX  — [Name] — [qty needed] units — Reason: demand [X], on hand [Y], short [Z]
  [ ] AC-XXXX  — ...

MUST LOAD (development/PR):
  [ ] AC-XXXX  — [Name] — [qty needed] units — Reason: PR box ingredient, launch [date]

CAN WAIT (sufficient stock at RMFG):
  - AC-XXXX  — [Name] — RMFG has [X], demand [Y], surplus [Z]
  - AC-XXXX  — ...

NOTES:
  - [Any items with runway < 2 weeks that should load as buffer]
  - [Any items COG hasn't finished processing yet — partial load?]
```

### Step 5 — Communicate to COG (You or Tommy, by 5 PM ET)

- [ ] Send pallet manifest to COG contact
- [ ] Confirm all MUST LOAD items will be on Friday's pallet
- [ ] If any MUST LOAD items aren't processed yet → escalate: can COG prioritize?

---

## Friday Verification

- [ ] Confirm LTL pickup happened
- [ ] Cross-check BOL (bill of lading) against pallet manifest
- [ ] Flag any MUST LOAD items that didn't make it → escalate immediately

---

## Edge Cases

| Situation | What to do |
|-----------|-----------|
| **Arcade delivery is late** (arrives Friday instead of Thursday) | Items won't be processed. Check if RMFG has enough for 1 week. If not, consider emergency parcel shipment for critical items. |
| **COG hasn't finished processing** | Partial load: send what's ready, hold rest for next week's pallet. Flag shortages. |
| **Item not in cut order BUT Tommy knows it's needed** | Trust Tommy's domain knowledge. If he says load it, load it. Add to MUST LOAD with note. |
| **New product / no SKU yet** | Use product name instead of SKU. Flag for Dan to create SKU. Still load if needed. |
| **Development ingredient for a box that might not launch** | Ask Dan for confidence level. If >70% launching, load it. Ingredients at RMFG cost nothing extra to store. |

---

## What This Prevents

- Missing production ingredients (like Wasabi peas)
- Last-minute scrambles on Monday/Tuesday
- Development boxes delayed because ingredients weren't staged
- Tommy/Dan assuming someone else checked

## Future Automation

This manual process will be replaced by an automated Pallet Loading Check in the fulfillment app that:
1. Reads Tommy's Incoming Product sheet automatically
2. Cross-references against cut order demand + calculated inventory
3. Generates the MUST LOAD / CAN WAIT checklist
4. Sends a Slack notification Thursday at 2 PM with the draft checklist
