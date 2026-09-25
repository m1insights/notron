"""The morning mail: Nemotron picks what needs a reply and drafts it; code saves
drafts into Mail and never sends.

No real Mail and no real model here: `mail._osascript` is refused by the global
subprocess block, and each test fakes the few `mail` functions it needs."""

import re
from dataclasses import dataclass

import pytest

from notron import channels, library, mail, mailroom
from notron.mail import Header

REAL_POST = mailroom._post


def hdr(n, sender="Vivek Patel <vivek@example.com>", subject="Invoice", age=3600, account="info@m1labs.io",
        read=False):
    return Header(account, n, 1000 + n, sender, subject, age, read)


class Decides:
    """A scripted Nemotron: each ask_json pops the next answer."""

    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    def ask_json(self, **kw):
        self.calls.append(kw)
        return self.outs.pop(0) if self.outs else {}


class FakeMail:
    def __init__(self, monkeypatch, inbox, *, bodies=None, gone=()):
        self.inbox, self.drafts = inbox, []
        self.body = bodies or {}
        self.gone = set(gone)
        monkeypatch.setattr(mail, "accounts", lambda: sorted({h.account for a in inbox.values() for h in a}
                                                             | set(inbox)))
        monkeypatch.setattr(mail, "headers", lambda account, limit=100: inbox.get(account, [])[:limit])
        monkeypatch.setattr(mail, "bodies", lambda hs: {h.key: self.body.get(h.key, "Can you sign this off?")
                                                        for h in hs if h.key not in self.gone})
        monkeypatch.setattr(mail, "save_draft", self.save)

    def save(self, header, text):
        if header.key in self.gone:
            return False
        self.drafts.append((header.key, text))
        return True


@dataclass
class Posted:
    ok: bool = True
    reason: str = "written"


@pytest.fixture
def mail_note(monkeypatch):
    ch = channels.Channel(mailroom.CHANNEL, "mail-note", "", "", ("research",), "")
    channels._save([ch])
    lib = library.load()
    lib.channels.add("mail-note")
    library.save(lib)
    posted = []
    monkeypatch.setattr(mailroom, "_post", lambda ch, md, dry_run: posted.append((md, dry_run)) or Posted())
    return posted


# ------------------------------------------------------------- the bridge

def _scripts():
    return [v for v in vars(mail).values() if isinstance(v, str) and 'tell application "Mail"' in v]


def test_no_script_in_the_mail_bridge_can_send_or_delete():
    scripts = _scripts()
    assert len(scripts) >= 4
    for script in scripts:
        assert not re.search(r"\bsend\b", script, re.I)
        assert not re.search(r"\bdelete\b", script, re.I)
        assert "move " not in script


def test_a_draft_is_saved_and_closed_never_left_open_on_screen():
    assert "reply (message i of mb) opening window false" in mail.DRAFT
    assert "save r" in mail.DRAFT and "close r saving no" in mail.DRAFT


def test_headers_are_parsed_from_one_bulk_request(monkeypatch):
    row = mail.US.join(["1", "8778", "Vivek <v@x.com>", "Invoice", "120", "false"])
    bad = mail.US.join(["2", "not-a-number", "x", "y", "1", "true"])
    monkeypatch.setattr(mail, "_osascript", lambda script, *a, **k: row + mail.RS + bad + mail.RS)
    [h] = mail.headers("info@m1labs.io", 50)
    assert (h.index, h.id, h.subject, h.age, h.read) == (1, 8778, "Invoice", 120, False)
    assert h.key == "info@m1labs.io#8778"


def test_a_message_that_moved_away_has_no_body_rather_than_the_wrong_one(monkeypatch):
    out = f"1001{mail.US}Please sign{mail.RS}1002{mail.US}{mail.RS}"
    seen = {}
    monkeypatch.setattr(mail, "_osascript", lambda script, *a, **k: seen.setdefault("args", a) and out)
    found = mail.bodies([hdr(1), hdr(2)])
    assert found == {"info@m1labs.io#1001": "Please sign"}
    # index then id for each message, so the script can check the id at that index
    assert seen["args"][2:] == ("1", "1001", "2", "1002")


def test_bodies_are_read_a_few_per_request_so_a_slow_mail_cannot_time_out_the_lot(monkeypatch):
    calls = []
    monkeypatch.setattr(mail, "_osascript", lambda script, *a, **k: calls.append(a) or "")
    mail.bodies([hdr(n) for n in range(1, 11)])
    assert [len(a[2:]) // 2 for a in calls] == [4, 4, 2]


# ----------------------------------------------------------- the decisions

def test_only_new_mail_from_people_in_the_window_is_considered(monkeypatch, mail_note):
    FakeMail(monkeypatch, {"info@m1labs.io": [
        hdr(1), hdr(2, sender="GitHub <noreply@github.com>"), hdr(3, age=3 * 86400), hdr(4)]})
    mailroom.remember([hdr(4)])
    assert [h.index for h in mailroom.fresh(hours=24)] == [1]


def test_only_the_accounts_chosen_at_setup_are_read(monkeypatch, mail_note):
    FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1)], "iCloud": [hdr(2, account="iCloud")]})
    mailroom.choose_accounts(["info@m1labs.io"])
    assert [h.account for h in mailroom.fresh()] == ["info@m1labs.io"]


def test_the_shortlist_keeps_only_numbers_that_name_an_email(monkeypatch):
    hs = [hdr(1), hdr(2), hdr(3)]
    picked = mailroom.shortlist(hs, brain=Decides({"maybe": [2, 2, 9, "1", 0, 3]}))
    assert [h.index for h in picked] == [2, 3]


def test_a_shortlist_is_decided_from_senders_and_subjects_only(monkeypatch):
    brain = Decides({"maybe": []})
    mailroom.shortlist([hdr(1, subject="Lease renewal")], brain=brain)
    [passage] = brain.calls[0]["user"]
    assert passage.origin == "mail" and "Lease renewal" in passage.text
    assert brain.calls[0]["tier"] == "smart"


def test_only_emails_nemotron_says_need_a_reply_are_listed_most_urgent_first():
    hs = [hdr(1), hdr(2), hdr(3), hdr(4)]
    bodies = {h.key: "text" for h in hs}
    out = mailroom.decide(hs, bodies, brain=Decides({"emails": [
        {"n": 1, "reply": True, "why": "wants a date", "when": "this week", "draft": "Hi"},
        {"n": 2, "reply": False, "why": "fyi"},
        {"n": 3, "reply": True, "why": "invoice due", "when": "today", "draft": "Paid"},
        {"n": 4, "reply": True, "why": "?", "when": "someday", "draft": "Ok"},
        {"n": 3, "reply": True, "why": "dup", "when": "today", "draft": "again"},
        {"n": 7, "reply": True, "why": "not an email", "when": "today", "draft": "x"}]}))
    assert [(v.header.index, v.when) for v in out] == [(3, "today"), (1, "this week"), (4, "whenever")]


def test_quoted_history_is_not_sent_to_the_model():
    text = "Can you confirm Friday?\n\nOn Tue, Sep 23, 2026 at 9:00 AM Manan wrote:\n> old thread\n> more"
    assert mailroom._trim(text) == "Can you confirm Friday?"


def test_a_credential_in_an_email_is_redacted_before_it_reaches_the_model(outbound_policy):
    from notron.outbound import Passage, prepare_outbound
    outbound_policy()
    [sent] = prepare_outbound("write", [Passage("Your password: hunter2hunter2", "mail")])
    assert "hunter2hunter2" not in sent


# ------------------------------------------------------------- the drafts

def test_a_draft_is_never_saved_twice_for_the_same_email(monkeypatch):
    fake = FakeMail(monkeypatch, {})
    v = mailroom.Verdict(hdr(1), "why", "today", "Sure, Friday works.")
    assert mailroom.save_drafts([v])[0].saved
    assert not mailroom.save_drafts([v])[0].saved
    assert len(fake.drafts) == 1


def test_a_draft_mail_could_not_save_is_released_for_a_later_run(monkeypatch):
    fake = FakeMail(monkeypatch, {}, gone={"info@m1labs.io#1001"})
    v = mailroom.Verdict(hdr(1), "why", "today", "Sure")
    assert not mailroom.save_drafts([v])[0].saved
    fake.gone.clear()
    assert mailroom.save_drafts([v])[0].saved


def test_mail_refusing_a_draft_does_not_stop_the_others(monkeypatch):
    fake = FakeMail(monkeypatch, {})

    def save(header, text):
        if header.index == 1:
            raise mail.MailError("Mail did not answer")
        fake.drafts.append(header.key)
        return True
    monkeypatch.setattr(mail, "save_draft", save)
    out = mailroom.save_drafts([mailroom.Verdict(hdr(1), "", "today", "a"),
                                mailroom.Verdict(hdr(2), "", "today", "b")])
    assert [v.saved for v in out] == [False, True]


def test_a_draft_that_timed_out_is_not_saved_again_because_mail_may_have_kept_it(monkeypatch):
    fake = FakeMail(monkeypatch, {})
    monkeypatch.setattr(mail, "save_draft", lambda h, t: (_ for _ in ()).throw(mail.MailError("timeout")))
    v = mailroom.Verdict(hdr(1), "", "today", "a")
    mailroom.save_drafts([v])
    monkeypatch.setattr(mail, "save_draft", fake.save)
    mailroom.save_drafts([v])
    assert fake.drafts == []


def test_no_more_than_the_morning_cap_of_drafts_is_saved(monkeypatch):
    fake = FakeMail(monkeypatch, {})
    vs = [mailroom.Verdict(hdr(n), "", "today", "ok") for n in range(1, mailroom.MAX_DRAFTS + 3)]
    mailroom.save_drafts(vs)
    assert len(fake.drafts) == mailroom.MAX_DRAFTS


# ---------------------------------------------------------- the whole pass

def test_a_morning_pass_drafts_replies_and_lists_them_in_the_mail_note(monkeypatch, mail_note):
    fake = FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1), hdr(2, sender="Shop <deals@shop.com>")]})
    brain = Decides({"maybe": [1]}, {"emails": [
        {"n": 1, "reply": True, "why": "wants invoice approved", "when": "today", "draft": "Approved, thanks."}]})
    out = mailroom.run(brain)
    assert out["drafts"] == 1 and fake.drafts == [("info@m1labs.io#1001", "Approved, thanks.")]
    [(md, dry)] = mail_note
    assert not dry
    assert "**Vivek Patel** — Invoice (today)" in md and "draft in Mail → Drafts" in md
    assert "Nothing was sent" in md


def test_the_next_pass_asks_the_model_nothing_about_mail_already_looked_at(monkeypatch, mail_note):
    FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1)]})
    mailroom.run(Decides({"maybe": []}))
    brain = Decides()
    out = mailroom.run(brain)
    assert brain.calls == [] and out["new"] == 0
    assert "nothing needs a reply" in mail_note[-1][0]


def test_a_dry_run_saves_no_draft_and_remembers_nothing(monkeypatch, mail_note):
    fake = FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1)]})
    brain = Decides({"maybe": [1]}, {"emails": [{"n": 1, "reply": True, "why": "w", "when": "today",
                                                 "draft": "Yes"}]})
    out = mailroom.run(brain, dry_run=True)
    assert fake.drafts == [] and mail_note[0][1] is True
    assert "Yes" in out["digest"]              # the draft is shown in the list instead
    assert mailroom.fresh() != []


def test_a_list_that_did_not_reach_notes_is_looked_at_again_next_time(monkeypatch, mail_note):
    FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1)]})
    monkeypatch.setattr(mailroom, "_post", lambda ch, md, dry_run: Posted(False, "Notes busy"))
    out = mailroom.run(Decides({"maybe": []}))
    assert out["written"] is False and len(mailroom.fresh()) == 1


def test_without_the_mail_note_the_pass_says_how_to_set_it_up(monkeypatch):
    FakeMail(monkeypatch, {"info@m1labs.io": [hdr(1)]})
    with pytest.raises(mailroom.MailroomError, match="notron mail setup"):
        mailroom.run(Decides())


def test_the_list_is_added_to_the_mail_note_as_her_turn(monkeypatch, mail_note):
    from notron import executor
    monkeypatch.setattr(mailroom, "_post", REAL_POST)     # the real one, with the Executor faked
    seen = {}
    monkeypatch.setattr(executor, "capture_write", lambda title, **kw: executor.Write(
        title=title, folder=kw["folder"], note_id=kw["note_id"], markdown="", mode=kw["mode"]))
    monkeypatch.setattr(executor.Executor, "apply_write", lambda self, w: seen.setdefault("w", w) and Posted())
    ch = channels.Channel(mailroom.CHANNEL, "mail-note", "", "", ("research",), "")
    mailroom._post(ch, "**Mail** — 1 need a reply", dry_run=False)
    w = seen["w"]
    assert (w.note_id, w.mode, w.title) == ("mail-note", "append", "Notron Mail")
    assert "**Notron:**" in w.markdown
