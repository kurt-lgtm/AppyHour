"""MATRIX_RULES rule 21: a translation NAME outside the authoritative meal-type export is a hard
MfgOnboardingError — an invented header must never become a vF column (wk0803 CH-OTTA burn)."""
import pytest

from matrix_commander import MfgOnboardingError, load_mfg_translations, validate_mfg_names, MFG_AUTHORITATIVE_PATH


def test_real_translations_all_authoritative():
    # The shipped mfg_translations.csv must validate clean against the shipped authority snapshot.
    validate_mfg_names(load_mfg_translations())


def test_invented_name_rejected_by_type():
    assert MFG_AUTHORITATIVE_PATH.exists()
    good = load_mfg_translations(MFG_AUTHORITATIVE_PATH)
    sku, _ = next(iter(good.items()))
    bad = dict(good)
    bad[sku] = "AHB (S_REG): Cheese Slice, Frumage L'Ottavio"   # the wk0803 invented header
    with pytest.raises(MfgOnboardingError) as e:
        validate_mfg_names(bad)
    assert sku in e.value.skus
    assert isinstance(e.value, ValueError)   # existing except ValueError handlers keep catching it
