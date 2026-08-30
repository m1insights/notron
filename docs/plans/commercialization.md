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

Worst-case cost per user per month must clear $12 with room. The number to measure
is tokens per active user per day on Nemotron — the usage ledger in
`.juno/usage.json` already records it, so the free version gives us real data before
we price anything. Nano-first routing exists precisely so cost tracks what the user
actually asked for.

## Sequence

1. Ship the hackathon build public and open (due 2026-10-30).
2. Reminders + Calendar — the features that make it a life manager rather than a
   notes toy. See `reminders-calendar.md`.
3. Signed installer + permission onboarding. About a week, and it is the difference
   between a demo and a product.
4. Hosted brain behind a subscription.
5. Setapp submission.

## Risks worth naming

- **Apple ships this themselves.** Apple Intelligence inside Notes is an obvious
  roadmap item. Reason to move fast; not a reason to stop.
- **Packaging is the real work.** Bundling Python, requesting Automation
  permissions, managing launchd. Underestimating this is how the project stalls.
- **Open source means someone can fork and host it.** The defence is the hosted
  brain and the brand, not the code — it is only ~1,400 lines.
