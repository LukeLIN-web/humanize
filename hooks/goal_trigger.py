#!/usr/bin/env python3
"""goal_trigger.py — the one place that decides whether a prompt starts or ends a goal run.

Every hook that reacts to a goal asks this module: goal-monitor-spawn.sh (the
overseer), goal-orchestrate.py (orchestrator mode), and a consuming repo's
conclusion-page Stop hook. Each used to parse the prompt itself and they drifted —
a pasted goal or Claude Code's own `/goal` started the overseer but not the rest.

A run starts two ways:
  goal <anything>     this fleet's convention: the first non-blank line starts with
                      the word `goal`
  /goal <condition>   Claude Code's own; a transcript records it as
                      <command-name>/goal</command-name> … <command-args>condition</command-args>
A paste arrives wrapped in <pasted_content id=…> tags behind blank lines; the tags
and the blank lines are skipped before the first line is read.

classify(prompt) -> (kind, text):
  "goal"       the prompt without paste tags    a goal run starts
  "goal-end"   the teardown word                `goal done` and the like — the whole
                                                remainder must be the word, so
                                                `goal stop the trainer` is a new goal
  "/goal"      the condition                    a /goal run starts
  "/goal-end"  the word                         the words the CLI clears a goal on
  None, ""                                      anything else; a bare `/goal` only opens
                                                the CLI's status panel

As a command: the prompt on stdin; prints `kind`, a newline, then `text` (nothing for None).
SSOT: this file lives in the humanize repo (hooks/); a consuming repo keeps a
byte-identical copy beside its copy of goal-monitor-spawn.sh.
"""
import re
import string
import sys

GOAL_END = {"cancel", "done", "finish", "stop", "end", "完成", "结束", "做完了"}
SLASH_END = {"clear", "stop", "off", "reset", "none", "cancel"}
PASTE_TAG = re.compile(r"</?pasted_content( [^>]*)?>")
SLASH_RECORD = re.compile(r"<command-name>/goal</command-name>.*?<command-args>(.*?)</command-args>", re.DOTALL)
GOAL_WORD = re.compile(r"goal(?![a-z0-9_])")
TRAILING = string.whitespace + string.punctuation + "。！？，、；：…"


def classify(prompt):
    text = PASTE_TAG.sub("", prompt).lstrip()
    record = SLASH_RECORD.match(text)
    if record:
        text = "/goal " + record.group(1)
    head = text.split("\n", 1)[0].lower()
    if GOAL_WORD.match(head):
        rest = head[4:].strip().rstrip(TRAILING)
        return ("goal-end", rest) if rest in GOAL_END else ("goal", text.rstrip("\n"))
    if head == "/goal" or head.startswith(("/goal ", "/goal\t")):
        cond = text[5:].strip()
        if not cond:
            return None, ""
        return ("/goal-end", cond) if cond.lower() in SLASH_END else ("/goal", cond)
    return None, ""


if __name__ == "__main__":
    kind, text = classify(sys.stdin.read())
    if kind:
        print(kind)
        print(text)
