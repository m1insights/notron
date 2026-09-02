"""The Filer: a brain dump sorted into the notes it belongs in, with nothing
ever deleted, and a question instead of a guess when no note fits.

Everything runs against an in-memory stand-in for Apple Notes and a brain that
answers from a table, so the suite needs no key and no network.
"""

import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import date

import pytest

from notron import conversation, filer, guard, markup, nodes, notedoc, workspace
from notron import executor as ex_mod
from notron.notes import Note


# ------------------------------------------------------------ stand-ins

class Store:
    """Apple Notes in a dict. Bodies are the HTML `markup.render` produces."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.writes: list[tuple[str, str]] = []

    def add(self, title, md, *, folder="Notes"):
        nid = f"n{len(self.rows) + 1}"
        self.rows[nid] = {"title": title, "folder": folder,
                          "body": markup.render(title, md), "modified": "1"}
        return nid

    def body(self, title):
        return next(r["body"] for r in self.rows.values() if r["title"] == title)

    def text(self, title):
        return markup.to_text(self.body(title))

    # -- the notes.py surface the code under test uses --
    def find_note(self, folder, title):
        for nid, r in self.rows.items():
            if r["folder"] == folder and r["title"] == title:
                return Note(nid, title, folder, r["modified"])
        return None

    def read_body(self, nid):
        return self.rows[nid]["body"]

    def write_body(self, nid, body):
        self.rows[nid]["body"] = body
        self.rows[nid]["modified"] = str(int(self.rows[nid]["modified"]) + 1)
        self.writes.append((self.rows[nid]["title"], body))

    def create_note(self, folder, body):
        title = markup.to_text(body).split("\n")[0].strip()
        nid = f"n{len(self.rows) + 1}"
        self.rows[nid] = {"title": title, "folder": folder, "body": body, "modified": "1"}
        self.writes.append((title, body))
        return nid

    def list_all_notes(self):
        return [Note(nid, r["title"], r["folder"], r["modified"]) for nid, r in self.rows.items()]


class FilerBrain:
    """Answers the Filer's question from a table keyed by line text.

    A verdict is {"note": title}, {"new": title}, or {"part_of": "<the lead
    line's text>"} — the fake turns the lead's text into its number in the
    prompt, the way the model would. `shapes` is returned as-is."""

    def __init__(self, verdicts: dict[str, dict], shapes: dict[str, str] | None = None):
        self.verdicts = verdicts
        self.shapes = shapes or {}
        self.calls = 0
        self.prompts = []

    def ask_json(self, *, system, user, **kw):
        self.calls += 1
        self.prompts.append(user)
        assert kw.get("tier") == filer.TIER
        numbered = {m.group(2): int(m.group(1)) for m in re.finditer(r"^(\d+)\. (.*)$", user, re.M)}
        rows = []
        for text, n in numbered.items():
            v = self.verdicts.get(text)
            if not v:
                continue
            if "part_of" in v:
                rows.append({"line": n, "part_of": numbered.get(v["part_of"], 0)})
            else:
                rows.append({"line": n, **v})
        return {"filed": rows, "shapes": dict(self.shapes)}

    def ask(self, **kw):
        raise AssertionError("the Filer must never pay for a text model call")


SEED = workspace.SEEDS[workspace.DUMP]


@pytest.fixture
def store(monkeypatch, tmp_path):
    s = Store()
    from notron import notes, index, library
    for name in ("find_note", "read_body", "write_body", "create_note", "list_all_notes"):
        monkeypatch.setattr(notes, name, getattr(s, name))
    monkeypatch.setattr(index, "glimpses", lambda chars=100, **kw: {})
    monkeypatch.setattr(filer, "STATE", tmp_path / "filer.json")
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")   # never the developer's own choices
    s.add(workspace.LOG, "Everything Notron did.\n\n———\n", folder=workspace.FOLDER)
    return s


def dump(store, md):
    return store.add(workspace.DUMP, SEED + "\n" + md, folder=workspace.FOLDER)


# ------------------------------------------------------- lines and marks

def test_a_bulleted_dump_is_still_one_thought_per_line():
    html = markup.render("x", "first\n- a\n- b\nlast")
    assert [ln.text for ln in notedoc.lines(html)] == ["x", "first", "a", "b", "last"]


def test_a_line_knows_when_a_blank_line_sits_above_it():
    """A blank line is how someone separates thoughts. Whitespace Apple Notes
    puts between elements is not a blank line; an empty <div><br></div> is."""
    html = markup.render("x", "first\nsecond\n\nthird\n- a\n- b\n\n\nlast")
    got = [(ln.text, ln.after_gap) for ln in notedoc.lines(html)]
    assert got == [("x", False), ("first", False), ("second", False),
                   ("third", True), ("a", False), ("b", False), ("last", True)]


def test_adjacent_lines_share_a_run_and_a_blank_line_or_a_ticked_line_starts_the_next(store):
    """A run is the most the model may ever group into one thought."""
    dump(store, "the stack today:\nmagnesium\nzinc\n\nact two needs a storm\n✓ old one → Book idea\nthat serum\n")
    items = filer.unfiled(store.body(workspace.DUMP))
    assert [(it.text, it.run) for it in items] == [
        ("the stack today:", 0), ("magnesium", 0), ("zinc", 0),
        ("act two needs a storm", 1),
        ("that serum", 2),                       # the ticked line between them broke the run
    ]


def test_an_item_carries_its_parts_through_json():
    lead = filer.Item("the stack today:", "the stack today:", 3, workspace.DUMP, "Notron",
                      parts=(filer.Item("magnesium", "magnesium", 4, workspace.DUMP, "Notron"),))
    back = filer.Item.from_dict(lead.as_dict())
    assert back == lead
    assert back.parts[0].text == "magnesium"
    assert lead.digest() == filer.Item("the stack today:", "x", 0, "y", "z").digest(), \
        "a digest is the line's own words — parts and position do not change it"


def test_a_mark_ticks_the_line_and_adds_the_receipt_inside_its_own_element():
    html = "<div><h1>x</h1></div>\n<div>magnesium at night</div>\n<div><br></div>"
    line = notedoc.find_line(html, "magnesium at night", near=1)
    out = notedoc.mark(html, line, suffix=" → Supplements")
    assert out == "<div><h1>x</h1></div>\n<div>✓ magnesium at night → Supplements</div>\n<div><br></div>"


def test_a_list_item_is_ticked_in_place():
    html = "<ul><li>a</li><li>b &amp; c</li></ul>"
    line = notedoc.find_line(html, "b & c", near=0)
    assert notedoc.mark(html, line, suffix=" → Q&A") == "<ul><li>a</li><li>✓ b &amp; c → Q&amp;A</li></ul>"


def test_a_trailing_break_stays_after_the_receipt():
    """Or the receipt renders on a line of its own, under the wrong text."""
    html = "<div>idea<br></div>"
    line = notedoc.lines(html)[0]
    assert notedoc.mark(html, line, suffix=" → Ideas") == "<div>✓ idea → Ideas<br></div>"


def test_the_checker_accepts_exactly_a_tick_and_a_receipt():
    old = "<div>a</div>\n<ul><li>b</li></ul>"
    new = "<div>✓ a → X</div>\n<ul><li>✓ b → Y</li></ul>"
    assert notedoc.marks_between(old, new) == ["✓ ", " → X", "✓ ", " → Y"]


@pytest.mark.parametrize("new", [
    "<div>✓ a BETTER → X</div>",       # a word slipped into the sentence
    "<div>✓ → X</div>",                # the user's text is gone
    "<div>a ✓ → X</div>",              # tick mid-sentence, not after the tag
    "<div>✓ a → X</div><div>new</div>",  # a whole new block
    "<div>a</div>",                    # nothing changed at all
    "<div>✓ a</div><div>b</div>",      # a block lost (old had two)
])
def test_the_checker_refuses_anything_that_is_not_a_mark(new):
    old = "<div>a</div><div>b</div>" if "lost" in new or new == "<div>✓ a</div><div>b</div>" else "<div>a</div>"
    if new == "<div>✓ a</div><div>b</div>":
        old = "<div>a</div><div>b</div><div>c</div>"
    assert notedoc.marks_between(old, new) is None


def test_the_guard_lets_a_mark_through_but_nothing_else_in_a_note_you_own():
    old = "<div><h1>Book idea</h1></div><div>a lighthouse</div>"
    ok = guard.check(folder="Notes", title="Book idea", old_body=old,
                     new_body="<div><h1>Book idea</h1></div><div>✓ a lighthouse → Ideas</div>", mode="mark")
    assert ok
    bad = guard.check(folder="Notes", title="Book idea", old_body=old,
                      new_body="<div><h1>Book idea</h1></div><div>✓ a better lighthouse → Ideas</div>", mode="mark")
    assert not bad and "refused" in bad.reason


def test_the_instruction_note_can_never_be_ticked():
    v = guard.check(folder=workspace.FOLDER, title=workspace.ABOUT, old_body="<div>a</div>",
                    new_body="<div>✓ a → X</div>", mode="mark")
    assert not v


def test_a_receipt_may_not_carry_a_secret():
    v = guard.check(folder="Notes", title="t", old_body="<div>a</div>",
                    new_body="<div>✓ a → password: hunter22</div>", mode="mark")
    assert not v and "password" in v.reason


# --------------------------------------------------------------- executor

def test_the_executor_finds_a_line_by_its_words_after_the_note_shifted(store):
    """The user added a line above while she was thinking. The block index is
    stale; the words are not."""
    nid = store.add("Book idea", "a lighthouse\nact two is weak")
    ex = ex_mod.Executor()
    # the note grows above the target between planning and writing
    store.rows[nid]["body"] = markup.render("Book idea", "NEW LINE\na lighthouse\nact two is weak")
    r = ex.mark("Book idea", [("act two is weak", 2, " → Writing")], folder="Notes")
    assert r.ok
    assert "✓ act two is weak → Writing" in store.text("Book idea")
    assert "NEW LINE" in store.text("Book idea")


def test_a_line_that_is_gone_is_left_alone_not_guessed_at(store):
    store.add("Book idea", "a lighthouse")
    r = ex_mod.Executor().mark("Book idea", [("something else", 1, " → X")], folder="Notes")
    assert not r.ok and "changed or gone" in r.reason
    assert store.writes == []


def test_a_mark_is_skipped_if_the_note_moved_between_read_and_write(monkeypatch):
    old = "<div><h1>t</h1></div><div>a</div>"
    live = "<div><h1>t</h1></div><div>a</div><div>b</div>"
    reads = [old, live]
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda f, t: Note("n1", "t", "Notes", "x"))
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda i: reads.pop(0))
    written = []
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda i, b: written.append(b))
    r = ex_mod.Executor(audit=False).mark("t", [("a", 1, " → X")], folder="Notes")
    assert not r.ok and written == []


# -------------------------------------------------------- reading the dump

def test_unfiled_skips_the_header_ticked_lines_her_own_turns_and_secrets():
    body = markup.render(workspace.DUMP, SEED + "\n"
                         "magnesium at night\n"
                         "✓ vitamin D → Supplements\n"
                         "netflix password: hunter22\n"
                         + conversation.turn("Want a “Skincare” note?") +
                         "\n- app idea: sleep tracker\n- file: call mum\n")
    items = filer.unfiled(body)
    assert [it.text for it in items] == ["magnesium at night", "app idea: sleep tracker", "call mum"]
    assert items[2].anchor == "file: call mum", "the anchor is the line as typed, for ticking"


def test_a_thought_that_starts_with_sort_is_not_mistaken_for_an_instruction():
    body = markup.render(workspace.DUMP, SEED + "\nsort out the garage\n")
    assert [it.text for it in filer.unfiled(body)] == ["sort out the garage"]


def test_a_bare_tag_files_the_lines_under_it():
    items, bare = filer.items_from_turn("@notron file these\nmagnesium at night\n• app idea",
                                        title="Scratch", folder="Notes", near=4)
    assert [it.text for it in items] == ["magnesium at night", "app idea"]
    assert [it.anchor for it in items] == ["magnesium at night", "app idea"]
    assert bare == ["@notron file these"]


def test_a_tag_with_words_files_that_line_only_and_the_rest_is_context():
    items, bare = filer.items_from_turn("random thoughts about the week\n@notron file magnesium at night",
                                        title="Scratch", folder="Notes", near=4)
    assert [it.text for it in items] == ["magnesium at night"]
    assert bare == []


def test_in_the_ask_note_nothing_is_tagged_and_every_line_is_the_request():
    items, bare = filer.items_from_turn("file these\nmagnesium at night", title=workspace.ASK,
                                        folder=workspace.FOLDER, near=2)
    assert [it.text for it in items] == ["magnesium at night"] and bare == ["file these"]


# ------------------------------------------------------------- the model

def test_a_title_the_model_invented_becomes_a_proposal_never_a_write():
    masters = [filer.Master("Supplements", "Notes", "magnesium 400mg"), filer.Master("Book idea", "Notes"),
               filer.Master("Book", "Notes")]
    items = [filer.Item("magnesium", "magnesium", 1, "d", "f"),
             filer.Item("lighthouse", "lighthouse", 2, "d", "f"),
             filer.Item("serum", "serum", 3, "d", "f"),
             filer.Item("oats", "oats", 4, "d", "f"),
             filer.Item("act two", "act two", 5, "d", "f")]
    brain = FilerBrain({"magnesium": {"note": "supplements"},                    # case slips are fine
                        "lighthouse": {"note": "Book Ideas"},                    # not on the list
                        "serum": {"new": "Skincare Brand."},
                        "oats": {"note": "Supplements: magnesium 400mg"},        # Nano pads with the glimpse
                        "act two": {"note": '"Book idea" — a lighthouse'}})      # longest title wins
    v, shapes = filer.classify(brain, items, masters)
    assert v == [("note", "Supplements"), ("new", "Book Ideas"), ("new", "Skincare Brand"),
                 ("note", "Supplements"), ("note", "Book idea")]
    assert ("json", filer.TIER) not in [] and brain.calls == 1


def test_the_model_sees_a_blank_line_between_runs():
    items = [filer.Item("a", "a", 0, "d", "f", run=0), filer.Item("b", "b", 1, "d", "f", run=0),
             filer.Item("c", "c", 3, "d", "f", run=1)]
    assert filer._prompt(items, []).endswith("# Lines to file\n1. a\n2. b\n\n3. c")


def test_a_part_points_at_an_earlier_line_in_the_same_run_or_it_is_ignored():
    items = [filer.Item("the stack today:", "x", 0, "d", "f", run=0),
             filer.Item("magnesium", "x", 1, "d", "f", run=0),
             filer.Item("zinc", "x", 2, "d", "f", run=0),
             filer.Item("act two needs a storm", "x", 4, "d", "f", run=1),
             filer.Item("a lighthouse", "x", 5, "d", "f", run=1)]
    brain = FilerBrain({"the stack today:": {"note": "Supps"},
                        "magnesium": {"part_of": "the stack today:"},
                        "zinc": {"part_of": "magnesium"},                 # a part of a part → the lead
                        "act two needs a storm": {"part_of": "zinc"},      # crosses a run → ignored
                        "a lighthouse": {"part_of": "a lighthouse"}},     # points at itself → ignored
                       shapes={"Supps": "log", "Nonsense": "list"})
    verdicts, shapes = filer.classify(brain, items, [filer.Master("Supps", "Notes")])
    assert verdicts == [("note", "Supps"), ("part", "0"), ("part", "0"), None, None]
    assert shapes == {"Supps": "log", "Nonsense": "list"}


def test_secret_looking_lines_never_reach_the_model_and_glimpses_are_redacted(monkeypatch, tmp_path):
    from notron import notes, index, library
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")   # never the developer's own choices
    monkeypatch.setattr(notes, "list_all_notes", lambda: [
        Note("n1", "Supplements", "Notes", "x"), Note("n2", "Passwords", "Notes", "x"),
        Note("n3", "Journal", "Notes", "x"), Note("n4", workspace.ASK, workspace.FOLDER, "x")])
    monkeypatch.setattr(index, "glimpses", lambda chars=100, **kw: {"n1": "api key sk-abcdefghijklmnopqrstuvwxyz1234 daily"})
    ms = filer.masters()
    assert [m.title for m in ms] == ["Supplements"]
    assert "sk-abc" not in ms[0].glimpse and "[redacted]" in ms[0].glimpse


# ------------------------------------------------------------ whole passes

def test_a_dump_pass_copies_each_line_into_its_note_and_ticks_it_with_a_receipt(store):
    store.add("Supplements", "magnesium 400mg before bed")
    store.add("Book idea", "a lighthouse keeper")
    dump(store, "took vitamin D today\nact two needs a storm\n")
    brain = FilerBrain({"took vitamin D today": {"note": "Supplements"},
                        "act two needs a storm": {"note": "Book idea"}})

    out = filer.run(brain)

    assert [(it.text, t) for it, t in out.filed] == [("took vitamin D today", "Supplements"),
                                                     ("act two needs a storm", "Book idea")]
    assert "took vitamin D today" in store.text("Supplements")
    assert "magnesium 400mg before bed" in store.text("Supplements"), "nothing of theirs was lost"
    d = store.text(workspace.DUMP)
    assert "✓ took vitamin D today → Supplements" in d
    assert "✓ act two needs a storm → Book idea" in d
    assert "Filed 2 thoughts" in out.summary()


STACK_DUMP = ("The stack did really well today once the trazodone wore off.\n"
              "Concerta 36mg\nAvmacol\nPQQ\n\ntook vitamin D today\n")
STACK_BRAIN = {"The stack did really well today once the trazodone wore off.": {"note": "Supps"},
               "Concerta 36mg": {"part_of": "The stack did really well today once the trazodone wore off."},
               "Avmacol": {"part_of": "The stack did really well today once the trazodone wore off."},
               "PQQ": {"part_of": "The stack did really well today once the trazodone wore off."},
               "took vitamin D today": {"note": "Supps"}}


def test_a_sentence_and_the_list_under_it_file_as_one_thought_in_journal_shape(store, monkeypatch):
    """The live failure of 2026-09-02: ten dated bullets where one entry belonged."""
    store.add("Supps", "Magnesium\nSeriphos\n")
    dump(store, STACK_DUMP)
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain(STACK_BRAIN, shapes={"Supps": "log"})

    out = filer.run(brain)

    assert [(it.text[:9], [p.text for p in it.parts], t) for it, t in out.filed] == [
        ("The stack", ["Concerta 36mg", "Avmacol", "PQQ"], "Supps"),
        ("took vita", [], "Supps")]
    assert "Filed 2 thoughts → Supps (2)." in out.summary()
    supps = store.text("Supps")
    assert supps == ("Supps\n\nMagnesium\nSeriphos\n\nWed 2 Sep 2026\n"
                     "The stack did really well today once the trazodone wore off.\n"
                     "• Concerta 36mg\n• Avmacol\n• PQQ\n\ntook vitamin D today")
    d = store.text(workspace.DUMP)
    assert "✓ The stack did really well today once the trazodone wore off. → Supps" in d
    assert "✓ Concerta 36mg\n✓ Avmacol\n✓ PQQ" in d, "the lines under it get a tick and no receipt"
    assert "✓ took vitamin D today → Supps" in d
    assert brain.calls == 1


def test_a_second_pass_the_same_day_joins_the_entry_already_there(store, monkeypatch):
    store.add("Supps", "-")
    dump(store, "took vitamin D today\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    filer.run(FilerBrain({"took vitamin D today": {"note": "Supps"}}, shapes={"Supps": "log"}))
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>zinc at lunch</div>"

    filer.run(FilerBrain({"zinc at lunch": {"note": "Supps"}}, shapes={"Supps": "list"}))

    assert store.text("Supps").count("Wed 2 Sep 2026") == 1
    assert store.text("Supps").endswith("Wed 2 Sep 2026\ntook vitamin D today\n\nzinc at lunch")
    assert filer._state()["shapes"] == {"Supps": "log"}, "the first shape sticks; a later 'list' is ignored"


def test_a_list_shaped_note_gets_bullets_and_no_date(store, monkeypatch):
    store.add("Recipes", "-")
    dump(store, "Pasta that worked:\ntomatoes\nbasil\n\nthat serum from the pop-up\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain({"Pasta that worked:": {"note": "Recipes"},
                        "tomatoes": {"part_of": "Pasta that worked:"},
                        "basil": {"part_of": "Pasta that worked:"},
                        "that serum from the pop-up": {"note": "Recipes"}},
                       shapes={"Recipes": "list"})
    filer.run(brain)
    assert store.text("Recipes") == "Recipes\n\n-\n\nPasta that worked:\n• tomatoes\n• basil\n\n• that serum from the pop-up"


def test_a_part_is_remembered_so_a_replay_regroups_without_the_model(store, monkeypatch):
    """The append is refused once (the note moved); next pass the group is
    still one thought, and the model is not asked about the parts again."""
    store.add("Supps", "-")
    dump(store, STACK_DUMP)
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain(STACK_BRAIN, shapes={"Supps": "log"})
    refuse = {"on": True}
    monkeypatch.setattr(guard, "check", lambda **kw: guard.Verdict(False, "moved")
                        if (refuse["on"] and kw["mode"] == "append") else guard.ALLOW)
    out = filer.run(brain)
    assert out.filed == [] and "✓" not in store.text(workspace.DUMP)

    refuse["on"] = False
    out = filer.run(brain)
    assert brain.calls == 2, "the leads are judged again (a refused copy is never remembered) …"
    assert "Concerta" not in brain.prompts[1], "… but the lines under them are not: the group came from memory"
    assert [[p.text for p in it.parts] for it, _ in out.filed] == [["Concerta 36mg", "Avmacol", "PQQ"], []]
    assert "• Concerta 36mg\n• Avmacol\n• PQQ" in store.text("Supps")


def test_a_two_level_chain_across_passes_does_not_drop_the_bottom_line(store, monkeypatch):
    """A part remembered from one pass (Concerta under "the stack today:")
    and a part judged fresh this pass ("the stack today:" under a new lead
    typed above it) used to chain two deep and lose the bottom line: the new
    lead folded only its direct part, and the middle line's own fold — the
    one line carrying Concerta — sat at an index nothing downstream reads,
    because its verdict this pass is "part", not "note". Concerta ended up
    neither filed nor ticked nor reported, and `judged` still called it done."""
    store.add("Supps", "-")
    dump(store, "the stack today:\nConcerta 36mg\n")
    refuse = {"on": True}
    monkeypatch.setattr(guard, "check", lambda **kw: guard.Verdict(False, "moved")
                        if (refuse["on"] and kw["mode"] == "append") else guard.ALLOW)
    filer.run(FilerBrain({"the stack today:": {"note": "Supps"},
                         "Concerta 36mg": {"part_of": "the stack today:"}}))
    assert "✓" not in store.text(workspace.DUMP), "the refusal left everything unticked"

    refuse["on"] = False
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] = markup.render(
        workspace.DUMP, SEED + "\nact two needs a storm\nthe stack today:\nConcerta 36mg\n")

    out = filer.run(FilerBrain({"act two needs a storm": {"note": "Supps"},
                                "the stack today:": {"part_of": "act two needs a storm"}}))

    assert [(it.text, [p.text for p in it.parts]) for it, _ in out.filed] == [
        ("act two needs a storm", ["the stack today:", "Concerta 36mg"])]
    assert "act two needs a storm\n• the stack today:\n• Concerta 36mg" in store.text("Supps")
    d = store.text(workspace.DUMP)
    assert "✓ act two needs a storm → Supps\n✓ the stack today:\n✓ Concerta 36mg" in d


def test_nothing_is_ever_deleted_from_the_dump(store):
    store.add("Supplements", "-")
    dump(store, "took vitamin D today\nsomething the model ignores\n")
    before = store.text(workspace.DUMP)
    filer.run(FilerBrain({"took vitamin D today": {"note": "Supplements"}}))
    after = store.text(workspace.DUMP)
    for line in before.split("\n"):
        assert line.strip() in after, f"lost: {line!r}"


def test_the_tick_is_only_written_after_the_copy_landed(store, monkeypatch):
    """Filing is copy-then-tick. If the copy is refused, the line stays unticked
    — a tick with nothing behind it would be a lie."""
    store.add("Supplements", "-")
    dump(store, "took vitamin D today\n")
    monkeypatch.setattr(guard, "check", lambda **kw: guard.Verdict(False, "blocked for the test")
                        if kw["mode"] == "append" else guard.ALLOW)
    out = filer.run(FilerBrain({"took vitamin D today": {"note": "Supplements"}}))
    assert out.filed == []
    assert "✓" not in store.text(workspace.DUMP)
    assert out.left and "blocked" in out.left[0][1]


def test_no_fitting_note_means_a_question_in_the_dump_not_a_guess(store):
    store.add("Supplements", "-")
    dump(store, "the serum from that brand\nthe cleanser too\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"},
                        "the cleanser too": {"new": "Skincare Brand"}})
    out = filer.run(brain)

    assert out.filed == [] and list(out.proposed) == ["Skincare Brand"]
    assert store.find_note("Notes", "Skincare Brand") is None, "nothing created without a yes"
    d = store.text(workspace.DUMP)
    assert "Notron:" in d and "Skincare Brand" in d and "yes" in d
    assert "✓" not in d, "unfiled lines stay untouched"
    assert filer.pending_proposals()["Skincare Brand"][0].text == "the serum from that brand"


def test_a_proposal_waiting_on_you_costs_no_second_model_call(store):
    store.add("Supplements", "-")
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)
    assert brain.calls == 1
    assert not filer.worth_a_pass(store.body(workspace.DUMP)), "the listener must not wake her"
    filer.run(brain)
    assert brain.calls == 1
    assert store.text(workspace.DUMP).count("Skincare Brand") == 1, "asked once, not every pass"


def test_yes_creates_the_note_files_the_lines_and_ticks_the_yes(store):
    store.add("Supplements", "-")
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"
    assert filer.worth_a_pass(store.body(workspace.DUMP))

    out = filer.run(brain)

    assert out.created == ["Skincare Brand"]
    assert "the serum from that brand" in store.text("Skincare Brand")
    assert store.find_note(filer.FILING_FOLDER, "Skincare Brand") is not None
    assert store.text("Skincare Brand").count("the serum from that brand") == 1, "filed once — see Task 6"
    assert [t for _, t in out.filed] == ["Skincare Brand"]
    d = store.text(workspace.DUMP)
    assert "✓ the serum from that brand → Skincare Brand" in d
    assert "✓ yes → made “Skincare Brand”, 1 filed" in d
    assert filer.pending_proposals() == {}
    assert brain.calls == 1, "a yes needs no model"


def test_a_note_she_makes_starts_in_shape_with_the_whole_thought(store, monkeypatch):
    """Becky never pre-builds a note. When Notron makes one it must already
    read like a journal: title, today's date, the sentence, the list under it."""
    store.add("Supplements", "-")
    dump(store, "Pasta that worked:\ntomatoes\nbasil\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain({"Pasta that worked:": {"new": "Recipes"},
                        "tomatoes": {"part_of": "Pasta that worked:"},
                        "basil": {"part_of": "Pasta that worked:"}},
                       shapes={"Recipes": "list"})
    filer.run(brain)
    assert list(filer.pending_proposals()) == ["Recipes"]
    assert [p.text for p in filer.pending_proposals()["Recipes"][0].parts] == ["tomatoes", "basil"]
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"

    out = filer.run(brain)

    assert out.created == ["Recipes"]
    assert store.text("Recipes") == "Recipes\n\nPasta that worked:\n• tomatoes\n• basil"
    d = store.text(workspace.DUMP)
    assert "✓ Pasta that worked: → Recipes\n✓ tomatoes\n✓ basil" in d
    assert "✓ yes → made “Recipes”, 1 filed" in d
    assert filer._state()["shapes"] == {"Recipes": "list"}
    assert brain.calls == 1
    assert store.text("Recipes").count("Pasta that worked:") == 1, "a yes files the thought once, not twice"


def test_no_leaves_the_lines_alone_and_stops_her_asking_again(store):
    store.add("Supplements", "-")
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>no thanks</div>"

    out = filer.run(brain)
    assert out.declined == ["Skincare Brand"]
    assert "✓ no thanks → left “Skincare Brand” alone" in store.text(workspace.DUMP)
    assert "✓ the serum" not in store.text(workspace.DUMP)

    out = filer.run(brain)
    assert brain.calls == 1 and out.proposed == {}, "declined lines are not re-proposed"


def test_a_stale_judged_note_does_not_hide_an_unrelated_line_in_landed(store):
    """A digest carrying a stale {kind: note, title: T} — from a note T that
    no longer exists as a master — must not be swept into `landed` just
    because a *different* proposal this pass happens to create a new note
    also called T."""
    store.add("Supplements", "-")
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)                       # proposes "Skincare Brand", still pending

    stale_digest = filer.Item("vitamin C", "vitamin C", 0, "x", "x").digest()
    state = filer._state()
    state["judged"][stale_digest] = {"kind": "note", "title": "Skincare Brand"}
    filer._save(state, dry_run=False)

    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>vitamin C</div><div>yes</div>"

    calls_before = brain.calls
    out = filer.run(brain)

    assert out.created == ["Skincare Brand"]
    assert "✓ vitamin C → Skincare Brand" in store.text(workspace.DUMP), \
        "an unrelated line must not be silently dropped by a stale digest"
    assert brain.calls == calls_before, "vitamin C resolves from memory, no model call"


def test_a_shape_reply_keyed_differently_from_the_canonical_title_still_resolves(store, monkeypatch):
    """`classify`'s shapes reply must be resolved through the same `_match`
    used for note verdicts before caching — a case mismatch used to fall back
    to layout.LOG and cache that wrong guess forever."""
    store.add("Recipes", "-")
    dump(store, "Pasta that worked:\ntomatoes\nbasil\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain({"Pasta that worked:": {"note": "Recipes"},
                        "tomatoes": {"part_of": "Pasta that worked:"},
                        "basil": {"part_of": "Pasta that worked:"}},
                       shapes={"recipes": "list"})   # model echoed lowercase

    filer.run(brain)

    assert filer._state()["shapes"] == {"Recipes": "list"}
    assert store.text("Recipes") == "Recipes\n\n-\n\nPasta that worked:\n• tomatoes\n• basil"


def test_two_leads_with_the_same_text_do_not_double_record_by_dict_equality(store, monkeypatch):
    """`record["items"]` used to dedup by full dict equality, which includes
    `parts` — so two leads that share a digest (identical wording, typed as
    two separate runs in the dump) but carry different parts would both get
    appended instead of the later one replacing the earlier. Same root cause
    as an edited part between two passes: the lead's digest is unchanged but
    its parts differ, and a `not in` dict check can't see they're the same
    lead."""
    store.add("Supplements", "-")
    dump(store, "Weekly meal plan:\nchicken\n\nWeekly meal plan:\nsalmon\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))

    class TwiceBrain:
        """Two runs, same lead text, different part each — hand-built rows
        rather than FilerBrain's text-keyed table, which can't represent two
        prompt lines sharing identical text."""

        def ask_json(self, **kw):
            return {"filed": [{"line": 1, "new": "Meals"}, {"line": 2, "part_of": 1},
                              {"line": 3, "new": "Meals"}, {"line": 4, "part_of": 3}],
                    "shapes": {}}

        def ask(self, **kw):
            raise AssertionError("the Filer must never pay for a text model call")

    brain = TwiceBrain()
    filer.run(brain)

    assert len(filer.pending_proposals()["Meals"]) == 1, "same digest must replace, not add"
    assert [p.text for p in filer.pending_proposals()["Meals"][0].parts] == ["salmon"]

    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"
    out = filer.run(brain)

    assert out.created == ["Meals"]
    assert store.text("Meals").count("Weekly meal plan:") == 1, "must not be filed twice"


def test_a_dry_run_judges_everything_and_writes_nothing(store):
    store.add("Supplements", "-")
    dump(store, "took vitamin D today\n")
    out = filer.run(FilerBrain({"took vitamin D today": {"note": "Supplements"}}), dry_run=True)
    assert store.writes == []
    assert not filer.STATE.exists()
    assert "Filed 1 thought" in out.summary()


def test_a_tagged_line_is_filed_where_it_sits(store):
    store.add("Supplements", "-")
    nid = store.add("Scratch", "random thoughts\n@notron file this: took vitamin D today")
    items, bare = filer.items_from_turn("@notron file this: took vitamin D today",
                                        title="Scratch", folder="Notes", near=2)
    out = filer.file_items(FilerBrain({"took vitamin D today": {"note": "Supplements"}}), items, bare=bare)
    assert "took vitamin D today" in store.text("Supplements")
    assert "✓ @notron file this: took vitamin D today → Supplements" in store.text("Scratch")
    assert ("Notes", "Scratch") in out.ticked


# ------------------------------------------------------- conversation loop

def test_a_ticked_turn_reads_as_answered_so_she_never_asks_again():
    body = markup.render("Scratch", "random thoughts\n✓ @notron file this: vitamin D → Supplements")
    assert conversation.unanswered(body, ignore=("Scratch",), require_tag=True) == []
    untouched = markup.render("Scratch", "random thoughts\n@notron file this: vitamin D")
    assert len(conversation.unanswered(untouched, ignore=("Scratch",), require_tag=True)) == 1


def test_in_the_ask_note_a_ticked_request_is_answered_by_its_receipt():
    from notron import watch
    body = markup.render(workspace.ASK, "Type anything below this line.\n\n———\n\n"
                                        "✓ file: vitamin D → Supplements")
    assert conversation.unanswered(body, ignore=watch.ASK_FURNITURE) == []


def test_her_proposal_turn_is_her_own_and_not_a_line_to_file():
    body = markup.render(workspace.DUMP, SEED + "\n" + conversation.turn("Want a note?") + "\n")
    assert conversation.unanswered(body, ignore=filer.FURNITURE) == []
    assert filer.unfiled(body) == []


# ------------------------------------------------------------------ graph

def test_file_my_brain_dump_reaches_the_filer_without_asking_the_router(monkeypatch):
    from notron import graph
    seen = {}

    def fake_run(brain, *, dry_run=False, on_step=None):
        seen["ran"] = dry_run
        return filer.Outcome(results=["✓ x — ok"])

    monkeypatch.setattr(filer, "run", fake_run)
    monkeypatch.setattr(nodes, "watcher", lambda state, brain=None: state)
    brain = FilerBrain({})
    state = graph.run("file my brain dump", brain=brain, dry_run=True)
    assert state.intent == "file" and brain.calls == 0
    assert seen == {"ran": True}
    assert [w.title for w in state.writes] == [workspace.ASK], "the Ask note still gets a reply"
    assert state.answer == "Nothing to file."


@pytest.mark.parametrize("said,files", [
    ("file this: magnesium", True), ("File my brain dump", True), ("sort these", True),
    ("a file I found yesterday", False), ("sort out my week", False), ("what's on today?", False),
])
def test_only_a_plain_filing_verb_is_routed_in_code(said, files):
    assert bool(nodes.FILE_WORDS.search(said)) is files


def test_a_tagged_filing_that_ticked_every_line_needs_no_extra_reply(monkeypatch):
    from notron.state import State

    def fake_file_items(brain, items, *, bare=(), dry_run=False, on_step=None):
        out = filer.Outcome(filed=[(items[0], "Supplements")], results=["✓ Scratch — written"])
        out.ticked.add(("Notes", "Scratch"))
        return out

    monkeypatch.setattr(filer, "file_items", fake_file_items)
    state = State(request="file this: vitamin D", intent="file", reply_to=("Scratch", "Notes", 3),
                  source="@notron file this: vitamin D")
    state = nodes.filer(state, brain=FilerBrain({}))
    assert state.writes == [], "the receipt on the line is the reply"


def test_a_tagged_filing_that_could_only_propose_gets_a_word_under_the_line(monkeypatch):
    from notron.state import State

    def fake_file_items(brain, items, *, bare=(), dry_run=False, on_step=None):
        return filer.Outcome(proposed={"Skincare Brand": items}, results=["✓ 🧠 Brain Dump — written"])

    monkeypatch.setattr(filer, "file_items", fake_file_items)
    state = State(request="file this: the serum", intent="file", reply_to=("Scratch", "Notes", 3),
                  source="@notron file this: the serum")
    state = nodes.filer(state, brain=FilerBrain({}))
    assert len(state.writes) == 1 and state.writes[0].mode == "insert"
    assert "Skincare Brand" in state.answer and workspace.DUMP in state.answer


def test_the_graph_declares_the_filer_between_doer_and_writer():
    from notron import graph
    assert graph.ORDER.index("doer") < graph.ORDER.index("filer") < graph.ORDER.index("writer")


# --------------------------------------------------------------- listener

def test_the_listener_files_the_dump_only_once_it_has_gone_quiet(store, monkeypatch):
    from notron import watch
    store.add("Supplements", "-")
    dump(store, "took vitamin D today\n")
    ran = []
    monkeypatch.setattr(filer, "run", lambda brain, on_step=None, **kw: ran.append(1) or filer.Outcome())

    w = watch.Watcher(brain=None, dump_settle=10, on_event=lambda m: None)
    w.check_dump()
    assert ran == [], "first sight starts the clock"
    w.check_dump()
    assert ran == [], "still inside the quiet window"

    w.dump_settle = 0
    w.check_dump()
    assert ran == [1], "quiet long enough — filed"


def test_a_dump_with_nothing_new_never_wakes_her(store, monkeypatch):
    from notron import watch
    dump(store, "✓ done → Supplements\n")
    ran = []
    monkeypatch.setattr(filer, "run", lambda brain, on_step=None, **kw: ran.append(1))
    w = watch.Watcher(brain=None, dump_settle=0, on_event=lambda m: None)
    w.check_dump()
    w.check_dump()
    assert ran == []


def test_a_new_line_in_the_dump_restarts_the_quiet_clock(store, monkeypatch):
    from notron import watch
    store.add("Supplements", "-")
    nid = dump(store, "took vitamin D today\n")
    ran = []
    monkeypatch.setattr(filer, "run", lambda brain, on_step=None, **kw: ran.append(1) or filer.Outcome())
    w = watch.Watcher(brain=None, dump_settle=10, on_event=lambda m: None)
    w.check_dump()
    store.rows[nid]["body"] += "<div>and magnesium</div>"
    w.check_dump()
    assert w._pending["dump"][0].endswith("and magnesium"), "the clock now runs on the new text"
    assert ran == []


# ---------------------------------------------------------------- surface

def test_setup_creates_the_dump_note_and_the_guard_treats_it_as_shared():
    assert workspace.DUMP in workspace.SYSTEM_NOTES
    assert workspace.DUMP in workspace.SHARED
    v = guard.check(folder=workspace.FOLDER, title=workspace.DUMP,
                    old_body="<div>x</div>", new_body="<div>rewritten</div>", mode="replace")
    assert not v, "Notron may tick and append in the dump, never rewrite it"


def test_the_cli_has_a_file_command():
    from notron import cli
    import argparse
    captured = {}
    cli.cmd_file = lambda args: captured.update(vars(args))
    try:
        cli.main(["file", "--dry-run"])
    finally:
        pass
    assert captured.get("dry_run") is True


def test_with_homes_chosen_only_homes_are_destinations(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    sup = store.add("Supps", "-")
    store.add("Track which supplements actually improve your sleep…", "App Store listing")
    library.save(library.Library(homes={sup}))
    titles = [m.title for m in filer.masters()]
    assert titles == ["Supps"], "the listing note is readable, but never a place to file"


def test_with_no_homes_chosen_every_readable_note_is_still_a_candidate(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    store.add("Supps", "-")
    store.add("Groceries", "-")
    assert sorted(m.title for m in filer.masters()) == ["Groceries", "Supps"]


def test_a_note_made_after_a_yes_joins_the_homes(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    sup = store.add("Supps", "-")
    library.save(library.Library(homes={sup}))
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"
    filer.run(brain)
    made = store.find_note(filer.FILING_FOLDER, "Skincare Brand").id
    assert made in library.load().homes
