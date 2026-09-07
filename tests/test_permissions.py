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


def test_json_flag_prints_the_same_checks_as_a_flat_list(capsys):
    import argparse, json
    from notron import cli

    args = argparse.Namespace(json=True)
    cli.cmd_permissions(args)
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert {"app", "ok", "detail", "fix"} <= out[0].keys()


def test_speech_is_reported_by_what_it_can_do_not_by_its_number():
    """Every other permission here is read numerically because a query that
    comes back empty is indistinguishable from a free week. Speech is the
    opposite: its number says `notDetermined` forever while transcription
    works. Measured 2026-09-05."""
    checks = {c.app: c for c in permissions.check(
        reader=lambda: {"events": 3, "reminders": 3},
        notes_runner=lambda: "4",
        speech=lambda: True)}
    assert checks["Speech"].ok
    assert "available" in checks["Speech"].detail


def test_a_mac_that_cannot_transcribe_says_so_rather_than_going_quiet():
    checks = {c.app: c for c in permissions.check(
        reader=lambda: {"events": 3, "reminders": 3},
        notes_runner=lambda: "4",
        speech=lambda: False)}
    assert not checks["Speech"].ok
    assert checks["Speech"].fix


def test_an_unreadable_calendar_does_not_hide_a_working_recogniser():
    def boom():
        raise RuntimeError("EventKit did not answer")
    checks = {c.app: c for c in permissions.check(
        reader=boom, notes_runner=lambda: "4", speech=lambda: True)}
    assert checks["Speech"].ok


# --- the cache under permissions.check() -----------------------------------

def test_a_working_permission_is_only_checked_once_per_process():
    """`check()` is three osascript round trips. The agenda consults it on every
    scheduling request, and a listener answers all day."""
    from notron import permissions as perm

    calls = []

    def spy():
        calls.append(1)
        return [perm.Check("Calendar", True, "full access", ""),
                perm.Check("Reminders", True, "full access", "")]

    perm.forget()
    perm.cached(checker=spy)
    perm.cached(checker=spy)
    assert len(calls) == 1
    perm.forget()


def test_a_broken_permission_is_checked_again_later():
    """Caching a failure for the life of the process means a listener that was
    blind at 9am is still saying so at 6pm, an hour after the user fixed it in
    System Settings. A grant is exactly the thing that changes underneath us."""
    from notron import permissions as perm

    calls = []
    clock = [1000.0]

    def spy():
        calls.append(1)
        return [perm.Check("Calendar", False, "is denied", "")]

    perm.forget()
    perm.cached(checker=spy, now=lambda: clock[0])
    perm.cached(checker=spy, now=lambda: clock[0])
    assert len(calls) == 1, "not on every single call, either"
    clock[0] += perm.RECHECK_SECONDS + 1
    perm.cached(checker=spy, now=lambda: clock[0])
    assert len(calls) == 2
    perm.forget()
