"""Regression tests for a real leak: Notron's first live morning run copied two of
the user's passwords out of their notes and into the plan it wrote."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import guard, privacy, workspace


def test_the_exact_leak_that_happened():
    leaked = "13:30 - Admin: review wedding admin notes (password ruchimanan)"
    assert privacy.contains_secret(leaked)
    assert "ruchimanan" not in privacy.redact(leaked)


def test_a_long_hex_secret_is_caught_even_without_a_label():
    text = "check the dashboard 008ace5e53445ae93462cdb7975dcddf"
    assert privacy.contains_secret(text)
    assert "008ace5e" not in privacy.redact(text)


def test_common_key_formats():
    for s in ("sk-abcdefghijklmnop1234", "ghp_abcdefghijklmnop1234", "xoxb-1234567890-abc"):
        assert privacy.contains_secret(f"my token {s}"), s


def test_ordinary_talk_about_passwords_is_left_alone():
    for s in ("use my password manager", "reset my password for Netflix",
              "the password field is broken", "buy milk and eggs"):
        assert not privacy.contains_secret(s), s
        assert privacy.redact(s) == s


def test_credential_notes_are_dropped_from_retrieval():
    passages = [("Passwords", "gmail hunter2"), ("Grocery list", "milk, eggs")]
    kept = privacy.filter_passages("plan my day", passages)
    assert [t for t, _ in kept] == ["Grocery list"]


def test_but_you_can_still_ask_about_your_own_password_note():
    passages = [("Passwords", "gmail: hunter2")]
    kept = privacy.filter_passages("what's in my passwords note?", passages)
    assert len(kept) == 1
    assert "hunter2" not in kept[0][1]      # shown, but the value is still masked


def test_the_guard_blocks_a_write_carrying_a_secret():
    v = guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace",
                    old_body="<div>old</div>",
                    new_body="<div>login with password ruchimanan</div>")
    assert not v and "password" in v.reason


def test_the_guard_lets_an_ordinary_plan_through():
    assert guard.check(folder=workspace.FOLDER, title=workspace.TODAY, mode="replace",
                       old_body="<div>old</div>", new_body="<div>09:00 walk the dog</div>")


def test_private_notes_are_not_dredged_up_for_an_unrelated_question():
    """Asked to compare two novels, she pulled explicit personal material out of
    the user's notes, quoted it verbatim, and presented it as being from one of
    the books."""
    passages = [("Roleplay log", "explicit private text"),
                ("Journal", "how I actually felt that night"),
                ("Book notes", "Fonda Lee, Jade City")]
    kept = privacy.filter_passages("how does Stranded compare to Fonda Lee?", passages)
    assert [t for t, _ in kept] == ["Book notes"]


def test_but_she_still_works_with_them_when_you_ask_directly():
    passages = [("Journal", "how I actually felt")]
    assert privacy.filter_passages("what did my journal say about last week?", passages)


def test_a_block_of_bare_codes_is_a_key_dump_whatever_the_title_says():
    """The user's real note called "CRITICAL": six Obsidian recovery codes and
    not one word for a pattern to key off."""
    assert privacy.is_key_dump("CRITICAL\n\nObsidian recovery:\n\n"
                               "3gndvxcgadhpt5nx\n\nsttxr5kj92y7wen2\n\ngh5hgvd6wyh7xecj")


def test_ordinary_prose_and_short_lists_are_not_key_dumps():
    for text in ["milk\neggs\nbread", "Call the dentist\nBook the flights",
                 "extraordinarily\nunbelievable\nconstitutional",   # long, but no digits
                 "abc123\ndef456"]:                                  # too short, too few
        assert not privacy.is_key_dump(text), text


def test_a_key_dump_never_blocks_a_write():
    """`is_key_dump` is for the preview panel only. A run of order numbers must
    cost one click there, never a refused write, so the Guard's check is blind
    to it on purpose."""
    codes = "srv-d0f97eq4d50c73f7stjg\nsrv-d4s31224d50c73b7fva0\nsrv-cvepainnoe9s73eqp3lg"
    assert privacy.is_key_dump(codes)
    assert not privacy.contains_secret(codes)
