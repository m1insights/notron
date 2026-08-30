import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import guard, workspace

OK = dict(old_body="<div>old</div>", new_body="<div>new</div>")


def test_the_instruction_note_can_never_be_written():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="replace", **OK)
    assert not v and "read-only" in v.reason


def test_the_instruction_note_cannot_even_be_appended_to():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, mode="append",
                    old_body="<div>old</div>", new_body="<div>old</div><div>x</div>")
    assert not v


def test_juno_may_rewrite_her_own_notes():
    assert guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace", **OK)


def test_a_note_outside_junos_folder_can_only_be_appended_to():
    v = guard.check(folder="Notes", title="My Diary", mode="replace", **OK)
    assert not v and "append" in v.reason


def test_append_outside_the_folder_is_allowed():
    assert guard.check(folder="Notes", title="My Diary", mode="append",
                       old_body="<div>mine</div>", new_body="<div>mine</div><div>juno</div>")


def test_an_append_that_would_lose_existing_content_is_blocked():
    v = guard.check(folder="Notes", title="My Diary", mode="append",
                    old_body="<div>mine</div>", new_body="<div>juno only</div>")
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
