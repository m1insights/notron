"""Talking to the Notes app, one request at a time.

Apple Notes serves a single AppleScript request at a time, application-wide. Two
callers asking at once do not queue politely — the second blocks until the first
is done, and if that takes long enough it simply times out. Worse, a slow enough
request can wedge Notes for everyone, including the background listener, which
then crashes and gets restarted, and asks again, and wedges it again.

So every request in JUNO passes through here, and here takes a lock first. The
lock is a real file lock, so it holds across processes: the listener running in
the background and a `juno ask` you type in a terminal take turns instead of
fighting. Waiting a second is always better than a timeout.

All dynamic values are passed as argv rather than interpolated into the script
source, so note bodies containing quotes, newlines or backslashes cannot break
out of the script or corrupt a write.
"""

from __future__ import annotations

import fcntl
import pathlib
import subprocess
import time

LOCK = pathlib.Path(__file__).resolve().parents[1] / ".juno" / "notes.lock"
DEFAULT_TIMEOUT = 45
LOCK_WAIT = 120


class AppleScriptError(RuntimeError):
    pass


class NotesBusy(AppleScriptError):
    """Notes did not answer in time — it is wedged or doing something large."""


def _lock_file():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    return LOCK.open("a+")


def run(script: str, *args: str, timeout: int = DEFAULT_TIMEOUT, retries: int = 1) -> str:
    """Run an AppleScript with `on run argv` and return its trimmed stdout."""
    deadline = time.time() + LOCK_WAIT
    with _lock_file() as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise NotesBusy("another Juno request has held Notes for two minutes")
                time.sleep(0.25)

        try:
            return _osascript(script, args, timeout, retries)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _osascript(script: str, args: tuple[str, ...], timeout: int, retries: int) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                ["osascript", "-", *args],
                input=script, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            last = e
            time.sleep(1 + attempt)
            continue
        if proc.returncode != 0:
            raise AppleScriptError(proc.stderr.strip() or "osascript failed")
        return proc.stdout.rstrip("\n")
    raise NotesBusy(f"Notes did not answer within {timeout}s") from last
