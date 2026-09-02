# Research — Notron

## Competitors (preliminary — from existing knowledge; Track 1 deep-research still running in background, will amend with verbatim review quotes + hackathon-tool specifics when it returns)

| Name | Price | Does well | Users complain about |
|---|---|---|---|
| Rewind / Limitless | $19-30/mo | Passive capture, "it remembers everything" | Privacy fear (records screen/audio), heavy local storage, still needs YOU to ask it things |
| ChatGPT (memory + Tasks) | $20/mo Plus | Ubiquity, no setup | "Forgets what I told it," memory feels shallow, doesn't act on your calendar/reminders |
| Notion AI | $10/mo add-on | Already inside a tool many use | "Another thing to configure," doesn't help people who don't already use Notion |
| Sunsama / Motion | $20-35/mo | Real day-planning | Steep setup cost, one more app + habit to build |
| Raycast (+ Raycast AI) | Free / $8-16/mo | Beloved onboarding, extension ecosystem | Developer-coded audience; non-technical people bounce off the command-palette metaphor |
| OpenClaw (Peter Steinberger) | Free, open-source | Proved free+open wins distribution (247K★, OpenAI acquihire rumored ~$1B, Feb 2026) | Terminal-first; built for developers, not the mainstream user Notron targets |
| Claude Code / Claude Agent SDK | Usage-based | The reference for "graph of specialized nodes, not a loop" agent design | Developer tool, not consumer-facing |

**Gap Notron sits in:** everyone above asks the user to move into a new app, a chat window, or a terminal. Nobody else turns the note-taking app already on a billion phones into the interface itself.

## What's proven

People already pay for: (1) an AI with memory of their life (Rewind, ChatGPT memory), (2) an AI that touches their calendar/reminders (Motion, Sunsama), (3) a free+open agent that a company later pays real money to acquire (OpenClaw). Nobody has combined "proven mechanism" #1+#2 with #3's distribution model on a platform this size (Apple Notes/iCloud). That combination is the bet.

## Our differentiation

**"It's a personal AI agent with memory and real task execution, but it lives inside the app you already have instead of asking you to open a new one."**

## Hackathon tool stack — VERIFIED 2026-08-31 (deep-research, 27 sources, adversarially checked)

| Tool | What it actually is | Verdict for Notron |
|---|---|---|
| **NemoClaw** | NVIDIA's Apache-2.0 CLI/management layer. Not an agent — it runs OpenClaw (default), Hermes Agent, or LangChain Deep Agents *inside* OpenShell, adding guided onboarding, managed inference routing, network policy, snapshots. Early preview, NVIDIA's own docs say "not production-ready." | Skip. It's an orchestrator for *other* harnesses running arbitrary shell/file access — the opposite of Notron's Guard-choked, no-terminal design. Wrapping Notron in it would fight the architecture, not strengthen it. |
| **OpenShell** | NVIDIA's separate Apache-2.0 sandboxed runtime — filesystem/network/process isolation + credential brokering (the agent never sees raw API keys). Also early preview. | Skip for the same reason — it's infrastructure for agents that need a sandbox because they can run arbitrary code. Notron's Guard already does the equivalent job in ~1,400 lines, for free, without a new dependency two weeks before a deadline. |
| **Hermes Agent** | Nous Research's MIT-licensed harness (not NVIDIA's). Manages skills/sessions/memory. Genuinely self-evolving: on repeated user correction it **writes a SKILL.md file to disk**, no retraining. | **This is the one worth citing, not integrating.** It independently validates the Skills & Plugins tab's exact mechanism — SKILL.md-as-the-unit-of-extension is already how a hackathon-adjacent project does it. Name-check it in the submission as prior art Notron's format is compatible with; don't add it as a runtime dependency. |
| **Nebius Serverless** | No verified detail surfaced — the brief lists it with zero description and research found no independent documentation. Likely just Nebius's model-serving infra (i.e., what Notron already calls via Token Factory) rather than a separate product to integrate. | Don't build around an unconfirmed tool. If it turns out to just be Nebius's serving layer, Notron already satisfies the requirement by using Nebius Token Factory. |

**Bottom line: Notron already satisfies the Personal AI Track's literal brief** — deep-research confirmed the track's verbatim scope is "an always-on, private assistant... persistent memory, reusable skills, access to the tools and information you choose, and the ability to carry out tasks across daily workflows," which is close to a one-to-one description of what's already built. The "recommended tools" are optional, not required, and three of the four are early-preview infra for a different architecture shape (arbitrary-code agents needing sandboxing) than Notron's (a declared graph with a hard-coded Guard). Cite Hermes Agent's SKILL.md pattern as validation of the plugin format; don't bolt on NemoClaw/OpenShell.

**DeepSeek Harness — confirmed real, and it's the right precedent** (MIT-licensed, developer preview since 2026-08-13, arXiv:2608.25512). Every capability — model, tools, skills, sessions, sandboxes, storage, loop, scheduler, UI — is a swappable plugin managed by a mount/unmount kernel called Cordis; developers swap or extend any piece via configuration, never forking the harness. This is the direct architectural template for making Notron's own graph (`graph.py`/`nodes.py`) end-user-pluggable — see the advisory note in the final report for how to apply it without destabilizing the hackathon build.

**OpenClaw correction (supersedes prior memory):** OpenAI's Feb 2026 move was **not** a corporate acquisition of OpenClaw for a disclosed sum — no dollar figure was ever confirmed by any source. Peter Steinberger personally joined OpenAI as an employee; OpenClaw itself spun out into an **independent, MIT-licensed, non-profit foundation** (live since 2026-07-08) backed as a sponsor by OpenAI, NVIDIA, Microsoft, and Tencent. Star growth is real and fast (100K by Feb 2 → 247K+ by Mar 2, 2M weekly visitors at peak) but a moving target — check a current count before citing in the submission.

## UI references (Mobbin — 10 searches run, 100+ distinct apps returned, floor cleared)

**Onboarding / connect flows:**
- [Craft — Connecting MCP](https://mobbin.com/flows/a15a58f8-ebca-49dc-85f0-693dbad0ca8a) — empty-state cards for "MCP connections" vs "API connections," clean separation of connection types
- [Notion — Connecting an AI connector](https://mobbin.com/flows/7ebf1f4f-6b89-4d52-805f-7d201d4a8e8e) — OAuth-style permission review screen, steal the "what this agent can view / what it can take action on" split
- [Rows — Adding an OpenAI account](https://mobbin.com/flows/538b98ab-9e6c-4677-af5d-33f6adc9ab6e) — "bring your own key" toggle vs. managed connection, exactly Notron's free-vs-hosted split

**Empty states:** [Linear](https://mobbin.com/screens/c6fde8ef-3e17-42d7-8803-bccfd511b597), [Claude](https://mobbin.com/screens/b77733cb-f80c-4f1f-b697-2283f0517227), [Zapier](https://mobbin.com/screens/eeac704d-4d9e-41af-ae7c-db5e66da6462) — illustration + one-line explanation + single primary button is the universal pattern.

**Model picker:** [Twenty](https://mobbin.com/screens/ecb8c8e1-26f5-431e-9360-8839dbf6d48e) (fast-model dropdown), [Cofounder](https://mobbin.com/screens/37801342-be92-4580-a24e-81e6c9f35b3a) (model list with tier badges), [Retool](https://mobbin.com/screens/fb50d9b9-96bc-4f79-ad46-c19672f1552c) (per-provider enable/disable with "bring your own key" rows) — steal Retool's row shape for Nano/Super/Ultra tier selection.

**Integrations/permissions dashboard:** [Qatalog](https://mobbin.com/screens/2d814275-9aa2-4976-a47b-4c2a101f734b), [Google Gemini connected apps](https://mobbin.com/screens/e90f55bd-da8b-4882-b8ab-591857e2b74c), [Amie calendar connect](https://mobbin.com/screens/93d7121b-27d1-44c5-a25f-6a3f4640404c) — toggle rows with a clear "what this grants" line under each app name. This is the direct template for Notron's Notes/Reminders/Calendar permission screen.

**API key entry:** near-universal pattern across [Grok](https://mobbin.com/screens/5fe0f0c1-56f6-4d67-942f-604073ce4c72), [LangChain](https://mobbin.com/screens/bf5c0b07-f753-4000-a6e9-69b99c003075), [OpenAI Platform](https://mobbin.com/screens/cf614c48-5aa6-4429-a4fe-5ccda5aaff38) — masked field, "copy now, you won't see this again" warning, inline validation. Steal directly for the Nebius key field.

**Pricing (free self-host vs. paid hosted):** [Slack](https://mobbin.com/screens/dcd2514c-5d0d-4a75-8740-93ed80d18f4e), [Coda](https://mobbin.com/screens/8a724e24-d751-4f02-8a08-629f2381b116), [Chatbase](https://mobbin.com/screens/8a74b51d-ea06-476e-b511-6c8a12544cb6) — 3-4 column ladder, checkmarks, one column highlighted "most popular." Notron only needs 2 columns (Free / Hosted $12), so simplify rather than clone the density.

**Permission-request screens (the "why before the OS prompt" pattern, iOS but transfers directly):** [Greenlight](https://mobbin.com/screens/88cef688-e768-4eef-a949-5a96a8560ced), [stoic.](https://mobbin.com/screens/caad9f7b-a360-4eaa-9c30-12136d6e0f89), [Brick](https://mobbin.com/screens/64bb7139-e3b1-4110-a9c4-233d8b564542) — icon/illustration, one bold headline naming the exact permission, 2-3 lines on why, single CTA. This is the template for Notron's "Allow Notes access" / "Allow Calendar access" screens — critical because CLAUDE.md notes an unapproved app *hangs* rather than failing, so the pre-prompt copy has to set the expectation correctly.

**Developer-tool dark/dense aesthetic (second direction candidate):** [Vapi](https://mobbin.com/screens/1556c026-06a7-46ca-b9d1-c9206df8d439), [Cursor](https://mobbin.com/screens/afdecea8-8cc3-4888-aa37-d7bf7a08442f), [Cloudflare AI Gateway](https://mobbin.com/screens/aa42e062-b4a1-4063-8843-7e733b2e0f28), [Bolt.new](https://mobbin.com/screens/e4c69ecf-2faa-4b2d-9ce9-a41a3c9f165c) — dark ground, single accent, monospace touches, dense settings rows.

**Distinct-app count: 100+** (well over the 10-app floor) across Craft, Notion, Rows, Vercel, Gorgias, StackAI, Chatbase, Zapier, Claude, Linear, Writer, SchoolAI, Plain, Tines, Sentry, ClickUp, Ferndesk, Aboard, Cloudflare, Relevance AI, ElevenLabs, Zendesk, Lindy, Wise, Twenty, Cofounder, Retool, Descript, Hume AI, PlayAI, Vapi, Sana AI, Google AI Studio, Krea AI, Langdock, Leonardo AI, Magnific, Peec AI, Midjourney, Gamma, Qatalog, Google Gemini, Mistral AI, Amie, Hex, Duolingo, Google Drive, Perplexity, Hotjar, Base44, Revolut Business, Clay, Slite, Grok, LangChain, Mailchimp, Manus, Buffer, Airwallex, Whop, AutoSend, Cursor, Copy.ai, OpenAI Platform, Adaline, Resend, Slack, Frame.io, Elicit, Miro, GitHub, User Interviews, Fabric, Grammarly, Better Stack, Coda, Synthesia, Dribbble, Kajabi, Cake Equity, Webflow, Jira, Assembly, AWS, Suno, Anthropic Claude, Neon, and 20+ mobile onboarding/permission apps (Greenlight, stoic, Brick, State Farm, Monzo, Duolingo ABC, Azar, BeReal, Clubhouse, etc.)

**Gap worth naming:** Mobbin's catalog skews web/mobile — true native macOS menu-bar chrome (the actual surface Notron's GUI lives in) is thin on the platform. Design decision: borrow the *information architecture* from these web settings panels (integrations list, model picker, API key row) but render it in native macOS menu-bar/window chrome, not a web-app shell.

## Design implications

1. Lead the onboarding with the Notes/Reminders/Calendar permission screens, styled like Greenlight/Brick — explain *why* before the OS prompt fires, since CLAUDE.md confirms an unapproved app hangs rather than erroring.
2. The API key field is a solved pattern — copy it exactly (masked, paste button, "won't see this again," inline validate), but make BYO-key vs. hosted-brain a first-class toggle at the top, not a settings sub-page (Rows' pattern).
3. Model/tier picker should look like Retool's provider rows, but simplified to 3: Nano (fast), Super (smart), Ultra (deep) — matching `brain.DEFAULT_MODELS` exactly so the GUI never drifts from what the agent actually runs.
4. Integrations/permissions screen = one list (Notes, Reminders, Calendar, Tavily web search) with a status pill per row, mirroring Qatalog/Amie — this is also where "no Terminal" gets proven visually.
5. Two directions worth drafting at Gate B: (A) light, Apple-native, friendly — matches the "friend, not tool" voice from the README; (B) dark, dense, single-cyan-accent developer-tool look — matches the existing `mac/` Swift app's likely default and the house palette rule (whites/blacks/greys/blues, cyan accent).
