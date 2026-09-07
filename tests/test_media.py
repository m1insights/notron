"""The picture Apple Notes throws away when a script writes to the note.

2026-09-06, live, on the developer's own library. A note called "Test" held one
photo dropped in from the camera roll. Asked to describe it, Notron read the
picture correctly and was then refused by the Guard: "body is 1867818 chars,
over the 200000 limit".

Chasing that number down turned up something much worse than a size limit:

  * Apple Notes' `body` **getter** serialises an embedded image as an inline
    `<img src="data:image/heic;base64,…">` — 1,867,394 characters for one
    photo, against 33 characters of actual text.
  * Apple Notes' `body` **setter** silently discards it. Proved on a throwaway
    note: a body sent with an `<img src="data:image/png;base64,…">` read back
    as `<div><br><br></div>` with no attachment and no error.

So any scripted write to a note holding a picture deletes the picture. The
size limit was catching this one by accident — a smaller image (under roughly
146KB, which is most screenshots) sails past 200,000 characters and would have
been destroyed silently. `notedoc.preserves` cannot see it: every character of
the string Notron sends really is preserved, and Notes throws the image away
afterwards.

These tests exist so that can never happen.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import guard, markup, workspace

# The exact shape the real note came back as, cut short.
PHOTO = ('<div><h1>Test</h1></div>\n<div><br></div>\n'
         '<div><img style="max-width: 100%; max-height: 100%;" '
         'src="data:image/heic;base64,AAAANGZ0eXBoZWlj"/><br></div>\n'
         '<div>@notron describe this image</div>')
PLAIN = "<div><h1>Test</h1></div>\n<div>@notron describe this image</div>"


def test_a_body_carrying_a_picture_is_recognised():
    assert markup.holds_media(PHOTO)
    assert not markup.holds_media(PLAIN)
    assert not markup.holds_media("")


def test_her_own_writing_is_never_mistaken_for_a_picture():
    """`markup.to_html` cannot emit an image, so nothing she writes may ever
    trip this — or she would lock herself out of her own notes."""
    body = markup.render("Notes", "# Heading\n\n- a list\n- **bold** and `code`\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert not markup.holds_media(body)


@pytest.mark.parametrize("mode", ["append", "insert", "mark", "replace", "restore"])
def test_no_write_of_any_kind_may_land_on_a_note_holding_a_picture(mode):
    """Including `restore`, which is exempt from the preserve checks and the
    secret scan — it is still a full-body write, and it would still delete the
    photo."""
    v = guard.check(folder="Notes", title="Test", old_body=PHOTO,
                    new_body=PHOTO + "<div>her answer</div>", mode=mode,
                    rewrite_allowed=True)
    assert not v.allowed
    assert "picture" in v.reason.lower()
    assert v.permanent, "trying again can never help — the note will still hold the photo"


def test_the_reason_names_the_picture_not_the_size():
    """The reported symptom was a size limit, which sent the reader looking for
    a bigger number instead of a deleted photo."""
    big = PHOTO.replace("AAAANGZ0eXBoZWlj", "A" * 300_000)
    v = guard.check(folder="Notes", title="Test", old_body=big,
                    new_body=big + "<div>x</div>", mode="insert")
    assert "picture" in v.reason.lower()
    assert "200000" not in v.reason


def test_a_note_without_a_picture_is_unaffected():
    v = guard.check(folder="Notes", title="Test", old_body=PLAIN,
                    new_body=PLAIN + "<div>her answer</div>", mode="insert")
    assert v.allowed


def test_an_ordinary_refusal_is_still_worth_retrying():
    """Only a refusal that can never change is permanent. A note that grew too
    big may be trimmed by the user five minutes from now."""
    v = guard.check(folder=workspace.FOLDER, title="x", old_body="",
                    new_body="<div>" + "y" * 300_000 + "</div>", mode="replace")
    assert not v.allowed and not v.permanent


# ------------------------------- she still answers, and only asks once more

from notron import conversation, nodes, watch
from notron.state import State, Write


@pytest.fixture
def notes_app(monkeypatch):
    """A tiny writable stand-in — the shared fake refuses every write on
    purpose, and this is a test about what gets written."""
    from notron import executor as ex_mod
    from notron.notes import Note

    bodies = {"Parking Garages": PHOTO, workspace.ASK: markup.render(workspace.ASK, "")}
    monkeypatch.setattr(ex_mod.notes, "find_note",
                        lambda folder, title: Note(title, title, folder, "x")
                        if title in bodies else None)
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda note_id: bodies[note_id])
    monkeypatch.setattr(ex_mod.notes, "write_body",
                        lambda note_id, body: bodies.__setitem__(note_id, body))
    monkeypatch.setattr(ex_mod.undo, "save", lambda note_id, body: None)
    return bodies


def _state_with_a_blocked_reply():
    s = State(request="@notron describe this image",
              answer="It is a smartwatch showing activity rings.",
              reply_to=("Parking Garages", "Notes", 3))
    s.writes = [Write(title="Parking Garages", folder="Notes", mode="insert",
                      after=3, anchor="describe this image",
                      markdown=conversation.turn("It is a smartwatch showing activity rings."))]
    return s


def test_an_answer_she_cannot_write_in_the_note_still_reaches_the_user(notes_app):
    """Refusing is right; going silent is not. The user tagged a note and got
    nothing at all — twice — and the only trace was a line in a log file."""
    s = nodes.executor(_state_with_a_blocked_reply(), brain=None)

    ask = notes_app[workspace.ASK]
    assert "smartwatch showing activity rings" in ask
    assert "Parking Garages" in ask, "she has to say which note it was about"
    assert any(r.startswith("✗") for r in s.results), "the block is still reported honestly"


def test_she_says_why_she_could_not_answer_in_the_note(notes_app):
    nodes.executor(_state_with_a_blocked_reply(), brain=None)
    assert "picture" in notes_app[workspace.ASK].lower()


def test_a_question_that_can_never_be_answered_there_is_not_asked_again(
        notes_app, monkeypatch, tmp_path):
    """`Watcher.COOLDOWN` rests a note for half an hour and then tries twice
    more, forever. For a refusal that cannot change that is a model call every
    thirty minutes for the life of the note, and the same refusal every time —
    and, because the tag stays unanswered, a full re-read of the note (1.8MB,
    for a note holding a photo) on every twenty-second sweep in between."""
    from notron import mentions

    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")
    # The stand-in keys notes by title, the way `find_note` hands them back.
    m = mentions.Mention(note_id="Parking Garages", title="Parking Garages",
                         folder="Notes", question="describe this image", after=3,
                         raw="@notron describe this image", modified="monday")

    w = watch.Watcher(brain=None, settle=0)
    w.scanner.scan = lambda: [m]
    monkeypatch.setattr(watch.attachments, "on_note", lambda note_id, modified="": [])
    monkeypatch.setattr(watch.graph, "run", lambda q, **kw: nodes.executor(
        _state_with_a_blocked_reply(), brain=None))

    w.sweep_mentions()      # first sight — she waits for the typing to settle
    w.sweep_mentions()

    assert "smartwatch" in notes_app[workspace.ASK], "she answered where she could"
    assert w.scanner.answered_elsewhere == {"Parking Garages": ["describe this image"]}, \
        "and the tag is retired, so the note stops owing a reply it can never get"


def test_an_ordinary_failure_still_gets_its_second_chance():
    w = watch.Watcher(brain=None)
    w._attempted("tag:n1", wrote=False, stuck="")
    assert w._worth_trying("tag:n1")
