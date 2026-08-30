"""Regression tests for a real leak: Juno's first live morning run copied two of
the user's passwords out of their notes and into the plan it wrote."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import guard, privacy, workspace


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
