"""The Reminders inbox: "Hey Siri, remind me to … in Notron" → an answer in Notes,
an Approve reminder whose tick is the go, and a Done reminder when the work is back.

No real Reminders, Claude or model here: `reminders` is a fake list, `_spawn`
is faked like the hand-off tests, and brains are scripted."""

import json
from pathlib import Path

import pytest

from notron import channels, handoff, inbox, library, nodes, reminders, watch, workspace
from notron.reminders import Reminder
from notron.state import State


class FakeReminders:
    """The Reminders app, as far as the inbox can see it."""

    def __init__(self, monkeypatch, *items, lists=("Notron",)):
        self.items = {r.id: r for r in items}
        self.done, self.made, self.lists = set(), [], list(lists)
        monkeypatch.setattr(reminders, "open_items",
                            lambda caller=None: [r for r in self.items.values() if r.id not in self.done])
        monkeypatch.setattr(reminders, "complete", self.complete)
        monkeypatch.setattr(reminders, "is_completed", lambda rid, caller=None: rid in self.done)
        monkeypatch.setattr(reminders, "create", self.create)
        monkeypatch.setattr(reminders, "resolve_targets", lambda name="", caller=None, **kw:
                            [{"id": f"list-{n}", "title": n} for n in self.lists if n == name])

    def complete(self, rid, caller=None):
        self.done.add(rid)
        return self.items[rid].title

    def create(self, title, *, notes="", when_iso=None, target_id=None, caller=None, **kw):
        rid = f"made-{len(self.made)}"
        self.made.append({"title": title, "notes": notes, "when": when_iso, "list": target_id})
        self.items[rid] = Reminder(rid, title, "Notron", when_iso or "")
        return rid


def ask(rid, title, list_name="Notron"):
    return Reminder(rid, title, list_name, "")


def _channel(name, note_id, *, repo="", allow=("research", "run"), hand="claude"):
    ch = channels.Channel(name, note_id, repo, "", tuple(allow), hand)
    channels._save([c for c in channels.load() if c.name != name] + [ch])
    lib = library.load()
    lib.channels.add(note_id)
    library.save(lib)
    return ch


class Decides:
    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    def ask_json(self, **kw):
        self.calls.append(kw)
        return self.outs.pop(0) if self.outs else {}


BRIEF = {"goal": "Draft a reply to the landlord about the leak", "steps": ["Say what happened", "Ask for a date"],
         "files": ["reply-to-landlord.md"], "done_when": "A polite draft is ready", "out_of_scope": ["Sending it"]}


# ------------------------------------------------------------------- reads

def test_only_the_users_own_requests_in_the_notron_list_are_waiting(monkeypatch):
    FakeReminders(monkeypatch, ask("r1", "draft the landlord reply"), ask("r2", "buy milk", "Groceries"),
                  ask("r3", "Approve: something Notron asked"))
    with inbox._editing() as data:
        data["ours"]["r4"] = 1.0
    reminders.open_items()           # the fake is live
    assert [r.id for r in inbox.waiting()] == ["r1"]


def test_a_workspace_channel_needs_no_repository():
    ch = channels._validate(channels.Channel("Tasks", "n", "", "", ("research", "run"), "claude"))
    assert ch.workspace


# ----------------------------------------------------------------- routing

def test_with_only_the_tasks_note_no_model_is_asked():
    _channel("Tasks", "tasks-note")
    ch, why = inbox.route("draft the landlord reply", brain=Decides())
    assert ch.name == "Tasks"


def test_nemotron_picks_the_project_and_code_checks_the_pick():
    _channel("Tasks", "tasks-note")
    _channel("Synqology", "synq-note", repo="/tmp/synq", allow=("read",), hand="")
    brain = Decides({"channel": "synqology", "why": "about the app"})
    ch, _ = inbox.route("is CI green on the app", brain=brain)
    assert ch.name == "Synqology" and brain.calls[0]["tier"] == "smart"
    ch, _ = inbox.route("plan a trip", brain=Decides({"channel": "Secret Project"}))
    assert ch.name == "Tasks"                     # a name not in the registry is not a place


def test_a_reminder_is_routed_once_even_across_a_restart():
    _channel("Tasks", "tasks-note")
    _channel("Synqology", "synq-note", repo="/tmp/synq", allow=("read",), hand="")
    first, _ = inbox.remembered_route("r1", "fix the app", brain=Decides({"channel": "Synqology"}))
    again, _ = inbox.remembered_route("r1", "fix the app", brain=Decides({"channel": "none"}))
    assert first.name == again.name == "Synqology"


# ---------------------------------------------------------------- buzzing

def test_an_approve_reminder_is_made_once_and_buzzes(monkeypatch):
    app = FakeReminders(monkeypatch)
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="draft it", brief=BRIEF, prompt="p",
                           source_reminder="r1")
    rid = inbox.ask_approval(task)
    assert inbox.ask_approval(task) == rid and len(app.made) == 1
    made = app.made[0]
    assert made["title"] == f"Approve: {BRIEF['goal']}" and made["list"] == "list-Notron"
    assert len(made["when"]) > 10                 # a time, so EventKit gives it an alarm
    assert handoff.get(task.id).approve_reminder == rid
    assert inbox.waiting() == []                  # never read back as a request


def test_a_failed_approve_reminder_can_be_tried_again(monkeypatch):
    app = FakeReminders(monkeypatch, lists=())
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="x", brief=BRIEF, prompt="p")
    with pytest.raises(inbox.InboxError):
        inbox.ask_approval(task)
    app.lists = ["Notron"]
    assert inbox.ask_approval(task)


def test_ticking_approve_is_the_go_for_exactly_that_brief(monkeypatch):
    app = FakeReminders(monkeypatch)
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="x", brief=BRIEF, prompt="p")
    rid = inbox.ask_approval(task)
    assert inbox.sync_approvals() == [] and handoff.get(task.id).status == "proposed"
    app.done.add(rid)
    (approved,) = inbox.sync_approvals()
    assert approved.id == task.id and handoff.get(task.id).status == "approved"


def test_a_typed_go_ticks_the_approve_reminder_so_the_phone_stops_asking(monkeypatch):
    app = FakeReminders(monkeypatch)
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="x", brief=BRIEF, prompt="p")
    rid = inbox.ask_approval(task)
    handoff.approve(task.id, task.digest)
    inbox.sync_approvals()
    assert rid in app.done


def test_a_ticked_approve_for_an_expired_brief_runs_nothing(monkeypatch):
    app = FakeReminders(monkeypatch)
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="x", brief=BRIEF, prompt="p")
    app.done.add(inbox.ask_approval(task))
    handoff._update(task.id, created=task.created - handoff.APPROVAL_TTL - 1)
    assert inbox.sync_approvals() == [] and handoff.get(task.id).status == "expired"


# ------------------------------------------------------- non-code work

@pytest.fixture
def spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(handoff, "_spawn", lambda cmd, **kw: spawned.append((cmd, kw)) or 4242)
    monkeypatch.setattr(handoff, "_alive", lambda pid, started="": False)
    monkeypatch.setattr(handoff, "_started_at", lambda pid: "Tue Sep 23 10:00:00 2026")
    monkeypatch.setattr(handoff, "_git", lambda *a, **k: pytest.fail("a workspace task touched git"))
    return spawned


def _approved_workspace_task():
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="w" * 32, channel=ch, request="draft it", brief=BRIEF, prompt="the brief")
    handoff.approve(task.id, task.digest)
    return handoff.dispatch_next()


def test_a_document_task_runs_in_an_empty_folder_with_document_rules(spawn):
    started = _approved_workspace_task()
    cmd, kw = spawn[0]
    assert started.status == "running" and not started.branch
    assert kw["cwd"] == Path(started.run_dir) / "work" and not any(kw["cwd"].iterdir())
    assert handoff.WORK_RULES in cmd and "Bash" in cmd[cmd.index("--disallowedTools") + 1]


def test_a_finished_document_task_is_saved_where_the_user_can_open_it(spawn):
    started = _approved_workspace_task()
    work = Path(started.run_dir) / "work"
    (work / "reply-to-landlord.md").write_text("Dear Sam,\n\nThe kitchen ceiling is leaking again.")
    (work / "planted").symlink_to(Path.home())
    (Path(started.run_dir) / "out.json").write_text(json.dumps({"result": "Wrote the draft."}))
    (done,) = handoff.poll()
    assert done.status == "finished" and done.changed == ["reply-to-landlord.md"]
    out = Path(done.output)
    assert out.parent == handoff.output_root() and BRIEF["goal"][:20] in out.name
    assert (out / "reply-to-landlord.md").read_text().startswith("Dear Sam")
    assert not (out / "planted").exists()              # a link the agent made never leaves its folder
    assert "kitchen ceiling" in done.diff and not Path(started.run_dir).exists()
    report = nodes._report(done, {"verdict": "done", "summary": "A polite draft."})
    assert "Saved to" in report and "reply-to-landlord.md" in report and "Branch" not in report


def test_a_second_task_with_the_same_goal_never_overwrites_the_first(spawn):
    started = _approved_workspace_task()
    task = handoff.get(started.id)
    first = handoff._output_folder(task, 0)
    first.mkdir(parents=True)
    assert handoff._output_folder(task, 0) != first


def test_document_work_is_briefed_and_reviewed_as_documents(monkeypatch):
    _channel("Tasks", "tasks-note")
    monkeypatch.setattr(nodes, "_prefetch", lambda ch: (type("T", (), {"is_alive": lambda s: False})(), {}))
    brain = Decides({"kind": "task", "tools": [], "web": False}, BRIEF)
    state = State(request="draft a reply to the landlord", intent="question", source_note_id="tasks-note",
                  trigger="reminder", request_id="reminder:r1",
                  reply_to=("Notron Tasks", workspace.FOLDER, 0))
    state = nodes.project(state, brain=brain)
    assert brain.calls[0]["system"] == nodes.WORK_SYSTEM and brain.calls[1]["system"] == nodes.WORK_BRIEF_SYSTEM
    assert state.proposal["source_reminder"] == "r1"
    assert "Tick **Approve** in Reminders" in state.answer and "Nothing is sent anywhere" in state.answer


# ------------------------------------------------------------ the reply

def test_a_reminder_reply_is_appended_and_carries_the_request(monkeypatch):
    _channel("Tasks", "tasks-note")
    state = State(request="what time is sunset", source_note_id="tasks-note", trigger="reminder",
                  reply_to=("Notron Tasks", workspace.FOLDER, 0), answer="7:12 pm.")
    w = nodes._reply(state)
    assert w.mode == "append" and w.title == "Notron Tasks"
    assert "From Reminders:" in w.markdown and "**From" not in w.markdown and "what time is sunset" in w.markdown


# ------------------------------------------------------------ listener

def test_the_listener_answers_a_reminder_then_ticks_it(monkeypatch):
    app = FakeReminders(monkeypatch, ask("r1", "draft the landlord reply"))
    _channel("Tasks", "tasks-note")
    runs = []

    def run(request, **kw):
        runs.append((request, kw))
        s = State(request=request)
        s.receipt_complete = len(runs) > 1          # the first reply does not land
        return s

    monkeypatch.setattr(watch.graph, "run", run)
    w = watch.Watcher(brain=Decides())
    assert w.check_inbox() and "r1" not in app.done   # not ticked until the answer is in Notes
    w._failures.clear()
    assert w.check_inbox() and "r1" in app.done
    (request, kw) = runs[-1]
    assert request == "draft the landlord reply" and kw["trigger"] == "reminder"
    assert kw["request_id"] == "reminder:r1" and kw["source_note_id"] == "tasks-note"
    assert kw["reply_to"] == ("Notron Tasks", workspace.FOLDER, 0)
    assert not w.check_inbox()


def test_without_a_tasks_note_the_listener_says_how_to_make_one(monkeypatch):
    FakeReminders(monkeypatch, ask("r1", "anything"))
    said = []
    w = watch.Watcher(brain=Decides(), on_event=said.append)
    monkeypatch.setattr(w, "_say", said.append)
    assert not w.check_inbox()
    assert any("notron tasks setup" in s for s in said)


def test_a_briefed_reminder_task_asks_for_approval_and_says_when_it_is_done(monkeypatch):
    app = FakeReminders(monkeypatch)
    ch = _channel("Tasks", "tasks-note")
    task = handoff.propose(task_id="t" * 32, channel=ch, request="x", brief=BRIEF, prompt="p",
                           source_reminder="r1")
    w = watch.Watcher(brain=None)
    w.tend_reminders()
    assert app.made[0]["title"].startswith("Approve:")
    handoff._update(task.id, status="finished", started=1.0, finished=2.0)
    monkeypatch.setattr(handoff, "poll", lambda: [])

    def run(request, **kw):
        s = State(request=request)
        s.receipt_complete = True
        return s

    monkeypatch.setattr(watch.graph, "run", run)
    assert w.check_tasks()
    assert app.made[-1]["title"] == f"✅ Done: {BRIEF['goal']}"
    assert handoff.get(task.id).done_reminder
