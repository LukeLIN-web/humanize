#!/usr/bin/env python3
"""goal-orchestrate.py — UserPromptSubmit hook.

A prompt that starts a goal run — `goal …`, typed or pasted, or Claude Code's own
`/goal <condition>` — is carried out in orchestrator mode: this hook appends a
directive (stdout of a UserPromptSubmit hook is added to the prompt as context)
telling Claude to invoke the `orchestrate` skill before doing anything else.
Which prompts start a run is goal_trigger.py's call (beside this file), shared with
goal-monitor-spawn.sh and every other goal hook.

Wiring: a project registers a byte-identical copy in its own .claude/settings.json
(beside its copies of goal_trigger.py and goal-monitor-spawn.sh) and links
skills/orchestrate into its .claude/skills/, so every machine gets it. Machine-wide
registrations (the plugin's hooks.json, or ~/.claude/settings.json on a machine
without the plugin) cover the other projects; they stay quiet in a project that
registers its own copy, so the directive is injected once.
"""
import json
import os
import sys

# goal_trigger.py sits beside this script, and Python puts the script's directory first on sys.path
from goal_trigger import classify

project = os.environ.get("CLAUDE_PROJECT_DIR", "")
own = os.path.join(project, ".claude", "hooks", "goal-orchestrate.py")
settings = os.path.join(project, ".claude", "settings.json")
if project and os.path.exists(own) and os.path.realpath(own) != os.path.realpath(__file__):
    with open(settings, encoding="utf-8") as f:
        if "goal-orchestrate.py" in f.read():
            sys.exit(0)

if classify(json.load(sys.stdin).get("prompt") or "")[0] in ("goal", "/goal"):
    print(
        "[goal-orchestrate] This prompt starts a `goal` run. As your first action, "
        "invoke the `orchestrate` skill (Skill tool) and carry out the goal under its rules."
    )
