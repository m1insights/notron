# Onboarding — first-run flow (fills the gap: permissions built, "how to talk to her" + "start listening" designed)

Extends `02-screens.md`'s **Screen 1 — First-run permissions** into the full
sequence a fresh DMG install actually needs. Offer, pricing and visual direction
are locked (`00-offer.md`, `03-directions.md`, direction **mix** — Notes-native
light skin everywhere except Skills & Plugins) — not re-litigated here.

**The gap this closes:** today, a fresh install shows the menu-bar icon, then
jumps straight to the "Your notes" window (200 rows, three switches) with zero
explanation. The listener is never started, so typing `#notron` anywhere does
nothing — the single most confusing possible first impression for a product
whose whole pitch is "type in Notes, she answers." Two steps were missing from
the design entirely (how to talk to her, start listening); the permissions step
was designed in `02-screens.md` but never built.

## Research — what shipped apps do here

10 Mobbin searches, 65+ distinct apps, ios + web (no macOS coverage on Mobbin —
these patterns translate directly since the app's own window chrome already
apes an iOS-style card in a 720×520 macOS window, per `DESIGN.md`).

- **Permission priming, one at a time, real headline + one-line why before the
  OS prompt:** [Craft](https://mobbin.com/screens/e7a3a849-0418-4622-8ffa-262b60847564), [Opal](https://mobbin.com/screens/6ac44043-77af-43b5-b2ff-7ff7c4b35788), [State Farm](https://mobbin.com/screens/6a05653b-b907-46b6-960e-9bc81d659463). This is
  exactly what `02-screens.md` Screen 1 already specs — confirms the design,
  no change needed.
- **Teaching the talk-to-it syntax with real example text, not abstract
  copy:** [Absolute Poll](https://mobbin.com/api/mcp/short/qPyeU6Vv) puts the exact `/command` and `@mention` syntax
  in the first chat bubble; [Amazon Rufus](https://mobbin.com/flows/90946833-0761-49bb-bf16-f9139bc68438) devotes a whole "Where to find
  Rufus" card to it; the health-AI app [Alan](https://mobbin.com/screens/92cb048d-71c6-4a55-888b-a5a040573304) opens its first message with
  three tappable example prompts. Pattern adopted: three short labeled rows,
  each showing the real glyph (`📥`, `🧠`, `#notron`) + a one-line real example,
  not a generic "here's how AI assistants work" paragraph.
- **Getting-started checklist, persistent, checkable:** [Google Workspace](https://mobbin.com/screens/05a829a2-525b-4a6e-911a-f4d5e2e5b9ce), [HoneyBook](https://mobbin.com/screens/7c915d6b-2a99-4eb0-956d-e7a3a97bb265),
  [Vanta](https://mobbin.com/screens/d6c8a960-3253-4ca4-b4aa-06e902fa0e4e) — informed the menu-bar "Getting started" line item (below), not a
  full screen of its own; Notron's flow is short enough not to need a persistent
  checklist UI.
- **Progress indicator on a short linear flow:** small dots/bar, never a
  numbered wizard chrome — [Grok Bot](https://mobbin.com/screens/f631158c-b9fb-44f7-8dd4-3429b2f975c9), [Meta AI](https://mobbin.com/screens/ef7d92b4-98a9-4f20-a721-6ab7aa383a6d) ("Setup complete 5/5"),
  [Superpower](https://mobbin.com/screens/6303e699-8c15-4f30-86b2-3078d637bd3b)'s gradient bar. Adopted as a new `DESIGN.md` component (below).
- **"You're set" screen names the exact trigger phrase to use next**, not just
  a generic "you're all set": [Meta AI](https://mobbin.com/screens/ef7d92b4-98a9-4f20-a721-6ab7aa383a6d) — *"Start conversation focus from the
  Devices tab or say 'Hey Meta, start conversation focus.'"* Directly maps to
  Notron: the finish screen should name `#notron` and "Hey Siri, ask
  Notron…" by name, not just say "done."
- **Connected/live state = the same success-teal checkmark already in
  `DESIGN.md`**, confirmed against [QuickBooks](https://mobbin.com/screens/c846a277-b056-4f86-a879-8822b316bc1d), [Wise](https://mobbin.com/screens/40f9c9a4-25bc-4bbb-80c9-e94c92902d73), [n8n](https://mobbin.com/screens/02b5e65b-850f-44ca-b907-4e0751c7a367) — no new
  color needed for "Notron is listening."

## The flow — 4 new/changed screens

Sits between first launch and the existing, already-built "Your notes" window
(`docs/plans/2026-09-01-your-notes-setup.md`) — that screen is now the last
step, not the first thing a fresh user sees.

```
Welcome  →  Permissions (3 cards)  →  How to talk to her  →  Start listening  →  [existing] Your notes
 (new)         (designed, unbuilt)         (new)                  (new)              (built, reused)
```

### 1. Welcome — who she is (new)
**Purpose:** a fresh install currently has zero words on screen before asking for
system access. Say who she is before asking for anything.
**Eye lands on:** the app icon in an accent-tinted tile (`DESIGN.md`'s
first-run empty-state component) + one headline.
**Content:** icon · "Meet Notron" (`headline`) · "She lives in your Notes, thinks
on Nemotron, and writes back what you ask." (`body`, one line — Apple-voice, no
exclamation) · single primary button.
**Action:** "Get started" → Permissions.
**Component:** reuses the existing **Empty/first-run state** spec verbatim —
icon tile, headline, one body line, single button, no skip.

### 2. Permissions — three cards in sequence (already designed, not built)
Unchanged from `02-screens.md` Screen 1: Notes → Reminders → Calendar, each its
own card, headline naming the exact permission + why, single "Allow" → the real
macOS prompt → next card. Implementation detail this pass adds: the app reads
live status from `notron permissions` (new `--json` flag, see the plan) so a
card shows the success-teal checkmark the instant the OS grants it, and — since
CLAUDE.md documents that an unapproved Notes automation **hangs, not fails** —
each check runs off the main thread with an 8s bounded probe (`permissions.PROBE_TIMEOUT`,
already in the core) so the window never locks up. A denied/restricted card
shows its `fix` text (from the same JSON) and an "Open System Settings" button
(`x-apple.systempreferences:` URL to the right pane) instead of a dead end.
**No skip** — unchanged rule, CLAUDE.md's hang-not-fail behavior means skipping
isn't safe here.

### 3. How to talk to her (new — was missing from the design entirely)
**Purpose:** the single highest-leverage screen in this whole gap. A user who
doesn't know `#notron` exists, or that 🧠 Brain Dump and 📥 Ask Notron are real
notes she's already created, has no way to discover them.
**Eye lands on:** three rows, each the real glyph + real folder name + one line
of real example text — not a diagram, not abstract copy.
**Content:**
- `📥 Ask Notron` — "Type anything in this note. She answers right below it."
- `🧠 Brain Dump` — "One thought per line — 'took vitamin D,' '5k run 24:10.'
  She sorts it into the right note."
- `#notron` — "Tag any note, anywhere in Notes, and she'll notice next time
  she looks."
Below the three rows, one small line: "All three live in 🤖 NOTRON — already in
your Notes." (confirms `notron setup` already ran and created the folder;
ties abstract instruction to something concrete the user can go look at).
**Action:** single "Got it" button → Start listening. No OS interaction on this
screen, so a text "Skip" is safe here — unlike the permissions step, this is
pure explanation with nothing left un-granted.
**Component (new):** **Example row** — see `DESIGN.md` addition below.

### 4. Start listening (new — was missing from the design entirely, and is
the literal root cause: "typing to her does nothing")
**Purpose:** everything above is inert until the background listener exists.
This is the one screen that turns Notron on.
**Eye lands on:** one large status card, off by default, that flips to a
success state live.
**Content — off state:** "Notron isn't listening yet." (`body`) · "Turn this on
and she'll notice what you type within a few seconds, even after you restart
your Mac." (`caption`) · single primary button **"Start listening."**
**Content — on state (after the button is pressed):** the same card, success-teal
checkmark + "Notron is listening" (mirrors the existing **API key field**
success convention), plus the exact trigger line from research: *"Try it —
type `#notron hello` in any note, or say 'Hey Siri, ask Notron…'"*
**Action:** "Start listening" runs `notron listen --install` (already exists,
launchd-backed, survives reboot per CLAUDE.md) via the same `Core.run` subprocess
bridge as every other Swift→Python call; button shows a spinner while the
launchd bootstrap runs (typically under a second), then flips to the success
state read back from a new `notron listen --status`. A de-emphasized text link
"I'll do this later" is allowed (unlike the permissions step — this is a
background service toggle, always reachable again from the menu, not a
one-shot OS dialog), but it is **not** the default-focused control; "Start
listening" is.
**Component (new):** **Listening status card** — see `DESIGN.md` addition below.

### 5. Handoff → the existing "Your notes" window
**Purpose:** don't rebuild what's already shipped
(`docs/plans/2026-09-01-your-notes-setup.md`, built 2026-09-02). This flow ends
by opening it, not by duplicating it.
**Content:** unchanged — the already-built list + preview panel.
**Action:** unchanged — its own "Done," which is also the point the whole
onboarding sequence is marked complete (see the plan for the storage detail).

## Progress indicator
A row of small dots above the card, one per step (Welcome · Permissions · Talk
· Listen) — never a numbered "Step 2 of 4" wizard label, matching the research
board's dot/bar convention over verbose stepper chrome. See `DESIGN.md`
addition below for the exact spec.

## New components — append to `DESIGN.md`

**Step dots** — row of 4–6 circles, 6px diameter, `space-2` gaps, current =
`accent` fill, done = `text-faint` fill, upcoming = `hairline`-bordered/empty.
Sits top-center of the card, `space-4` above the headline. Light skin only —
this flow never touches the dark skin.

**Example row** (new, for "How to talk to her") — `surface` background,
`hairline` border, radius per skin (light: `lg`), laid out as: the glyph at
`headline` size on the left, name in `body` weight, one line of real example
text in `caption`/`text-dim` below it. No icon tile, no chrome beyond the card
itself — the emoji already reads as the icon (`📥`/`🧠`/`#`), and Notron's
copy voice is plain text, not iconography layered on emoji.

**Listening status card** (new) — full-width `surface` card, `hairline`
border, radius `lg`. Off state: `text-dim` body copy, primary button
right-aligned. On state: swaps the button for the same success-teal checkmark
+ "Notron is listening" pattern as the **API key field** component, plus one
caption line below in `text-faint`. Transition uses `DS.Motion.standard`
(200ms ease) — this is the light skin, not Skills & Plugins.

## Do / Don't (adds to the existing list)
- Do let the "How to talk to her" and "Start listening" screens use a
  de-emphasized skip · Don't add a skip to the Permissions screen — CLAUDE.md's
  hang-not-fail behavior makes that unsafe, unchanged from the original spec.
- Do show the exact trigger phrase (`#notron`, "Hey Siri, ask Notron…") on the
  finish state · Don't end on a content-free "You're all set."
- Do read permission and listener state live from the core (`--json` flags) ·
  Don't hardcode a "granted" assumption in the Swift layer — the whole reason
  this gap exists is a screen that never checked real state before opening.
