# P06 Task 2 (part 1) — the credential bridge

**2026-09-21.** The first half of P06 Task 2: the credential helper, the signed
bundle verification that gates it, and the replacement of the placeholder
startup gate. The permission-identity half of Task 2 (fresh account, TCC grants
per signed identity, reboot/login) is **not** done and is recorded as pending.

---

## 1. What was blocking, and what now works

`credentials.startup()` used to raise unconditionally:

```python
def startup() -> None:
    # Deliberate native release gate, not an environment-controlled opt-out.
    configure(None)
    raise CredentialUnavailable('Secure startup requires the signed Keychain integration in P06.')
```

That was not "a credential the user had not entered yet". It was a hardcoded
refusal with nothing behind it, so `notron ask` — and therefore the entire Siri
path — could not run at all. `transport.py` said what was owed: *"P06 must
replace credentials.startup with signed-bundle verification first."*

It is now a real check. Verified end to end on this machine:

| Step | Result |
|---|---|
| `bundle.keychain_helper()` | `.../mac/Notron.app/Contents/Helpers/NotronKeychainHelper`, real `codesign` verification |
| `credentials.startup()` | configured a `KeychainStore` (was: raised) |
| `credentials.get('storage-key')` | `None` — a **real Keychain read** through the helper, item genuinely absent, no error |
| `credentials.require('nebius-api-key')` | `Required credential missing; protected processing paused.` |
| `credentials.get('managed-refresh')` | refused before reaching the helper |

The fourth row is the honest new state: the gate is passed, and what is missing
is now a genuine secret rather than a missing implementation.

**Tests: 1361 passed, 0 failed**, on 3.14.2 and on the declared 3.11.14 floor.
Swift: 17 tests passed.

---

## 2. What was built

- **`mac/Sources/NotronKeychainHelper/main.swift`** — the helper's entry point.
  Rejects anything that is not exactly `--credential-fd <n>` with a silent
  non-zero exit; a helper that explains itself on a bad argument can be probed.
- **`mac/Sources/Notron/KeychainStore.swift`** — moved into `NotronCore` and made
  public. It previously held a `#if NOTRON_KEYCHAIN_HELPER` `@main` that could
  never fire, because SwiftPM built the app target only.
- **`notron/bundle.py`** — finds the app bundle and verifies it before returning a
  helper path. Two rules: the bundle is *derived, never supplied* (no environment
  override, so "verify then execute" cannot become "execute what you were told"),
  and verification *fails closed* (bundle id, Team ID, `codesign --verify
  --strict`, and the helper's own nested signature).
- **`notron/credentials.py`** — `startup()` now resolves and configures; on any
  refusal it calls `configure(None)` and raises, so every protected command stays
  paused.
- **`scripts/probes/assemble_app.sh`** — places the helper at
  `Contents/Helpers/`, signs it **before** the bundle, and verifies both.
- **`tests/test_bundle.py`** — 12 tests. Another team's signature is refused;
  a valid bundle with a swapped helper is refused; nothing is executable before
  verification; no environment variable changes the answer.

### Two existing tests changed, deliberately

`test_default_native_startup_remains_gated` and
`test_startup_cannot_be_enabled_by_environment` asserted the old unconditional
raise. **They were not deleted — their properties were retargeted**, because the
properties matter more than the mechanism: startup still refuses, still leaves
`_provider` unset, still starts no process, and still cannot be enabled by
environment. A fourth hostile variable (`NOTRON_BUNDLE`) was added to the list.

---

## 3. Three SwiftPM traps, each with a message that did not name its cause

Recorded because each cost a build cycle and the diagnostics were misleading:

1. **Two executable targets and no `products:` declared** → SwiftPM built only the
   app and **silently skipped the helper**. No error, no warning; just no binary
   at packaging time.
2. **Declaring the products** → `target 'NotronKeychainHelper' referenced in
   product ... is empty`, because `exclude` removes the very file `sources` names
   back. **`exclude` wins over `sources`.**
3. **Fixing that** → `target has overlapping sources`. This one is structural:
   SwiftPM forbids one source file in two targets of the same package, so the app
   and the helper cannot share `KeychainStore.swift`. Resolution: it lives in
   `NotronCore` and both depend on that library. **This is the correct outcome
   anyway** — two copies of a credential boundary would drift.

A fourth, in the metadata step: `appintents_metadata.sh` builds with `-wmo`, and
once the helper existed that build tried to link it, failed, and aborted *before*
the app module's const values were written — which surfaced as "No const values
emitted", three steps from the cause. It now builds `--product Notron`, which is
also correct: only the app module carries App Intents.

---

## 4. The finding that matters for the next step

**There is no way to provision the Nebius API key.** The only credential write
path in the whole codebase is `credentials.provision_storage_key`, which writes
`storage-key`. Nothing writes `nebius-api-key`; `network.py` only reads it.

So `notron storage initialize` can create the storage key, and `notron ask` will
still stop at:

```
Required credential missing; protected processing paused.
```

Closing that needs either a small BYO-key provisioning command (a CLI addition,
~an hour) or P06 Task 4's onboarding flow, which the plan already specifies
(*"managed login or BYO key"*) and which does not exist yet.

---

## 5. Still pending in P06 Task 2

- **Permission identity on a fresh macOS account.** Which signed binary receives
  each TCC grant, GUI → helper → Notes/EventKit, launchd worker → same
  interfaces, revoke and regrant. Needs a human at a fresh account; not started.
- **`ProcessRunner`** with cancellation-safe async API and pipe-saturation
  handling. Partially done: this is a *different* concern from the credential
  helper, which uses its own socketpair.
- **Moving Siri's blocking subprocess call off the main actor**, and resolving
  the 88 `#SendableClosureCaptures` warnings rather than suppressing them.
- The app has still never been launched, so App Shortcut phrases are still not
  registered. Scoped and verified (`Metadata.appintents` reaches the bundle) but
  unproven at the system level.
