#!/bin/bash
# Produce Metadata.appintents for the SwiftPM-built Notron app, outside Xcode.
#
# MEASURED 2026-09-21 on macOS 26.2 / Xcode 26.2 / Swift 6.2.3. This is the step
# that decides whether Siri can see Notron at all, and SwiftPM does not do it:
# `swift build` emits no const values and has no App Intents support, so a plain
# SwiftPM build produces an app whose App Shortcuts are invisible to the system.
# Every Siri phrase in this project depends on running something like this.
#
# Two non-obvious facts this script exists to record, both found the hard way:
#
#  1. The flag is `-emit-const-values-path`, not `-emit-const-values`. The wrong
#     name fails with `error: unknown argument`.
#  2. `-const-gather-protocols-file` takes a **JSON array of bare protocol names**
#     (`["AppIntent","AppEntity"]`). Newline-separated names, module-qualified
#     names and JSON objects all fail with "input file ... is malformed".
#
# Usage: scripts/probes/appintents_metadata.sh <out-dir>
# Requires: Xcode command line tools. Writes <out-dir>/Metadata.appintents.
set -euo pipefail

OUT="${1:?usage: appintents_metadata.sh <out-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAC="$ROOT/mac"
TOOLCHAIN=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain
PROCESSOR="$TOOLCHAIN/usr/bin/appintentsmetadataprocessor"

[ -x "$PROCESSOR" ] || { echo "appintentsmetadataprocessor not found; is Xcode installed?" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The protocols whose conformances must be gathered. AppShortcutsProvider is what
# carries the Siri phrases; AppIntent is the action itself.
cat > "$WORK/protocols.json" <<'JSON'
["AppIntent","AppEntity","AppEnum","AppShortcutsProvider","AppIntentsPackage",
 "IndexedEntity","TransientAppEntity","AppUnionValue","AssistantEntity",
 "AssistantIntent","AssistantEnum","AssistantSchemaEntity"]
JSON

ls "$MAC"/Sources/Notron/*.swift > "$WORK/sources.txt"

# --disable-index-store is required: without it, whole-module optimisation
# collides with SwiftPM's per-file index-unit paths and the build dies with
# "index output filenames do not match input source files".
#
# `-wmo` also makes SwiftPM's per-file object expectations unsatisfiable, so this
# exits non-zero with clang "no such file" errors for every .o it expected. That
# is an artifact of the flag combination, not a compile failure: the modules
# compile and the const-values file is written. The const-values file — not the
# exit code — is the success criterion, so it is checked directly.
set +e
swift build --package-path "$MAC" --disable-sandbox --scratch-path "$WORK/build" \
    --disable-index-store -Xswiftc -wmo \
    -Xswiftc -emit-const-values-path -Xswiftc "$WORK/Notron.swiftconstvalues" \
    -Xswiftc -Xfrontend -Xswiftc -const-gather-protocols-file \
    -Xswiftc -Xfrontend -Xswiftc "$WORK/protocols.json"
set -e

if [ ! -f "$WORK/Notron.swiftconstvalues" ]; then
    echo "No const values emitted — App Intents metadata cannot be extracted." >&2
    echo "Check that -emit-const-values-path and -const-gather-protocols-file were accepted." >&2
    exit 1
fi

# The processor wants a file LIST of const-values paths, not a single path.
echo "$WORK/Notron.swiftconstvalues" > "$WORK/constvals.txt"

mkdir -p "$OUT"
"$PROCESSOR" \
    --output "$OUT" \
    --toolchain-dir "$TOOLCHAIN" \
    --module-name Notron \
    --sdk-root "$(xcrun --sdk macosx --show-sdk-path)" \
    --xcode-version "$(xcodebuild -version | awk '/Build version/{print $3}')" \
    --platform-family macOS \
    --deployment-target 14.0 \
    --target-triple arm64-apple-macosx26.0 \
    --source-file-list "$WORK/sources.txt" \
    --swift-const-vals-list "$WORK/constvals.txt" \
    --force

echo "wrote $OUT/Metadata.appintents"
