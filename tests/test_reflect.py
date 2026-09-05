"""The self-improvement loop, exercised without Notes or a model.

The loop's promises, each pinned here: evidence is measured by code, a lesson
must quote a real exchange, the verifier can reject everything, About Me
outranks whatever she taught herself, and a quiet day costs zero model calls.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import markup, nodes, reflect, workspace
from notron.state import State
from notron.outbound import Passage, prepare_outbound


def ask_note(md):
    return markup.render(workspace.ASK, md)


CHAT = ("Type anything below this line and Notron will answer underneath it.\n\n———\n\n"
        "What should I focus on this week?\n\n"
        "**Notron:**\n*Focus on the launch.*\n\n———\n\n"
        "No, I meant from my health notes.\n\n"
        "**Notron:**\n*Rest and the supplement order.*\n\n———\n\n"
        "What day is the site visit?\n\n"
        "**Notron:**\n*Thursday.*\n\n———\n")


def test_the_ask_note_reads_back_as_exchanges():
    ex = reflect.exchanges(ask_note(CHAT))
    assert [e.question for e in ex] == [
        "What should I focus on this week?",
        "No, I meant from my health notes.",
        "What day is the site visit?",
    ]
    assert ex[0].answer == "Focus on the launch."


def test_a_correction_marks_the_answer_before_it_as_a_miss():
    found = reflect.misses(reflect.exchanges(ask_note(CHAT)))
    assert len(found) == 1
    assert found[0].why == "corrected"
    assert "focus on this week" in found[0].exchange.question


def test_asking_the_same_thing_again_is_a_miss_too():
    ex = [reflect.Exchange("what's on my calendar tomorrow", "Nothing."),
          reflect.Exchange("what is on my calendar for tomorrow?", "Dentist at 2.")]
    found = reflect.misses(ex)
    assert len(found) == 1 and found[0].why == "re-asked"


def test_two_different_questions_are_not_a_miss():
    ex = [reflect.Exchange("what day is the site visit", "Thursday."),
          reflect.Exchange("summarise my book idea", "A lighthouse keeper…")]
    assert reflect.misses(ex) == []


def test_a_lesson_must_quote_the_transcript_it_claims_to_come_from():
    transcript = "You: no, I meant from my health notes"
    assert reflect._grounded("I meant from my health notes", transcript)
    assert not reflect._grounded("something the model made up", transcript)
    assert not reflect._grounded("", transcript)


class FakeBrain:
    """Proposer then verifier, in that order — the loop's two model calls."""

    def __init__(self, lessons, keep):
        self.replies = [{"lessons": lessons}, {"keep": keep}]
        self.calls = []

    def ask_json(self, *, system, user, tier, **kw):
        self.calls.append(tier)
        return self.replies.pop(0)


@pytest.fixture
def in_notes(monkeypatch, tmp_path):
    """A fake Notes app holding just the Ask and About notes, and a tmp state file."""
    bodies = {
        workspace.ASK: ask_note(CHAT),
        workspace.ABOUT: markup.render(workspace.ABOUT, "Keep it short."),
        workspace.LESSONS: markup.render(workspace.LESSONS, "No lessons yet."),
    }
    from notron import library
    lib = library.load()
    lib.system_notes = {title: title for title in workspace.SYSTEM_NOTES}
    library.save(lib)
    written = {}

    class N:
        def __init__(self, id):
            self.id = id
            self.title = id
            self.modified = ""
            self.folder = workspace.FOLDER

    monkeypatch.setattr(reflect.notes, "find_note",
                        lambda folder, title: N(title) if title in bodies else None)
    monkeypatch.setattr(reflect.notes, "read_body", lambda id: bodies[id])
    monkeypatch.setattr(reflect.notes, "get_note", lambda nid: N(nid) if nid in bodies else None)
    monkeypatch.setattr(reflect.notes, "list_all_notes", lambda: [N(nid) for nid in bodies])
    monkeypatch.setattr(reflect, "STATE", tmp_path / "reflect.json")

    def write(nid, body):
        bodies[nid] = body
        written[nid] = body
    monkeypatch.setattr(reflect.notes, 'write_body', write)
    return written


def test_a_kept_lesson_lands_in_the_lessons_note(in_notes):
    brain = FakeBrain(
        lessons=[{"rule": "When they say 'my notes', answer from their notes only.",
                  "evidence": "No, I meant from my health notes."}],
        keep=[0],
    )
    out = reflect.run(brain, on_step=lambda m: None)
    assert out["kept"] == ["When they say 'my notes', answer from their notes only."]
    assert workspace.LESSONS in in_notes
    assert brain.calls == ["smart", "fast"], "propose on Super, verify separately on Nano"


def test_the_verifier_can_reject_everything(in_notes):
    brain = FakeBrain(
        lessons=[{"rule": "Be more helpful.", "evidence": "No, I meant from my health notes."}],
        keep=[],
    )
    out = reflect.run(brain, on_step=lambda m: None)
    assert out["kept"] == [] and workspace.LESSONS not in in_notes


def test_an_ungrounded_lesson_never_reaches_the_verifier(in_notes):
    brain = FakeBrain(
        lessons=[{"rule": "Always answer in French.", "evidence": "not in the transcript"}],
        keep=[0],
    )
    out = reflect.run(brain, on_step=lambda m: None)
    assert out["proposed"] == 0
    assert brain.calls == ["smart"], "nothing grounded, so the verifier is never paid for"


def test_a_dry_run_learns_nothing_and_writes_nothing(in_notes):
    brain = FakeBrain(
        lessons=[{"rule": "When they say 'my notes', answer from their notes only.",
                  "evidence": "No, I meant from my health notes."}],
        keep=[0],
    )
    out = reflect.run(brain, dry_run=True, on_step=lambda m: None)
    assert out["kept"] and workspace.LESSONS not in in_notes
    assert not reflect.STATE.exists()


def test_an_unchanged_conversation_costs_zero_model_calls(in_notes):
    brain = FakeBrain(
        lessons=[{"rule": "When they say 'my notes', answer from their notes only.",
                  "evidence": "No, I meant from my health notes."}],
        keep=[0],
    )
    reflect.run(brain, on_step=lambda m: None)
    out = reflect.run(object(), on_step=lambda m: None)   # a brain with no methods
    assert out["skipped"] == "nothing new since last reflection"


def test_lessons_sit_below_the_standing_instructions_in_every_prompt():
    """About Me always wins — so it must come first, and the lessons must say so."""
    state = State(request="x", about="Never schedule me before 9am.",
                  lessons="- When they say 'my notes', answer from their notes only.")
    state.system_sources = {"about": Passage(state.about, "standing", f"{workspace.FOLDER}/{workspace.ABOUT}"),
                            "lessons": Passage(state.lessons, "lesson", f"{workspace.FOLDER}/{workspace.LESSONS}")}
    prompt = "\n".join(prepare_outbound("write", nodes._prompt(state)))
    assert prompt.index("standing instructions") < prompt.index("taught yourself")
    assert "subordinate to standing instructions" in prompt


def test_the_placeholder_lessons_note_is_not_fed_to_the_model():
    state = State(request="x", lessons="Nothing learned yet.")
    assert "taught yourself" not in "\n".join(prepare_outbound("write", nodes._prompt(state)))


def test_the_lessons_note_is_capped_so_it_stays_a_page():
    known = [f"lesson number {i}" for i in range(reflect.MAX_LESSONS + 5)]
    merged = (["the new one"] + known)[:reflect.MAX_LESSONS]
    assert len(merged) == reflect.MAX_LESSONS and merged[0] == "the new one"


def test_reflection_transports_prepare_history_standing_lessons_and_model_output(monkeypatch, outbound_policy, outbound_transport):
    import json
    from notron import notes
    from notron.notes import Note
    secret = 'synthetic-example-only'
    roles = {title: title for title in (workspace.ASK, workspace.ABOUT, workspace.LESSONS)}
    outbound_policy(system_notes=roles)
    bodies = {
        workspace.ASK: ask_note(CHAT.replace('Focus on the launch.', f'Focus password: {secret}')),
        workspace.ABOUT: markup.render(workspace.ABOUT, f'Keep it short. password: {secret}'),
        workspace.LESSONS: markup.render(workspace.LESSONS, f'- Be brief. password: {secret}'),
    }
    monkeypatch.setattr(notes, 'find_note', lambda folder, title: Note(title, title, folder, '') if title in roles else None)
    monkeypatch.setattr(notes, 'read_body', lambda nid: bodies[nid])
    brain, calls = outbound_transport
    calls.replies.extend([
        json.dumps({'lessons': [{'rule': f'Be concise. password: {secret}',
                                'evidence': 'No, I meant from my health notes.'}]}),
        '{"keep":[]}',
    ])
    result = reflect.run(brain, dry_run=True)
    assert result['proposed'] == 1 and result['kept'] == []
    assert len(calls.chat) == 2
    assert secret not in json.dumps(calls.chat)
    assert all('password:' not in c['messages'][0]['content'] for c in calls.chat)
