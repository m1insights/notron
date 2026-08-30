import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import reminders

RS, US = reminders.RS, reminders.US


def _fake(raw):
    """Stand in for osascript so the suite needs no Mac, no apps, no network."""
    return lambda script, *args, **kw: raw


def test_open_reminders_come_back_with_list_name_and_due_date():
    raw = US.join([
        RS.join(["Groceries", "x-1", "Buy milk", "2026-09-03T09:00"]),
        RS.join(["Groceries", "x-2", "Call the pharmacy", ""]),
    ])
    items = reminders.open_items(runner=_fake(raw))
    assert [r.title for r in items] == ["Buy milk", "Call the pharmacy"]
    assert items[0].list_name == "Groceries"
    assert items[0].due == "2026-09-03T09:00"
    assert items[1].due == ""


def test_an_undated_reminder_does_not_break_the_whole_read():
    """`due date of rs` yields `missing value` for undated reminders, and a list
    holding one cannot be coerced to text. The script maps it to empty first."""
    raw = RS.join(["Inbox", "x-9", "Someday", ""])
    assert reminders.open_items(runner=_fake(raw))[0].due == ""


def test_no_reminders_at_all_is_an_empty_list_not_a_crash():
    assert reminders.open_items(runner=_fake("")) == []
    assert reminders.open_items(runner=_fake("   ")) == []


def test_the_summary_juno_reads_is_short_enough_for_a_prompt():
    raw = US.join(RS.join(["Inbox", f"x-{i}", f"Thing {i}", ""]) for i in range(40))
    text = reminders.summary(runner=_fake(raw))
    assert "Thing 0" in text
    assert len(text) < 2000


def test_creating_a_dated_reminder_sends_the_date_as_numbers_not_a_string():
    """A date built from a string is read in the Mac's region format and lands
    on the wrong day. The script gets five integers instead."""
    seen = {}

    def spy(script, *args, **kw):
        seen["args"] = args
        return "x-new"

    rid = reminders.create("Call the pharmacy", when_iso="2026-09-03T09:00", runner=spy)
    assert rid == "x-new"
    assert seen["args"] == ("Call the pharmacy", "", "", "1", "2026", "9", "3", "9", "0")


def test_an_undated_reminder_sends_no_date_at_all():
    seen = {}
    reminders.create("Buy milk", runner=lambda s, *a, **kw: (seen.setdefault("a", a), "x")[1])
    assert seen["a"][3] == "0"


def test_completing_a_reminder_never_deletes_it():
    """Completing is not deleting. There is no delete path in this module at all."""
    assert not hasattr(reminders, "delete")
    assert "delete" not in reminders._COMPLETE.lower()


def test_a_reminder_can_be_found_by_what_the_user_actually_called_it():
    raw = US.join([
        RS.join(["Inbox", "x-1", "Call the pharmacy about the repeat", ""]),
        RS.join(["Inbox", "x-2", "Buy milk", ""]),
    ])
    hit = reminders.find_open("call the pharmacy", runner=_fake(raw))
    assert hit.id == "x-1"
    assert reminders.find_open("book a flight", runner=_fake(raw)) is None
