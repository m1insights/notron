import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import conversation, markup, notedoc


IGNORE = ("📥 Ask Notron", "Type anything below this line", "header", conversation.QA_RULE)


def note(md):
    return markup.render("📥 Ask Notron", md)


def test_a_question_typed_at_the_top_is_found():
    """The bug that lost a real message: the note says 'type below this line',
    which is at the top, but the reader only looked at the bottom."""
    body = note("Type anything below this line and Notron will answer underneath it.\n\n"
                "Hi Notron. How are you?\n\n———\n\n**Notron:** an older answer\n\n———\n")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert len(qs) == 1
    assert "How are you?" in qs[0].text


def test_a_question_at_the_bottom_is_found_too():
    body = note("header\n\n———\n\n**Notron:** old answer\n\n———\n\nwhat's on today?")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["what's on today?"]


def test_an_answered_question_is_not_asked_again():
    body = note("header\n\n———\n\nwhat's on today?\n\n**Notron:** three things\n\n———\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_a_ticked_line_written_as_a_bullet_is_not_asked_again():
    """`to_text` turns a list item's opening tag into a literal "• " ahead of
    whatever the line starts with, so a ticked list item reads "• ✓ …", not
    "✓ …". A bare `startswith(FILED)` missed that and called it unfiled
    forever — the exact loop the undo tick relies on this function to close."""
    body = "<div>📥 Ask Notron</div><ul><li>✓ @notron file this → Somewhere</li></ul>"
    assert conversation.unanswered(body, ignore=IGNORE, require_tag=True) == []


def test_two_questions_in_different_places_are_both_found():
    body = note("header\n\nfirst thing\n\n———\n\n**Notron:** answered that\n\n———\n\nsecond thing")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["first thing", "second thing"]


def test_notrons_own_words_are_never_treated_as_a_question():
    body = note("header\n\n**Notron:** I answered already\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_in_someone_elses_note_she_only_answers_when_tagged():
    body = markup.render("Book idea", "A story about a lighthouse.\n\nchapter two is weak")
    assert conversation.unanswered(body, require_tag=True) == []

    tagged = markup.render("Book idea", "A story about a lighthouse.\n\n#notron is chapter two weak?")
    qs = conversation.unanswered(tagged, require_tag=True)
    assert len(qs) == 1 and "chapter two" in qs[0].text


def test_the_tag_is_stripped_before_she_reads_it():
    assert conversation.strip_tag("#notron what do you think?") == "what do you think?"
    assert conversation.strip_tag("hey @Notron help") == "hey  help".replace("  ", " ")


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
    body = note("header\n\n**Notron:** here is what you owe\n\n"
                "## What's still owed\n\n- one thing\n- another thing\n\n———\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_a_real_question_after_her_long_reply_is_still_seen():
    body = note("header\n\n**Notron:** here is what you owe\n\n- one\n- two\n\n———\n\nand what about Friday?")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert [q.text for q in qs] == ["and what about Friday?"]


def test_the_question_is_the_line_you_tagged_not_the_whole_paragraph():
    turn = ("A story about a lighthouse keeper.\n"
            "Act two falls apart.\n"
            "#notron what would give act two some pressure?")
    q = conversation.tagged_lines(turn)
    assert q == "#notron what would give act two some pressure?"
    assert conversation.strip_tag(q) == "what would give act two some pressure?"


def test_boundaries_survive_the_newlines_apple_notes_puts_between_blocks():
    """Notes writes '</div>\\n<div>', not '</div><div>'. A boundary check that
    only looked for '><' found none, and every insert was refused."""
    old = "<div>a</div>\n<div>b</div>"
    assert notedoc.preserves(old, "<div>a</div>\n<div>NEW</div><div>b</div>")
    assert not notedoc.preserves(old, "<div>a</div>\n<div>CHANGED</div>")


def test_a_new_question_above_an_old_exchange_is_not_swallowed():
    """A real lost message: typed at the top of the note, above an older
    question that already had a reply. The two ran together as one turn, saw
    Notron's old answer underneath, and counted as answered."""
    body = note("header\n\n\n\nHow would you rate Stranded versus Fonda Lee?\n\n\n\n"
                "Hi Notron, I'm about to read a book.\n\n**Notron:** Noted.\n\n———\n")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert len(qs) == 1
    assert "Fonda Lee" in qs[0].text


def test_lines_typed_together_still_count_as_one_question():
    body = note("header\n\n———\n\nplan my week\nI have a shoot Thursday")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert len(qs) == 1
    assert "shoot Thursday" in qs[0].text


def test_a_reply_finds_its_question_again_after_the_note_shifted():
    """The block index is captured when the listener reads the note; the user
    keeps typing while the model thinks. Every block below their edit shifts,
    and an uncorrected index puts the answer under the wrong words."""
    # The user adds two paragraphs above while the model is thinking.
    shifted = markup.render("📥 Ask", "groceries\n\nring the bank\n\nWhat day is it?")
    at = notedoc.locate(shifted, "What day is it?", near=1)
    assert "What day is it?" in markup.to_text(notedoc.blocks(shifted)[at])


def test_a_missing_anchor_falls_back_to_the_index():
    body = markup.render("📥 Ask", "a\n\nb")
    assert notedoc.locate(body, "not in the note", near=2) == 2
    assert notedoc.locate(body, "", near=3) == 3


def test_the_light_rule_before_her_reply_still_reads_as_answered():
    """QA_RULE (the break drawn before **Notron:**) must be scenery, not a
    line of yours — otherwise the scan never finds her reply and she
    re-answers the same question forever."""
    body = note(f"header\n\n———\n\nwhat's on today?\n\n{conversation.QA_RULE}\n\n"
                "**Notron:** three things\n\n———\n")
    assert conversation.unanswered(body, ignore=IGNORE) == []


def test_the_light_rule_is_never_mistaken_for_your_own_words():
    """A stray QA_RULE line (one somehow left with no reply after it) must
    never get folded into the recorded question text — it's furniture, same
    as the standing header, not a line you wrote."""
    body = note(f"header\n\n———\n\nwhat's on today?\n\n{conversation.QA_RULE}\n\nstill waiting")
    qs = conversation.unanswered(body, ignore=IGNORE)
    assert all(conversation.QA_RULE not in q.text for q in qs)


# --- she answers to what autocorrect makes of her name ---------------------

def test_she_answers_to_what_autocorrect_makes_of_her_name():
    """macOS autocorrects "Notron" to "Norton" the moment you type it, and the
    iPhone has its own dictionary we cannot reach. A tag she does not recognise
    is not a small annoyance — it is silence, and the user has no way to tell
    that from her being asleep."""
    for tag in ("@notron", "@Norton", "#norton", "@nortron", "@notrn", "#NOTRON"):
        assert conversation.TAG.search(f"hey {tag} what's on today"), tag


def test_the_misspellings_are_stripped_from_the_question_too():
    """`without_tag` feeds the model. Leaving "@Norton" in the question is how
    she ends up introducing herself by the wrong name."""
    assert conversation.strip_tag("@Norton what's on today") == "what's on today"


def test_she_does_not_answer_to_a_word_that_merely_contains_her_name():
    for text in ("@nortonantivirus scan", "#notronic music", "email norton@x.com"):
        assert not conversation.TAG.search(text), text


def test_her_own_signature_is_untouched():
    """She still signs *Notron*. Accepting a misspelling in must never change
    what goes out — `SIGNATURE` is matched against her own writing."""
    assert conversation.SIGNATURE == "Notron:"
    assert not conversation.TAG.search("**Notron:** here is your day")
