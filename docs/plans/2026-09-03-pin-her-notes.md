# Pin her notes — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an onboarding step (and a permanently reachable window) that names the
notes in 🤖 NOTRON worth pinning in Apple Notes, suggests three, offers all nine, and
opens any of them in Notes with one click — so the notes the user lives in never get
buried under a decade of old ones.

**Architecture:** Apple's Notes scripting has **no pin.** Verified 2026-09-03 against
`sdef /System/Applications/Notes.app`: the `note` class exposes `name`, `id`,
`container`, `body`, `plaintext`, `creation date`, `modification date`,
`password protected`, `shared` — and nothing else. No Shortcuts action either. The
only writable pin state is `ZISPINNED` inside the TCC-protected iCloud SQLite store,
which this project will not touch. So the whole feature is: **name the notes, say why,
open the right one in Notes, and let the user Control-click → Pin Note.** The screen
never claims to have pinned anything, because it cannot know.

Core side is a static taxonomy in `workspace.py` (it already owns every system-note
constant) joined to live note ids at call time, exposed as `notron pins [--json]`.
Mac side is a fifth `OnboardingStep`, placed **after** "Start listening" so the two
must-do steps (permissions, listener) complete before the user is sent out of the app,
plus a standalone window so skipping is not permanent. Opening a row reuses the
existing `notron library open <id>` → `notes.show_note` path — no new AppleScript.

**Tech Stack:** Python 3.12 + pytest (core), SwiftUI / macOS 14 (`mac/`), the existing
`Core.run` subprocess bridge, `DS` design tokens.

---

## Decisions already made (do not re-litigate)

1. **Placement:** new step 5, between "Start listening" and the handoff to "Your
   notes". Rationale matches CLAUDE.md's decision 3 for the rewrite-default sheet —
   screens that prevent real damage come first; findability is a nice-to-have.
2. **No fake confirmation.** No checkbox, no "Pinned ✓". A row that has been opened
   dims and its button reads "Show again" — true state (we did open it), no claim
   about the pin.
3. **Three suggested, nine available.** 📌 About Me · 📥 Ask Notron · 🧠 Brain Dump are
   always visible. A "Her other notes" disclosure reveals ☀️ Today · 🗓️ This Week ·
   🌱 Take Care of Notron · 🧠 Memory · 📖 Lessons · 📊 Log, each equally openable.
4. **Reachable forever.** A `pins` window + menu-bar item, and `notron pins` in the
   terminal, so a skip on day one is recoverable on day 200.
5. **No new persisted state.** Which rows were opened is in-memory only. Onboarding
   completion already lives in `.notron/onboarding.json`.

---

### Task 1: Core — the pin taxonomy in `workspace.py`

**Files:**
- Modify: `notron/workspace.py`
- Test: `tests/test_workspace.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_workspace.py`:

```python
"""The pin guide — what the onboarding screen offers the user to pin.

Apple's Notes scripting has no `pinned` property (checked 2026-09-03 against
the Notes.app sdef), so nothing here pins anything. It names notes and finds
their ids; a human does the Control-clicking.
"""

from notron import notes, workspace


def fake(title, id="x"):
    return notes.Note(id=id, title=title, folder=workspace.FOLDER,
                      modified="Monday, 1 September 2026 at 09:00:00")


def test_every_system_note_is_offered_and_has_a_why():
    # A note bootstrap creates but the guide forgets is a note the user can
    # never be told to pin — keep the three lists locked together.
    assert set(workspace.PIN_ORDER) == set(workspace.SYSTEM_NOTES)
    assert set(workspace.PIN_WHY) == set(workspace.SYSTEM_NOTES)


def test_the_three_suggested_are_the_ones_the_user_types_in():
    assert workspace.PIN_SUGGESTED == (workspace.ABOUT, workspace.ASK, workspace.DUMP)
    assert workspace.PIN_ORDER[:3] == workspace.PIN_SUGGESTED


def test_pin_guide_carries_live_ids_and_marks_the_suggested(monkeypatch):
    monkeypatch.setattr(notes, "list_notes",
                        lambda folder: [fake(t, f"id-{i}")
                                        for i, t in enumerate(workspace.SYSTEM_NOTES)])
    rows = workspace.pin_guide()
    assert [r["title"] for r in rows] == list(workspace.PIN_ORDER)
    assert all(r["id"] for r in rows)
    assert [r["suggested"] for r in rows[:3]] == [True, True, True]
    assert not any(r["suggested"] for r in rows[3:])
    assert all(r["why"] for r in rows)


def test_a_note_bootstrap_has_not_made_yet_is_left_out(monkeypatch):
    # A row with no id is a row whose "Show in Notes" button does nothing.
    # Better absent than dead.
    monkeypatch.setattr(notes, "list_notes",
                        lambda folder: [fake(workspace.ASK, "id-ask")])
    rows = workspace.pin_guide()
    assert [r["title"] for r in rows] == [workspace.ASK]
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_workspace.py -q`
Expected: FAIL — `AttributeError: module 'notron.workspace' has no attribute 'PIN_ORDER'`

**Step 3: Implement**

Append to `notron/workspace.py`, after `SYSTEM_NOTES`:

```python
#: The three she is used through, every day — the ones worth pinning first.
PIN_SUGGESTED = (ABOUT, ASK, DUMP)

#: Every system note, suggested first, then in the order the user meets them.
PIN_ORDER = (ABOUT, ASK, DUMP, TODAY, WEEK, CARE, MEMORY, LESSONS, LOG)

#: One plain line per note, for the screen that asks the user to pin it.
PIN_WHY: dict[str, str] = {
    ABOUT: "Your instruction note. She reads it before everything — you'll edit it often.",
    ASK: "Where you ask her things. The note you'll open most.",
    DUMP: "Throw a thought in and she files it. Only works if it's one click away.",
    TODAY: "What she's lined up for today. Rebuilt every morning.",
    WEEK: "The week ahead. Rebuilt Sunday night.",
    CARE: "What she needs from you to keep working well.",
    MEMORY: "What she's learned about you.",
    LESSONS: "Rules she's taught herself. Delete any you disagree with.",
    LOG: "Everything she's done, newest first.",
}


def pin_guide() -> list[dict]:
    """Her system notes with their live ids, for the screen that asks the user
    to pin them.

    Nothing here pins anything: Apple's Notes scripting has no `pinned`
    property — not in AppleScript, not in Shortcuts — and the only writable pin
    state is `ZISPINNED` in the TCC-protected iCloud SQLite store, which this
    project will not touch. All she can do is name the note and open it.

    A note `bootstrap()` has not created yet is left out rather than offered
    with no id: a row whose one button does nothing is worse than no row.
    """
    from . import notes

    live = {n.title: n.id for n in notes.list_notes(FOLDER)}
    return [{"title": t, "id": live[t], "why": PIN_WHY[t], "suggested": t in PIN_SUGGESTED}
            for t in PIN_ORDER if t in live]
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_workspace.py -q`
Expected: PASS, 4 passed

**Step 5: Commit**

```bash
git add notron/workspace.py tests/test_workspace.py
git commit -m "feat(core): the pin guide — which NOTRON notes are worth pinning, and why"
```

---

### Task 2: Core — `notron pins [--json]`

**Files:**
- Modify: `notron/cli.py` (new `cmd_pins`, new subparser next to `library`)

**Step 1: Write the failing test**

Append to `tests/test_workspace.py`:

```python
def test_the_cli_prints_the_guide_as_json(monkeypatch, capsys):
    import json

    from notron import cli

    monkeypatch.setattr(notes, "list_notes", lambda folder: [fake(workspace.ASK, "id-ask")])
    cli.cmd_pins(type("A", (), {"json": True})())
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"title": workspace.ASK, "id": "id-ask",
                     "why": workspace.PIN_WHY[workspace.ASK], "suggested": True}]
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_workspace.py -q`
Expected: FAIL — `AttributeError: module 'notron.cli' has no attribute 'cmd_pins'`

**Step 3: Implement**

Add to `notron/cli.py`, next to `cmd_library`:

```python
def cmd_pins(args):
    """Which of her notes to pin in Apple Notes, and why.

    She cannot pin them herself — Notes exposes no `pinned` property to any
    script — so this command names them and the user Control-clicks. The
    Mac app reads `--json`; a person reads the plain list.
    """
    import json

    from . import workspace

    rows = workspace.pin_guide()
    if args.json:
        print(json.dumps(rows))
        return

    print("\n  Pin these in Notes and they'll sit above everything else —")
    print("  in her folder and in All iCloud. Control-click a note → Pin Note.\n")
    for row in rows:
        mark = "★" if row["suggested"] else " "
        print(f"  {mark} {row['title']} — {row['why']}")
    print("\n  ★ = start with these three.\n")
```

Register it beside the `library` parser in the same function:

```python
    pn = sub.add_parser("pins", help="which of her notes to pin in Apple Notes")
    pn.add_argument("--json", action="store_true", help="JSON for the Mac app")
    pn.set_defaults(fn=cmd_pins)
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_workspace.py -q && .venv/bin/python -m notron pins`
Expected: 5 passed, then the nine notes printed with ★ on the first three.

**Step 5: Commit**

```bash
git add notron/cli.py tests/test_workspace.py
git commit -m "feat(cli): notron pins — the list of notes worth pinning, plain or --json"
```

---

### Task 3: Design docs — spec the screen before building it

**Files:**
- Modify: `docs/design/DESIGN.md` (append one component variant near line 83)
- Modify: `docs/design/04-onboarding-flow.md` (new screen between §4 and §5; update the
  flow diagram and the Step-dots count from 4 to 5)

**Step 1: DESIGN.md — append after the existing "Example row" entry**

```markdown
**Example row — action variant** (added 2026-09-03, "Keep her notes at the top") —
the same **Example row** with a trailing control on the right edge: a plain-text
`accent` button in `caption`, vertically centered, `space-3` from the text column.
Once used, the whole row drops to `text-dim` and the button relabels — it marks
what the user has already looked at, never a state the app cannot verify.
```

**Step 2: 04-onboarding-flow.md — update the flow diagram**

```
Welcome → Permissions (3 cards) → How to talk to her → Start listening → Pin her notes → [existing] Your notes
```

**Step 3: 04-onboarding-flow.md — insert the new screen spec before "### 5. Handoff"**

```markdown
### 5. Pin her notes (new, 2026-09-03)
**Purpose:** 📌 About Me, 📥 Ask Notron and 🧠 Brain Dump are the notes the user
lives in, and they sit in a folder that will be one of dozens within a year.
Apple Notes pins a note to the top of **every** list that contains it — the
🤖 NOTRON folder *and* All iCloud — which is exactly the problem this solves.
**The constraint that shapes the whole screen:** Notes exposes no `pinned`
property to AppleScript or Shortcuts (checked 2026-09-03 against the sdef), and
the only writable pin state is `ZISPINNED` in the TCC-protected iCloud SQLite
store. Notron can never pin a note, and can never read whether one is pinned.
So the screen instructs and opens; it never confirms.
**Eye lands on:** three **Example rows — action variant**, one per suggested note.
**Content:** "Keep her notes at the top" (`headline`) · "Pinned notes sit above
everything else in Notes — in her folder and in All iCloud. Apple doesn't let
her pin them for you." (`caption`) · three rows, each glyph + name + one line of
why + a "Show in Notes" button · below them a disclosure, "Her other notes (6)",
revealing ☀️ Today, 🗓️ This Week, 🌱 Take Care of Notron, 🧠 Memory, 📖 Lessons,
📊 Log in the same row style · one `text-faint` line: "Notes will come to the
front — Control-click the note in the list, then Pin Note."
**Action:** "Show in Notes" per row runs `notron library open <id>`, which
selects that note in the Notes list — where the Control-click has to happen.
A used row dims and its button becomes "Show again." Primary "Done" → Your
notes; de-emphasized "I'll do this later" is allowed (pure instruction, nothing
left un-granted — same rule as §3 and §4).
**Reachable later:** the same view is a standalone window, opened from the menu
bar ("Pin her notes…"), because a skip on day one should be recoverable on day
200. `notron pins` prints the same list in the terminal.
```

**Step 4: Update the Step-dots line**

In the "## Progress indicator" section, change the dot list to
`(Welcome · Permissions · Talk · Listen · Pin)` and 4 → 5.

**Step 5: Commit**

```bash
git add docs/design/DESIGN.md docs/design/04-onboarding-flow.md
git commit -m "docs(design): spec the 'Pin her notes' onboarding screen"
```

---

### Task 4: Mac — the model side

**Files:**
- Modify: `mac/Sources/Notron/Onboarding.swift`

**Step 1: Add the step case**

```swift
enum OnboardingStep: Int, CaseIterable {
    case welcome, permissions, talk, listening, pins
}
```

**Step 2: Add the row type, above `OnboardingModel`**

```swift
/// One row of `notron pins --json`. Field names already match, like
/// `PermissionCheck` — no key strategy needed.
struct PinNote: Codable, Equatable, Identifiable {
    var id: String { noteID }
    let title: String
    let why: String
    let suggested: Bool
    private let noteID: String

    enum CodingKeys: String, CodingKey {
        case title, why, suggested
        case noteID = "id"
    }

    /// The Notes id, for `library open`. Named apart from `Identifiable.id`
    /// only because SwiftUI wants that name and the core already uses it.
    var notesID: String { noteID }
}
```

**Step 3: Add the model methods to `OnboardingModel`**

```swift
    @Published var pins: [PinNote] = []
    @Published var opened: Set<String> = []
    @Published var opening: String? = nil

    // ------------------------------------------------------------------ pins

    func loadPins() {
        Task.detached { [weak self] in
            guard let json = try? Core.run(["pins", "--json"]),
                  let rows = try? JSONDecoder().decode([PinNote].self, from: Data(json.utf8))
            else { return }
            await MainActor.run { self?.pins = rows }
        }
    }

    /// Brings the note up in Notes and selects it in the list — which is where
    /// the Control-click has to happen, because nothing in Notes' scripting
    /// surface can pin it for us. Off the main thread like every other core
    /// call: this one goes through the AppleScript lock and can queue behind
    /// the listener that the previous step just installed.
    func show(_ note: PinNote) {
        opening = note.notesID
        Task.detached { [weak self] in
            _ = try? Core.run(["library", "open", note.notesID])
            await MainActor.run {
                self?.opened.insert(note.notesID)
                self?.opening = nil
            }
        }
    }
```

**Step 4: Build**

Run: `cd mac && swift build 2>&1 | tail -20`
Expected: `Build complete!` — `OnboardingView.swift` still compiles because the
`switch` over `model.step` is not yet exhaustive… it will fail here with
`switch must be exhaustive`. That failure is the handoff to Task 5; do not
paper over it with a `default:` case.

**Step 5: Commit (after Task 5 builds clean — this task alone does not compile)**

---

### Task 5: Mac — the screen

**Files:**
- Modify: `mac/Sources/Notron/OnboardingView.swift`

**Step 1: Route the new step** — in `OnboardingView.body`'s switch:

```swift
                case .listening:
                    ListeningStep(model: model) { model.step = .pins }
                case .pins:
                    PinStep(model: model, finish: finish)
```

`ListeningStep`'s own "I'll do this later" and its primary now advance rather
than finish — change its `finish` parameter name to `advance` and its primary
button label from "Set up your notes" to "Next".

**Step 2: Give `ExampleRow` an optional trailing action**

```swift
private struct ExampleRow<Trailing: View>: View {
    let glyph: String
    let name: String
    let example: String
    var dimmed: Bool = false
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .center, spacing: DS.Space.s3) {
            Text(glyph).font(DS.Font.headline)
                .foregroundStyle(dimmed ? DS.Color.textDim : DS.Color.text)
                .frame(width: 32, alignment: .leading)
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text(name).font(DS.Font.body)
                    .foregroundStyle(dimmed ? DS.Color.textDim : DS.Color.text)
                Text(example).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            Spacer(minLength: DS.Space.s3)
            trailing()
        }
        .padding(DS.Space.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
        .animation(DS.Motion.standard, value: dimmed)
    }
}

extension ExampleRow where Trailing == EmptyView {
    init(glyph: String, name: String, example: String) {
        self.init(glyph: glyph, name: name, example: example, dimmed: false) { EmptyView() }
    }
}
```

The three existing `TalkStep` rows keep working through that convenience init —
the `HStack` alignment moves from `.top` to `.center`, which is the only visible
change to that screen and is what the action variant needs.

**Step 3: Add the screen**

```swift
// ------------------------------------------------------------- pin her notes

/// Apple Notes pins a note to the top of every list that holds it — her folder
/// *and* All iCloud — which is the whole answer to "these will get buried."
/// But nothing in Notes' scripting surface can set that flag or read it, so
/// this screen names the notes, opens them, and stops. It never says "pinned."
struct PinStep: View {
    @ObservedObject var model: OnboardingModel
    let finish: () -> Void
    @State private var showingOthers = false

    private var suggested: [PinNote] { model.pins.filter(\.suggested) }
    private var others: [PinNote] { model.pins.filter { !$0.suggested } }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text("Keep her notes at the top").font(DS.Font.headline)
                    .foregroundStyle(DS.Color.text)
                Text("Pinned notes sit above everything else in Notes — in her folder and in All iCloud. Apple doesn't let her pin them for you.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }

            ScrollView {
                VStack(spacing: DS.Space.s3) {
                    ForEach(suggested) { PinRow(note: $0, model: model) }
                    if showingOthers {
                        ForEach(others) { PinRow(note: $0, model: model) }
                    }
                    if !others.isEmpty {
                        Button(showingOthers ? "Fewer" : "Her other notes (\(others.count))") {
                            withAnimation(DS.Motion.standard) { showingOthers.toggle() }
                        }
                        .buttonStyle(.plain).font(DS.Font.caption)
                        .foregroundStyle(DS.Color.accent)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }

            Text("Notes comes to the front — Control-click the note in the list, then Pin Note.")
                .font(DS.Font.label).foregroundStyle(DS.Color.textFaint)

            HStack {
                Button("I'll do this later", action: finish)
                    .buttonStyle(.plain).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                Spacer()
                PrimaryButton("Done", action: finish)
            }
        }
        .onAppear { model.loadPins() }
    }
}

private struct PinRow: View {
    let note: PinNote
    @ObservedObject var model: OnboardingModel

    private var used: Bool { model.opened.contains(note.notesID) }

    var body: some View {
        ExampleRow(glyph: String(note.title.prefix(1)),
                   name: String(note.title.dropFirst()).trimmingCharacters(in: .whitespaces),
                   example: note.why,
                   dimmed: used) {
            if model.opening == note.notesID {
                ProgressView().controlSize(.small)
            } else {
                Button(used ? "Show again" : "Show in Notes") { model.show(note) }
                    .buttonStyle(.plain).font(DS.Font.caption)
                    .foregroundStyle(DS.Color.accent)
            }
        }
    }
}
```

**Step 4: Build**

Run: `cd mac && swift build 2>&1 | tail -20`
Expected: `Build complete!`

**Step 5: Commit**

```bash
git add mac/Sources/Notron/Onboarding.swift mac/Sources/Notron/OnboardingView.swift
git commit -m "feat(mac): 'Keep her notes at the top' — the pin step (Task 5 of onboarding)"
```

---

### Task 6: Mac — reachable after onboarding

**Files:**
- Create: `mac/Sources/Notron/PinNotesWindow.swift`
- Modify: `mac/Sources/Notron/NotronApp.swift`

**Step 1: The standalone wrapper**

```swift
import SwiftUI

/// The same `PinStep`, outside onboarding — because the notes that get buried
/// get buried in month six, not on day one, and a user who pressed "I'll do
/// this later" would otherwise never see this screen again.
struct PinNotesView: View {
    @StateObject private var model = OnboardingModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        PinStep(model: model, finish: { dismiss() })
            .padding(DS.Space.s6)
            .frame(minWidth: 620, idealWidth: 620, minHeight: 480, idealHeight: 520)
            .background(DS.Color.bg)
    }
}
```

**Step 2: Register the window and the menu item** in `NotronApp.swift`:

```swift
        Window("Pin her notes", id: "pins") {
            PinNotesView()
        }
        .windowResizability(.contentMinSize)
```

and, in the `MenuBarExtra` block after `OpenLibraryButton`:

```swift
            OpenWindowButton(title: "Pin her notes\u{2026}", id: "pins")
```

Generalise the existing `OpenLibraryButton` into `OpenWindowButton(title:id:)`
rather than copy-pasting it, and update its one existing use.

**Step 3: Build and run**

Run: `cd mac && swift build 2>&1 | tail -5 && .build/debug/Notron`
Expected: `Build complete!`, menu bar shows "Pin her notes…", clicking it opens
the window with the three suggested rows and a "Her other notes (6)" disclosure.

**Step 4: Manual verification (there is no Swift test target)**

1. Click "Show in Notes" on 📥 Ask Notron → Notes comes to the front with that
   note selected in the list.
2. Control-click it → "Pin Note" is in the menu → pin it.
3. Switch Notes to All iCloud → the note appears under **Pinned**, at the top.
4. Back in the app, that row is dimmed and its button reads "Show again."
5. Expand "Her other notes (6)" → 🌱 Take Care of Notron is there and opens too.

**Step 5: Commit**

```bash
git add mac/Sources/Notron/PinNotesWindow.swift mac/Sources/Notron/NotronApp.swift
git commit -m "feat(mac): 'Pin her notes' window + menu item — a skip on day one is recoverable"
```

---

### Task 7: Wire it into the project's own notes

**Files:**
- Modify: `CLAUDE.md` (the Design paragraph, ~line 27)

**Step 1: Extend the onboarding sentence**

```
the first-run onboarding sequence (welcome → permissions → how to talk to her →
start listening → pin her notes → handoff to "Your notes") is specced in
`docs/design/04-onboarding-flow.md`. The pin step exists because **Apple Notes
exposes no `pinned` property to any script** — not AppleScript, not Shortcuts —
so Notron can neither pin a note nor tell whether one is pinned; that screen
instructs and opens (`notron pins`, `notron library open <id>`) and never
confirms.
```

**Step 2: Full test run**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 366 passed (361 existing + 5 new)

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note that Notes has no scriptable pin, and what the pin step does about it"
```
