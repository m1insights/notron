# Offer — Notron

Existing product already has real positioning (README, CLAUDE.md, docs/plans/commercialization.md). This extends it for the Mac companion GUI + pluggable-harness direction, not a reset.

**1. Customer.** A non-technical person (decided persona: 27-year-old woman) who already runs her life out of Apple Notes — to-dos, half-formed plans, journal-ish dumps — and has never opened a terminal or pasted an API key. Secondary, GUI-specific customer this design pass adds: the *slightly* more technical user (has used Raycast/Notion/ChatGPT, comfortable with settings screens, not comfortable with `.env` files or Python) who wants to customize which model/tools Notron uses without touching code.

**2. Pain.** "I want an AI that actually knows my life, not one more app to feed." Every competitor (Notion AI, ChatGPT memory, Rewind, Sunsama) asks her to move in, paste a key, or trust a cloud company with everything she writes. `[ASSUMED — to verify in Phase 2]`: reviews will surface "forgets what I told it," "another app to check," and privacy distrust of always-listening AI as the dominant complaints.

**3. Outcome.** "My notes app already runs my day" — reminders and calendar events set themselves from what she types, her week plans itself, and nothing leaves her Mac she didn't choose to send. For the GUI specifically: "I can see and control exactly what Notron can touch and which brain it's using, without opening a file."

**4. Mechanism — the magic moment.** She types a request into a note on her phone; iCloud carries it to her Mac; a local agent graph (not a single prompt loop) reads it, reasons on NVIDIA Nemotron via Nebius, and writes the answer back into the same note — action taken (real reminder/event created) or not, provably safe either way (the Guard) — and it's back on her phone in ~10 seconds. Nothing installed on the phone.

**5. Pricing.** Already decided (`docs/plans/commercialization.md`): free/open agent, BYO Nebius key. $12/mo hosted tier = hosted brain (no key/terminal), signed installer, phone-capture w/o iCloud lag, deeper memory. The companion Mac GUI is the installer/onboarding surface for that paid tier — it's what makes "no Terminal" true — so it ships free-tier-visible but paid-tier-necessary (BYO-key users can still use a lightweight settings pane; the polished onboarding wizard is what $12/mo buys). `[FLAG]`: worst-case Nemotron token cost per active user/day on Nebius not yet re-verified against the $12 price — carried over as an open item from the existing plan, not new.

**6. Proof.** Live on the user's real 358-note library since 2026-08-29; 164 tests; a real security catch-and-fix (credential leak into a synced note, fixed and pinned by a regression test) — a concrete "we found a way this could hurt you and closed it" story, which is exactly the proof a privacy-sensitive customer needs. Self-improvement loop (`reflect.py`) proven live same day.
