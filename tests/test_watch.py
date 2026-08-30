import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import markup, watch


def _note(md):
    return markup.render("📥 Ask Juno", md)


def test_the_question_is_whatever_follows_junos_last_reply():
    body = _note("Type below.\n\n———\n\n**Juno:** earlier answer\n\n———\n\nwhat's on today?")
    assert watch.pending_question(body) == "what's on today?"


def test_nothing_pending_right_after_she_replies():
    body = _note("Type below.\n\n———\n\n**Juno:** answered\n\n———\n")
    assert watch.pending_question(body) == ""


def test_her_own_answer_is_never_mistaken_for_a_question():
    body = _note("Type below.\n\n———\n\n**Juno:** a long answer about your week\n\n———\n\n")
    assert watch.pending_question(body) == ""


def test_a_multi_line_question_survives_intact():
    body = _note("Type below.\n\n———\n\nplan my week\n\nI have a shoot Thursday")
    q = watch.pending_question(body)
    assert "plan my week" in q and "shoot Thursday" in q


def test_a_first_question_works_before_she_has_ever_replied():
    body = markup.render("📥 Ask Juno", "Type anything below this line.\n\nwho am I?")
    assert "who am I?" in watch.pending_question(body)


def test_a_question_asked_while_the_mac_slept_is_still_pending_on_wake():
    body = _note("Type below.\n\n———\n\n**Juno:** old answer\n\n———\n\nasked from my phone at 2am")
    assert watch.pending_question(body) == "asked from my phone at 2am"
