import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import permissions


def test_a_hang_is_reported_as_missing_permission_not_a_broken_app():
    """The first request to an unapproved app hangs forever rather than failing.
    Measured 2026-08-30: Notes answered in 0.12s, Reminders and Calendar never
    answered at all. A timeout here means a dialog nobody can see."""
    def always_hangs(script, *args, **kw):
        raise TimeoutError("no answer")

    checks = permissions.check(runner=always_hangs)
    reminders = next(c for c in checks if c.app == "Reminders")
    assert not reminders.ok
    assert "Automation" in reminders.fix


def test_an_app_that_answers_is_reported_ready():
    checks = permissions.check(runner=lambda *a, **kw: "Groceries")
    assert all(c.ok for c in checks)
