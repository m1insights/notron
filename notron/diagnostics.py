"""Seven days of fixed event codes only: no note text, exceptions, or credentials."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from .paths import DATA_DIR
from .persistence import atomic_write_json
from .securestore import private_directory

ROOT = DATA_DIR / 'diagnostics'
EVENTS = frozenset({'processing_started', 'processing_paused', 'processing_finished',
                    'listener_event', 'maintenance_finished'})


def prune(*, root: Path | None = None, now: datetime | None = None) -> None:
    root = root or ROOT
    now = now or datetime.now(timezone.utc)
    private_directory(root)
    cutoff = now.date() - timedelta(days=6)
    for path in root.glob('*.json'):
        try:
            day = datetime.strptime(path.stem, '%Y-%m-%d').date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()


def record(event: str, *, root: Path | None = None, now: datetime | None = None) -> None:
    if event not in EVENTS:
        raise ValueError('Unknown diagnostic event.')
    root = root or ROOT
    now = now or datetime.now(timezone.utc)
    prune(root=root, now=now)
    path = root / f'{now.date()}.json'
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    # Do not carry unvalidated historical fields forward.
    safe = {code: count for code, count in data.items()
            if code in EVENTS and type(count) is int and count >= 0}
    safe[event] = safe.get(event, 0) + 1
    atomic_write_json(path, safe)


def prune_usage(path: Path, *, today=None) -> dict:
    """Allow only day/tier/token counters; drop malformed or payload fields."""
    from datetime import date
    today = today or date.today()
    cutoff = today - timedelta(days=6)
    if not path.exists(): return {}
    private_directory(path.parent)
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        raw = {}
    safe = {}
    if not isinstance(raw, dict): raw = {}
    for day, tiers in raw.items():
        try:
            if not cutoff <= date.fromisoformat(day) <= today or not isinstance(tiers, dict): continue
        except ValueError:
            continue
        values = {}
        for tier, row in tiers.items():
            if tier not in ('fast', 'smart', 'deep', 'embed') or not isinstance(row, dict): continue
            values[tier] = {k: row.get(k, 0) for k in ('calls', 'in', 'out')
                            if type(row.get(k, 0)) is int and row.get(k, 0) >= 0}
        if values: safe[day] = values
    atomic_write_json(path, safe)
    return safe
