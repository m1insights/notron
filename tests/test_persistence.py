import json
import os
import stat
import pytest
from notron import persistence


def test_atomic_json_is_private_and_fsynced(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    calls = []
    real_fsync = os.fsync
    def sync(fd):
        calls.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        real_fsync(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    persistence.atomic_write_json(path, {'ok': True})
    assert json.loads(path.read_text()) == {'ok': True}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert calls == [False, True]


def test_interrupted_replace_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    persistence.atomic_write_json(path, {'old': True})
    def fail(*args):
        raise OSError('synthetic interruption')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        persistence.atomic_write_json(path, {'new': True})
    assert json.loads(path.read_text()) == {'old': True}
    assert list(tmp_path.glob('.state.json.*')) == []


def test_serialization_failure_does_not_touch_original(tmp_path):
    path = tmp_path / 'state.json'
    persistence.atomic_write_json(path, {'old': True})
    with pytest.raises((TypeError, ValueError)):
        persistence.atomic_write_json(path, {'bad': object()})
    assert json.loads(path.read_text()) == {'old': True}
