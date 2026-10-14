#!/usr/bin/env python3
"""goal-orchestrate.py — UserPromptSubmit hook.

A prompt that starts a goal run — `goal …`, typed or pasted, or Claude Code's own
`/goal <condition>` — is carried out in orchestrator mode: this hook appends a
directive (stdout of a UserPromptSubmit hook is added to the prompt as context)
telling Claude to invoke the `orchestrate` skill before doing anything else.
Which prompts start a run is goal_trigger.py's call (beside this file), shared with
goal-monitor-spawn.sh and every other goal hook.

Wiring: the plugin's hooks.json registers it wherever the plugin is linked to this
checkout; a machine without that link registers it in ~/.claude/settings.json and
links skills/orchestrate into ~/.claude/skills/. Never both on one machine — the
directive would be injected twice.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goal_trigger import classify  # noqa: E402

if classify(json.load(sys.stdin).get("prompt") or "")[0] in ("goal", "/goal"):
    print(
        "[goal-orchestrate] This prompt starts a `goal` run. As your first action, "
        "invoke the `orchestrate` skill (Skill tool) and carry out the goal under its rules."
    )
