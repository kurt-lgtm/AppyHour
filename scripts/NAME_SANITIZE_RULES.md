# NAME_SANITIZE_RULES.md — single source of truth for the vF `Name` character rule

🔴 **PRE-CHANGE GATE.** Read this before touching `scripts/name_sanitize.py` or anything that
writes the vF `Name` column. Change the rules HERE first, in the same commit as the code.

## 🧭 North star

Every RMFG production sheet we send is **accepted on the first submission**. Not "mostly clean" —
accepted, because a rejected sheet holds the entire week's production until a corrected one is
resubmitted.

---

## 🔴 THE BURN THAT MOTIVATED THIS RULE — order #169525

Kurt, 2026-08-09, verbatim:

> **"shinsato/johnson was found by them and I got yelled at for it."**

`Shinsato/Johnson` was in the **submitted sheet of record**
(`_outputs/artifacts/vF-intent-2026-08-10/AHB_WeeklyProductionQuery_08-10-26_vF.xlsx`, row for
order **#169525**). The slash shipped. **RMFG caught it. Kurt took the complaint.**

This is not a theoretical class. It happened, to us, in the most recent ship week.

### 🔴 The argument you are about to make was already made, and overruled — twice

Someone will read "strip special characters from names" and think: *but that mangles real surnames,
`Shinsato/Johnson` becomes `ShinsatoJohnson`, surely we should preserve readability.* That exact
argument was raised on 2026-08-09 and Kurt overruled it **twice**:

> **"it doesn't matter if they looked fucked."**
> **"just strip it."**

An intermediate `/` → space proposal (`Shinsato Johnson`) was also put to him and rejected in favour
of plain removal. **Appearance is explicitly not the constraint.** Do not re-litigate this in code,
and do not "improve" the rule back toward readability.

---

## What FAILS (negatives first — this is what the rule exists to stop)

| failure | consequence |
|---|---|
| Any special character in a `Name` | 🔴 **WHOLE SHEET REJECTED** — not the row |
| An invented route / bad tag | 🔴 whole sheet rejected |
| An invented MFG name | 🔴 whole sheet rejected |
| A negative fence naming a dead carrier (`!NO Veho` on a Tuesday) | 🔴 whole sheet rejected |

A rejection comes back as `AHB_Failed Tags_<M-D-YY>.xlsx`. One bad token costs the week.

**Nothing validated characters before 2026-08-09.** `qc_gate` has PO-box regex validation,
`presend_check` has ice/filename/ledger gates, `matrix_commander` validates MFG names — none of them
looked at characters. Kurt believed it was covered. It was not.

---

## The rule — a PIPELINE, and the order is load-bearing

Scope: **the `Name` column only.**

```
1. Remove the C/O token   (case-insensitive)   "C/O Jane Smith"    -> "Jane Smith"
2. "1/2" -> "half"        (THIS TOKEN ONLY)    "3226 1/2"          -> "3226 half"
3. Remaining "/" REMOVED  (no space)           "Shinsato/Johnson"  -> "ShinsatoJohnson"
4. Remove every other non-alphanumeric EXCEPT space and HYPHEN (U+002D)
5. Collapse whitespace runs, trim
```

- **KEEP** letters (including accented — they are letters), digits, space, hyphen.
  Kurt: *"you can keep the hyphens."*
- **`O'Brien` → `OBrien`.** Nothing is replaced by a space.

### 🔴 Why steps 1 and 2 MUST precede step 3
Reversed, the general slash rule eats them first and corrupts both:
- `C/O Jane` → `COJane` (wrong — leading garbage token)
- `1/2` → `12` (wrong — `3226 1/2` becomes house number `3226 12`)

`tests/test_name_sanitize.py` pins the ordering explicitly. It is the part most likely to regress
under a later "simplification".

### 🔴 `1/2` is the ONLY fraction. Do not add others.
Kurt: *"1/2 turned into half. that's it."* `1/4`, `3/4`, etc. must **not** be given a spelling —
that would be an **invented token a person at RMFG reads**, the same class of error as an invented
MFG name, just smaller ([[never-fabricate]]). An unknown fraction correctly falls through to step 3
and lands in the change log, where Kurt decides. **Unknown case gets LOGGED, never guessed at.**

---

## 🔴 Scope — permanently out of bounds

**NEVER addresses.** Survey of the submitted wk0810 sheet found `/` in real street numbers
(`3226 1/2 LA AVENIDA DE SAN MARCOS`, `2714 8 1/2 ST APT 4`). Naive stripping gives `3226 12` — a
**different house**, i.e. undeliverable mail. That is a meaning change, not an appearance one, and
is the single case the cosmetic overrule does not reach.

*The `1/2 → half` rule now makes the transform address-**safe*** (`3226 half` stays deliverable) —
that answers the original objection. It does **not** widen the scope. Addresses go through this
function only on an explicit instruction from Kurt, and if that ever happens it must be **this
function, not a second implementation**.

**NEVER the `AHB (S_REG):` product headers.** All 121 contain zero special characters in their cell
values, and a stripped header would itself become an **invented MFG name** — its own whole-sheet
reject ([[sku-mfg-name-validation-gate]], "we never make up MFG names").

---

## 🔴 CALIBRATION — cosmetic concerns lose, without deliberation

Kurt, 2026-08-09, verbatim:

> **"it doesn't matter. humans read this shit when they get to the house."**

The final reader of the `Name` field is **a delivery driver at the door**, and humans are robust
readers. `Tucker Rhonda M`, `ShinsatoJohnson`, `Renee` — a person works any of them out.

**Name mangling has near-zero real cost. Sheet rejection costs $250,000.** Calibrate entirely for
the second. The asymmetry is roughly six orders of magnitude; there is no version of this trade
where the cosmetic side wins.

### This applies to "should we flag it?" too — not just "should we strip it?"

Three separate hesitations were raised on 2026-08-09 and **all three were overruled**:

1. *Preserve surnames* — "`Shinsato/Johnson` shouldn't become one word." → **"just strip it."**
2. *Preserve readability* — "at least map `/` to a space." → **"it doesn't matter if they looked
   fucked."**
3. *Flag the ambiguous ones for review* — a `LAST, FIRST` comma heuristic, and an unresolved
   question about whether RMFG accepts `é`. → **"it doesn't matter. humans read this shit."**

The third is the subtle one and the one most likely to come back: **raising a review queue is not a
neutral, cautious act.** It spends Kurt's attention, which is the genuinely scarce resource, on a
question whose worst outcome is a slightly odd-looking name. A decision queue that isn't a decision
is a cost, not a safeguard.

**So: apply the rule and move on.** Do not add a "needs Kurt" / "needs review" section to the log,
the report, or the code. Do not special-case a `LAST, FIRST` reorder. Do not add a transliteration
pass or chase RMFG's charset.

### Settled by this rule — closed, not open

| case | outcome | do NOT |
|---|---|---|
| `#170553 'Tucker, Rhonda M'` | → `'Tucker Rhonda M'`, comma removed like any other punctuation | reorder, or detect `LAST, FIRST` |
| `#168458 'Renée Rivers'` | `é` **survives** — the rule strips punctuation, and `é` is a letter | transliterate, or chase RMFG's charset |

Both are logged as **ordinary changes** (the `Renée` row does not even change, so it is simply not
in the log). Neither is a decision.

---

## Ground truth — which file is authoritative

🔴 **`_outputs/artifacts/vF-intent-2026-08-10/AHB_WeeklyProductionQuery_08-10-26_vF.xlsx`**
(347,639 B · 08-07 16:55 · 2,253 rows) is Kurt's **submitted sheet of record**. The claim directory
is the authority and is **never written into**.

The ROOT `_outputs/artifacts/AHB_WeeklyProductionQuery_08-10-26_vF.xlsx` is **NOT** authoritative —
it was overwritten 2026-08-09 17:16 by another session's acceptance run (a hardcoded path literal
escaped its isolation). Any figure derived from the root must be re-derived against the claim
directory, stating its source.

**Measured on the authoritative file: 25 names change** (`_vF_MERGED` also 25, identical change
set — an earlier hand-count of 24 was wrong; `#169525` is in both files).

## Tooling

| need | call |
|---|---|
| survey + log a sheet (no writes) | `python AppyHour/scripts/name_sanitize.py <vF.xlsx>` |
| write a sanitized COPY | `... --out <new.xlsx>` (refuses to overwrite; never touches the source) |
| compare two sheets' counts | `... a.xlsx b.xlsx --compare` |
| tests | `pytest AppyHour/tests/test_name_sanitize.py` |

The log (`_outputs/reports/<date>-name-sanitize-<sheet>.md` + `.csv`) is the deliverable: order,
column, before, after, every removed character with codepoint and unicode name, plus a frequency
table. Kurt writes the permanent rule from it without re-running anything.
