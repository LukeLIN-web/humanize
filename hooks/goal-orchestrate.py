#!/usr/bin/env python3
"""goal-orchestrate.py — UserPromptSubmit hook.

A prompt whose first line starts with the word `goal` starts a long autonomous
run. Every such run is carried out in orchestrator mode: this hook appends a
directive (stdout of a UserPromptSubmit hook is added to the prompt as context)
telling Claude to invoke the `orchestrate` skill before doing anything else.

The trigger is the one goal-monitor-spawn.sh owns (first line starts with the
word `goal`; a bare teardown word after it ends a run instead of starting one).
Keep the two in step.

Wiring: the plugin's hooks.json registers it wherever the plugin is linked to this
checkout; a machine without that link registers it in ~/.claude/settings.json and
links skills/orchestrate into ~/.claude/skills/. Never both on one machine — the
directive would be injected twice.
"""
import json
import re
import sys

TEARDOWN = {"cancel", "done", "finish", "stop", "end", "完成", "结束", "做完了"}

prompt = json.load(sys.stdin).get("prompt") or ""
head = prompt.split("\n", 1)[0].lstrip().lower()
m = re.match(r"goal(?![a-z0-9_])", head)
if not m:
    sys.exit(0)
rest = re.sub(r"[\W_]+$", "", head[m.end():].lstrip())
if rest in TEARDOWN:
    sys.exit(0)

print(
    "[goal-orchestrate] This prompt starts a `goal` run. As your first action, "
    "invoke the `orchestrate` skill (Skill tool) and carry out the goal under its rules."
)
