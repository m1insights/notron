import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import conversation, markup, notedoc


IGNORE = ("📥 Ask Juno", "Type anything below this line", "header")


def note(md):
    return markup.render("📥 Ask Juno", md)


def test_a_question_typed_at_the_top_is_found():
    """The bug that lost a real message: the note says 'type below this line',
    which is at the top, but the reader only looked at the bottom."""
    body = note("Type anything below this line and Juno will answer underneath it.\n\n"
                "Hi Juno. How are you?\n\n———\n\n**Juno:** an older answer\n\n———\n")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert len(qs) == 1
    assert "How are you?" in qs[0].text


def test_a_question_at_the_bottom_is_found_too():
    body = note("header\n\n———\n\n**Juno:** old answer\n\n———\n\nwhat's on today?")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["what's on today?"]


def test_an_answered_question_is_not_asked_again():
    body = note("header\n\n———\n\nwhat's on today?\n\n**Juno:** three things\n\n———\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_two_questions_in_different_places_are_both_found():
    body = note("header\n\nfirst thing\n\n———\n\n**Juno:** answered that\n\n———\n\nsecond thing")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["first thing", "second thing"]


def test_junos_own_words_are_never_treated_as_a_question():
    body = note("header\n\n**Juno:** I answered already\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_in_someone_elses_note_she_only_answers_when_tagged():
    body = markup.render("Book idea", "A story about a lighthouse.\n\nchapter two is weak")
    assert conversation.unanswered(body, require_tag=True) == []

    tagged = markup.render("Book idea", "A story about a lighthouse.\n\n#juno is chapter two weak?")
    qs = conversation.unanswered(tagged, require_tag=True)
    assert len(qs) == 1 and "chapter two" in qs[0].text


def test_the_tag_is_stripped_before_she_reads_it():
    assert conversation.strip_tag("#juno what do you think?") == "what do you think?"
    assert conversation.strip_tag("hey @Juno help") == "hey  help".replace("  ", " ")


def test_the_reply_lands_directly_under_what_you_wrote():
    body = note("header\n\nfirst question\n\n———\n\nlater stuff")
    q = conversation.unanswered(body, ignore=IGNORE)[0]
    out = notedoc.insert_after(body, q.after, "<div>REPLY</div>")
    text = markup.to_text(out)
    assert text.index("first question") < text.index("REPLY") < text.index("later stuff")


def test_an_insert_provably_loses_nothing():
    old = "<div>a</div><div>b</div>"
    assert notedoc.preserves(old, "<div>a</div><div>NEW</div><div>b</div>")
    assert not notedoc.preserves(old, "<div>a</div>")
    assert not notedoc.preserves(old, "<div>a</div><div>CHANGED</div>")


def test_splitting_and_rejoining_a_note_changes_nothing():
    body = note("# Heading\n\n- one\n- two\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nplain")
    assert "".join(notedoc.blocks(body)) == body


def test_a_long_reply_of_hers_is_one_turn_not_five_new_questions():
    """Only the first line of her reply is signed. Without a closing rule, her
    own headings and bullets came back as fresh questions from the user."""
    body = note("header\n\n**Juno:** here is what you owe\n\n"
                "## What's still owed\n\n- one thing\n- another thing\n\n———\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_a_real_question_after_her_long_reply_is_still_seen():
    body = note("header\n\n**Juno:** here is what you owe\n\n- one\n- two\n\n———\n\nand what about Friday?")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["and what about Friday?"]


def test_the_question_is_the_line_you_tagged_not_the_whole_paragraph():
    turn = ("A story about a lighthouse keeper.\n"
            "Act two falls apart.\n"
            "#juno what would give act two some pressure?")
    q = conversation.tagged_lines(turn)
    assert q == "#juno what would give act two some pressure?"
    assert conversation.strip_tag(q) == "what would give act two some pressure?"


def test_boundaries_survive_the_newlines_apple_notes_puts_between_blocks():
    """Notes writes '</div>\\n<div>', not '</div><div>'. A boundary check that
    only looked for '><' found none, and every insert was refused."""
    old = "<div>a</div>\n<div>b</div>"
    assert notedoc.preserves(old, "<div>a</div>\n<div>NEW</div><div>b</div>")
    assert not notedoc.preserves(old, "<div>a</div>\n<div>CHANGED</div>")
