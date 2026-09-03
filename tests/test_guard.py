import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import guard, workspace

OK = dict(old_body="<div>old</div>", new_body="<div>new</div>")


def test_the_instruction_note_can_never_be_written():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="replace", **OK)
    assert not v and "read-only" in v.reason


def test_the_instruction_note_cannot_even_be_appended_to():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="append",
                    old_body="<div>old</div>", new_body="<div>old</div><div>x</div>")
    assert not v


def test_notron_may_rewrite_her_own_notes():
    assert guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace", **OK)


def test_a_note_outside_notrons_folder_can_never_be_rewritten():
    v = guard.check(folder="Notes", title="My Diary", mode="replace", **OK)
    assert not v and "never rewrite" in v.reason


def test_she_may_answer_inside_your_note_as_long_as_nothing_is_lost():
    old = "<div>my book idea</div><div>chapter two</div>"
    assert guard.check(folder="Notes", title="Book idea", mode="insert", old_body=old,
                       new_body="<div>my book idea</div><div>Notron: try a cold open</div>"
                                "<div>chapter two</div>")


def test_an_insert_that_quietly_edits_your_words_is_blocked():
    v = guard.check(folder="Notes", title="Book idea", mode="insert",
                    old_body="<div>my book idea</div><div>chapter two</div>",
                    new_body="<div>my BETTER book idea</div><div>chapter two</div>")
    assert not v and "changed or removed" in v.reason


def test_append_outside_the_folder_is_allowed():
    assert guard.check(folder="Notes", title="My Diary", mode="append",
                       old_body="<div>mine</div>", new_body="<div>mine</div><div>notron</div>")


def test_an_append_that_would_lose_existing_content_is_blocked():
    v = guard.check(folder="Notes", title="My Diary", mode="append",
                    old_body="<div>mine</div>", new_body="<div>notron only</div>")
    assert not v and "preserve" in v.reason


def test_the_ask_note_is_shared_so_replace_is_refused():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ASK, mode="replace", **OK)
    assert not v


def test_empty_writes_are_refused():
    v = guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace",
                    old_body="x", new_body="   ")
    assert not v


def test_absurdly_large_writes_are_refused():
    v = guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace",
                    old_body="x", new_body="y" * (guard.MAX_BODY_CHARS + 1))
    assert not v


def test_restore_is_allowed_outside_the_folder():
    v = guard.check(folder="Notes", title="Parking Garages", old_body="<div>new</div>",
                    new_body="<div>original</div>", mode="restore")
    assert v.allowed


def test_restore_still_refuses_an_empty_body():
    v = guard.check(folder="Notes", title="X", old_body="<div>y</div>",
                    new_body="", mode="restore")
    assert not v.allowed


def test_replace_outside_the_folder_needs_rewrite_allowed():
    blocked = guard.check(folder="Notes", title="X", old_body="<div>a</div>",
                          new_body="<div>b</div>", mode="replace")
    assert not blocked.allowed
    allowed = guard.check(folder="Notes", title="X", old_body="<div>a</div>",
                          new_body="<div>b</div>", mode="replace", rewrite_allowed=True)
    assert allowed.allowed


def test_the_instruction_note_cannot_even_be_restored():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="restore",
                    old_body="<div>old</div>", new_body="<div>original</div>")
    assert not v and "read-only" in v.reason


def test_a_restored_body_is_not_rescanned_for_secrets():
    # The text was already live in this exact note a moment ago — it's the
    # user's own, not the model's, so the privacy scan that guards new
    # AI-authored content doesn't apply here (design decision 6).
    v = guard.check(folder="Notes", title="Parking Garages", mode="restore",
                    old_body="<div>x</div>", new_body="<div>password: hunter22</div>")
    assert v.allowed


def test_rewrite_allowed_does_not_unlock_the_instruction_note_or_a_shared_note():
    about = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="replace",
                        rewrite_allowed=True, **OK)
    assert not about.allowed
    ask = guard.check(folder=workspace.FOLDER, title=workspace.ASK, mode="replace",
                      rewrite_allowed=True, **OK)
    assert not ask.allowed
