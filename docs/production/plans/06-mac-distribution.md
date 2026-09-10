# P06 — Production Mac application and distribution implementation plan

**September 10 integration with the new roadmap:** P06 remains required.
Tasks 1–3 are on the critical path before R03's real Siri demo; Tasks 4–6 qualify
the developer-preview installation by October 9. Reuse P01's existing paths,
encrypted migration and key handling. Bundle R02's qualified adapter runtimes,
not just core Python. Qualify plugin subprocess signing, credential IPC and
permission identity separately; plugins must never inherit an Apple Notes writer
capability simply because they ship in the app. [R03](R03-siri-experience.md) owns connection/task UI and
consumes this plan's async process runner. Its supervised plugin lifecycle must
also stop/recover correctly on quit, sleep and upgrade. BYO developer setup is
the first preview path; managed P05 staging and paid onboarding remain open.
Manual signed full-installer updates are sufficient for the preview; Sparkle
and the full paid-release gates remain required before public paid launch.

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Before UI edits read `docs/design/DESIGN.md` and reuse `DesignSystem.swift` tokens.

**Goal:** A developer installs Notron, chooses Notes and connection access, sees a useful result and can stop/remove it without using Terminal; the same safe foundation supports the later consumer release.
**Architecture:** Portable bundled Python, Application Support state, stable native helper identity, explicit onboarding state machine, health-backed menu, signed distribution/update chain and safe migration.
**Tech Stack:** Swift/SwiftUI/AppKit, macOS Security and EventKit, Python 3.11+, launchd/appropriate supported login-item APIs, Developer ID/notarization, proposed Sparkle 2 public updater.
**Spec:** [Shared design §§1–2, 6](../design.md).
**Dependencies:** P01 credentials/policy; P02 worker/health; P03 conversation/help. Runtime work can precede P05, but the managed paid path cannot be marked complete without P05. BYO signed builds remain usable and are not paywalled.

## Global constraints

- macOS 14+; initial release Apple silicon. No developer paths, `.venv` dependency or mutable files inside `.app`.
- First reading/sending of Notes content follows user selection. Notes-only setup works without Calendar/Reminders.
- Closing a settings window leaves service running; `Quit Notron` stops it. Pause/Resume explicit.
- No fresh-account permission claim based on Terminal-granted permissions.
- UI uses existing tokens and plain language. Update conflicting old no-skip onboarding copy as part of the feature; the owner approved optional grants.

## Task 1 — Runtime layout and safe legacy migration

**Files:** Modify existing `notron/paths.py`, `notron/migration.py`, `tests/test_cache_migration.py`; Create `tests/test_paths.py`, `mac/Sources/Notron/RuntimePaths.swift`; Modify `Core.swift`, `NotronApp.swift`, `notron/cli.py`, all `.notron` path constants in brain/index/library/rewrite/undo/watch/mentions/care/reflect/filer/applescript and corresponding tests.
**Consumes:** P01 encrypted/atomic stores and validated legacy state.
**Produces:** `RuntimePaths` for immutable resources, Python/helper executable and writable state; `state_root()` in Python; versioned migration journal and backup.

- [ ] Test runtime selection with no repository on disk and read-only app resources:

```python
def test_production_state_is_outside_app(monkeypatch, tmp_path):
    from notron.paths import state_root
    monkeypatch.setenv('NOTRON_DEV_MODE', '1')
    monkeypatch.setenv('NOTRON_STATE_DIR', str(tmp_path / 'Application Support' / 'com.m1labs.notron'))
    root = state_root()
    assert root == tmp_path / 'Application Support' / 'com.m1labs.notron'
    assert '.app' not in root.parts
```

- [ ] Reconcile the already implemented P01 migration before modifying it. Preserve its explicit offline initialize/migrate/accept sequence, encrypted backups and refusal to auto-import secrets. Add `state_root()` as a tested accessor around the existing `DATA_DIR`, not a second competing root. Development override requires explicit development mode; production ignores it. Do not replay accepted migrations or replace working retention behavior.
- [ ] Production Swift computes paths from Bundle URLs and the user's Application Support; Python receives only explicit nonsecret path configuration. Development overrides are opt-in and tested. Reject writable resource/state confusion and missing bundled interpreter with a useful error.
- [ ] Migration stops the old listener, copies validated choices/state to a versioned backup, invokes P01 sanitization/encryption, verifies counts/schema, then atomically switches roots. On failure retain old data and paused state. Do not automatically migrate raw `.env` secrets or upload an index.
- [ ] Run migration twice to prove idempotence; interrupt each phase; test existing user notes are untouched and a failed migration does not launch either worker. Run targeted tests and commit.

## Task 2 — Subprocess bridge, native helpers and permission identity

**Files:** Modify `mac/Package.swift`, `mac/Info.plist`, `mac/Sources/Notron/Core.swift`, `AskNotronIntent.swift`, `Onboarding.swift`, `notron/permissions.py`, `notron/eventkit.py`; Create `mac/Tests/NotronCoreTests/CoreProcessTests.swift`, `mac/Sources/NotronHelpers/`, `mac/Entitlements/`, `tests/test_permission_protocol.py`, `docs/production/evidence/P06-permission-identity.md`.
**Consumes:** P01 KeychainStore and RuntimePaths; fixed permission/action JSON protocol.
**Produces:** A testable subprocess runner with timeout, cancellation, concurrent stdout/stderr draining, explicit exit status; helper `permissions check|request notes|calendar|reminders` returning typed status.

- [ ] Reproduce pipe saturation with a synthetic child that writes more than a pipe buffer to stdout and stderr; ensure UI stays responsive and timeout cancels the process. Nonzero exit with nonempty output must still be treated as failure.

```swift
func testNonzeroExitIsFailureEvenWithStdout() async throws {
    let result = try await ProcessRunner.run(
        executable: URL(fileURLWithPath: "/usr/bin/false"), arguments: [], timeout: 2)
    XCTAssertNotEqual(result.exitCode, 0)
}
```

Create `ProcessRunner` in the extracted core target with `ProcessResult(stdout, stderr, exitCode)` and a cancellation-safe async API. Add a second fixture executable for the nonempty-output failure and large-pipe cases; `/usr/bin/false` proves exit handling only.

- [ ] Move Siri's blocking subprocess call off the main actor. Resolve Swift concurrency warnings rather than suppress them; make state transitions testable without opening real Notes.
- [ ] Implement explicit Calendar/Reminders full-access requests where the permission-owning helper supports them; status reads do not request access. Add Apple Events and EventKit usage descriptions/appropriate entitlements to the actual responsible signed binaries. Do not indiscriminately grant all entitlements to every executable.
- [ ] On a fresh test macOS account, exercise GUI → helper → Notes/EventKit, then launchd worker → same interfaces, reboot/login, revoke and regrant. Record which signed identity receives each grant. Keychain access must succeed for the worker with GUI closed and fail safely while unavailable.
- [ ] If JXA cannot share the stable approved identity in the shipped process chain, implement the signed Swift EventKit helper in this task with the same typed adapter interface. Do not compensate by asking Becky to grant Terminal permissions. Document evidence and final architecture.
- [ ] Run Swift/Python protocol tests and the real permission matrix. Commit; do not mark this task complete with mocks alone.

## Task 3 — Worker installation, status and explicit stop

**Files:** Create `mac/Sources/Notron/ListenerModel.swift`, `mac/Tests/NotronCoreTests/ListenerModelTests.swift`; Modify `NotronApp.swift`, `Core.swift`, `notron/watch.py`, `notron/cli.py`.
**Consumes:** P02 health JSON, pause/resume and single-worker controls; bundle runtime paths.
**Produces:** Start/stop/pause/resume UI, correct background registration, health-derived menu and tested uninstall helper.

- [ ] Test ready vs registered-but-dead, permission-needed, offline, paused and stale heartbeat. Mood never substitutes for health.
- [ ] Install the background process pointing at bundled executables, with state/log files in Application Support. Verify supported macOS login/background registration and stable identity before choosing the final launchd/SMAppService mechanism; record exact registration in the permission evidence file.
- [ ] Make menu `Quit Notron` request worker shutdown, wait with a bounded deadline, show a failure if shutdown cannot be verified, and then close. Closing the settings window does not quit. Provide Start later users a visible Start control.
- [ ] Test reboot/login, moving the app to Applications, replacing a version, deleting/reinstalling the app, and changing runtime paths. Never leave two enabled agents. Run Python lifecycle and Swift model tests; commit.

## Task 4 — Ordered onboarding with real first success

**Files:** Modify `Onboarding.swift`, `OnboardingView.swift`, `YourNotesView.swift`, `Library.swift`, `RewriteDefault.swift`, `NotronApp.swift`, `docs/design/04-onboarding-flow.md`, conflicting passages in `docs/design/DESIGN.md`; Create `mac/Tests/NotronCoreTests/OnboardingStateTests.swift`.
**Consumes:** P01 ready policy and credentials, P05 account/errors or BYO mode, P02 health, P03 help.
**Produces:** Resumable onboarding state machine persisted only after completed milestones; no listener activation before policy approval.

- [ ] Test state transitions in a pure model before wiring screens:

```swift
func testCannotStartBeforeNoteSelection() {
    var state = SetupState()
    state.notesPermission = .granted
    state.aiAccess = .ready
    XCTAssertFalse(state.canStart)
    state.noteSelectionSaved = true
    state.preparationComplete = true
    XCTAssertTrue(state.canStart)
}
```

- [ ] Implement the spec sequence: explain cloud/access → Notes grant → choose notes/homes → managed login or BYO key → optional Calendar/Reminders → seed/index selected notes → start → explicitly requested first-success demo. Preparation has progress/cancel; no inference before valid choices/credentials.
- [ ] Allow zero homes and Notes-only mode; explain resulting capabilities. New notes remain unread until permitted under P01. Never imply Ignore is a model-only exclusion while silently indexing it elsewhere.
- [ ] Explain Mac sleep, Brain Dump's 15-minute settling wait, direct filing command and Ask follow-ups. Only advertise the mobile Shortcut after P04's device gate. Do not promise a universal response time or unattended phone processing.
- [ ] Recovery screens handle denied/revoked access, missing iCloud/shared account, offline provider, empty Notes library, existing duplicate system notes, incomplete migration and unfinished setup. Check that intended system notes are in a syncing account; if not, describe Mac-only operation and offer a deliberate user-selected syncing destination instead of moving existing notes silently.
- [ ] Mark onboarding done only after successful preparation and verified user-approved test, or explicitly persist a paused incomplete state. Managed subscription cancellation does not block viewing choices or managing local data.
- [ ] Run state tests, keyboard/VoiceOver/Dynamic Type/reduced-motion checks and fresh-account walkthrough; commit evidence and UI.

## Task 5 — Reproducible app and notarized DMG

**Files:** Create `scripts/build_macos.py`, `scripts/package_macos.py`, `scripts/verify_release.py`, `mac/runtime-manifest.json`, `mac/RELEASE.md`, `.github/workflows/macos-release.yml`, `tests/test_release_manifest.py`.
**Consumes:** Bundle/runtime layout, dependency lockfiles, verified helper identity; owner-provided Developer ID/team credentials only at signing time.
**Produces:** A reproducible arm64 app and notarized/stapled DMG with hashes, license notices, version/build IDs and verifiable nested signatures.

- [ ] Bundle a redistributable Python runtime with pinned version/source/checksum and compatible minimum macOS deployment target. Install pinned desktop dependencies into the bundle; no `pip install` or developer tool download during user onboarding. Validate runtime licensing and include notices.
- [ ] Release scripts validate required inputs and refuse unsigned public output. Sign nested Python/extensions/helpers/app with appropriate hardened-runtime settings, notarize with `notarytool`, staple and verify. No private signing files or passwords committed.
- [ ] Verify output using these commands against the actual artifact path supplied to the script:

```text
codesign --verify --deep --strict --verbose=2 <built-app>
spctl --assess --type execute --verbose=2 <built-app>
xcrun stapler validate <built-app>
xcrun stapler validate <built-dmg>
```

`<built-app>`/`<built-dmg>` denote runtime artifact arguments, not literal shell paths. `verify_release.py` accepts `--app PATH --dmg PATH`, constructs argv arrays and records actual paths/results. Inspect nested signatures explicitly; `--deep` verification does not replace correct nested signing.

- [ ] Test the downloaded/quarantined DMG on a fresh account with no repo, `.venv`, API key or Terminal grants. Verify app launch, help, permissions, AI access, first useful result, sleep/wake, reboot, upgrade and removal. Never claim a DMG passes because `swift build` succeeds.
- [ ] Record supported OS/hardware matrix, artifact hashes, signing identity and evidence; commit scripts/manifests, not binaries or credentials. Owner approval remains the final step before publishing.

## Task 6 — Updates, uninstall and release handoff

**Files:** Create `mac/Sources/Notron/UpdateModel.swift`, `mac/Sources/Notron/UninstallModel.swift`, `mac/Tests/NotronCoreTests/UpdateTests.swift`, `docs/production/evidence/P06-installation.md`; Modify release scripts, menu and `README.md`.
**Consumes:** Verified app signatures, stable worker lifecycle and state migrations.
**Produces:** Manual signed full-installer pilot update instructions; Sparkle 2 signed in-app update flow before public release; explicit uninstall/data-retention choices.

- [ ] Integrate the upstream-supported Sparkle package/appcast/signature flow using current official instructions; keep update-signing keys outside source and prevent arbitrary feed URL override in production. Pin dependency and test signature rejection, tampered archive, wrong feed, downgrade and incomplete download.
- [ ] Pause worker before replacement, finish/reconcile in-flight operations, migrate state after verified update and restart one worker. Preserve a known-good signed installer and state backup; only restore a compatible schema. Test recovery from failed update without downgrading database files blindly.
- [ ] Uninstall stops/removes background registrations, revokes managed device grant, clears credentials, and offers keep/delete local cache/history separately. It never deletes the user's Apple Notes, Reminders or Calendar data. Explain `.app` deletion alone if it cannot guarantee complete cleanup.
- [ ] Run all desktop tests and release validation; produce P06 evidence showing which checks were real, mocked or blocked by external inputs. Hand off signed candidate to P07; do not distribute automatically.

**Exit gate:** A real fresh-account installation works without development dependencies; permission identity, first success, pause/quit, update and uninstall are proven. Managed paid onboarding additionally requires P05.

**Primary references:** [Apple Developer ID](https://developer.apple.com/developer-id/), [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [native Keychain](https://developer.apple.com/documentation/security/keychain-services). Verify the exact helper/login-item/update APIs against the deployment target when implementing.
