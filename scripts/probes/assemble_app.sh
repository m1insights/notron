#!/bin/bash
# Build, assemble and Developer ID sign mac/Notron.app from the SwiftPM package.
#
# MEASURED WORKING 2026-09-21 on macOS 26.2 / Xcode 26.2. This is the first time
# the Apple half of this project has existed as a properly signed artifact:
#
#   Signature=adhoc, TeamIdentifier=not set        (what a SwiftPM build gives you)
#   Developer ID Application: M1 Insights Inc (NW783CVSJH), flags=0x10000(runtime)
#
# Why signing properly matters more than it looks: TCC grants are keyed to the
# *signing identity*, and an ad-hoc identity is derived from the binary, so every
# rebuild silently drops the app's Notes and EventKit grants. That failure
# presents as zero events and no error — the single most dangerous failure mode
# this project has. A Developer ID identity is stable across rebuilds.
#
# `spctl --assess` will report "rejected - source=Unnotarized Developer ID" and
# that is expected here. Notarization is a distribution gate and needs the
# bundled Python runtime (P06 Task 5). It does not affect local testing.
#
# Usage: scripts/probes/assemble_app.sh [signing-identity]
#   Defaults to the M1 Insights Developer ID. Pass "-" to skip signing.
set -euo pipefail

IDENTITY="${1:-Developer ID Application: M1 Insights Inc (NW783CVSJH)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAC="$ROOT/mac"
APP="$MAC/Notron.app"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> building"
# -wmo is not used here: this is a normal build. `swift build` alone does NOT
# produce App Intents metadata, which is what appintents_metadata.sh is for.
swift build --package-path "$MAC" --disable-sandbox --scratch-path "$WORK/build"

echo "==> app intents metadata"
"$ROOT/scripts/probes/appintents_metadata.sh" "$WORK/meta" >/dev/null

echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Helpers"
cp "$MAC/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$WORK/build/debug/Notron" "$APP/Contents/MacOS/Notron"

# The credential helper. Contents/Helpers/ is a location codesign recognises as
# nested code, which is what we want: notron/bundle.py verifies this file's own
# signature before handing its path to KeychainStore, so a helper swapped in
# after the bundle was signed is refused even though the bundle still verifies.
cp "$WORK/build/debug/NotronKeychainHelper" "$APP/Contents/Helpers/NotronKeychainHelper"
chmod 755 "$APP/Contents/Helpers/NotronKeychainHelper"

# METADATA GOES UNDER Contents/Resources/, NOT Contents/.
# At Contents/ it makes codesign fail with:
#   "bundle format unrecognized, invalid, or unsuitable
#    In subcomponent: .../Metadata.appintents"
# because codesign treats the .appintents directory as nested code. Verified
# against real apps: Books, Weather, Notes, Finder and Spotlight all ship it at
# Contents/Resources/Metadata.appintents.
cp -R "$WORK/meta/Metadata.appintents" "$APP/Contents/Resources/"

# Post-condition, because the trap above is easy to reintroduce and fails in a
# confusing place (codesign, with a message about "bundle format unrecognized")
# rather than here. Assert the layout instead of trusting the line above.
if [ -d "$APP/Contents/Metadata.appintents" ]; then
    echo "ERROR: Metadata.appintents is at Contents/ -- it must be under Contents/Resources/." >&2
    exit 1
fi
if [ ! -f "$APP/Contents/Resources/Metadata.appintents/extract.actionsdata" ]; then
    echo "ERROR: no extract.actionsdata under Contents/Resources/ -- metadata step failed." >&2
    exit 1
fi

# Signing helper as a function so nested code gets the same retry and the same
# guarantee. Nested code must be signed BEFORE the bundle that contains it; the
# outer signature seals the helper's signature, not the helper's place in the
# world, so an unsigned helper would verify inside an otherwise valid bundle.
sign_with_retry() {
    local target="$1" signed=0 out
    # codesign fails intermittently with `errSecInternalComponent` -- measured
    # 2 failures in 5 consecutive runs on 2026-09-21, with identical inputs. It is
    # a Keychain/securityd contention error, not a problem with the identity or
    # the target. Retry, because the failure mode if we do not is the dangerous
    # one: codesign leaves the target AD-HOC and still exits 0, so a naive script
    # reports success while shipping a signature with no stable identity -- which
    # silently drops TCC grants on the next rebuild.
    for attempt in 1 2 3 4 5 6; do
        out=$(codesign --force --options runtime --timestamp --sign "$IDENTITY" "$target" 2>&1) || true
        if [[ "$out" != *errSecInternalComponent* ]]; then
            return 0
        fi
        echo "    attempt $attempt on $(basename "$target"): errSecInternalComponent, retrying" >&2
        sleep 1
    done
    echo "signing failed after 6 attempts on $target" >&2
    return 1
}

if [ "$IDENTITY" = "-" ]; then
    echo "==> signing skipped; ad-hoc signing instead"
    codesign --force --sign - "$APP/Contents/Helpers/NotronKeychainHelper"
    codesign --force --sign - "$APP"
else
    echo "==> signing nested helper, then the bundle, as: $IDENTITY"
    sign_with_retry "$APP/Contents/Helpers/NotronKeychainHelper" || exit 1
    sign_with_retry "$APP" || exit 1
fi

echo "==> verifying"
codesign --verify --strict --verbose=2 "$APP/Contents/Helpers/NotronKeychainHelper"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=4 "$APP" 2>&1 | grep -E '^Identifier|^Authority=Developer|^TeamIdentifier|^Timestamp|^flags'

# Never trust the exit status alone: confirm the Developer ID actually landed.
#
# Deliberately NOT `codesign -dv ... | grep -q ...`. Under `set -o pipefail`,
# `grep -q` exits on its first match, codesign then dies of SIGPIPE, and the
# pipeline reports 141 even though the match SUCCEEDED -- so this check would
# cry wolf on a perfectly signed bundle. Measured: exit 141. Capture the output
# and string-match instead, so there is no pipe to break.
if [ "$IDENTITY" != "-" ]; then
    sig=$(codesign -dv --verbose=4 "$APP" 2>&1 || true)
    if [[ "$sig" != *"Authority=Developer ID Application"* ]]; then
        echo "ERROR: bundle is not signed with a Developer ID -- it is ad-hoc." >&2
        echo "TCC grants will not survive a rebuild in this state." >&2
        exit 1
    fi
fi

cat <<'NOTE'

==> Next, and it cannot be automated

The phrases only register when an installed app runs once. Launching will raise
fresh TCC prompts against this new signing identity -- that is the P06 Task 2
permission-identity test, so be present for it and record which identity each
grant lands on:

    open mac/Notron.app

Then check the phrases are known to the system:

    pluginkit -m -v | grep -i notron
NOTE
