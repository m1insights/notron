"""Durable replacement of local JSON state. Errors are never swallowed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError("Unsafe state path.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload) -> None:
    data = json.dumps(payload, indent=1, allow_nan=False).encode('utf-8')
    atomic_write_bytes(path, data)


def durable_unlink(path: Path) -> None:
    """Idempotent deletion with directory fsync for migration recovery markers."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
