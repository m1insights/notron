# Onboarding — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The design it implements is `docs/design/04-onboarding-flow.md` — read it first (5 min), and `docs/design/DESIGN.md`'s "Onboarding" component additions. Tests run with no API key and no network; never run a Notes-touching command while the background listener is mid-request (see `CLAUDE.md`).

**Goal:** a fresh DMG install walks the user through who Notron is, the three
system permissions (already designed, never built), how to talk to her
(`📥 Ask Notron`, `🧠 Brain Dump`, `#notron` — missing from the design entirely),
and turning the background listener on (also missing — the literal reason
typing to her does nothing today) — before handing off to the existing, already
built "Your notes" window.

**Architecture:** two small core additions (`--json` on `notron permissions`,
`--status` on `notron listen`) so the Mac app can read real permission and
listener state instead of assuming it; everything else is new SwiftUI in
`mac/`, following the exact `Core.run` subprocess-bridge pattern already used
by `AskNotronIntent.swift` and `Library.swift`. No model call anywhere in this
feature — it's all plain code and OS-level checks. One new marker file,
`.notron/onboarding.json`, records that the sequence completed, the same way
`.notron/library.json`'s mere existence gates today's first-run check.

**Tech Stack:** Python 3.11 (plain functions, pytest, monkeypatched
subprocess/EventKit — same style as `test_permissions.py`/`test_watch.py`),
SwiftUI on macOS 14 (`mac/Package.swift`, tokens from
`mac/Sources/Notron/DesignSystem.swift` — the two new components,
**Step dots** and **Listening status card**, are already specced there, don't
invent new ones), the existing `Core.run` bridge.

**Run everything from `apps/juno`:** `.venv/bin/python -m pytest tests -q`
(278 tests green at the start), `cd mac && swift build` for the app.

---

## Task 1: `notron permissions --json` — machine-readable checks

**Files:** Modify `notron/cli.py` (`cmd_permissions`, its `add_parser`), `notron/permissions.py` unchanged (already returns structured `Check` objects — this task just exposes them).
**Test:** `tests/test_permissions.py` (add JSON-shape assertions)

**Step 1 — failing test:**
```python
def test_json_flag_prints_the_same_checks_as_a_flat_list(capsys):
    import argparse, json
    from notron import cli

    args = argparse.Namespace(json=True)
    cli.cmd_permissions(args)
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert {"app", "ok", "detail", "fix"} <= out[0].keys()
```
(Route `cmd_permissions` through the real `permissions.check()` here — no
monkeypatch needed for the shape assertion; keep the existing text-mode tests
for behavior.)

**Step 2 — implement:** add `pe.add_argument("--json", action="store_true", ...)` to the `permissions` subparser; in `cmd_permissions`, when `args.json`, print `json.dumps([{"app": c.app, "ok": c.ok, "detail": c.detail, "fix": c.fix} for c in permissions.check()])` and return, before the existing human-readable loop. `dataclasses.asdict(c)` is simpler if the field names already match the JSON shape (they do) — prefer it.

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_permissions.py -q`

## Task 2: `notron listen --status` — is the launchd job actually running?

**Files:** Create/modify `notron/watch.py` (`is_running`), `notron/cli.py` (`cmd_listen`, its `add_parser`)
**Test:** `tests/test_watch.py`

**Step 1 — failing test:**
```python
def test_is_running_reads_the_launchctl_exit_code():
    from notron import watch
    assert watch.is_running(runner=lambda: 0) is True
    assert watch.is_running(runner=lambda: 113) is False  # launchd's "not found"
```

**Step 2 — implement** in `watch.py`:
```python
def is_running(runner=None) -> bool:
    """True if the listener's launchd job is loaded — not whether it's healthy,
    just whether `notron listen --install` (or a reboot) has it running."""
    import os, subprocess
    runner = runner or (lambda: subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{WATCH_LABEL}"],
        capture_output=True).returncode)
    return runner() == 0
```
In `cmd_listen`, add `--status` (checked before `--off`/`--install`, since it's
read-only and should never fall through to them): `print(json.dumps({"running": watch.is_running()}))`.

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_watch.py -q`

## Task 3: Swift — the onboarding model + four screens

**Files:** Create `mac/Sources/Notron/Onboarding.swift` (model), `mac/Sources/Notron/OnboardingView.swift` (the four screens + step dots), following `Library.swift`/`YourNotesView.swift`'s split.
**No unit tests** — this repo has no Swift test target (matches the precedent set by `docs/plans/2026-09-01-your-notes-setup.md`'s GUI task); verified by device QA in Task 5.

**`OnboardingModel` (`@MainActor`, `ObservableObject`)** — mirrors `LibraryModel`'s
`Task.detached` + `Core.run` pattern exactly:
- `@Published var step: Step` (`.welcome, .permissions, .talk, .listening`)
- `@Published var checks: [PermissionCheck]` — decoded from `notron permissions --json` (`struct PermissionCheck: Codable { let app, detail, fix: String; let ok: Bool }`), refreshed on `.onAppear` of the Permissions screen and after each "Allow" tap (poll every 1s for up to 8s after a tap — `permissions.PROBE_TIMEOUT` bounds a single check, but the OS dialog itself is user-paced).
- `@Published var listening: Bool` — decoded from `notron listen --status`.
- `func allow(_ app: String)` — for **Notes**, no direct trigger exists (the
  probe itself, `notes_runner`, is what surfaces the OS prompt on an
  unapproved app — CLAUDE.md: it hangs until answered); the button's real job
  is running the check off-thread and letting the hang resolve into either a
  granted result or a still-pending one, never blocking the UI thread. For
  **Reminders**/**Calendar**, same: EventKit's own prompt fires from the
  probe. Do not try to invent a separate "request access" call — CLAUDE.md
  documents there isn't a clean one distinct from the probe itself.
- `func startListening()` — `Task.detached { try Core.run(["listen", "--install"]) }`, then re-polls `--status` until `true` (typically well under a second) and flips `listening`.
- `static var done: Bool` — `.notron/onboarding.json` exists (mirrors `LibraryModel.exists`); `func markDone()` writes `{"completed_at": ISO8601}`.
- Denied/restricted card: surface the `fix` string from the JSON and an
  "Open System Settings" button — `NSWorkspace.shared.open(URL(string:
  "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation")!)`
  for Notes, `...?Privacy_Calendars` / `...?Privacy_Reminders` for the other two
  (`PANE` in `permissions.py` already names which pane each app uses).

**`OnboardingView`** — four sub-views behind the model's `step`, each built from
`docs/design/04-onboarding-flow.md`'s copy verbatim and the existing
`DS` tokens (`Empty/first-run state` component for Welcome; a new small
`StepDots` view; the **Permissions card**, unchanged from `02-screens.md`'s
original spec, now wired to `checks` instead of static copy; the **Example row**
component for "How to talk to her"; the **Listening status card** component,
off/on driven by `model.listening`). "Got it" and "I'll do this later" both
just advance `step` — the difference between them is copy, not behavior, per
`04-onboarding-flow.md`'s skip rule (permissions screen has no skip; the two
screens after it do). The last screen's "Set up your notes" button calls
`model.markDone()` then `openWindow(id: "library")` and dismisses the
onboarding window.

## Task 4: Wire it into the app launch sequence

**Files:** Modify `mac/Sources/Notron/NotronApp.swift`

Today, `MenuBarLabel.onAppear` opens the "Your notes" window whenever
`!LibraryModel.exists`. Change the gate to a small decision, in order:
1. `!OnboardingModel.done` → open the new **onboarding** window (add a second
   `Window("Welcome", id: "onboarding") { OnboardingView() }` scene, same
   `.windowResizability(.contentMinSize)` as "library").
2. else if `!LibraryModel.exists` → today's behavior, unchanged (covers a user
   who already has `.notron/onboarding.json` from a previous build but never
   finished "Your notes" — don't force them back through Welcome).
3. else → no window opens automatically; menu bar only, as today.

The menu bar's "Set up your notes…" item is unaffected — it always opens
"Your notes" directly, regardless of onboarding state, since a returning user
reopening it deliberately shouldn't be routed back through Welcome.

## Task 5: Device QA (manual — no automated coverage for this half)

Not test-driven; run once on a clean `.notron/` (rename it aside, don't
delete a real one):
1. Fresh launch with no `.notron/onboarding.json`, no `.notron/library.json` →
   Welcome opens automatically, not "Your notes."
2. Click through all three permission cards on a Mac where they're **not** yet
   granted — confirm each "Allow" produces a real OS prompt (not a hang) and
   the card flips to the success state within a couple seconds of granting.
3. Deny one on purpose — confirm the `fix` text and "Open System Settings"
   button appear and the button opens the right pane.
4. "Start listening" → confirm `launchctl print gui/$(id -u)/io.m1labs.notron.listen`
   shows loaded, then actually type `#notron hello` in a note and confirm she
   answers within the listener's poll interval — this is the step that proves
   the whole gap is closed, not just that a screen exists.
5. Quit and relaunch the app → onboarding does **not** reopen; "Your notes"
   opens if it was never finished, otherwise neither window auto-opens.
6. Reboot the Mac (or `launchctl bootout`+log back in) → listener still
   running, per the existing `--install` guarantee — this task didn't touch
   that mechanism, only surfaced its status.

## Effort

| Piece | Time |
|---|---|
| Task 1 + 2 (`--json` / `--status`, tests) | ~1 hour |
| Task 3 (`Onboarding.swift` + `OnboardingView.swift`, 4 screens) | ~1 day |
| Task 4 (launch-sequence wiring) | ~30 min |
| Task 5 (device QA) | ~1–2 hours |

Total ≈ 1.5 days. Core first (testable, unblocks nothing else waiting on it),
Swift second, device QA last — same order as `docs/plans/2026-09-01-your-notes-setup.md`.

**Out of scope:** localizing onboarding copy (de/fr/ja — tracked with every
other queued translation); a "why we need this" video/animation; remembering
partial progress if the user quits mid-flow (each relaunch restarts at
Welcome until `markDone()` fires — acceptable for a 4-screen, under-a-minute
flow).
