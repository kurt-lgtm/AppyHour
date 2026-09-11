# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Validate the MFG-name AUTHORITY ITSELF — the one thing every other guard assumes is clean.

🔴 THE AUTHORITY IS THE DO TABLE `mfg_names_authoritative` (Kurt 2026-09-11: "not a csv"), read
through the ONE resolver `matrix_commander.load_mfg_names`. The csv beside the code is a local
read-mirror; `--path` reads a FILE only as an explicit test override.

🔴 WHY THIS EXISTS. `matrix_commander.validate_mfg_names` checks translations *against* the
authority. Nothing checks the authority. That is the authority-registry meta-rule again: **a guard
that reads the source it is guarding validates nothing about that source.** Every downstream check
inherits whatever is in the table, so a polluted row is not caught anywhere — it becomes the
authority and validates itself.

Kurt 2026-08-09: *"we also have to take care to separate mfg names from other shit as to not
pollute our export when we add to mfg names."* The risk is at the ADD, not the read.

🔴 THE FAILURE THIS PREVENTS (negatives first):
  - A name pasted from a screenshot / Shopify title / meal-type PDF instead of the export — the
    wk0803 "Frumage L'Ottavio" class. A curly apostrophe or a stray label word is invisible in a
    csv and reaches a SENT vF as a column header RMFG's floor cannot pick (234 count rows).
  - The same NAME on two SKUs — a reverse map picks one silently (the CH-BRZ/CH-PRBZ class is
    DIFFERENT names; an exact duplicate is a real defect).
  - A blind overwrite with a fresh export that DROPPED items — pollution's mirror image. Additions
    must be reviewed as a DELTA, never as a file swap (see --diff-against).

🔴 THIS SCRIPT NEVER WRITES THE AUTHORITY. It validates and it diffs. The table is written ONLY by
the console upload (`/admin/upload kind=mfg_names`, MATRIX_RULES rule 21); this makes that step
reviewable instead of blind.

    python scripts/utilities/validate_mfg_authority.py
    python scripts/utilities/validate_mfg_authority.py --diff-against ~/Downloads/meal_type_export.csv
    python scripts/utilities/validate_mfg_authority.py --path <test.csv>      # explicit file override
"""
import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# Windows console defaults to cp1252 and dies on the 🔴/✅ markers this script leads with — the
# workspace-wide crash class. Guard here so a VIOLATION never gets swallowed by an encoding
# traceback (a validator that crashes while reporting is a validator that reports nothing).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APPYHOUR = Path(__file__).resolve().parents[2]
AUTHORITY_LABEL = "DO table mfg_names_authoritative"

# Grammar measured across all 285 rows on 2026-08-09: every name is "AHB (S_REG): <name>".
# Asserted, not assumed — if RMFG ever issues a second prefix this fails LOUDLY and the rule gets
# re-decided deliberately, which is the point. Never widen this pattern to make a paste pass.
NAME_RE = re.compile(r"^AHB \(S_REG\): \S.*$")

# SKU prefixes present in the authority + the product-rules taxonomy. A new prefix is a real
# onboarding event (Kurt decision), not something a validator should quietly accept.
KNOWN_PREFIXES = {"AC", "CH", "CHW", "MT", "PK", "EX", "PR", "TR", "MR", "BL", "AHB", "CEX"}

# Characters that mean "this was pasted from a rendered document, not exported".
# The wk0803 burn came in as a curly apostrophe. Straight ASCII punctuation only.
SMART = {"‘", "’", "“", "”", "–", "—", "…", " "}


def read_rows(path):
    """A FILE's rows — for --diff-against (a fresh export) and the --path test override only."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.reader(f) if any((c or "").strip() for c in r)]


def authority_rows():
    """The authority's rows as [sku, name] pairs, through the ONE resolver (never a csv)."""
    if str(APPYHOUR) not in sys.path:
        sys.path.insert(0, str(APPYHOUR))
    from matrix_commander import load_mfg_names
    return [[sku, name] for sku, name in load_mfg_names().items()]


def validate(rows, label=AUTHORITY_LABEL):
    """-> list of violation strings. Empty list = clean."""
    bad = []
    if not rows:
        return [f"{label} is EMPTY — every downstream name check now FAILS CLOSED on it "
                f"(MfgAuthorityUnavailable), so the matrix and every vF tool are blocked until "
                f"RMFG's export is re-uploaded via the console (kind=mfg_names)"]

    for i, r in enumerate(rows, 1):
        if len(r) != 2:
            bad.append(f"row {i}: {len(r)} columns, expected exactly 2 (SKU,name) — "
                       f"extra columns belong in a SEPARATE file: {r!r}")
            continue
        sku, name = (r[0] or "").strip(), (r[1] or "").strip()
        if not sku or not name:
            bad.append(f"row {i}: blank SKU or name: {r!r}")
            continue
        if sku != r[0] or name != r[1]:
            bad.append(f"row {i}: leading/trailing whitespace — {r!r}")
        if not NAME_RE.match(name):
            bad.append(f"row {i} [{sku}]: name does not match the export grammar "
                       f"'AHB (S_REG): <name>' — {name!r}. A name off a screenshot/title/PDF "
                       f"lands here (wk0803 class). Re-export; do not hand-fix.")
        if sku.split("-")[0] not in KNOWN_PREFIXES:
            bad.append(f"row {i}: unknown SKU prefix {sku.split('-')[0]!r} ({sku}) — a new prefix "
                       f"is an onboarding decision, not a validator default")
        for ch in name:
            if ch in SMART:
                bad.append(f"row {i} [{sku}]: {unicodedata.name(ch, repr(ch))} in {name!r} — "
                           f"smart punctuation means PASTED, not exported")
                break

    two = [r for r in rows if len(r) == 2]
    for lbl, idx in (("SKU", 0), ("name", 1)):
        dupes = {v: n for v, n in Counter(r[idx].strip() for r in two).items() if n > 1}
        for v, n in sorted(dupes.items()):
            bad.append(f"duplicate {lbl} {v!r} appears {n}× — a reverse map picks one silently "
                       f"(the table's SKU is its PRIMARY KEY, so a duplicate SKU means a corrupt "
                       f"override file, never the table)")
    return bad


def diff(cur_rows, fresh):
    """Show the ADD/DROP/CHANGE delta so an addition is reviewed, never a blind file swap."""
    cur = {r[0].strip(): r[1].strip() for r in cur_rows if len(r) == 2}
    new = {r[0].strip(): r[1].strip() for r in read_rows(fresh) if len(r) == 2}
    added = sorted(set(new) - set(cur))
    dropped = sorted(set(cur) - set(new))
    changed = sorted(s for s in set(cur) & set(new) if cur[s] != new[s])
    print(f"\nDELTA vs {Path(fresh).name}: +{len(added)} added · -{len(dropped)} dropped · "
          f"~{len(changed)} renamed\n")
    for s in added:
        print(f"  + {s:<12} {new[s]}")
    for s in dropped:
        print(f"  - {s:<12} {cur[s]}      🔴 DROPPED — confirm this item is truly retired")
    for s in changed:
        print(f"  ~ {s:<12} {cur[s]!r}\n      -> {new[s]!r}")
    if dropped or changed:
        print("\n🔴 Drops and renames are NOT automatically safe. A rename mid-cohort changes a vF "
              "header RMFG is already picking from. Confirm with the export's owner before "
              "uploading the export to the console.")
    return added, dropped, changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None,
                    help="TEST override: validate this csv instead of the DO table")
    ap.add_argument("--diff-against", default=None,
                    help="a FRESH meal-type export; prints the add/drop/rename delta to review "
                         "BEFORE uploading it to the console")
    a = ap.parse_args(argv)

    if a.path:
        p = Path(a.path)
        if not p.exists():
            print(f"🔴 override file not found: {p}")
            return 1
        rows, label = read_rows(p), str(p)
    else:
        try:
            rows, label = authority_rows(), AUTHORITY_LABEL
        except Exception as e:                                       # noqa: BLE001 — named below
            print(f"🔴 {AUTHORITY_LABEL} unreachable ({type(e).__name__}: {e}) — nothing validated")
            return 1

    bad = validate(rows, label)
    print(f"MFG AUTHORITY — {label}")
    print(f"  {len(rows)} rows")
    if bad:
        print(f"\n🔴 {len(bad)} VIOLATION(S):")
        for b in bad:
            print(f"  - {b}")
    else:
        print("  ✅ schema clean: 2 columns, export grammar, unique SKU + name, no pasted punctuation")

    if a.diff_against:
        fresh = Path(a.diff_against).expanduser()
        if not fresh.exists():
            print(f"\n🔴 --diff-against file not found: {fresh}")
            return 1
        fbad = validate(read_rows(fresh), str(fresh))
        if fbad:
            print(f"\n🔴 the FRESH export itself has {len(fbad)} violation(s) — do NOT upload it:")
            for b in fbad:
                print(f"  - {b}")
            return 1
        diff(rows, fresh)

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
