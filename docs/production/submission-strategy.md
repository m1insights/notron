# Submission strategy — reading the rubric literally

**2026-09-21.** This document corrects the September 10 repositioning where it
drifted away from what is actually scored. It supersedes §1–§3 of
[repositioning-design.md](repositioning-design.md) on product direction; the
task/plugin contracts there remain binding until a task versions them. It
supersedes the untracked `docs/assessments/2026-09-20-hackathon-and-personal-ai-strategy.md`,
which was written before the Stage One blockers were cleared and is now stale.

## 1. Why this correction exists

The four criteria are equally weighted, and **three of them name Nemotron
explicitly**:

| Criterion | What it actually asks |
|---|---|
| Technological Implementation | How well is it built, **and how effectively does it use Nebius Token Factory / AI Cloud models and NVIDIA Nemotron**? |
| Design | A complete, coherent product experience — not a technical proof of concept. |
| Potential Impact | A credible, specific case for a real audience, **addressed by what is demonstrated**. |
| Quality of the Idea | A creative, **non-obvious use of those models**, and genuine understanding of the problem space. |

The direction agreed on September 20 made the **Claude session adapter the first
workflow** — Notron reads an issue, delegates analysis to a Claude session, and
returns the result. Under this rubric that is an own-goal: it moves the
interesting reasoning off the scored models and onto a third-party provider, and
it makes the product's hero moment something NVIDIA's own tooling already does.

**Correction: Nemotron owns the reasoning and the decisions. Claude Code and
Codex are disclosed, opt-in, bring-your-own capability — never the source of a
decision, never the thing the video rests on.** This is also the stronger
engineering position, because it is the only version in which Notron's central
claim survives: *the model proposes, plain code disposes.*

## 2. The idea, in one paragraph

There is no API for Apple Notes. There is no API for Siri's personal context.
A third-party app cannot invoke another app's intents, and Apple's own Notes
does not use the public `.notes` schema path either (verified first-hand on
macOS 26.2: 48 of 48 App Intents declare empty `assistantDefinedSchemas`).
Everyone building personal AI works *beside* Apple because the documented doors
are shut.

Notron goes *through* Apple using the seams that are open and unused: bulk
AppleScript queries addressed by index, EventKit read directly, a headless
`Shortcuts Events` runner, and a per-binary TCC identity. That gives it
something no sandboxed agent framework can have — **the user's own durable,
Siri-writable, cross-device context** — and it is the half of this product that
cannot be copied by adding a feature.

On the other side of that bridge sit agents that can touch real repositories.
The problem is not capability; it is that you cannot currently let one act while
you are away from the keyboard. So:

> **Siri speaks. Nemotron decides. Plain code authorizes. A contained sandbox
> acts. The result comes back into Notes.**

## 3. The non-obvious part, and it is Nemotron

The intuition everyone brings to model tiering is *small model = fast, cheap,
good enough for the easy call*. **We measured it, and on this workload it is
false.**

Recorded 2026-09-01 while choosing the tier for the Brain Dump filer, on the
same prompt: **Nemotron Nano 30B took 43 s on a two-line prompt and 235 s on
five**, padding its answer with irrelevant context; **Nemotron Super 120B
answered in 1.3 s**, exact. The nominally smaller, cheaper model was **30–180×
slower** and less precise.

That inverts the architecture. Tier selection here is not "route the easy work
down"; it is **a function of whether a human is waiting, and of what a wrong
answer costs**:

- **Nemotron Nano 30B** — classification and routing on paths nobody is watching.
  Cheap and fine when the answer is checked by code and a retry is free.
- **Nemotron Super 120B** — everything a person waits on, and everything whose
  error is expensive. The measured sweet spot, not the compromise.
- **Nemotron Ultra 550B** — configured deep tier, optional, off the default path.
- **Qwen3-Embedding-8B** — embeddings, because Nebius serves no NVIDIA embedding
  model. Disclosed rather than hidden.

Two documented Nemotron behaviours then shaped the code, and both are the kind of
finding that only comes from running the thing:

1. **Reasoning is billed against `max_tokens` and is not the answer.** A 500-token
   request can spend all 500 thinking and return an empty string with
   `finish_reason: "stop"` — no error. `Brain.ask` adds reasoning headroom and
   retries once at double budget. `reasoning_effort="none"`, `/no_think` and
   `chat_template_kwargs={"thinking": false}` all failed to disable it.
2. **JSON replies truncate mid-string**, so `Brain.ask_json` degrades through four
   progressively more forgiving parsers and the router falls back to a safe
   intent rather than stopping the graph.

The honest claim for the video is therefore not "we used Nemotron." It is:
**"we measured which Nemotron tier is actually fast for this workload, found it
is not the small one, rebuilt the graph's tiering around that, and reported our
own numbers."** Almost nothing else in this track will have measured anything.

> **Before it goes on camera:** the 43 s / 235 s / 1.3 s figures come from a
> single 2026-09-01 measurement on one prompt. Re-measure, report the new
> numbers, and keep the method visible. A measured number that a judge can
> reproduce beats a vendor claim they can discount.

### The multi-model pattern worth showing

`reflect.py` is the strongest existing evidence of genuine Nemotron fluency and
it is currently invisible in the README. Super proposes ≤3 lessons, **each forced
to quote the transcript verbatim and string-checked in code**; a **separate** Nano
call then verifies those lessons against the standing instructions; the Guard
writes only the survivors. Two different Nemotron tiers, adversarially arranged,
with plain code as the arbiter. That is a real technique, not a call to an API.

## 4. Audience and Impact

**The problem, stated as a risk rather than a productivity claim:** developers now
run coding agents that can read and write real repositories, across several
projects at once. The only options today are *watch it* or *trust it*. Neither
survives being away from the keyboard, and neither leaves an audit trail a person
can read a week later.

**The audience:** developers running local agents across multiple projects —
precisely the audience NVIDIA is addressing with OpenShell and NemoClaw, which
this track names as suggested tooling. That is evidence the organizers consider
it real, not a niche we invented.

**Why Notron rather than the agent's own permission flags:** session permission
modes are per-session, configured by the provider, and forgotten. Notron's grants
are the user's own, bound to a project, a note and an action class, durable, and
enforceable outside the model.

**What must be demonstrated, not asserted:** an unattended request is resolved to
the right project, gated by policy, contained, and audited — and a hostile
instruction in the input fails closed.

## 5. Design: one hero screen, three surfaces, one job each

The September 10 plan added tasks, approvals, connections and a plugin kit
alongside an already-coherent Notes product. Under a criterion that asks for
*coherence*, that reads as fragmentation.

**One hero screen: the task board.** A single view showing each request as a row —
what was asked, which project it resolved to, **the decision and the measured
latency**, whether it is waiting on approval, the contained run's state, and the
receipt. Approval happens inline, bound to a digest of the exact proposal.

| Surface | Its one job |
|---|---|
| **Siri / App Intents** | Speak the request and hear a short status. Never a chat surface. |
| **Mac app** | The hero screen: watch, approve, audit. |
| **Apple Notes** | The grant and the durable receipt. `#notron` on a line in a project's note *is* the connection — it binds the request to the right context and survives on every device with no install. |

Notes is load-bearing here, not decorative and not a competitor to typing into
Claude mobile. **It is how a spoken request acquires a project.** Siri has no idea
which of twenty repositories "the checkout thing" means; the note does. That is
also, precisely, what no other submission in this track can do — there is no API
for it.

## 6. The demonstrated workflow and the three-minute video

```
"Hey Siri, ask Notron to look at why checkout is failing in synqology"
  → App Intent → `notron tasks start --json` → task ID returned immediately
  → NEMOTRON decides: which project, does this need approval, task or question
  → plain-code gates + the Guard authorize (no model in the write path)
  → the task runs contained, under that project's policy
  → the result lands in the project's note and on the hero screen
"Hey Siri, what did Notron find?" → spoken summary
```

| Time | Beat |
|---|---|
| 0:00–0:20 | The problem: agents that can touch real repos, and you are not at the keyboard. |
| 0:20–0:50 | The Siri request, and the task appearing immediately rather than blocking. |
| **0:50–1:30** | **Nemotron decides, on screen** — project resolution and the safety gate, with the tier named and **the measured latency shown**. This is the hero beat, not a subtitle. |
| 1:30–2:10 | Containment. The policy is shown, then the agent is handed a hostile instruction telling it to exfiltrate. **It fails closed, and the denial is in the log.** |
| 2:10–2:40 | The result returns to the note and the hero screen; Siri reads a short summary. |
| 2:40–3:00 | The architecture slide: Siri → App Intents → Nemotron → code gates → Guard → containment → Notes, with the measured numbers and an explicit shipped/planned split. |

The 1:30–2:10 beat is the one worth rehearsing hardest. A submission that attacks
its own agent live and shows the refusal is worth more than three features, and
it is the visible consequence of the invariant this repo already enforces.

## 7. Where the other pieces go

**OpenShell** — containment, not the story. `openshell sandbox create -- claude`
ships `claude`, `codex`, `opencode` and `copilot` in the image, with filesystem
and process policy locked at creation and hot-reloadable L7 network policy. It
converts R00 Task 2's open "can we safely steer a Claude session?" question into
a containable one. **It must not become the reason the project is interesting**,
and if macOS support disappoints the fallback is dispatching as a local
subprocess under the existing Guard and `outbound.py`. The submission does not
depend on it.

**Claude Code / Codex** — disclosed, opt-in, bring-your-own. Labelled in the
video and the README as the user's capability, separate from the scored models.

**Jev** — a measured footnote, off the critical path. It earns nothing on this
rubric. Used as an A/B against the Nemotron routing lane it becomes evidence
rather than a third provider: our numbers against a 127 ms p50 reference. Keep it
that way, and never on guardrail duty — its `state` is data and is not treated as
hostile, while this product's input is user notes and fetched issues.

**The plugin protocol (R04) is cut.** A protocol with one implementation is not
extensibility, it is overhead. Two reviewed adapters plus a documented contract
is the honest version.

## 8. The 39 days

Today is **2026-09-21**; the deadline is **2026-10-30, 10:00 PT**.

| Window | Work | Exit |
|---|---|---|
| Sep 21–23 | **Priority Zero** — ✅ done. Repo public, 63 commits pushed, 1344 tests green on 3.11 and 3.14 and in CI, isolation bug repaired, 3.11 floor fixed. | [Handoff](handoffs/2026-09-21-R00-task-1.md) |
| Sep 24–26 | **R00 Task 2** — the real-device Siri/App Intents probe on macOS 26.2. Measure acknowledgment, confirm the 30-second wall, verify the branded-shortcut fallback. | Measured numbers, not a document |
| Sep 27–Oct 3 | **OpenShell spike** — go/no-go, including whether a Nemotron-backed agent can run inside the sandbox. | Written yes/no and the working commands |
| Oct 4–12 | **The vertical slice** — task ingress, the Nemotron decision lane with its latency recorded, contained dispatch, Notes receipt. | Siri → task ID → decision → contained run → result |
| Oct 13–18 | **The adversarial demo, the hero screen, judge demo mode.** | A judge's first minute works with nothing installed |
| Oct 19–24 | **Video and submission packet**; README narrative corrected. | 3-minute video, every field drafted |
| Oct 25–29 | Buffer. Submit early. | Submitted before Oct 29 |

**The Oct 1 internal demo is no longer a target.** Move it to Oct 6 and scope it
to the slice, or drop it. Do not let it pull work forward in parallel.

## 9. Cut list

Cut from the story (keep the code where it exists — it is finished and it is not
hurting anything): the P05 accounts/billing/managed-service surface · reminders
and calendar from the README headline · whole-library note indexing · the plugin
developer kit (R04) · the Claude session adapter as a first workflow.

Never cut: the Guard · `outbound.py` · the encrypted store · durable task
admission · accurate outcome reporting · **Nemotron runtime use** · the measured
numbers · a judge being able to run something.

## 10. The risks in this plan, stated plainly

1. **The Nemotron latency finding may not reproduce.** Re-measure first. If Super
   is not reliably fast on the new workload, the video claims the opposite of what
   the numbers say.
2. **R00 Task 2 is a real-device probe that has not started.** The whole Apple half
   depends on Siri being available and on the 30-second intent wall behaving as
   the analysis predicts. This machine has no macOS 27 SDK, so `LongRunningIntent`
   is unavailable and the async start-then-poll shape is mandatory.
3. **Two new surfaces in 39 days** — contained dispatch and the task board — on
   top of an unstarted vertical slice. The cut list above is what pays for it.
4. **OpenShell is alpha**, needs a container runtime this machine does not have
   yet, and is the one dependency whose failure would force the fallback design.
5. **The demo's strongest beat depends on a deliberate attack** against our own
   agent. If the containment story is not true by Oct 13, the beat must be
   replaced, not faked.
