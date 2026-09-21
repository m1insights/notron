"""Managed content storage; never infer an old repository cache as a fallback."""
import os
from pathlib import Path


def data_dir() -> Path:
    """Where private state lives.

    `NOTRON_DATA_DIR` exists so the suite can point every writer at a tmp_path.
    `transport._identity` imports `DATA_DIR` *inside* the function, so it
    resolves at call time rather than at import — which is why it was the one
    module no disposable-state fixture covered. Before this override existed, 57
    tests in `test_managed_transport.py` were writing `managed-requests.json`
    into the developer's real application state and calling
    `securestore.private_directory` (a mkdir and a chmod) on the way in. On a
    runner that cannot chmod outside its own workspace the same bug surfaced as
    57 PermissionErrors instead, which is how it was finally found.
    """
    override = os.environ.get('NOTRON_DATA_DIR')
    return Path(override) if override else Path.home() / 'Library' / 'Application Support' / 'com.m1labs.notron'


DATA_DIR = data_dir()
