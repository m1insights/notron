"""Fail-closed cache readiness and conservative removal of stale note content."""
from __future__ import annotations

from pathlib import Path

from . import credentials, policy

LEGACY_ROOT = Path(__file__).resolve().parents[1] / ".notron"
from .securestore import read_json, write_json, EncryptedStore, StorageError, reject_legacy


def content_paths():
    from . import index, undo, filer, reflect
    return (index.CACHE, undo.STATE, filer.STATE, reflect.STATE)


def require_ready() -> None:
    key = credentials.storage_key()  # before inspecting protected state
    from . import index
    from .migration import FILES
    if any((LEGACY_ROOT / name).exists() for name in FILES):
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
        for path in (filer.STATE, reflect.STATE):
            if path.with_suffix('.enc').exists():
                write_json(path, {})
    if index.CACHE.with_suffix('.enc').exists():
        index._load()  # persists removal using full title/date-aware policy
    if undo.STATE.with_suffix('.enc').exists():
        undo._load()
    if previous.get('policy') != signature:
        write_json(marker, {'policy': signature})


def reconcile() -> set[str]:
    """A successful metadata-only Apple inventory is required to infer deletion."""
    from . import index, undo, filer, reflect, notes, mentions
    require_ready()
    policy.require_ready()
    snapshot = policy.current()
    live = {n.id for n in notes.list_all_notes() if snapshot.readable(n)}
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

    return live
