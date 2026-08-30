import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import calendar as cal

SAMPLE = [
    {"calendar": "Work", "title": "Standup", "start": "2026-09-03T09:30",
     "end": "2026-09-03T09:45", "location": ""},
    {"calendar": "Home", "title": "Dentist", "start": "2026-09-03T14:00",
     "end": "2026-09-03T15:00", "location": "Baker St"},
    {"calendar": "Work", "title": "Next week thing", "start": "2026-09-10T09:30",
     "end": "2026-09-10T10:00", "location": ""},
]


def _fake(payload):
    return lambda body, **kw: payload


def test_events_come_back_with_calendar_start_and_location():
    events = cal.window(days=7, caller=_fake(SAMPLE))
    assert [e.title for e in events] == ["Standup", "Dentist", "Next week thing"]
    assert events[1].location == "Baker St"


def test_an_empty_calendar_is_an_empty_list():
    assert cal.window(caller=_fake([])) == []


def test_todays_brief_only_shows_today():
    text = cal.brief(on=datetime(2026, 9, 3), caller=_fake(SAMPLE))
    assert "Standup" in text and "Dentist" in text
    assert "Next week thing" not in text


def test_a_free_day_says_so_rather_than_going_blank():
    """A blank calendar section reads as 'the read failed', not 'you are free'.
    This mattered for real: write-only access reported an empty calendar all day."""
    assert "Nothing" in cal.brief(on=datetime(2026, 9, 3), caller=_fake([]))


def test_the_week_is_grouped_by_day_so_it_reads_on_a_phone():
    text = cal.week(caller=_fake(SAMPLE))
    assert "Thursday 3 September" in text
    assert "- 09:30 Standup" in text


def test_a_read_is_always_a_bounded_window():
    """Unbounded reads are what made the AppleScript version unusable. The habit
    is worth keeping even though EventKit is fast."""
    assert "predicateForEventsWithStartDateEndDateCalendars" in cal._WINDOW


def test_there_is_no_way_to_move_or_delete_an_event_from_this_module():
    """The user's standing instruction is 'never move anything already in my
    calendar without asking'. The safest enforcement is having no such code."""
    assert not hasattr(cal, "delete")
    assert not hasattr(cal, "move")
    assert not hasattr(cal, "update")


def test_creating_an_event_sends_both_ends():
    seen = {}

    def spy(body, **kw):
        seen["body"] = body
        return {"id": "uid-1", "calendar": "Home"}

    uid = cal.create("Dentist", start_iso="2026-09-03T14:00",
                     end_iso="2026-09-03T15:00", caller=spy)
    assert uid == "uid-1"
    assert "2026-09-03T14:00" in seen["body"]
    assert "2026-09-03T15:00" in seen["body"]


def test_an_event_with_no_end_time_gets_a_sensible_hour():
    seen = {}
    cal.create("Coffee", start_iso="2026-09-03T14:00",
               caller=lambda body, **kw: (seen.setdefault("b", body), {"id": "u", "calendar": "x"})[1])
    assert "2026-09-03T15:00" in seen["b"]


def test_an_end_before_the_start_is_corrected_not_saved():
    seen = {}
    cal.create("Backwards", start_iso="2026-09-03T14:00", end_iso="2026-09-03T13:00",
               caller=lambda body, **kw: (seen.setdefault("b", body), {"id": "u", "calendar": "x"})[1])
    assert "2026-09-03T15:00" in seen["b"]


def test_an_unusable_start_is_refused_before_anything_is_saved():
    import pytest

    with pytest.raises(ValueError):
        cal.create("X", start_iso="next Thursday", caller=lambda *a, **kw: {})
