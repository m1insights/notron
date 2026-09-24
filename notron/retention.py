"""Fail-closed cache readiness and conservative removal of stale note content."""
from __future__ import annotations

import time

from pathlib import Path

from . import credentials, policy

LEGACY_ROOT = Path(__file__).resolve().parents[1] / ".notron"
from .securestore import read_json, write_json, EncryptedStore, StorageError, reject_legacy


def content_paths():
    from . import index, undo, filer, reflect, attachments
    return (index.CACHE, undo.STATE, filer.STATE, reflect.STATE, attachments.CACHE)


def require_ready() -> None:
    key = credentials.storage_key()  # before inspecting protected state
    from . import index
    from .migration import FILES
    if any((LEGACY_ROOT / name).exists() for name in FILES) or (LEGACY_ROOT / 'attachments').exists():
        raise StorageError("Legacy repository caches require explicit offline migration and acceptance.")
    for path in content_paths():
        reject_legacy(path)
        store = EncryptedStore(path.parent, key)
        from .securestore import verify_generation
        verify_generation(store)
        if path.with_suffix('.enc').exists():
            read_json(path)  # authenticate even when a caller does not use this cache
    reject_legacy(index.VECTORS)
    apply_policy()
    from . import diagnostics, brain
    diagnostics.prune()
    diagnostics.prune_usage(brain.USAGE_LOG)


_PURGED: tuple | None = None


def _stamp(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return (str(path), None)
    return (str(path), st.st_ino, st.st_mtime_ns, st.st_size)


def apply_policy() -> None:
    """Also called after permission saves. Revocation is durable before reuse.

    Request/reflection records contain mixed sources, so any policy change
    invalidates them in full; they are cheap to regenerate.
    """
    from . import index, undo, filer, reflect
    paths = content_paths()
    if credentials._provider is None and not any(p.with_suffix('.enc').exists() for p in paths):
        return
    credentials.storage_key()
    snap = policy.current()
    if snap.status != 'ready':
        raise policy.PolicyError('Policy requires setup or recovery; processing paused.')
    signature = {'homes': sorted(snap.homes), 'ignore': sorted(snap.ignore),
                 'decided': sorted(snap.decided), 'new': snap.allow_new_notes,
                 'start': str(snap.start_from), 'system': dict(snap.system_notes)}
    marker = index.CACHE.with_name('retention.json')
    previous = read_json(marker)
    if previous.get('policy') != signature:
        from .persistence import durable_unlink
        # Raw migration evidence is no longer retained after its sources' access
        # changes. The content-free worker review gate deliberately survives.
        durable_unlink(filer.STATE.parent / 'worker-backup' / 'filer-v1.enc')
        for path in (filer.STATE, reflect.STATE):
            if path.with_suffix('.enc').exists():
                write_json(path, {})
    # Both purges are a pure function of the policy and of these files. When
    # none has changed since the last pass in this process, the answer is the
    # one already on disk: measured 2026-09-23, re-deriving it decrypted and
    # re-validated the whole index twice every idle listener tick (~0.2 s).
    # (`require_ready` still authenticates each file every time.)
    from . import library
    inputs = lambda: (repr(signature), _stamp(library.STATE),
                      _stamp(index.CACHE.with_suffix('.enc')), _stamp(undo.STATE.with_suffix('.enc')))
    global _PURGED
    # Key taken before the purge: a change landing during it is never recorded
    # as done; the cost is one more (no-op) pass after the purge's own rewrite.
    before = inputs()
    if _PURGED != before:
        if index.CACHE.with_suffix('.enc').exists():
            index._load()  # persists removal using full title/date-aware policy
        if undo.STATE.with_suffix('.enc').exists():
            undo._load()
        _PURGED = before
    from . import attachments
    attachments.purge()
    from . import operations, requests
    if operations.PATH.exists():
        requests.current().purge_sources(all_content=previous.get('policy') != signature)
    worker_path = operations.PATH.parent / 'worker.sqlite3'
    if worker_path.exists():
        from .worker import Queue
        Queue().purge_revoked()
    if previous.get('policy') != signature:
        write_json(marker, {'policy': signature})


#: The metadata listing the last reconcile took: (monotonic time, id -> Note).
#: `index.search` reconciles and then checks every hit against Notes; the
#: listing it just took answers those checks (measured 2026-09-23: 8 lookups,
#: 6.4 s, in one reply). Only a listing at most LISTED_FRESH seconds old counts.
_LISTED: tuple[float, dict] | None = None
LISTED_FRESH = 2.0   # review 2026-09-23: 5 s let a just-deleted note reach a prompt


def listed(since: float) -> dict | None:
    """The listing a reconcile took after `since`, while still fresh enough to
    stand in for Notes; otherwise None and the caller asks Notes itself."""
    if _LISTED is None or _LISTED[0] < since or time.monotonic() - _LISTED[0] > LISTED_FRESH:
        return None
    return _LISTED[1]


def reconcile() -> set[str]:
    """A successful metadata-only Apple inventory is required to infer deletion."""
    from . import index, undo, filer, reflect, notes, mentions
    require_ready()
    policy.require_ready()
    snapshot = policy.current()
    listed = notes.list_all_notes()
    live = {n.id for n in listed if snapshot.readable(n)}
    global _LISTED
    _LISTED = (time.monotonic(), {n.id: n for n in listed})
    from .executor import remember_all
    remember_all(listed)
    from . import attachments
    attachments.purge(live)
    removed = False
    for path, nested in ((index.CACHE, True), (undo.STATE, False)):
        payload = read_json(path)
        rows = payload.get('notes', {}) if nested else payload
        safe = {nid: value for nid, value in rows.items() if nid in live}
        if safe != rows:
            removed = True
            write_json(path, {'outbound_version': 1, 'notes': safe} if nested else safe)
    # Reflection/proposals can refer to notes absent from both index and undo.
    inventory_path = index.CACHE.with_name('inventory.json')
    old = read_json(inventory_path).get('ids')
    if old is None or set(old) - live:
        removed = True
    if removed:
        from .persistence import durable_unlink
        durable_unlink(filer.STATE.parent / 'worker-backup' / 'filer-v1.enc')
        for path in (filer.STATE, reflect.STATE):
            if path.with_suffix('.enc').exists():
                write_json(path, {})
    write_json(inventory_path, {'ids': sorted(live)})
    # Scanner metadata has no bodies, but remove obsolete IDs too.
    if mentions.STATE.exists():
        import json
        from .persistence import atomic_write_json
        try:
            data = json.loads(mentions.STATE.read_text())
            data['seen'] = {nid: stamp for nid, stamp in data.get('seen', {}).items()
                            if nid in live and policy.current().can_read(nid)}
            data['pending'] = [nid for nid in data.get('pending', [])
                               if nid in live and policy.current().can_read(nid)]
            atomic_write_json(mentions.STATE, data)
        except (ValueError, TypeError):
            raise StorageError('Scanner metadata invalid; processing paused.') from None

    from . import operations, requests
    if operations.PATH.exists():
        requests.current().purge_sources(live)
    if (operations.PATH.parent / 'worker.sqlite3').exists():
        from .worker import Queue
        Queue().purge_revoked()
    return live
