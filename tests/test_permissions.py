import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import permissions


def test_write_only_calendar_access_is_reported_as_a_problem():
    """The state that cost the most time to diagnose. Write-only access does not
    fail — it reports one calendar and zero events, so the calendar looks empty
    instead of blocked. Measured 2026-08-30: events=4, reminders=3."""
    checks = permissions.check(reader=lambda: {"events": 4, "reminders": 3})
    cal = next(c for c in checks if c.app == "Calendar")
    assert not cal.ok
    assert "write" in cal.detail.lower()
    assert "Calendars" in cal.fix


def test_full_access_to_both_is_reported_ready():
    checks = permissions.check(reader=lambda: {"events": 3, "reminders": 3})
    assert all(c.ok for c in checks if c.app in ("Calendar", "Reminders"))


def test_never_asked_and_denied_are_told_apart():
    checks = {c.app: c for c in permissions.check(reader=lambda: {"events": 0, "reminders": 2})}
    assert "not been asked" in checks["Calendar"].detail
    assert "denied" in checks["Reminders"].detail
