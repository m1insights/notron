"""Locate the credential helper inside the signed app bundle, and verify it.

This module is the trust boundary for the whole credential path. `KeychainStore`
will execute whatever path it is handed, so the path must be *derived and
verified*, never accepted from the environment, a config file, or model output —
`credentials.py` says exactly that about the helper, and this is where it is
enforced.

Two rules, and they are the point of the file:

1. **The bundle is found, not supplied.** There is no environment override. A
   production interpreter lives inside the bundle and is found by walking its own
   path; a development checkout finds `mac/Notron.app` relative to this package.
   An override would turn "verify then execute" into "execute what you were told".
2. **Verification is the boundary, and it fails closed.** The bundle must pass
   `codesign --verify --strict`, carry our bundle identifier, and carry our Team
   ID. A correct path with a wrong signature is refused, and so is a wrong path.

A caveat worth stating plainly rather than implying otherwise: this checks that
the bundle is signed by our team and is internally consistent. It does not
attest that the on-disk bundle is untampered beyond what codesign itself proves,
and it is not a sandbox. See SECURITY.md.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

BUNDLE_ID = 'com.m1labs.notron'
#: The team that is allowed to sign a bundle whose helper we will execute.
TEAM_ID = 'NW783CVSJH'
HELPER_NAME = 'NotronKeychainHelper'
HELPER_SUBPATH = ('Contents', 'Helpers', HELPER_NAME)

CODESIGN = '/usr/bin/codesign'


class BundleUnavailable(RuntimeError):
    """Fixed, payload-free. Never carries a path or a signature detail."""


def _codesign_fields(path: Path) -> dict[str, str]:
    """`codesign -dv --verbose=4` as a dict. Injected in tests.

    codesign writes its report to stderr, not stdout.
    """
    result = subprocess.run(
        [CODESIGN, '-dv', '--verbose=4', str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env={'PATH': '/usr/bin:/bin'})
    if result.returncode != 0:
        raise BundleUnavailable('Bundle signature could not be read.')
    fields = {}
    for line in result.stderr.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _codesign_verifies(path: Path) -> bool:
    """Strict verification, including nested code. Injected in tests."""
    result = subprocess.run(
        [CODESIGN, '--verify', '--strict', '--deep', str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/bin:/bin'})
    return result.returncode == 0


def _looks_like_our_bundle(candidate: Path) -> bool:
    """Cheap structural check before any signature work: a real bundle, ours."""
    manifest = candidate / 'Contents' / 'Info.plist'
    if not manifest.is_file():
        return False
    try:
        with manifest.open('rb') as handle:
            return plistlib.load(handle).get('CFBundleIdentifier') == BUNDLE_ID
    except Exception:
        return False


def app_bundle() -> Path:
    """Find the containing app bundle by walking our own location.

    Deliberately no environment override. In a shipped app the interpreter sits
    at `<bundle>/Contents/Resources/...`, so walking up finds it. In a checkout,
    the app is a build artifact beside this package at `mac/Notron.app`.
    """
    origin_here = Path(__file__).resolve()
    for origin in (origin_here, Path(sys.executable).resolve()):
        for parent in origin.parents:
            if parent.suffix == '.app' and _looks_like_our_bundle(parent):
                return parent
            if _looks_like_our_bundle(parent):
                return parent
    # Development: the repository that owns this package also owns mac/Notron.app.
    development = origin_here.parents[1] / 'mac' / 'Notron.app'
    if _looks_like_our_bundle(development):
        return development
    raise BundleUnavailable('Signed Notron bundle not found.')


def keychain_helper(*, describe=None, verify=None, bundle=None) -> Path:
    """The verified helper path, or raise. Never returns anything unverified.

    `describe` and `verify` are injected in tests; production passes codesign.
    """
    describe = _codesign_fields if describe is None else describe
    verify = _codesign_verifies if verify is None else verify
    target = app_bundle() if bundle is None else Path(bundle)

    if not _looks_like_our_bundle(target):
        raise BundleUnavailable('Signed Notron bundle not found.')

    # Order matters: check the cheap field reads first so a bundle that is not
    # ours at all fails with a plain refusal rather than a codesign invocation.
    try:
        fields = describe(target)
    except BundleUnavailable:
        raise
    except Exception:
        raise BundleUnavailable('Bundle signature could not be read.') from None

    if fields.get('Identifier') != BUNDLE_ID:
        raise BundleUnavailable('Bundle identity does not match.')
    if fields.get('TeamIdentifier') != TEAM_ID:
        raise BundleUnavailable('Bundle is not signed by the expected team.')

    try:
        if not verify(target):
            raise BundleUnavailable('Bundle signature is not valid.')
    except BundleUnavailable:
        raise
    except Exception:
        raise BundleUnavailable('Bundle signature could not be verified.') from None

    helper = target.joinpath(*HELPER_SUBPATH)
    if not helper.is_file() or not helper.stat().st_mode & 0o111:
        raise BundleUnavailable('Credential helper is missing from the bundle.')

    # Nested code must carry its own valid signature; the outer bundle's
    # signature does not vouch for a file that was swapped after signing.
    try:
        if not verify(helper):
            raise BundleUnavailable('Credential helper signature is not valid.')
    except BundleUnavailable:
        raise
    except Exception:
        raise BundleUnavailable('Credential helper signature could not be verified.') from None

    return helper
