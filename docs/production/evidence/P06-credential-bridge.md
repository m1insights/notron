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

## 4. Provisioning a key: `notron key`

Added because the gap was real — before this, `provision_storage_key` was the
**only** credential write path in the codebase, so nothing could put a Nebius key
in the Keychain and `notron ask` could never get past "Required credential
missing".

```
notron key list                 # names and presence, never a value
notron key set nebius-api-key   # secret read from stdin
notron key delete nebius-api-key
```

Design points that are load-bearing:

- **The secret never touches argv.** argv is visible to every process on the
  machine and lands in shell history, so the value is read from stdin —
  `printf '%s' "$KEY" | notron key set nebius-api-key` or an interactive paste with
  echo disabled. There is deliberately no argument that accepts it.
- **`PROVISIONABLE` excludes `storage-key` and `managed-refresh`.** The storage key
  is generated by `storage initialize` against an empty destination; refresh
  material is native-only and the helper refuses it. A general "store a key"
  command must not be able to write either.
- **Write, then read back.** A store that did not take fails at once instead of at
  the first inference call.
- **A paste accident is refused at the door.** Multi-line, empty, oversized, or
  internally-spaced input is rejected. It would otherwise store fine and fail much
  later, far from the cause.
- `key set` reports the *specific* reason. `main()`'s generic "protected processing
  paused" is right for an agent command and useless for a setup command.

### What is verified, and what is not

| Path | Status |
|---|---|
| `key list` | **Verified** — prints the three names with presence |
| `key delete` | **Verified** — removes the item |
| Bundle resolution + `codesign` verification | **Verified** — real signatures, refuses other teams |
| `get` through the helper | **Verified** — a real Keychain read, `missing` for an absent item |
| **`put` through the helper** | **NOT VERIFIED — see below** |

**`put` has never been observed to succeed.** It returns `unavailable`. The cause
is the development runner, not the code:

```
$ security add-generic-password -s com.m1labs.notron -a probe -w synthetic-value -U
security: SecKeychainItemCreateFromContent (<default>): UNIX[Operation not permitted]
```

**Apple's own signed tool fails identically from that shell.** A standalone Swift
probe failed with the same status (`100001`) across four attribute variants —
with and without an authentication context, with and without
`kSecAttrAccessible` — which is what an environmental block looks like rather
than an attribute problem. Keychain reads work; writes do not.

**An earlier fix was reverted.** `interactionNotAllowed = true` was suspected of
blocking `SecItemAdd`, and the context was split so writes could interact. The
evidence above does not support that: with no context at all the add still failed.
Weakening a documented security property on a falsified hypothesis is worse than
leaving it alone, so every operation is non-interactive again, and the question is
recorded in `KeychainStore.query` as open. **If writes do turn out to need
interaction, split the context by operation — never weaken reads**, which run in
the background worker where an unanswerable dialog is a hang.

**The owner should run this from a normal login session:**

```bash
printf 'not-real-yet' | .venv/bin/python -m notron key set tavily-api-key
printf '' | .venv/bin/python -m notron key list
printf '' | .venv/bin/python -m notron key delete tavily-api-key
```

If `set` succeeds there, the bridge is complete and the remaining step is a real
Nebius key. If it fails, the status code is the next thing to chase.

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
