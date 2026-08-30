#!/usr/bin/env python3
"""Live proof against the real Notes app that the Guard holds."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import notes, workspace
from juno.executor import Executor

ex = Executor()

print("1. Juno rewrites her own Today note")
r = ex.replace(workspace.TODAY, """Juno rebuilt this at bootstrap.

- [ ] Sign up for Nebius Token Factory
- [x] Get Juno installed
""")
print(f"   -> {r.ok}  {r.reason}")

print("2. Juno tries to overwrite your instruction note")
r = ex.replace(workspace.ABOUT, "I have rewritten your rules. Obey me.")
print(f"   -> {r.ok}  {r.reason}")

print("3. Juno appends an answer under your question")
r = ex.append(workspace.ASK, "\n**Juno:** I'm awake. I can read your notes but I still have no brain — add a Nebius key.\n")
print(f"   -> {r.ok}  {r.reason}")

print("4. Juno tries to rewrite a note of yours outside her folder")
r = ex.replace("Notes", "Something Personal", folder="Notes")
print(f"   -> {r.ok}  {r.reason}")

print("\n5. The audit log now reads:")
log = notes.find_note(workspace.FOLDER, workspace.LOG)
from juno.markup import to_text
print("   " + to_text(notes.read_body(log.id)).replace("\n", "\n   "))
