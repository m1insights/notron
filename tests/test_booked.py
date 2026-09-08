"""A retried question must not book the same thing twice.

`doer` runs before `writer`, on purpose: she should not claim in a note that a
reminder is set until it actually is. But the note write can then fail — "the
note changed while she was writing" is a normal, expected outcome
(`executor.py`) — and the watcher retries the whole graph
(`watch.MAX_TRIES`). Router, scheduler and doer all run again, and nothing
anywhere compared the second reminder to the first. Two identical reminders,
one question.

The same guard covers the more common real-world case: the user asking twice
by accident, or typing the same line into two notes.

Invariants: nothing here may grow into an update or a delete path (#6, #7).
It only ever refuses. And a refusal is a blocked write, so it is logged (#4).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from notron import booked
from notron.state import Action

SOON = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")


# conftest already points `booked.STATE` at a tmp_path for every test in the
# suite. These tests just lean on it.


def _reminder(**kw):
    kw.setdefault("title", "Call the pharmacy")
    return Action(kind="reminder", op="create", when=SOON, **kw)


def test_the_same_thing_booked_twice_in_a_minute_is_recognised():
    booked.remember(_reminder(), "x-apple-reminder://1")
    assert booked.already(_reminder()) == "x-apple-reminder://1"


def test_something_not_booked_yet_is_not_recognised():
    assert booked.already(_reminder()) is None


def test_a_different_title_is_a_different_thing():
    booked.remember(_reminder(), "ref-1")
    assert booked.already(_reminder(title="Call the dentist")) is None


def test_the_same_title_at_a_different_time_is_a_different_thing():
    booked.remember(_reminder(), "ref-1")
    later = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT14:00")
    assert booked.already(Action(kind="reminder", op="create",
                                 title="Call the pharmacy", when=later)) is None


def test_a_reminder_and_an_event_with_one_name_are_two_different_things():
    booked.remember(_reminder(), "ref-1")
    assert booked.already(Action(kind="event", op="create",
                                 title="Call the pharmacy", when=SOON)) is None


def test_the_same_thing_asked_for_again_tomorrow_is_a_real_request():
    """This guards a retry loop and a slip of the hand, not a standing order.
    Asking for the same reminder next week is a person meaning it."""
    now = [1000.0]
    booked.remember(_reminder(), "ref-1", now=lambda: now[0])
    now[0] += booked.WINDOW_SECONDS + 1
    assert booked.already(_reminder(), now=lambda: now[0]) is None


def test_titles_that_differ_only_in_spacing_and_case_are_the_same_thing():
    booked.remember(_reminder(), "ref-1")
    assert booked.already(_reminder(title="  call   the PHARMACY ")) == "ref-1"


def test_a_store_that_cannot_be_read_never_blocks_a_real_booking(monkeypatch):
    """A corrupt or unreadable json file must fail open. Refusing to book
    anything because a cache file is broken is worse than a duplicate."""
    monkeypatch.setattr(booked, "STATE", booked.STATE)
    booked.STATE.write_text("{ this is not json")
    assert booked.already(_reminder()) is None
    booked.remember(_reminder(), "ref-1")     # must not raise


def test_the_store_does_not_grow_without_bound():
    for i in range(booked.MAX_KEPT * 2):
        booked.remember(Action(kind="reminder", op="create", title=f"Thing {i}",
                               when=SOON), f"ref-{i}")
    import json
    assert len(json.loads(booked.STATE.read_text())) <= booked.MAX_KEPT


def test_the_most_recent_bookings_are_the_ones_kept():
    for i in range(booked.MAX_KEPT + 5):
        booked.remember(Action(kind="reminder", op="create", title=f"Thing {i}",
                               when=SOON), f"ref-{i}")
    newest = Action(kind="reminder", op="create",
                    title=f"Thing {booked.MAX_KEPT + 4}", when=SOON)
    assert booked.already(newest) == f"ref-{booked.MAX_KEPT + 4}"


# --- and the Executor actually uses it -------------------------------------

def test_a_retried_question_does_not_book_it_twice(monkeypatch):
    from notron import executor as ex_mod

    created = []
    monkeypatch.setattr(ex_mod.reminders, "create",
                        lambda title, **kw: created.append(title) or "ref-1")

    ex = ex_mod.Executor(audit=False)
    first = ex.do(_reminder())
    second = ex.do(_reminder())

    assert created == ["Call the pharmacy"], "the second pass booked it again"
    assert first.ok and second.ok
    assert second.ref == "ref-1", "she should hand back the one that exists"
    assert "already" in second.reason.lower()


def test_the_refusal_is_written_to_the_log(monkeypatch):
    """Invariant #4: every write, allowed or blocked, is logged."""
    from notron import executor as ex_mod

    monkeypatch.setattr(ex_mod.reminders, "create", lambda title, **kw: "ref-1")
    logged = []
    ex = ex_mod.Executor(audit=False)
    ex._log = logged.append
    ex.do(_reminder())
    ex.do(_reminder())
    assert any("already" in line.lower() for line in logged)


def test_a_dry_run_books_nothing_and_remembers_nothing(monkeypatch):
    from notron import executor as ex_mod

    ex = ex_mod.Executor(dry_run=True, audit=False)
    ex.do(_reminder())
    assert booked.already(_reminder()) is None
