"""Juno's morning routine.

One command, run by macOS at a fixed hour: catch up on anything you wrote since
yesterday, rebuild today's list, and tell you what she needs to keep working well.
The order matters — she learns your new notes first, so the plan she writes is
based on the life you actually have this morning.
"""

from __future__ import annotations

from datetime import datetime

from . import care, graph, index, workspace


def morning(brain, *, dry_run: bool = False, on_step=None) -> dict:
    def say(msg: str) -> None:
        if on_step:
            on_step(msg)

    out: dict[str, object] = {"at": datetime.now().isoformat(timespec="minutes")}

    # 1. Learn anything written since yesterday.
    if not dry_run:
        say("reading what you wrote since yesterday")
        stats = index.build(brain, on_progress=say)
        out["indexed"] = stats
        say(f"learned {stats['embedded']} new passages")

    # 2. Rebuild today's list from your notes and standing instructions.
    say("rebuilding ☀️ Today")
    state = graph.run("plan my day", brain=brain, trigger="morning", dry_run=dry_run)
    out["today"] = state.results
    out["plan"] = state.answer

    # 3. Report her own upkeep.
    say("checking on herself")
    signals, body, result = care.run(brain, dry_run=dry_run)
    out["care"] = [s.fact for s in signals if s.severity != "ok"]
    out["care_written"] = result.ok

    say("done")
    return out


PLIST_LABEL = "io.m1labs.juno.morning"


def plist(python: str, project: str, hour: int, minute: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>juno</string>
    <string>morning</string>
  </array>
  <key>WorkingDirectory</key><string>{project}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key><string>{project}/.juno/morning.log</string>
  <key>StandardErrorPath</key><string>{project}/.juno/morning.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""
