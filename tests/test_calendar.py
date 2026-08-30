import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import calendar as cal

RS, US = cal.RS, cal.US


def _fake(raw):
    return lambda script, *args, **kw: raw


def test_events_come_back_with_calendar_start_and_end():
    raw = US.join([
        RS.join(["Work", "Standup", "2026-09-03T09:30", "2026-09-03T09:45", ""]),
        RS.join(["Home", "Dentist", "2026-09-03T14:00", "2026-09-03T15:00", "Baker St"]),
    ])
    events = cal.window(days=7, runner=_fake(raw))
    assert [e.title for e in events] == ["Standup", "Dentist"]
    assert events[1].location == "Baker St"


def test_a_window_is_always_a_date_range_never_every_event():
    """Asking a real calendar for `every event` takes minutes. Only a bounded
    `whose start date is greater than…` query is fast enough to be usable."""
    assert "whose start date" in cal._WINDOW
    assert "every event of c\n" not in cal._WINDOW


def test_an_empty_calendar_is_an_empty_list():
    assert cal.window(runner=_fake("")) == []


def test_todays_brief_only_shows_today():
    raw = US.join([
        RS.join(["Work", "Today thing", "2026-09-03T09:30", "2026-09-03T10:00", ""]),
        RS.join(["Work", "Next week thing", "2026-09-10T09:30", "2026-09-10T10:00", ""]),
    ])
    text = cal.brief(on=datetime(2026, 9, 3), runner=_fake(raw))
    assert "Today thing" in text
    assert "Next week thing" not in text


def test_a_free_day_says_so_rather_than_going_blank():
    """A blank calendar section reads as 'the read failed', not 'you are free'."""
    assert "Nothing" in cal.brief(on=datetime(2026, 9, 3), runner=_fake(""))


def test_creating_an_event_sends_both_ends_as_numbers():
    seen = {}
    uid = cal.create("Dentist", start_iso="2026-09-03T14:00", end_iso="2026-09-03T15:00",
                     runner=lambda s, *a, **kw: (seen.setdefault("a", a), "uid-1")[1])
    assert uid == "uid-1"
    assert seen["a"] == ("Dentist", "", "", "2026", "9", "3", "14", "0",
                                          "2026", "9", "3", "15", "0")


def test_an_event_with_no_end_time_gets_a_sensible_hour():
    seen = {}
    cal.create("Coffee", start_iso="2026-09-03T14:00",
               runner=lambda s, *a, **kw: (seen.setdefault("a", a), "u")[1])
    assert seen["a"][-5:] == ("2026", "9", "3", "15", "0")


def test_there_is_no_way_to_move_or_delete_an_event_from_this_module():
    """The user's standing instruction is 'never move anything already in my
    calendar without asking'. The safest enforcement is having no such code."""
    assert not hasattr(cal, "delete")
    assert not hasattr(cal, "move")
    assert not hasattr(cal, "update")
    assert "delete" not in cal._CREATE.lower()
