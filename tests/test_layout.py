"""How a filed thought is laid out in the note it lands in: a journal for
things that happen over time, a plain list for things that simply exist."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import date

from notron import layout, markup

DAY = date(2026, 9, 2)
STACK = ("The stack did really well today.", ["Concerta 36mg", "Avmacol", "PQQ"])
SINGLE = ("took vitamin D today", [])


def test_a_log_gets_one_bold_date_then_prose_then_bullets():
    md = layout.markdown([STACK, SINGLE], shape=layout.LOG, existing_text="", day=DAY)
    assert md == ("\n**Wed 2 Sep 2026**\n"
                  "The stack did really well today.\n- Concerta 36mg\n- Avmacol\n- PQQ\n"
                  "\n"
                  "took vitamin D today\n")
    html = markup.to_html(md)
    assert "<b>Wed 2 Sep 2026</b>" in html
    assert "<ul><li>Concerta 36mg</li><li>Avmacol</li><li>PQQ</li></ul>" in html


def test_a_second_pass_the_same_day_sits_under_the_heading_already_there():
    first = layout.markdown([SINGLE], shape=layout.LOG, existing_text="", day=DAY)
    note_text = "Supps\n\nMagnesium\n" + markup.to_text(markup.to_html(first))
    second = layout.markdown([STACK], shape=layout.LOG, existing_text=note_text, day=DAY)
    assert "Wed 2 Sep 2026" not in second
    assert second.startswith("\nThe stack did really well today.\n- Concerta 36mg")


def test_a_new_day_gets_its_own_heading_even_under_an_older_one():
    note_text = "Supps\n\nTue 1 Sep 2026\ntook vitamin D today\n"
    md = layout.markdown([SINGLE], shape=layout.LOG, existing_text=note_text, day=DAY)
    assert md.startswith("\n**Wed 2 Sep 2026**\n")


def test_a_list_is_bullets_with_no_date_and_a_multi_line_thought_keeps_its_shape():
    md = layout.markdown([SINGLE, ("that serum from the pop-up", []), STACK],
                         shape=layout.LIST, existing_text="", day=DAY)
    assert md == ("\n- took vitamin D today\n- that serum from the pop-up\n"
                  "\n"
                  "The stack did really well today.\n- Concerta 36mg\n- Avmacol\n- PQQ\n")
    assert "2026" not in md


def test_an_unknown_shape_is_a_log():
    assert layout.shape("whatever") == layout.LOG
    assert layout.shape("list") == layout.LIST
    assert layout.shape(" Log ") == layout.LOG
