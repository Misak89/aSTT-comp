"""
Unit testy pro WER/CER výpočet (text_metrics.py).
Testují: přesný přepis, chyby, prázdné vstupy, česká diakritika.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.benchmarks.metrics.text_metrics import word_error_rate, char_error_rate


# ---------------------------------------------------------------------------
# WER
# ---------------------------------------------------------------------------

def test_wer_perfect():
    assert word_error_rate("ahoj světe", "ahoj světe") == 0.0


def test_wer_one_substitution():
    # "pes" místo "kočka" = 1 substituce z 2 slov = 0.5
    assert word_error_rate("ahoj kočka", "ahoj pes") == 0.5


def test_wer_all_wrong():
    wer = word_error_rate("jeden dva tři", "čtyři pět šest")
    assert wer == 1.0


def test_wer_extra_word():
    # reference 2 slova, hypotéza 3 slova → 1 insertion / 2 = 0.5
    assert word_error_rate("ahoj světe", "ahoj krásný světe") == 0.5


def test_wer_missing_word():
    # reference 3 slova, hypotéza 2 slova → 1 deletion / 3
    wer = word_error_rate("jeden dva tři", "jeden tři")
    assert abs(wer - 1/3) < 1e-9


def test_wer_empty_reference_empty_hypothesis():
    assert word_error_rate("", "") == 0.0


def test_wer_empty_reference_nonempty_hypothesis():
    assert word_error_rate("", "něco") == 1.0


def test_wer_nonempty_reference_empty_hypothesis():
    # všechna slova chybí → WER = 1.0
    assert word_error_rate("ahoj světe", "") == 1.0


def test_wer_case_insensitive():
    # normalizace na lowercase
    assert word_error_rate("Ahoj Světe", "ahoj světe") == 0.0


def test_wer_czech_diacritics():
    # diakritika musí být zachována (ne stripped)
    assert word_error_rate("příliš žluťoučký kůň", "příliš žluťoučký kůň") == 0.0
    assert word_error_rate("příliš žluťoučký kůň", "prilis zlutoucky kun") != 0.0


# ---------------------------------------------------------------------------
# CER
# ---------------------------------------------------------------------------

def test_cer_perfect():
    assert char_error_rate("ahoj", "ahoj") == 0.0


def test_cer_one_char_substitution():
    # "ahoj" vs "ahoj" = 0, "ahok" vs "ahoj" = 1/4 = 0.25
    cer = char_error_rate("ahoj", "ahok")
    assert abs(cer - 0.25) < 1e-9


def test_cer_empty_reference_empty_hypothesis():
    assert char_error_rate("", "") == 0.0


def test_cer_empty_reference_nonempty_hypothesis():
    assert char_error_rate("", "a") == 1.0


def test_cer_czech():
    assert char_error_rate("češtině", "češtině") == 0.0
