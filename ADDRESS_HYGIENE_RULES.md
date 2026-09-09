# ADDRESS_HYGIENE_RULES.md — single source of truth for automated address repair

🔴 **PRE-CHANGE GATE.** Read this BEFORE touching `scripts/address_split_sweep.py`, the Mechanic
`address-clean` task, or any code that writes a customer/order shipping address. Change the rule
HERE first, in the same commit as the code.

## 🧭 North star

A shipping address we hold is deliverable **as the customer meant it** — repaired automatically only
where the repair is provably lossless, and escalated to a human everywhere else. An address we
"fixed" into something the carrier can't deliver is worse than one we left alone and flagged.

---

## 0. THE RULE THAT PRODUCED THIS DOC — an address repair that GUESSES is a defect

Every incident in this doc's history is an automated repair that inferred something:

| what it inferred | what it cost |
|---|---|
| `address2` is a duplicate if `address1 contains address2` (Liquid substring) | 7 orders lost a real unit designator — `B`, `1`, `210`, `A`, `D` — because a 1-char unit is a substring of nearly any street line |
| a trailing letter after a street suffix is a unit | dry-run caught it before it shipped: `15401 E 40th St S` → `15401 E 40th St` + unit `S`, and `4609 Jim Mitchell Trl w`. Both letters are DIRECTIONALS, part of the street |
| an address matching a target zip is the target address | zip-trimmed an unrelated address (`349 BELLE DOWDLE RD`) on a customer who happened to share the zip |

**Rule: repair only what is provably lossless. Everything else gets FLAGGED, never guessed.**
Provably lossless = the output contains every character of meaning from the input, rearranged.
Concatenating `address1` + `address2` is lossless. Deleting a token is not. Correcting a spelling
is not — `Countrt`→`Country` is near-certain and still requires a human, because the sweep cannot
tell it from a genuine street name it has never seen.

---

## 1. What the sweep MAY repair automatically

**The split-address pattern, and nothing else.** `address1` is a bare house number
(`^\d+[A-Za-z]?$`) and `address2` carries the street name. Repair = join them into `address1`.

- **An explicit unit designator stays in `address2`** — `Apt`, `Unit`, `Ste`, `Suite`, `Bldg`,
  `Fl`, `Rm`, `Lot`, `Trlr`, `#`. Kurt 2026-08-27: "keep unit in address 2."
- 🔴 **A bare trailing letter is NEVER treated as a unit.** It is far more often a directional
  belonging to the street. It stays in `address1`, untouched.
- **A unit keyword the splitter cannot cleanly separate → SKIP the whole row**, report it, move on.

## 2. What the sweep must NEVER do

- **Never correct a spelling, expand an abbreviation, or normalize a street suffix.** Not `Lanr`,
  not `Ridg`, not `Blvd`→`Boulevard`. Report them.
- **Never edit a ZIP.** ZIP+4 is valid; trimming it is a delivery-affecting change for no gain.
- **Never match a record by one field.** Match on the full `(address1, address2)` pair actually
  observed. Matching by zip alone edited an unrelated address.
- **Never write a record whose live values have drifted** from what the scan saw — the customer may
  have fixed it themselves. Re-read before every write; on mismatch, skip and report.
- **Never delete an address record** as part of the sweep. Deletion is a Kurt decision, taken
  manually, and only when a clean sibling already holds the merged value.

## 3. Shopify's "Address already exists" is expected, not an error

A customer often already has BOTH the broken record and a clean one. Shopify returns
`422 {"errors":{"base":["Address already exists"]}}` when you try to edit the broken one into the
clean one's value. **The correct response is to make the clean record the default**, not to retry or
force. 4 of 24 customers hit this on 2026-08-27; all 4 had a clean sibling already on file.

## 4. Scope — active only, and SHOPIFY FIRST

🔴 **Shopify is the surface that matters most: labels print from the Shopify ORDER address**
(Kurt 2026-08-27). Recharge is the upstream source that stops recurrence, but a broken Recharge
address costs nothing this week — a broken Shopify order address ships a box to a number with no
street. **The sweep repairs in this order, every run: (1) open unfulfilled ORDERS, (2) Shopify
customer default, (3) Recharge.** If a run dies partway, it must have already fixed the thing that
ships.

- **Shopify:** OPEN UNFULFILLED orders, plus the customer default address so the next renewal does
  not recreate the split.
- **Recharge:** addresses backing an ACTIVE subscription. On 2026-08-27, 143 split addresses existed
  across 47,758 records but only 24 backed an active sub. The other 119 are dead weight; touching
  them is write volume against customers who are not shipping.
- 🔴 **Never rewrite the address on a FULFILLED order.** Shopify freezes it at fulfillment, so it is
  the historical record of what actually shipped — and it is the evidence trail for carrier address
  correction charges. 73 fulfilled orders carried `invalid_address` on 2026-08-27; that tag is
  ACCURATE on them and must stay.

## 5. The `invalid_address` tag

Applied by a Shopify **Flow** on order creation. It does not re-evaluate — three orders were found
tagged with addresses that had since been fixed. **The sweep clears the tag only on an order whose
address it verified clean in the same pass.** Whoever fixes the address owns clearing the flag.

## 6. Division of labor with Mechanic

The Mechanic `address-clean` task owns the ORDER at intake (name cleaning, PO Box, address2
duplicates). This sweep owns the **subscription source**, which Mechanic cannot reach.

🔴 **Do not add split-merge logic to Mechanic.** The split regenerates from the Recharge address on
every renewal, so a Mechanic-only fix repairs the same customers monthly and never clears the
source. One writer per field: Mechanic clears `address2` duplicates, this sweep merges splits.

## 7. 🔴 Redundancy is required — one trigger is not enough

**Kurt 2026-08-27: "it has to be redundant."** A single scheduled job is a single point of silent
failure, and this operation's whole dead-cadence class is jobs that stopped and nobody noticed. The
sweep therefore runs from MORE THAN ONE trigger, and is safe to run any number of times:

1. **Scheduled** — catch-up-on-miss, the routine floor.
2. **Pre-ship gate** — called from the weekly shipping run BEFORE labels print. This is the one that
   actually protects a cohort; the schedule is the backstop, not the other way round.
3. **On demand** — plain CLI, dry-run by default.

**Idempotence is what makes redundancy safe.** Every repair re-reads live state and matches on the
exact `(address1, address2)` pair before writing, so a second run over the same data is a no-op, and
two triggers firing close together cannot double-apply or fight. Never add a trigger that assumes it
is the only one.

## 8. Ownership and cadence (writer-ownership gate)

- Scheduled owner with **catch-up-on-miss** semantics (`StartWhenAvailable` / logon cycle). Never a
  bare fixed-time task, never early morning — Kurt's machine is off.
- Every run reports to `#kurt-ops` (`C0BT47XG8CW`, bot `appyhouropsreader` must be a member) when it
  changed anything or found something ambiguous. A clean run is silent.
- Findings and ambiguous rows land in `_outputs/reports/address-split-sweep-*.json` regardless of
  delivery, so an undelivered alert is never a lost finding.

Implementation: `AppyHour/scripts/address_split_sweep.py`.
Origin: the 2026-08-27 Veho Address Correction audit — 8 ADC charges, 6 of 8 on orders already
tagged `invalid_address` and shipped anyway.

## A CANCELLED order is never an address decision

🔴 **Never stage a cancelled order for a human address ruling.** A cancelled order will not ship, so
its address cannot be wrong in any way that costs anything — asking for a decision on it spends the
scarcest thing in the loop (Kurt's attention) on a box that does not exist, and trains him to skim
the alarm that also carries the real ones.

Burn 2026-09-09: `wrong_address_automation.py` staged `#182251` (Franklin St, Melrose MA 02176) as
`NEEDS_FIX (your call)` for the third consecutive day while the order was cancelled. Kurt: *"that one
order is cancelled. i don't need to address it. this is a bad alarm."*

- **The invalid-address tag SURVIVES cancellation**, so tag presence alone can never distinguish
  these. The order must be asked for `cancelledAt` and skipped on it.
- **Check it BEFORE the tag test**, not after — otherwise a cancelled-and-still-tagged order falls
  through into triage exactly as it did on 09-09.
- **Count it, don't hide it.** Skipped-cancelled is reported on the summary line
  (`cancelled(skipped): N`) so a suddenly large number is visible rather than silently swallowed.
- Same reasoning applies to any future consumer of the wrong-address feed. It is a property of the
  ORDER STATE, not of this one script.

Measured after the fix (`--days 3`, dry): 16 detected → 12 tag-cleared, **4 cancelled skipped**,
0 NEEDS_FIX. Before the fix that run put a cancelled order in front of Kurt.

Implementation: `scripts/automations/wrong_address_automation.py` (`ORDER_QUERY` requests
`cancelledAt`; the guard sits at the top of the per-order loop).
