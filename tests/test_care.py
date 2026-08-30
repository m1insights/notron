import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import care
from juno.care import Signal


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
