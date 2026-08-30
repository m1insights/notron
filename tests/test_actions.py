"""The Guard's rules for anything that leaves Notes and touches a real app.

Each test is named after the promise it keeps. If one of these ever has to be
deleted to make a feature work, the feature is wrong.
"""

import sys, pathlib
from datetime import datetime, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import guard
from juno.state import Action

SOON = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
ABOUT_9AM = "## My rules\n- Never schedule me before 9am.\n- Keep it short."


def _reminder(**kw):
    return Action(kind="reminder", op="create", title="Call the pharmacy", when=SOON, **kw)


def _event(**kw):
    return Action(kind="event", op="create", title="Dentist", when=SOON, **kw)


# --- calendar events are create-only ---------------------------------------

def test_an_event_may_be_created():
    assert guard.check_action(_event(), about="")


def test_an_event_can_never_be_moved():
    v = guard.check_action(Action(kind="event", op="move", title="Dentist", when=SOON), about="")
    assert not v and "never move" in v.reason.lower()


def test_an_event_can_never_be_deleted():
    v = guard.check_action(Action(kind="event", op="delete", title="Dentist"), about="")
    assert not v


# --- reminders may be created and completed, never deleted -----------------

def test_a_reminder_may_be_created_and_completed():
    assert guard.check_action(_reminder(), about="")
    assert guard.check_action(Action(kind="reminder", op="complete", title="Buy milk"), about="")


def test_a_reminder_can_never_be_deleted():
    v = guard.check_action(Action(kind="reminder", op="delete", title="Buy milk"), about="")
    assert not v and "delet" in v.reason.lower()


# --- the standing instructions are enforced in code, not by the model ------

def test_nothing_is_scheduled_before_the_hour_the_user_said():
    early = Action(kind="event", op="create", title="Standup",
                   when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT07:30"))
    v = guard.check_action(early, about=ABOUT_9AM)
    assert not v and "9" in v.reason


def test_the_same_time_is_fine_when_the_user_never_said_otherwise():
    early = Action(kind="event", op="create", title="Standup",
                   when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT07:30"))
    assert guard.check_action(early, about="Keep it short.")


def test_an_all_day_item_is_not_caught_by_an_hour_rule():
    allday = Action(kind="reminder", op="create", title="Renew passport",
                    when=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"))
    assert guard.check_action(allday, about=ABOUT_9AM)


def test_the_hour_rule_is_read_out_of_plain_english():
    assert guard.earliest_hour("- Never schedule me before 9am.") == 9
    assert guard.earliest_hour("nothing before 08:30") == 8
    assert guard.earliest_hour("no meetings before 10 am please") == 10
    assert guard.earliest_hour("Keep it short.") is None


def test_an_evening_rule_works_the_same_way():
    late = Action(kind="event", op="create", title="Call",
                  when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT21:00"))
    v = guard.check_action(late, about="- Nothing after 7pm.")
    assert not v


# --- the model's dates are checked, because models get dates wrong ---------

def test_a_date_that_does_not_parse_is_refused_clearly():
    v = guard.check_action(Action(kind="event", op="create", title="X", when="next Thursday"),
                           about="")
    assert not v and "date" in v.reason.lower()


def test_an_event_in_the_past_is_refused_because_it_is_almost_always_a_wrong_year():
    past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT14:00")
    v = guard.check_action(Action(kind="event", op="create", title="X", when=past), about="")
    assert not v and "past" in v.reason.lower()


def test_a_date_years_away_is_refused_because_it_is_a_typo_not_a_plan():
    far = (datetime.now() + timedelta(days=800)).strftime("%Y-%m-%dT14:00")
    v = guard.check_action(Action(kind="event", op="create", title="X", when=far), about="")
    assert not v


def test_if_you_said_thursday_and_she_heard_friday_she_asks_instead_of_guessing():
    """The single most likely way this feature embarrasses itself: a confident
    reminder on the wrong day. If the user named a weekday, the resolved date has
    to actually fall on it."""
    friday = datetime.now() + timedelta(days=(4 - datetime.now().weekday()) % 7 or 7)
    action = Action(kind="reminder", op="create", title="Call the pharmacy",
                    when=friday.strftime("%Y-%m-%dT09:00"))
    v = guard.check_action(action, about="", request="remind me to call the pharmacy thursday")
    assert not v and "thursday" in v.reason.lower()


def test_the_right_weekday_passes():
    thursday = datetime.now() + timedelta(days=(3 - datetime.now().weekday()) % 7 or 7)
    action = Action(kind="reminder", op="create", title="Call the pharmacy",
                    when=thursday.strftime("%Y-%m-%dT09:00"))
    assert guard.check_action(action, about="", request="remind me to call the pharmacy thursday")


# --- the same privacy promise as every other write -------------------------

def test_a_password_can_no_more_reach_reminders_than_it_can_reach_a_note():
    v = guard.check_action(
        Action(kind="reminder", op="create", title="wifi password is hunter2trombone",
               when=SOON),
        about="")
    assert not v


def test_an_empty_title_is_refused():
    assert not guard.check_action(Action(kind="reminder", op="create", title="   "), about="")


def test_an_unknown_kind_is_refused_rather_than_guessed_at():
    assert not guard.check_action(Action(kind="email", op="create", title="Hi"), about="")


# --- Executor.do — the one place an action is applied -----------------------

def test_a_blocked_action_is_logged_and_never_reaches_the_app(monkeypatch):
    """Invariant 4: every write, allowed or blocked, lands in the Log."""
    from juno import executor as ex_mod

    logged, created = [], []
    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: logged.append(line))
    monkeypatch.setattr(ex_mod.reminders, "create",
                        lambda *a, **kw: created.append(a) or "x")

    r = ex_mod.Executor().do(Action(kind="reminder", op="delete", title="Buy milk"), about="")
    assert not r.ok
    assert created == []
    assert any("BLOCKED" in line for line in logged)


def test_an_allowed_reminder_reaches_the_app_and_is_logged(monkeypatch):
    from juno import executor as ex_mod

    logged, created = [], []
    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: logged.append(line))
    monkeypatch.setattr(ex_mod.reminders, "create",
                        lambda title, **kw: created.append(title) or "x-7")

    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    r = ex_mod.Executor().do(Action(kind="reminder", op="create",
                                    title="Call the pharmacy", when=soon), about="")
    assert r.ok and r.ref == "x-7"
    assert created == ["Call the pharmacy"]
    assert logged


def test_a_dry_run_touches_nothing(monkeypatch):
    from juno import executor as ex_mod

    called = []
    monkeypatch.setattr(ex_mod.reminders, "create", lambda *a, **kw: called.append(a) or "x")
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    r = ex_mod.Executor(dry_run=True).do(
        Action(kind="reminder", op="create", title="X", when=soon), about="")
    assert r.ok and called == []


def test_completing_looks_the_reminder_up_by_what_the_user_called_it(monkeypatch):
    from juno import executor as ex_mod
    from juno.reminders import Reminder

    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: None)
    monkeypatch.setattr(ex_mod.reminders, "find_open",
                        lambda phrase, **kw: Reminder("Inbox", "x-3", "Call the pharmacy", ""))
    done = []
    monkeypatch.setattr(ex_mod.reminders, "complete", lambda rid, **kw: done.append(rid) or "ok")

    r = ex_mod.Executor().do(Action(kind="reminder", op="complete",
                                    title="call the pharmacy"), about="")
    assert r.ok and done == ["x-3"]


def test_ticking_off_something_that_isnt_there_says_so(monkeypatch):
    from juno import executor as ex_mod

    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: None)
    monkeypatch.setattr(ex_mod.reminders, "find_open", lambda phrase, **kw: None)
    r = ex_mod.Executor().do(Action(kind="reminder", op="complete", title="feed the cat"), about="")
    assert not r.ok and "couldn't find" in r.reason.lower()
