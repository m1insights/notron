import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import reminders

SAMPLE = [
    {"id": "x-1", "title": "Buy milk", "list": "Shopping", "due": "2026-09-03T09:00"},
    {"id": "x-2", "title": "Call the pharmacy", "list": "GENERAL TO DO LIST", "due": ""},
]


def _fake(payload):
    return lambda body, **kw: payload


def test_open_reminders_come_back_with_list_name_and_due_date():
    items = reminders.open_items(caller=_fake(SAMPLE))
    assert [r.title for r in items] == ["Buy milk", "Call the pharmacy"]
    assert items[0].list_name == "Shopping"
    assert items[1].due == ""


def test_no_reminders_at_all_is_an_empty_list_not_a_crash():
    assert reminders.open_items(caller=_fake([])) == []


def test_the_summary_juno_reads_is_short_enough_for_a_prompt():
    many = [{"id": f"x-{i}", "title": f"Thing {i}", "list": "Inbox", "due": ""}
            for i in range(60)]
    text = reminders.summary(caller=_fake(many))
    assert "Thing 0" in text
    assert len(text) < 2000
    assert "and 35 more" in text


def test_a_free_list_says_so_rather_than_going_blank():
    """A blank section reads as 'the read failed', not 'you're all clear'."""
    assert "Nothing" in reminders.summary(caller=_fake([]))


def test_creating_a_reminder_sends_an_iso_date_the_script_can_use():
    seen = {}

    def spy(body, **kw):
        seen["body"] = body
        return {"id": "x-new"}

    rid = reminders.create("Call the pharmacy", when_iso="2026-09-03T09:00", caller=spy)
    assert rid == "x-new"
    assert "2026-09-03T09:00" in seen["body"]


def test_completing_a_reminder_never_deletes_it():
    """Completing is not deleting. There is no delete path in this module at all."""
    assert not hasattr(reminders, "delete")
    assert "remove" not in reminders._COMPLETE.lower()


def test_a_reminder_can_be_found_by_what_the_user_actually_called_it():
    payload = [
        {"id": "x-1", "title": "Call the pharmacy about the repeat", "list": "Inbox", "due": ""},
        {"id": "x-2", "title": "Buy milk", "list": "Inbox", "due": ""},
    ]
    assert reminders.find_open("call the pharmacy", caller=_fake(payload)).id == "x-1"
    assert reminders.find_open("book a flight", caller=_fake(payload)) is None
