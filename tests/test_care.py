import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import care
from notron.care import Signal


def test_the_plain_version_lists_what_you_must_do_first():
    sigs = [Signal("a", "needs you", "About Me is huge.", "Trim it."),
            Signal("b", "ok", "I've read all your notes.", "")]
    out = care._plain(sigs)
    assert out.index("What I need from you") < out.index("How I'm doing")
    assert "- [ ] Trim it." in out


def test_nothing_to_do_means_no_ask_list():
    out = care._plain([Signal("b", "ok", "All good.", "")])
    assert "What I need from you" not in out


def test_the_care_note_is_never_blank_when_the_model_says_nothing():
    class Mute:
        def ask(self, **kw):
            return ""
    body = care.compose([Signal("b", "ok", "All good.", "")], Mute())
    assert body.strip()


def test_thresholds_are_ordered_sensibly():
    assert care.ABOUT_COMFORTABLE < care.ABOUT_HEAVY


def test_mood_is_the_worst_signal_not_the_average():
    signals = [Signal("a", "ok", "fine", ""), Signal("b", "needs you", "broken", "fix it"),
               Signal("c", "nudge", "growing", "trim it")]
    severity, emoji, label = care.overall_mood(signals)
    assert severity == "needs you"
    assert (emoji, label) == care.MOOD["needs you"]


def test_mood_is_ok_when_every_signal_is_ok():
    severity, _, _ = care.overall_mood([Signal("a", "ok", "fine", "")])
    assert severity == "ok"


def test_the_care_note_leads_with_the_mood_emoji():
    body = care.compose([Signal("a", "ok", "fine", "")])
    emoji, label = care.MOOD["ok"]
    assert body.startswith(f"{emoji} {label}")


def test_she_says_about_me_is_missing_not_nearly_empty(monkeypatch):
    """A note that's gone (deleted, maybe by accident) reads very differently
    from a note that exists but is unfilled-in — she has zero of the user's
    standing instructions either way, but only one of those is a note nobody
    can restore for you, so it needs its own, louder message."""
    monkeypatch.setattr(care.notes, "find_note", lambda folder, title: None)
    signals = care.check()
    about = [s for s in signals if s.key == "about_size"]
    assert about and about[0].severity == "needs you"
    assert "missing" in about[0].fact
    assert "nearly empty" not in about[0].fact
    assert "setup" in about[0].ask


def test_she_asks_for_help_when_an_app_stops_answering(monkeypatch):
    """Automation approval can be revoked in System Settings at any time, and the
    only symptom is silence. The care note is where silence becomes a sentence."""
    from notron import permissions

    monkeypatch.setattr(care.permissions, "check", lambda: [
        permissions.Check("Notes", True, "ready", ""),
        permissions.Check("Reminders", False, "no answer", "Turn on Reminders"),
    ])
    signals = care.check()
    apps = [s for s in signals if s.key == "apps"]
    assert apps and apps[0].severity == "needs you"
    assert "Reminders" in apps[0].fact
