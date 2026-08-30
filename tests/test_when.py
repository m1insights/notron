import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import when


def test_an_iso_string_from_the_model_becomes_a_real_datetime():
    assert when.parse("2026-09-03T09:00") == datetime(2026, 9, 3, 9, 0)
    assert when.parse("2026-09-03") == datetime(2026, 9, 3, 0, 0)


def test_nonsense_from_the_model_is_none_not_an_exception():
    """A bad date must never stop the graph — it must be refusable."""
    assert when.parse("next Thursday") is None
    assert when.parse("") is None
    assert when.parse(None) is None


def test_a_date_is_passed_to_applescript_as_numbers_never_as_a_string():
    """date "3/9/2026" means two different days depending on the Mac's region."""
    assert when.components(datetime(2026, 9, 3, 9, 5)) == ("2026", "9", "3", "9", "5")


def test_a_confirmation_says_the_weekday_out_loud_so_a_wrong_date_is_obvious():
    assert when.human(datetime(2026, 9, 3, 9, 0)) == "Thursday 3 September at 09:00"
    assert when.human(datetime(2026, 9, 3)) == "Thursday 3 September"


def test_a_weekday_named_in_the_request_can_be_checked_against_the_answer():
    assert when.weekday_named("remind me to call the pharmacy thursday") == 3
    assert when.weekday_named("remind me tomorrow") is None
