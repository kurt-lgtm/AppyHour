"""Tests for scripts/name_sanitize.py — the RMFG special-character total-loss class.

Every case here is drawn from a REAL name in the submitted wk0810 sheet
(`_outputs/artifacts/vF-intent-2026-08-10/AHB_WeeklyProductionQuery_08-10-26_vF.xlsx`),
so the fixtures are evidence, not invented examples.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from name_sanitize import is_kept, sanitize  # noqa: E402


def clean(s):
    return sanitize(s)[0]


# --------------------------------------------------------------- 🔴 THE BURN
def test_shinsato_johnson_the_motivating_burn():
    """🔴 Order #169525. Kurt, verbatim: "shinsato/johnson was found by them and I got yelled at
    for it." This slash SHIPPED in the submitted sheet of record, RMFG caught it, Kurt took the
    complaint. Kurt's ruling: "just strip it." Appearance is explicitly not the constraint.

    If a future reader wants to soften this to preserve readability — that argument was made and
    overruled twice. Do not re-litigate it in code.
    """
    assert clean("Shinsato/Johnson") == "ShinsatoJohnson"


# --------------------------------------------------- 🔴 PIPELINE ORDER (most likely to regress)
def test_co_token_removed_before_general_slash():
    """Reversed, 'C/O Jane' -> 'COJane'. Step 1 must precede step 3."""
    assert clean("C/O Jane Smith") == "Jane Smith"
    assert clean("C/O Jane Smith") != "COJane Smith"
    assert clean("c/o jane smith") == "jane smith"          # case-insensitive


def test_fraction_converted_before_general_slash():
    """Reversed, '1/2' -> '12'. Step 2 must precede step 3."""
    assert clean("3226 1/2") == "3226 half"
    assert clean("3226 1/2") != "3226 12"


def test_full_pipeline_ordering_together():
    """All three slash consumers in one string, in order."""
    assert clean("C/O Shinsato/Johnson 1/2") == "ShinsatoJohnson half"


def test_co_does_not_fire_on_names_containing_co():
    """The token requires the slash — 'Cohen' and 'Coleman' must survive untouched."""
    assert clean("Cohen Coleman") == "Cohen Coleman"


@pytest.mark.parametrize("frac", ["1/4", "3/4", "2/3", "5/8"])
def test_only_one_half_is_converted_others_fall_through(frac):
    """🔴 Kurt: "1/2 turned into half. that's it." Any other fraction must NOT be given an
    invented spelling — it falls through to step 3 (slash removed) and lands in the change log,
    where Kurt decides. Unknown case gets LOGGED, never guessed at."""
    out = clean(f"Jane {frac} Smith")
    assert "half" not in out
    assert out == f"Jane {frac.replace('/', '')} Smith"


def test_one_half_is_converted():
    assert clean("Jane 1/2 Smith") == "Jane half Smith"


# --------------------------------------------------------------- removals
@pytest.mark.parametrize("before,after", [
    ("Bill & Barb Beach", "Bill Barb Beach"),                 # #164903 ampersand
    ("Rachael + Chris Sellers", "Rachael Chris Sellers"),     # #169204 plus
    ("John O'Brien", "John OBrien"),                          # #169609 ASCII apostrophe
    ("Paula O’Neil", "Paula ONeil"),                     # #168570 U+2019 smart quote
    ("Barbara J. Biddle", "Barbara J Biddle"),                # #169602 period
    ("J.L. Baldridge", "JL Baldridge"),                       # #170422 two periods
    ("Tucker, Rhonda M", "Tucker Rhonda M"),                  # #170553 comma
])
def test_removals(before, after):
    assert clean(before) == after


def test_apostrophe_removed_not_spaced():
    """Kurt: O'Brien -> OBrien beats O Brien. Nothing is replaced by a space any more."""
    assert clean("John O'Brien") == "John OBrien"


# --------------------------------------------------------------- keeps
def test_hyphen_is_kept():
    """Kurt 2026-08-09: 'you can keep the hyphens' — hyphenated surnames stay intact."""
    assert clean("Anne-Marie Smith-Jones") == "Anne-Marie Smith-Jones"
    assert is_kept("-")


def test_accented_letters_preserved():
    """#168458 'Renée Rivers' carries U+00E9. The rule strips PUNCTUATION; `é` is a letter, so it
    survives and the row is not even a change. Settled, not open — no transliteration pass, no
    chasing RMFG's charset (NAME_SANITIZE_RULES.md "Calibration")."""
    assert clean("Renée Rivers") == "Renée Rivers"
    assert clean("José García") == "José García"


def test_comma_removed_never_reordered():
    """🔴 #170553 'Tucker, Rhonda M'. The comma may look like LAST, FIRST structure — irrelevant.
    Kurt: "humans read this shit when they get to the house." Remove it like any other
    punctuation; never special-case a reorder, never flag it for review."""
    assert clean("Tucker, Rhonda M") == "Tucker Rhonda M"
    assert clean("WERGES, TERRI") == "WERGES TERRI"


def test_digits_and_space_kept():
    assert clean("Suite 4 Bell") == "Suite 4 Bell"


# --------------------------------------------------------------- whitespace
@pytest.mark.parametrize("before,after", [
    ("orene  r contini", "orene r contini"),                          # #168420
    ("Gary M  Bell", "Gary M Bell"),                                  # #170711
    ("Jocelyn.   David Beauregard.  Thomas", "Jocelyn David Beauregard Thomas"),  # #170062
    ("  Leading And Trailing  ", "Leading And Trailing"),
])
def test_whitespace_collapsed_and_trimmed(before, after):
    assert sanitize(before)[0] == after


# --------------------------------------------------------------- no-op safety
@pytest.mark.parametrize("name", [
    "Bob Smith", "Mary Jane Watson", "Anne-Marie OConnor", "Gary M Bell",
])
def test_clean_names_untouched(name):
    out, removed, transforms = sanitize(name)
    assert out == name
    assert removed == [] and transforms == []


# --------------------------------------------------------------- the log contract
def test_removed_chars_carry_codepoints():
    """The log is the deliverable: Kurt writes the permanent rule from it, so every removal
    must report its codepoint."""
    _, removed, _ = sanitize("Bill & Barb’s")
    cps = {cp for _, cp, _ in removed}
    assert "U+0026" in cps and "U+2019" in cps
    assert all(cp.startswith("U+") for _, cp, _ in removed)


def test_token_transforms_are_logged():
    """C/O and 1/2 are token-level, so they must surface as transforms — otherwise a name that
    changed would show no reason in the log."""
    _, _, tr = sanitize("C/O Jane 1/2")
    assert any("C/O" in t for t in tr)
    assert any("1/2 -> half" in t for t in tr)


# --------------------------------------------------------------- 🔴 scope guards
def test_address_fraction_is_now_deliverable_but_scope_is_still_name_only():
    """🔴 The `1/2 -> half` rule makes the transform ADDRESS-SAFE — it answers the original
    objection that stripping '3226 1/2' gave '3226 12', a different house. 'half' keeps the
    address deliverable.

    That does NOT widen the scope. Kurt scoped this to the Name field; addresses are only ever
    routed through here on an explicit instruction, and if that ever happens it must be THIS
    function, not a second implementation.
    """
    assert clean("3226 1/2 LA AVENIDA DE SAN MARCOS") == "3226 half LA AVENIDA DE SAN MARCOS"
    assert clean("2714 8 1/2 ST APT 4") == "2714 8 half ST APT 4"
    assert clean("C/O THE MERCHANT COWORKING SUITE 130") == "THE MERCHANT COWORKING SUITE 130"


def test_mfg_header_would_be_mangled_proving_headers_are_out_of_scope():
    """🔴 A stripped MFG header becomes an INVENTED MFG NAME — its own whole-sheet reject.
    'AHB (S_REG): Jambon Honey & Herb' must never be passed through this function."""
    assert clean("AHB (S_REG): Jambon Honey & Herb") == "AHB SREG Jambon Honey Herb"


# --------------------------------------------------------------- real-artifact regression
# 🔴 THE CLAIM DIRECTORY IS THE AUTHORITY. `vF-intent-2026-08-10/` holds Kurt's submitted sheet of
# record (347,639 B, 08-07 16:55, 2,253 rows). The ROOT `_outputs/artifacts/..._vF.xlsx` is NOT —
# it was overwritten 2026-08-09 17:16 by another session's acceptance run. Never assert against it.
VF = (Path(__file__).resolve().parents[2] / "_outputs" / "artifacts" / "vF-intent-2026-08-10"
      / "AHB_WeeklyProductionQuery_08-10-26_vF.xlsx")


@pytest.mark.skipif(not VF.exists(), reason="submitted wk0810 artifact not present")
def test_submitted_wk0810_sheet_change_count():
    """Read-only regression against Kurt's SUBMITTED sheet (claim directory): 25 names change.

    An earlier hand-count said 24 here and 25 in _MERGED, attributing the extra to
    'Shinsato/Johnson'. Wrong on both: #169525 is in BOTH files and both change exactly 25.
    Pinned so the number stops drifting.
    """
    from name_sanitize import scan
    changes, rows = scan(VF)
    assert rows == 2253
    assert len(changes) == 25
    # 🔴 the burn: the slash SHIPPED in this file and RMFG complained
    assert any(c["order"] == "169525" and c["after"] == "ShinsatoJohnson" for c in changes)
    # Renée Rivers is NOT a change — `é` is a letter and survives untouched
    assert not any(c["order"] == "168458" for c in changes)


@pytest.mark.skipif(not VF.exists(), reason="submitted wk0810 artifact not present")
def test_generated_log_has_no_review_queue(tmp_path, monkeypatch):
    """🔴 A decision queue that isn't a decision costs Kurt's attention, the scarce thing here.
    Every change is an ordinary change; nothing is escalated. Asserts on the GENERATED markdown,
    not on source text (a source scan trips over its own guard comment).
    """
    import name_sanitize
    from name_sanitize import scan, write_log

    monkeypatch.setattr(name_sanitize, "REPORTS", tmp_path)
    changes, rows = scan(VF)
    md, _ = write_log(changes, rows, VF, "2026-01-01")
    text = md.read_text(encoding="utf-8").lower()
    for banned in ("decision needed", "needs kurt", "needs review", "awaiting kurt"):
        assert banned not in text, f"review queue reintroduced into the log: {banned!r}"
    # the ordinary changes are still all there
    assert "shinsatojohnson" in text and "tucker rhonda m" in text
