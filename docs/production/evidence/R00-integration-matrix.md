# R00 Task 2 — integration matrix and Siri feasibility

**2026-09-21.** Measured on this machine, not recalled. Every row below is either
a probe result with the command that produced it, or explicitly marked pending.

Task 1 (baseline repair) is recorded in
[the R00 Task 1 handoff](../handoffs/2026-09-21-R00-task-1.md).

---

## 1. The machine, as probed

| | |
|---|---|
| Device | MacBook Pro `Mac16,8`, Apple **M4 Pro**, 48 GB |
| OS | **macOS 26.2** (build 25C56), arm64 |
| Xcode | **26.2** (17C52) |
| Swift | **6.2.3**, default target `arm64-apple-macosx26.0` |
| SDKs present | `macosx26.2` only — **no macOS 27 SDK** |
| Locale / language | `en_US` / `en-US` |
| Siri state | `com.apple.assistant.support`: `Assistant Enabled = 1`, `Dictation Enabled = 1`; `com.apple.Siri`: `GMSignedUp = 1` |
| Signing | bundle id `com.m1labs.notron`; **no signed identity for the Python side** — P06 pending |

Siri AI is English-first, and this machine is `en-US`, so the language gate is
satisfied. That is the only availability question this probe can settle without a
voice interaction.

---

## 2. App Intents SDK surface — measured against the installed SDK

Read directly from
`MacOSX26.2.sdk/.../AppIntents.swiftmodule/arm64e-apple-macos.swiftinterface`
rather than inferred from the OS version.

**ABSENT on this SDK** — these cannot be compiled against, so no plan may depend
on them before October 30:

| Symbol | Consequence |
|---|---|
| `LongRunningIntent` | The 30-second intent timeout **cannot be raised** |
| `CancellableIntent` / `IntentCancellationReason` | Siri-side cancellation is unavailable |
| `performBackgroundTask` | No background escape hatch from the intent |
| `SyncableEntity` | No stable cross-device entity IDs |
| `AppSchema` | Not present — on this toolchain `AssistantSchemas` is the only namespace |
| `OwnershipProvidingEntity`, `IntentExecutionTargets`, `AppIntentsTesting` | Unavailable |
| `NotesIntent` / `NotesEntity` | **0 references** — the `.notes` domain is not in this SDK |

**PRESENT, and earlier analysis was wrong about them:**

| Symbol | Availability | Why it matters |
|---|---|---|
| `IndexedEntity` | **macOS 15.0+** | Semantic entity indexing is **buildable today** |
| `AssistantSchemas` | **macOS 13.0+** | The live schema namespace here, not deprecated |
| `AssistantSchemaEntity` | macOS 15.0+ | Usable now |
| `ProgressReportingIntent` | macOS 14.0+ | Exposes `progress: Progress`. **Untested** — whether it buys time past the 30s wall is unknown and must not be assumed |
| `IntentResultContainer` | present | — |

> **Correction to the September 20 review.** That document concluded the entire new
> API surface required upgrading to macOS 27. Measured, that is **half wrong**.
> `IndexedEntity` has been available since macOS 15, so the "the login screen
> thing" resolving to a real entity — the feature the review called a roadmap
> slide — is buildable on the current toolchain. What genuinely requires macOS 27
> is long-running and cancellable intents, and the `.notes` schema domain. The
> review's recommendation not to upgrade the OS still stands; its claim about
> what is unreachable does not.

---

## 3. The Siri path as it actually stands — **blocked, on three measured causes**

`AskNotronIntent.perform()` is **synchronous**: it shells `Core.run(["ask",
"--quiet", …])`, waits, and returns the answer as its dialog. Its latency *is*
Siri's acknowledgment latency, against a 30-second wall that cannot be raised
(§2). So this is the measurement that matters, and it cannot yet be taken,
because the path does not run.

### 3a. The app has no executable

```
mac/Notron.app/Contents/          Info.plist  PkgInfo  MacOS/  _CodeSignature/
mac/Notron.app/Contents/MacOS/    (empty)
```

`_CodeSignature/CodeResources` shows the bundle was signed when a binary existed;
the binary is gone because `mac/Notron.app/` is gitignored. LaunchServices still
carries a stale record (`executable: Contents/MacOS/Notron`), so this is a
partially-deleted artifact, not a deliberate stub.

`pluginkit -m -v | grep -i notron` → **nothing**. No App Intents extension is
registered.

**Consequence: the phrases `"Ask Notron <request>"` and `"Notron, <request>"` are
not registered with the system, because the app can never have launched. The
branded App Shortcut — the fallback the whole design rests on — does not work on
this machine today.**

### 3b. `notron ask` refuses without credentials

```
$ .venv/bin/python -m notron ask --dry-run --quiet "what did I decide about pricing"
exit=2 in 0.52s
stderr: Protected processing paused. Secure storage requires setup or recovery.
```

This is the P01 secure-startup gate working as designed, and `_brain()` runs
before any Notes access, so nothing was touched. `Core.run` treats a non-zero exit
with empty stdout as a `Failure`, so the intent would speak *"Notron hit a problem:
Protected processing paused. Secure storage requires setup or recovery."*

**Consequence: even with a launchable app, asking Siri today returns an error
dialog.** Clearing this needs credential/secure-storage setup, which is P01/P06
work and an explicit owner action — not something a probe may bypass. The plan is
explicit that the startup gate must not be flipped to make a probe pass.

### 3c. The Python floor, measured

| Command | Time | Note |
|---|---|---|
| `python -m notron --help` | **0.50–0.64 s** (3 runs) | interpreter + import cost |
| `python -m notron graph` | 0.50 s | no credentials, no network |
| `python -m notron ask --dry-run --quiet` | 0.52 s (exit 2) | fails at the credential gate |

**This ~0.5 s is a fixed floor on every Siri request before any model call.**
It is small enough not to threaten the design, but it belongs in the latency
budget, and it is the one number here that will still be true after the blockers
clear.

### The one thing that was *not* blocked: packaging

SwiftPM cannot produce App Intents metadata. `swift build` emits no const values,
`swift build --help` has no App Intents support, and a SwiftPM-built app is
therefore invisible to Siri regardless of the code it contains.

**The pipeline works anyway, and that is now proven rather than assumed.** See
[`scripts/probes/appintents_metadata.sh`](../../../scripts/probes/appintents_metadata.sh),
which reproduces this from scratch:

```
Metadata.appintents/extract.actionsdata   (2402 bytes)
  "fullyQualifiedTypeName" => "Notron.AskNotronIntent"
  "formatString"           => "Ask Notron ${request}"
  phraseTemplates          => "Ask ${applicationName} ${request}"
                              "${applicationName}, ${request}"
  "systemImageName"        => "brain.head.profile"
  "assistantDefinedSchemas" => []          ← empty
```

Three findings worth keeping, each of which cost a failed attempt:

1. The flag is **`-emit-const-values-path`**, not `-emit-const-values`.
2. **`-const-gather-protocols-file` takes a JSON array of bare protocol names** —
   `["AppIntent","AppEntity"]`. Newline-separated, module-qualified and JSON-object
   forms all fail with *"input file is malformed"*.
3. `--disable-index-store` is mandatory, or whole-module optimisation collides
   with SwiftPM's per-file index paths: *"index output filenames do not match
   input source files"*.

**Consequence for P06: converting the app to an Xcode project is not required.**
The existing SwiftPM package can produce a Siri-registrable app via a packaging
step. That removes the largest unknown from the Apple half of the submission.

Note `"assistantDefinedSchemas" => []` — the same emptiness measured in Apple's
own Notes.app (48 of 48). Notron registers hand-authored phrases, not schemas,
exactly as Apple's Notes does. This is honest and it is worth saying out loud
rather than implying schema-level integration.

---

## 4. Decision matrix

| Path | Verdict | Blocked by | Owner action |
|---|---|---|---|
| **Branded App Shortcut** (the mandatory fallback) | **Blocked, two fixable causes** | (a) empty app bundle — §3a; (b) credentials — §3b | P06 packaging + credential setup |

> **The fallback has to be true before the demo can be, and it is the one *planned*
> thing that is not yet working.** Because `LongRunningIntent` is absent, an
> asynchronous start-then-poll design is not a preference — a synchronous intent
> is capped at 30 s and the verbatim `notron ask` path is already ~0.5 s of Python
> before any inference. That design is now justified by measurement rather than
> by argument.

| Path | Verdict |
|---|---|
| **Async Siri entry (start → task ID → poll)** | **Required.** Forced by the absent `LongRunningIntent`, not stylistic |
| **Typed `AppEntity` + `IndexedEntity`** | **Available now** (macOS 15+). Optional for October; buildable without an OS upgrade |
| **`ProgressReportingIntent`** | **Untested.** Present since macOS 14; may or may not extend the wall. Do not rely on it before measuring |
| **`LongRunningIntent` / `CancellableIntent`** | **Unavailable.** macOS 27. Roadmap slide, not a build item |
| **`.notes` schema domain** | **Unavailable.** macOS 27, and Apple's own Notes does not use the public path anyway |
| **Phone → Mac relay** | **Excluded**, per the roadmap |
| **Claude session adapter** | **Contract pinned; live probe pending an owner credential decision.** See §5 |

---

## 5. Claude session probe — contract delivered, live half pending

Delivered and tested:

- [`scripts/probes/claude_session_probe.py`](../../../scripts/probes/claude_session_probe.py) —
  `run_probe(client)`, three injected calls, no SDK import.
- [`tests/test_claude_probe_contract.py`](../../../tests/test_claude_probe_contract.py) —
  **5 passed**, including the plan's required refusing-client case.
- Full suite: **1349 passed**, up from 1344.

The probe enforces two rules worth more than the probe itself: a capability is
reported only when the provider **affirmed** it (a `"true"` string is not an
acknowledgement), and `cancel_acknowledged` requires an explicit provider
acknowledgement because **killing a local process does not prove remote work or
billing stopped**.

**Not done, and not going to be guessed:** the live three-call probe against a
real account, hostile denial fixtures (Bash, edits, writes, unselected projects),
budget/turn/time enforcement, and connectivity death after submission. These need
an Anthropic credential decision that is the owner's to make, and per the
September 21 rubric correction Claude is now **optional, disclosed,
bring-your-own capability** rather than the first workflow — so this is correctly
off the critical path. `scripts/probes/claude/pyproject.toml` and its lock are
deliberately **not** created: there is no pinned SDK version to lock until that
decision is made, and fabricating one would put a fake artifact in the evidence.

---

## 6. The remaining live check — owner, ~15 minutes

Everything below needs a human at the machine. Do these in order.

**Prerequisites (both are P06/P01 work, not this probe's):**

1. Populate and sign `Notron.app` from the SwiftPM build, including the
   `Metadata.appintents` from `scripts/probes/appintents_metadata.sh`.
2. Make `notron ask --quiet "hello"` return words instead of exit 2.

**Then:**

3. Launch the app once so the system registers the App Shortcut. Confirm the
   phrases appear in System Settings → Apple Intelligence & Siri, or via
   Shortcuts.app.
4. Say **"Hey Siri, ask Notron what did I decide about pricing"** and record:
   - time from end of speech to first spoken word (**the number the plan targets
     at ≤5 s in ≥9 of 10 trials**)
   - whether it completed or hit the 30 s timeout
   - whether Siri offered the shortcut unprompted after one or two uses
5. Repeat 10× and record the distribution, not the best run.

Record the results here. Until then this row is **pending**, and the ≤5 s target
is a project target, not a measured result.

---

## 7. What this task did *not* establish

- **No voice interaction was performed.** No Siri acknowledgment latency was
  measured. The ≤5 s / 9-of-10 target is untouched.
- **No signed identity, no notarization, no DMG.** P06.
- **No Claude SDK was installed, called, or qualified.** Contract only.
- **`ProgressReportingIntent` was found, not tested.**
- **EventKit/Automation permission behaviour is unverified on this install**, and
  every fresh install starts at `notDetermined` (see `CLAUDE.md`). Python tests
  cannot establish native permission safety.
- The `macos-26` CI job builds the app target but does **not** run the metadata
  processor, so the packaging path proven here is not yet CI-covered. Wiring it
  into `service.yml` would be the cheapest way to keep it from rotting.
