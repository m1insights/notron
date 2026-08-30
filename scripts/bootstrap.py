#!/usr/bin/env python3
"""Create the 🤖 JUNO folder and its six system notes. Safe to re-run."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import workspace  # noqa: E402

if __name__ == "__main__":
    for title, state in workspace.bootstrap().items():
        print(f"  {state:8} {title}")
    print(f"\nOpen Notes → {workspace.FOLDER}")
