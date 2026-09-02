# Key screens — Notron Mac companion app

Two audiences, one app, a mode switch. Simple = paid ($12/mo hosted brain). Advanced = free (BYO Nebius key, full Skills/Plugins access) — confirmed 2026-08-31, consistent with `docs/plans/commercialization.md`'s existing open-core decision.

## 1. First-run — permissions (onboarding step 1)
**Purpose:** get Notes/Reminders/Calendar access without the app silently hanging (CLAUDE.md: an unapproved app hangs, not errors).
**Eye lands on:** one bold headline naming the exact permission + why, styled like the Greenlight/Brick/StackAI Mobbin references.
**Content:** app icon, "Allow Notron to read and write in Notes" / "why: this is where you talk to her" — three permission cards in sequence (Notes, Reminders, Calendar), each with a single primary button.
**Action:** "Allow" → triggers the real macOS prompt → next card.

## 2. Skills & Plugins — THE MAGIC MOMENT
**Purpose:** prove Notron is genuinely extensible without a terminal — the differentiator the user is most excited about, and a direct answer to the hackathon brief's "reusable skills" requirement.
**Eye lands on:** a list of installed skills as cards (name, one-line description, source, on/off toggle), plus one "Add a skill" action.
**Content:** ships with Notron's own built-ins (Reminders, Calendar, Reflect, Care) shown as "Core" and non-removable; below, an "Add skill" flow that reads a `SKILL.md`-format folder (the same format Nous Research's Hermes Agent uses — verified 2026-08-31, see 01-research.md) and a matched folder for any DeepSeek-harness plugin format. Advanced-tier only; simple tier sees a locked/blurred version with "Unlock in Advanced mode."

**Added 2026-08-31 (user's idea) — a 5th Core skill, "Filer":** type anything into a new `📥 Brain Dump` note — "took vitamin D today," "5k run, 24:10," a recipe that worked — and Filer routes it into the right master note on its own (supplement log, workout log, recipe box), creating one if none exists. This is the concrete build of the "auto-organize whatever I type" idea and gets the highlighted full-width Core card treatment (see DESIGN.md) since it's the feature this screen leads with. Build note: this is a new node in the graph — `filer` — sitting between watcher and executor: classify (which master note, or a new one) then insert via the existing Guard, same lossless-insert guarantee as everything else. Nano-tier classification, no Super call needed for the common case.
**Action:** drag a skill folder in, or paste a GitHub URL; toggle on/off.
**Why this screen, not a CLI:** every other agent harness (OpenClaw, Claude Code, DeepSeek's) does this with `npm install`-style commands. Notron's whole pitch is zero terminal — so "install" has to mean drag-and-drop or paste-a-link, reviewed by the Guard before anything runs.

## 3. Settings hub — mode + model + key
**Purpose:** the single screen that proves "no Terminal, you control what leaves your Mac" — houses the Simple/Advanced switch itself.
**Eye lands on:** a two-option segmented control at the top: "Simple ($12/mo, hosted)" vs "Advanced (free, your own key)."
**Content:** below the switch, the fields relevant to whichever mode is selected — Simple shows only "Manage subscription"; Advanced shows the Nebius API key field (masked, paste, validate — the universal Mobbin pattern), the Nano/Super/Ultra model-tier list mirroring `brain.DEFAULT_MODELS`, and (added 2026-08-31, user's idea) a **Monthly budget slider** — "How much can she spend before pausing?", $5–$100, plain endpoint words ("Careful" / "No limit"), backed by the real `.notron/usage.json` ledger. This is the pattern for every advanced numeric knob: a slider + a plain question, never a bare number field — see DESIGN.md principle 4.
**Action:** flip the switch; paste a key; see it validate live; drag the budget slider.

## 4. Money screen — upgrade to Simple
**Purpose:** the paywall. Convert an Advanced (free, technical) user who's tired of managing a key, or greet a non-technical user who lands here first.
**Eye lands on:** the ten-second promise — "No key. No Terminal. Just Notes." — above a single $12/mo card.
**Content:** proof element above the CTA: "<N> notes indexed, zero data leaves your Mac except your own Nebius calls" — pull `<N>` live from the index at render time (358 at last count, will have grown by ship date); the credential-leak-caught-and-fixed story belongs here as a trust line too.
**Action:** "Start Simple" → Paddle/Stripe checkout (per commercialization.md, not the App Store).

## 5. Menu-bar quick view (the daily driver)
**Purpose:** what the user actually opens 10x/day — this is the "she's alive" proof, not a settings page.
**Eye lands on:** last thing Notron did, with a timestamp — "Set a reminder: Call the pharmacy · 2 min ago."
**Content:** a short activity log (last 5 actions), a "Ask Notron" quick-open-Notes button, and a small Nano/Super/Ultra usage meter (ties to the existing `.notron/usage.json` ledger).
**Action:** click an entry to jump to that note in Notes.

**Note on scope:** 5 screens, at the cap. Skills & Plugins is designed hardest per the prime directive (it's the magic moment); the other four get one clean pass each.
