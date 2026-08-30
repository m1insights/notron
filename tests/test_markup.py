import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import markup


def test_title_leads_the_body_so_notes_names_it_right():
    out = markup.render("Today", "hello")
    assert out.startswith("<div><h1>Today</h1></div>")


def test_bold_and_italic():
    assert markup.to_html("**a** and *b*") == "<div><b>a</b> and <i>b</i></div>"


def test_bullets_collapse_into_one_list():
    assert markup.to_html("- one\n- two") == "<ul><li>one</li><li>two</li></ul>"


def test_numbered_list():
    assert markup.to_html("1. one\n2. two") == "<ol><li>one</li><li>two</li></ol>"


def test_headings():
    assert markup.to_html("## Week") == "<h2>Week</h2>"


def test_table_drops_the_separator_row():
    html = markup.to_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert html.count("<tr>") == 2
    assert "---" not in html


def test_checkboxes_become_text_marks_because_notes_strips_checklists():
    assert markup.TODO in markup.to_html("- [ ] buy milk")
    assert markup.DONE in markup.to_html("- [x] buy milk")


def test_html_in_user_text_is_escaped_not_executed():
    assert "<script>" not in markup.to_html("<script>alert(1)</script>")
    assert "&lt;script&gt;" in markup.to_html("<script>alert(1)</script>")


def test_round_trip_back_to_text():
    body = markup.render("Ask Juno", "- one\n- two\n\nplain line")
    text = markup.to_text(body)
    assert "one" in text and "two" in text and "plain line" in text
    assert "<" not in text
