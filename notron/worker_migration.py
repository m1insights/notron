"""Conservative local migration: old effects are evidence, never replay authority."""
from __future__ import annotations

import json

from . import credentials, filer, mentions
from .securestore import EncryptedStore, StorageError, read_json, write_json


def scanner_state(value):
    """Validate before backing up or replacing scanner metadata."""
    if (not isinstance(value, dict) or value.get('version', 1) not in (1, 2)
            or not isinstance(value.get('seen', {}), dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.get('seen', {}).items())
            or not isinstance(value.get('pending', []), list)
            or not all(isinstance(k, str) for k in value.get('pending', []))
            or not isinstance(value.get('review', []), list)
            or not all(isinstance(k, str) for k in value.get('review', []))):
        raise StorageError('Scanner metadata requires recovery; history was preserved.')
    return value


def legacy_summary(raw):
    """P01 offline migration carries only uncertainty and scanner metadata forward.

    Original filing text is kept by P01's encrypted backup until explicit
    acceptance; cached judgments never become permission to perform a new effect.
    """
    seen = scanner_state(json.loads(raw['seen.json'])) if 'seen.json' in raw else None
    old_filer = json.loads(raw.get('filer.json', b'{}'))
    return {'version': 1, 'scanner': seen,
            'filer_review_required': bool(old_filer.get('judged') or old_filer.get('proposals'))}


def backup(root, name, raw):
    store = EncryptedStore(root / 'worker-backup', credentials.storage_key())
    if (store.root / (name + '.enc')).exists():
        if store.read(name) != raw:
            raise StorageError('Worker migration backup differs; review required.')
    else:
        store.write(name, raw)
    if store.read(name) != raw:
        raise StorageError('Worker migration backup could not be verified.')


def imported_scanner():
    marker = read_json(mentions.STATE.with_name('worker-history.json'))
    return marker.get('scanner')


def migrate_filer():
    from .worker import require_owner
    require_owner()
    marker_path = filer.STATE.with_name('worker-history.json')
    if marker_path.with_suffix('.enc').exists():
        return
    old = read_json(filer.STATE)
    uncertain = bool(old.get('judged') or old.get('proposals'))
    if uncertain:
        backup(filer.STATE.parent, 'filer-v1', json.dumps(old, sort_keys=True).encode())
    write_json(marker_path, {'version': 1, 'filer_review_required': uncertain})


def require_filing_ready():
    marker = read_json(filer.STATE.with_name('worker-history.json'))
    if marker and marker.get('version') != 1:
        raise StorageError('Worker migration version requires recovery.')
    if marker.get('filer_review_required'):
        raise StorageError('Legacy filing outcomes require review before automatic filing.')
