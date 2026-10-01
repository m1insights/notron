"""Notron's own email address: approved, proven senders become requests in the
Notron Reminders list; everyone else is ignored or held. Mail is only read.

No real Mail or Reminders here: `mail.headers` / `mail.message` and the
Reminders calls are faked, like the mail and inbox tests."""

import pytest

from notron import inbox, letterbox, mail, mailroom, nodes, reminders
from notron.mail import Header
from notron.reminders import Reminder

VIVEK = "Vivek Patel <vivek@pharmacy.co.uk>"
PASS = ("Authentication-Results: mx.google.com;\n"
        "       dkim=pass header.i=@pharmacy.co.uk header.s=s1;\n"
        "       spf=pass smtp.mailfrom=vivek@pharmacy.co.uk;\n"
        "       dmarc=pass (p=NONE) header.from=pharmacy.co.uk\n"
        "From: Vivek Patel <vivek@pharmacy.co.uk>\nSubject: Rota\n")


def hdr(n, sender=VIVEK, subject="Rota for next week", age=60, mid=None):
    return Header("Notron", n, 1000 + n, sender, subject, age, False, mid or f"<m{n}@pharmacy.co.uk>")


class FakeMail:
    def __init__(self, monkeypatch, *headers, raw=PASS, body="Can you draw up next week's rota? Thanks"):
        self.headers, self.raw, self.body, self.read = list(headers), raw, body, []
        monkeypatch.setattr(mail, "headers", lambda account, limit=100, max_age=None: self.headers[:limit])
        monkeypatch.setattr(mail, "message", self.message)

    def message(self, h):
        self.read.append(h.key)
        return self.raw, self.body


class FakeReminders:
    def __init__(self, monkeypatch, lists=("Notron",)):
        self.made, self.items, self.lists = [], {}, list(lists)
        monkeypatch.setattr(reminders, "resolve_targets", lambda name="", caller=None, **kw:
                            [{"id": f"list-{n}", "title": n} for n in self.lists if n == name])
        monkeypatch.setattr(reminders, "create", self.create)
        monkeypatch.setattr(reminders, "open_items", lambda caller=None: list(self.items.values()))
        monkeypatch.setattr(reminders, "find_by_operation", lambda op, caller=None: [])

    def create(self, title, *, notes="", when_iso=None, target_id=None, caller=None, **kw):
        rid = f"made-{len(self.made)}"
        self.made.append({"title": title, "notes": notes, "when": when_iso})
        self.items[rid] = Reminder(rid, title, "Notron", when_iso or "")
        return rid


@pytest.fixture
def box():
    letterbox.setup("Notron", ["vivek@pharmacy.co.uk"], now=0.0)


def test_a_proven_email_from_an_approved_sender_becomes_a_request(monkeypatch, box):
    FakeMail(monkeypatch, hdr(1))
    rem = FakeReminders(monkeypatch)
    out = letterbox.file(letterbox.gather(now=10_000.0))
    assert out == {"filed": 1, "held": 0, "ignored": 0}
    (made,) = rem.made
    assert made["title"].startswith("Vivek Patel emailed Notron: Rota for next week — Can you draw up")
    assert made["when"] is None                                   # a request, not an alarm
    assert [r.id for r in inbox.waiting()] == ["made-0"]          # answered like one the user dictated


def test_mail_from_anyone_else_is_never_read(monkeypatch, box):
    fake = FakeMail(monkeypatch, hdr(1, sender="Someone <someone@else.com>"))
    rem = FakeReminders(monkeypatch)
    out = letterbox.file(letterbox.gather(now=10_000.0))
    assert out["ignored"] == 1 and rem.made == [] and fake.read == []


def test_a_spoofed_sender_is_held_not_answered(monkeypatch, box):
    """Anyone can type Vivek's name into From. His server's signature they cannot."""
    forged = ("Authentication-Results: mx.google.com;\n       dmarc=fail header.from=pharmacy.co.uk;\n"
              "       spf=pass smtp.mailfrom=attacker@evil.example\n"
              "Authentication-Results: fake; dmarc=pass\n")
    FakeMail(monkeypatch, hdr(1), raw=forged)
    rem = FakeReminders(monkeypatch)
    out = letterbox.file(letterbox.gather(now=10_000.0))
    assert out["held"] == 1 and out["filed"] == 0
    (made,) = rem.made
    assert made["title"].startswith("Held email: ") and made["when"]       # buzzes the user
    assert inbox.waiting() == []                                           # never a request


@pytest.mark.parametrize("raw, ok", [
    (PASS, True),
    ("Authentication-Results: mx; dkim=pass header.d=pharmacy.co.uk; dmarc=none\n", True),
    ("Authentication-Results: mx; spf=pass smtp.mailfrom=vivek@pharmacy.co.uk\n", True),
    ("Authentication-Results: mx; dkim=pass header.d=gmail.com; spf=pass smtp.mailfrom=x@gmail.com\n", False),
    ("Received: by mx\n", False),
    ("", False),
])
def test_only_the_top_authentication_verdict_for_the_senders_domain_proves_it(raw, ok):
    assert letterbox.proven(raw, "vivek@pharmacy.co.uk") is ok


@pytest.mark.parametrize("body", ["Please reorder for patient Jane Doe", "NHS number 943 476 5919, needs a call",
                                  "DOB 01/02/1960 - can you check stock"])
def test_patient_details_are_held_and_never_sent_to_a_model(monkeypatch, box, body):
    FakeMail(monkeypatch, hdr(1), body=body)
    rem = FakeReminders(monkeypatch)
    out = letterbox.file(letterbox.gather(now=10_000.0))
    assert out["held"] == 1 and body not in rem.made[0]["title"] + rem.made[0]["notes"]


def test_mail_from_before_setup_is_not_a_request(monkeypatch):
    letterbox.setup("Notron", ["vivek@pharmacy.co.uk"], now=10_000.0)
    FakeMail(monkeypatch, hdr(1, age=3600))                      # arrived an hour before setup
    rem = FakeReminders(monkeypatch)
    assert letterbox.gather(now=10_060.0) == [] and rem.made == []


def test_the_same_email_is_filed_once(monkeypatch, box):
    FakeMail(monkeypatch, hdr(1))
    rem = FakeReminders(monkeypatch)
    letterbox.file(letterbox.gather(now=10_000.0))
    letterbox.file(letterbox.gather(now=10_120.0))
    assert len(rem.made) == 1


def test_an_email_whose_reminder_failed_is_read_again(monkeypatch, box):
    FakeMail(monkeypatch, hdr(1))
    rem = FakeReminders(monkeypatch, lists=())                  # no Notron list yet
    with pytest.raises(inbox.InboxError):
        letterbox.file(letterbox.gather(now=10_000.0))
    rem.lists.append("Notron")
    assert letterbox.file(letterbox.gather(now=10_120.0))["filed"] == 1


def test_quoted_history_is_not_part_of_the_request(monkeypatch, box):
    FakeMail(monkeypatch, hdr(1), body="Yes please do it\n\nOn Tue, Vivek wrote:\n> old patient stuff")
    rem = FakeReminders(monkeypatch)
    letterbox.file(letterbox.gather(now=10_000.0))
    assert "old" not in rem.made[0]["title"] and rem.made[0]["title"].endswith("Yes please do it")


def test_an_emailed_go_can_never_approve_a_brief():
    """The request always names its sender, so it is never a bare "go"."""
    letter = letterbox.Letter(hdr(1, subject="go"), "vivek@pharmacy.co.uk", "Vivek", "go", "")
    assert not nodes.GO_WORDS.fullmatch(letterbox.request_text(letter))


def test_notrons_own_address_is_not_read_for_the_users_email_to_dos(monkeypatch, box):
    monkeypatch.setattr(mail, "accounts", lambda: ["info@m1labs.io", "Notron"])
    assert mailroom._accounts() == ["info@m1labs.io"]


def test_a_sender_must_be_an_address():
    with pytest.raises(letterbox.LetterboxError):
        letterbox.setup("Notron", ["vivek"])
    with pytest.raises(letterbox.LetterboxError):
        letterbox.setup("Notron", [])


def test_the_new_mail_script_only_reads():
    import re
    for verb in ("send", "delete", "reply", "save", "move", "make new", "set read status", "close"):
        assert not re.search(rf"\b{verb}\b", mail.MESSAGE, re.I), verb


def test_the_listener_reads_on_a_side_thread_and_files_on_the_loop(monkeypatch, box):
    from notron import watch
    FakeMail(monkeypatch, hdr(1))
    rem = FakeReminders(monkeypatch)
    w = watch.Watcher(brain=None, settle=0)
    assert w.check_letters(1_000_000.0) is False                 # started the read
    w._letters_reading.join(5)
    assert w.check_letters(1_000_001.0) is True and len(rem.made) == 1
    assert w.check_letters(1_000_002.0) is False                 # not again until EVERY has passed


def test_without_an_address_the_listener_reads_no_mail(monkeypatch):
    from notron import watch
    monkeypatch.setattr(mail, "headers", lambda *a, **k: pytest.fail("read Mail with no address set up"))
    w = watch.Watcher(brain=None, settle=0)
    assert w.check_letters(1_000_000.0) is False and w._letters_reading is None
