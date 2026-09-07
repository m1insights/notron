import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import calendar as cal

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


def test_a_failed_save_says_what_macos_actually_said():
    """`err` is filled in by EventKit and was being thrown away, so every failure
    read as the same four words. The user needs to read "Calendar access denied"."""
    def caller(_body, **kw):
        return {"error": "save failed", "why": "Calendar access denied"}
    import pytest
    from notron import eventkit
    with pytest.raises(eventkit.EventKitError, match="Calendar access denied"):
        cal.create("x", start_iso="2026-09-07T11:00", caller=caller)


def test_the_create_script_carries_macos_reason_home():
    from notron import eventkit
    assert "reason(err)" in cal._CREATE
    assert "localizedDescription" in eventkit.PRELUDE


def test_no_script_here_builds_its_own_date_formatter():
    """`NSDateFormatter` with a fixed `dateFormat` and no locale uses the Mac's
    region. Under a Buddhist or Japanese region setting `yyyy` is not the year we
    mean, so `stringFromDate` writes a date nobody asked for and `dateFromString`
    returns nil — a wrong date, or a save that fails for no visible reason. This
    Mac happens to be en_US/gregorian; the next one is not our call.

    So there is exactly one formatter constructor, in `eventkit.DATES`, and this
    test is what stops a sixth script quietly growing its own."""
    from notron import eventkit

    for script in (cal._WINDOW, cal._CREATE, cal._NAMES):
        assert "NSDateFormatter" not in script, "build it with pinned() instead"
    assert "en_US_POSIX" in eventkit.PRELUDE
    assert "gregorian" in eventkit.PRELUDE


# --- all-day events --------------------------------------------------------
#
# `_WINDOW` never read `isAllDay`, so "Team offsite" — a whole day blocked out —
# rendered as "- 00:00 Team offsite", which reads as a midnight meeting. And a
# multi-day event appeared only on the day it started, so the middle of a
# week-long trip looked free.

ALL_DAY = [
    {"calendar": "Home", "title": "Team offsite", "start": "2026-09-03T00:00",
     "end": "2026-09-04T00:00", "location": "", "all_day": True},
]

THREE_DAYS = [
    {"calendar": "Home", "title": "Lisbon", "start": "2026-09-03T00:00",
     "end": "2026-09-06T00:00", "location": "", "all_day": True},
]


def test_an_all_day_event_is_not_an_event_at_midnight():
    text = cal.brief(on=datetime(2026, 9, 3), caller=_fake(ALL_DAY))
    assert "All day — Team offsite" in text
    assert "00:00" not in text


def test_an_all_day_event_shows_in_the_week_without_a_time():
    text = cal.week(caller=_fake(ALL_DAY), on=datetime(2026, 9, 3))
    assert "All day — Team offsite" in text
    assert "00:00" not in text


def test_a_three_day_event_is_on_all_three_days_of_the_week():
    text = cal.week(caller=_fake(THREE_DAYS), on=datetime(2026, 9, 3))
    assert text.count("Lisbon") == 3, text


def test_a_multi_day_event_still_shows_on_a_day_it_did_not_start():
    """The middle of a week-long trip must not look like a free day."""
    text = cal.brief(on=datetime(2026, 9, 4), caller=_fake(THREE_DAYS))
    assert "Lisbon" in text


def test_a_timed_event_is_unchanged_by_any_of_this():
    text = cal.brief(on=datetime(2026, 9, 3), caller=_fake(SAMPLE))
    assert "- 09:30 Standup" in text
    assert "All day" not in text


def test_an_all_day_event_that_ends_the_next_midnight_is_one_day_not_two():
    """EventKit's end for a single all-day event is the following midnight.
    Counting that as a second day puts a phantom offsite on tomorrow."""
    text = cal.week(caller=_fake(ALL_DAY), on=datetime(2026, 9, 3))
    assert text.count("Team offsite") == 1, text


# --- "today" means today, not "from now on" --------------------------------

def test_todays_brief_still_shows_this_mornings_meetings():
    """`brief()` asked for a window starting at *now*. Run at 10:00 the 09:00
    meeting the user had just walked out of was simply not in the day, so
    `notron agenda` and the morning routine under-reported it."""
    asked = {}

    def caller(body, **kw):
        asked["body"] = body
        return SAMPLE

    text = cal.brief(on=datetime(2026, 9, 3, 16, 0), caller=caller)
    assert "Standup" in text, "a 09:30 meeting is still part of today at 16:00"
    assert "2026-09-03T00:00" in asked["body"], "the window must start at midnight"


def test_the_far_end_of_the_window_is_still_measured_from_now():
    """Only the near end moves. Reaching further back than today would make
    every brief re-read history it does not use."""
    asked = {}

    def caller(body, **kw):
        asked["body"] = body
        return []

    cal.brief(on=datetime(2026, 9, 3, 16, 0), caller=caller)
    assert "2026-09-05T16:00" in asked["body"]
