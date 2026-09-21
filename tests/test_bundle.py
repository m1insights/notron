"""Bundle verification: the trust boundary for the credential helper.

`KeychainStore` executes whatever path it is handed, so these tests are about the
rule that keeps that safe — *derive the bundle, then verify it, then hand over a
path* — and about the ways it must refuse.

`describe` and `verify` are injected everywhere. Nothing here shells out to
codesign, which matters twice over: the suite globally blocks subprocesses, and a
test that really code-signed things would be asserting against whatever happened
to be on the machine rather than against the logic.
"""
import plistlib

import pytest

from notron import bundle

HELPER_RELATIVE = ('Contents', 'Helpers', 'NotronKeychainHelper')


def make_bundle(root, *, identifier=bundle.BUNDLE_ID, helper=True, executable=True):
    """A structurally plausible bundle. Says nothing about signatures."""
    app = root / 'Notron.app'
    contents = app / 'Contents'
    contents.mkdir(parents=True)
    with (contents / 'Info.plist').open('wb') as handle:
        plistlib.dump({'CFBundleIdentifier': identifier}, handle)
    if helper:
        helper_path = app.joinpath(*HELPER_RELATIVE)
        helper_path.parent.mkdir(parents=True, exist_ok=True)
        helper_path.write_bytes(b'\x00')
        helper_path.chmod(0o755 if executable else 0o644)
    return app


def describe_ours(_):
    return {'Identifier': bundle.BUNDLE_ID, 'TeamIdentifier': bundle.TEAM_ID}


def verify_ok(_):
    return True


def verify_no(_):
    return False


def test_a_verified_bundle_yields_the_helper_path(tmp_path):
    app = make_bundle(tmp_path)
    helper = bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify_ok)
    assert helper == app.joinpath(*HELPER_RELATIVE)
    assert helper.name == bundle.HELPER_NAME


def test_another_teams_signature_is_refused(tmp_path):
    """The whole point. A correctly located, correctly named, validly signed
    bundle that is not OURS must not be executed."""
    app = make_bundle(tmp_path)

    def wrong_team(_):
        return {'Identifier': bundle.BUNDLE_ID, 'TeamIdentifier': 'SOMEONEELSE1'}

    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=wrong_team, verify=verify_ok)


def test_a_wrong_bundle_identifier_is_refused(tmp_path):
    app = make_bundle(tmp_path, identifier='com.example.other')

    def other(_):
        return {'Identifier': 'com.example.other', 'TeamIdentifier': bundle.TEAM_ID}

    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=other, verify=verify_ok)


def test_an_invalid_signature_is_refused(tmp_path):
    """Right identity fields, but codesign does not accept the bundle."""
    app = make_bundle(tmp_path)
    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify_no)


def test_the_helper_is_itself_verified_not_just_the_bundle(tmp_path):
    """Nested code carries its own signature. A helper swapped in after the
    bundle was signed must be refused even though the bundle still verifies."""
    app = make_bundle(tmp_path)
    seen = []

    def verify(path):
        seen.append(path.name)
        return path.name != bundle.HELPER_NAME

    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify)
    assert bundle.HELPER_NAME in seen, 'the helper must be verified separately'


def test_a_missing_helper_is_refused(tmp_path):
    app = make_bundle(tmp_path, helper=False)
    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify_ok)


def test_a_non_executable_helper_is_refused(tmp_path):
    app = make_bundle(tmp_path, executable=False)
    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify_ok)


def test_an_unreadable_signature_is_refused_rather_than_ignored(tmp_path):
    """A describe() that blows up must fail closed, not fall through to a
    path that was never verified."""
    app = make_bundle(tmp_path)

    def explode(_):
        raise OSError('codesign missing')

    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=app, describe=explode, verify=verify_ok)


def test_a_directory_that_is_not_a_bundle_is_refused(tmp_path):
    plain = tmp_path / 'not-a-bundle'
    plain.mkdir()
    with pytest.raises(bundle.BundleUnavailable):
        bundle.keychain_helper(bundle=plain, describe=describe_ours, verify=verify_ok)


def test_no_environment_override_exists_for_the_bundle(monkeypatch, tmp_path):
    """`credentials.py` says the helper path must never come from an environment
    override. This pins that there is nothing to override."""
    app = make_bundle(tmp_path)
    monkeypatch.setenv('NOTRON_BUNDLE', str(tmp_path / 'elsewhere'))
    monkeypatch.setenv('NOTRON_KEYCHAIN_HELPER', '/tmp/attacker')
    helper = bundle.keychain_helper(bundle=app, describe=describe_ours, verify=verify_ok)
    assert helper == app.joinpath(*HELPER_RELATIVE)


def test_startup_pauses_when_the_bundle_cannot_be_verified(monkeypatch):
    """Fail closed: a provider must not be left configured after a refusal."""
    from notron import credentials

    def refuse():
        raise bundle.BundleUnavailable('nope')

    monkeypatch.setattr(bundle, 'keychain_helper', refuse)
    with pytest.raises(credentials.CredentialUnavailable):
        credentials.startup()
    assert credentials._provider is None
    credentials.configure(None)


def test_startup_configures_a_keychain_store_when_verified(monkeypatch, tmp_path):
    from notron import credentials

    monkeypatch.setattr(bundle, 'keychain_helper', lambda: tmp_path / 'helper')
    credentials.startup()
    try:
        assert isinstance(credentials._provider, credentials.KeychainStore)
        assert credentials._provider.helper == tmp_path / 'helper'
    finally:
        credentials.configure(None)
