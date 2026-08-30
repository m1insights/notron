"""Thin, injection-safe bridge to osascript.

All dynamic values are passed as argv rather than interpolated into the script
source, so note bodies containing quotes, newlines or backslashes cannot break
out of the script or corrupt a write.
"""

from __future__ import annotations

import subprocess


class AppleScriptError(RuntimeError):
    pass


def run(script: str, *args: str, timeout: int = 60) -> str:
    """Run an AppleScript with `on run argv` and return its trimmed stdout."""
    proc = subprocess.run(
        ["osascript", "-", *args],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AppleScriptError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.rstrip("\n")
