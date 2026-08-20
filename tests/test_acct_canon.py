"""Canonical account identity — regression for the five-spellings/two-accounts burn (2026-08-20).

These assert against the ACTUAL spellings measured in the DBs, not invented ones:
  FedEx  203738113 (5,427) / -113 (5,770)   -> one account, ours
  FedEx  206137911 (1,003) / -911 (10,150)  -> one account, RMFG's
  UPS    0000C411H4 (419) / '' (9,029) / NULL (5,080)
plus the 3,550 NULL FedEx rows, 4 of which carry their account only in the FILENAME.
"""
import pytest

from appyhour_lib import acct_canon as ac


@pytest.mark.parametrize("raw", ["203738113", "-113", "113", " 203738113 ", "203738113.0", '"203738113"'])
def test_our_fedex_account_collapses_to_one_key(raw):
    assert ac.canon("FedEx", raw) == "203738113"


@pytest.mark.parametrize("raw", ["206137911", "-911", "911", "206137911.0"])
def test_rmfg_fedex_account_collapses_to_one_key(raw):
    assert ac.canon("FedEx", raw) == "206137911"


def test_the_two_fedex_accounts_never_collapse_into_each_other():
    """The whole point: they unify per account, they do NOT merge across accounts."""
    assert ac.canon("FedEx", "-113") != ac.canon("FedEx", "-911")


@pytest.mark.parametrize("raw", ["C411H4", "0000C411H4", "000000C411H4336", "000000C411H4326"])
def test_ups_account_including_invoice_number_form(raw):
    assert ac.canon("UPS", raw) == "0000C411H4"


@pytest.mark.parametrize("raw", [None, "", "   ", "none", "nan", "NULL"])
def test_empty_forms_are_unknown_never_guessed(raw):
    assert ac.canon("FedEx", raw) == ac.UNKNOWN


def test_unrecognized_account_is_unknown_not_invented():
    """🔴 never-fabricate: an account we have never declared must not be echoed back as canonical."""
    assert ac.canon("FedEx", "999999999") == ac.UNKNOWN
    assert ac.canon("UPS", "SOMEONE_ELSES") == ac.UNKNOWN


def test_unknown_carrier_is_unknown():
    assert ac.canon("Veho", "203738113") == ac.UNKNOWN
    assert ac.canon("", "203738113") == ac.UNKNOWN


def test_account_does_not_leak_across_carriers():
    """A FedEx number handed to UPS must not resolve — accounts are carrier-scoped."""
    assert ac.canon("UPS", "203738113") == ac.UNKNOWN
    assert ac.canon("FedEx", "C411H4") == ac.UNKNOWN


def test_filename_recovers_the_acct911_rows():
    """4 rows landed NULL from FedEx_invoice_acct911_2026-05-06_12_50.CSV — the account was in the
    filename and never read into the column. Filename evidence is from the SOURCE, not a guess."""
    got = ac.canon("FedEx", None, filename="FedEx_invoice_acct911_2026-05-06_12_50.CSV")
    assert got == "206137911"


def test_filename_is_last_resort_only_and_never_overrides_a_real_value():
    assert ac.canon("FedEx", "203738113", filename="FedEx_invoice_acct911_x.CSV") == "203738113"


def test_filename_without_an_acct_marker_stays_unknown():
    """No `acctNNN` marker = no evidence. A date or an invoice number in the name proves nothing."""
    assert ac.canon("FedEx", None, filename="FedEx_invoice_2026-05-14_09_58.CSV") == ac.UNKNOWN


def test_canon_output_is_always_declared_or_unknown():
    """The invariant db_invariants_check relies on: canon() never emits a third kind of value."""
    allowed = ac.canonical_keys() | {ac.UNKNOWN}
    probes = ["203738113", "-113", "206137911", "-911", "C411H4", "0000C411H4", "", None,
              "999999999", "203738113.0", "junk", "  ", "911", "113"]
    for carrier in ("FedEx", "UPS", "OnTrac", "Veho", ""):
        for p in probes:
            assert ac.canon(carrier, p) in allowed


def test_owner_labels():
    assert ac.owner("FedEx", "203738113") == "ours"
    assert ac.owner("FedEx", "206137911") == "rmfg"
    assert ac.owner("FedEx", "nope") == ""


def test_is_declared_only_accepts_canonical_form():
    assert ac.is_declared("FedEx", "203738113")
    assert not ac.is_declared("FedEx", "-113")      # an alias is not yet canonical
    assert not ac.is_declared("FedEx", ac.UNKNOWN)


def test_no_two_accounts_share_an_alias():
    """An ambiguous alias would silently merge two real accounts — the exact bug, re-introduced."""
    seen = {}
    for a in ac.ACCOUNTS:
        for alias in a.aliases:
            key = (a.carrier.lower(), alias.lstrip("-").lower())
            prior = seen.get(key)
            # Same account may list '113' and '-113' — that is the point. Two DIFFERENT canonicals
            # sharing one alias is the bug: it would silently merge two real accounts.
            assert prior in (None, a.canonical), \
                f"alias {alias!r} claimed by both {prior} and {a.canonical}"
            seen[key] = a.canonical


def test_sole_account_is_ups_only_and_explicit():
    """Kurt 2026-08-20: "yes its only our account" — a declared fact, kept OUT of canon()."""
    assert ac.sole_account("UPS") == "0000C411H4"
    assert ac.sole_account("FedEx") == ac.UNKNOWN     # two accounts — must never pick one
    assert ac.sole_account("OnTrac") == ac.UNKNOWN
    assert ac.sole_account("") == ac.UNKNOWN


def test_canon_never_widens_on_its_own():
    """canon() stays evidence-only: an unattributed UPS row is UNKNOWN unless a caller opts in."""
    assert ac.canon("UPS", None) == ac.UNKNOWN
    assert ac.canon("UPS", "") == ac.UNKNOWN


def test_sole_account_fails_closed_if_a_second_account_appears():
    """Adding a 2nd UPS account without leaving SOLE_ACCOUNT_CARRIERS must NOT silently pick one."""
    extra = ac.Account("UPS", "9999XXXX", "ours", ("9999XXXX",))
    ac._BY_CARRIER["ups"].append(extra)
    try:
        assert ac.sole_account("UPS") == ac.UNKNOWN
    finally:
        ac._BY_CARRIER["ups"].remove(extra)
