# Commercialization — open core, paid cloud

**Decided 2026-08-29.** Path 1 of the five options: free and open source, with a
paid hosted layer for the things a normal person cannot self-host.

## Why this shape

OpenClaw is the proof. Peter Steinberger put a local AI agent on GitHub in
November 2025, free and open, hit 247K stars, and OpenAI acqui-hired him in
February 2026 for a rumoured $1B. He never charged. The distribution was the asset.

The gap he left: **OpenClaw was built for developers** — terminal, shell commands,
Telegram bridges. JUNO is for someone who runs their life out of the Notes app and
has never opened a terminal. Bigger market, and its best-known builder just went
in-house at OpenAI.

Open source also costs nothing here: the hackathon already requires a public repo
under an OSS licence.

## What is free vs paid

**Free, open source, forever** — the agent, the graph, the Notes integration. Bring
your own Nebius key. This is the viral artifact and it must be genuinely good, not
crippled.

**Paid, $12/month** — everything a non-technical person cannot do for themselves:

1. **A hosted brain.** No API key, no `.env`, no signup at a second company. This is
   the single biggest reason a normal user cannot use the free version.
2. **A signed installer.** One download, handles the macOS permission prompts,
   installs the background jobs. No Terminal.
3. **Capture from the phone** without waiting on iCloud.
4. **Longer memory** — larger index, deeper research runs.

The $12 buys a monthly allowance, not unlimited use. How that is billed is the
next section, and it is a decision, not a detail.

## How the paid tier is billed — flat price, hard cap, top-up

**Decided 2026-09-06.** $12/month includes an allowance. When it runs out she
**pauses and says so**, and the user taps **Top up**. She never spends past the
allowance silently, and there is no invoice nobody pressed a button for.
`allowance_exhausted` is already one of the visible error codes in
`../production/plans/05-accounts-service.md` — this decision is what that code
is for.

The alternative considered was a low base fee plus a markup on whatever
inference the user burns past it. Rejected for three reasons:

1. **She spends money on the user's behalf.** The listener polls every twenty
   seconds, the Filer runs on a settle timer, the morning routine runs unasked.
   A metered bill on an agent that acts without being asked is a bill the user
   cannot predict — and people cancel an unpredictable subscription not because
   it cost too much but because they cannot tell what next month costs.
2. **The amounts do not justify the anxiety.** Routing, guarding and filing are
   Nano; the expensive calls are vision (~3.5s each, already capped by
   `nodes.MAX_LOOKS`) and the one-off `notron index`. Steady-state days are
   cheap. Metering pennies buys a pricing page nobody reads.
3. **Nano-first routing already is the cost control.** The tier split exists so
   cost tracks what the user actually asked for. Billing by token hands the user
   a job the graph already does for them.

Three things follow from the decision:

- **The allowance is counted in her work, not in tokens.** "Answers left this
  month," never "input tokens." `.notron/usage.json` records per-tier `calls`,
  `in` and `out`; the user-facing number is derived from it and the raw ledger is
  never the thing shown. Nobody should have to know what a token is to know
  whether they are near the edge.
- **Running out is a pause, not a breakage.** Her hands and her head are already
  separate (`BrainUnavailable`). Reading notes, receipts and undo keep working
  with no model behind them; only new paid calls stop.
- **Advanced tier is the pressure valve.** The heaviest users are the least
  profitable and the most likely to resent a cap — free BYO-key mode is where
  they belong, and the monthly budget slider in Skills & Plugins is the same
  pause behaviour spending their own money.

## Distribution

**Not the Mac App Store.** A sandboxed App Store app cannot script other apps
without an `apple-events` exception entitlement, and Apple's stated position is
that these "will not be granted". It also cannot install the background jobs JUNO
needs. This was checked, and it rules the App Store out for this product shape.

Ship the way Raycast, BoltAI and CleanShot do:

- Notarized `.dmg` from our own site, Sparkle for updates.
- Paddle or Stripe for subscriptions — keeps ~95% against Apple's 70–85%.
- **Setapp as a second channel** (~$3–5/user/month, they own the billing and the
  audience). Good early revenue with no marketing spend.

## Unit economics to prove before launch

The ledger in `.notron/usage.json` records, per day and per tier, `calls`, `in`
and `out`. Two numbers set both the price and the allowance, and a week of
single-user data produces both before anyone else installs anything:

1. **A median answer.** One Ask-note round trip end to end — the retrieval embed,
   the model call, any vision look.
2. **A quiet day.** The listener, the Filer's settle passes and the morning
   routine, with the user asking nothing at all. This is the floor under every
   subscriber and it is the number a flat price actually has to survive.

Set the allowance so an ordinary month sits comfortably inside it and only
genuinely heavy use ever reaches the cap. **A cap most users never see is a
promise; a cap most users hit every month is a metered bill with extra steps** —
which is the thing the section above rejected.

## Rename before we charge — a hard gate

"Juno" is fine for a free, open-source hackathon entry. It is not fine on a paid
product, and the rename must land **before the first dollar is taken**. Checked
2026-08-29; not legal advice, and worth a lawyer's hour before filing anything.

Three live conflicts, all in the same lane:

1. **"JUNO AI"** — filed 2025-08-21 by TechBridge Solutions LLC (Brooklyn), serial
   99349201, software and AI services class, in commercial use. Our exact phrase.
2. **Juno — Python and Jupyter** (juno.sh, by MWM) — an active Apple-platform
   software app that *added an AI assistant in 2026*. Same platform, same category,
   same description. The most dangerous of the three.
3. **Juno** (GT Gettaxi, rideshare) and Juno the email/ISP brand — both hold
   software-class marks.

Every obvious domain is gone too: `juno.app`, `junoai.com`, `heyjuno.com`,
`getjuno.com`. Even keeping the name, there is no clean address to launch on.

The precedent is recent and exact: Anthropic forced Clawdbot to rename twice and
hand over its domains. Solo developer, beloved name, gone anyway.

**The rename is cheap — do it with this work, not as a separate project.** The name
lives in five constants: `AGENT` in `workspace.py`, `SIGNATURE` in
`conversation.py`, and the two launchd labels in `watch.py` and `daily.py`. Roughly
twenty minutes plus the folder rename in the user's own Notes, which needs a small
migration so existing users do not lose their notes.

Two directions worth considering:

- **Rename outright.** `heynella.com` and `heywinnow.com` were both available when
  checked — most short names are long gone, so expect to buy a `hey-` or `try-`
  prefix or an unusual word.
- **Keep Juno as the character, brand the product separately** — the Alexa/Echo
  split. She stays Juno in the copy and on TikTok; the trademark sits on something
  clean. This keeps the name we actually like and is probably the better answer.

## Sequence

1. Ship the hackathon build public and open (due **2026-10-30, 10:00am PDT**).
2. Reminders + Calendar — the features that make it a life manager rather than a
   notes toy. See `reminders-calendar.md`.
3. Signed installer + permission onboarding. About a week, and it is the difference
   between a demo and a product. **Rename here** — it has to be settled before the
   thing people download carries a brand.
4. Hosted brain behind a subscription.
5. Setapp submission.

## Risks worth naming

- **Apple ships this themselves.** Apple Intelligence inside Notes is an obvious
  roadmap item. Reason to move fast; not a reason to stop.
- **Packaging is the real work.** Bundling Python, requesting Automation
  permissions, managing launchd. Underestimating this is how the project stalls.
- **Open source means someone can fork and host it.** The defence is the hosted
  brain and the brand, not the code — it is only ~1,400 lines.

## 2026-08-30 — Renamed to Notron; native Siri app built

The rename above landed: package, Notes folder, GitHub repo, and the agent's own
memory of itself are all Notron now.

Also landed: a real answer to item #2 in "What is free vs paid" — "no Terminal."
The original plan for voice ("Hey Siri, ask Notron…") was a hand-built macOS
Shortcut: open the Shortcuts app, add four actions, paste a Python path into a
shell-script step, record a Siri phrase. Fine for us; unshippable to a normal
downloader who has never seen a terminal.

Replaced it with a native macOS menu-bar app (`mac/` — Swift, App Intents). It
registers "Hey Siri, ask Notron ___" with the OS itself the moment it's installed
and opened once — no Shortcuts app, no pasted paths, no dictation step. Built,
compiled, signed (ad-hoc), and confirmed running locally today.

Two blockers before this ships inside the paid DMG, ~half a day combined:

1. **Bundle a portable Python runtime inside the app.** Right now it shells out to
   this machine's dev Python by absolute path — works here, not on a downloaded
   copy on someone else's Mac.
2. **Real Developer ID signing + notarization, not ad-hoc.** Not just a Gatekeeper
   checkbox: an ad-hoc signature changes on every rebuild, and Siri's per-app trust
   is tied to that signature — the same reason CLAUDE.md's invariant #6 already
   rules out a compiled EventKit helper. A stable signed identity is what keeps the
   Siri phrase working across app updates instead of silently going quiet.
