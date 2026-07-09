---
name: ale-task-submission
description: Use when the user has completed a task/workflow in a session and wants to turn it into an Agents' Last Exam (ALE / agenthle.org / agents-last-exam) submission — e.g. asks for a "New Task Submission" draft, "投稿到 Agents' Last Exam", "生成投稿草稿", or a "task_card.json".
---

# ALE Task Submission Generator

## Overview

Turn the work completed in the current session into TWO artifacts, written to `./ale-submission/<task_short_name>/`:

1. `submission-draft.md` — a filled draft of the agenthle.org **New Task Submission** web form (copy-paste ready).
2. `task_card.json` — the agents-last-exam repo task format (for a later code PR).

Always produce both files, then print a short summary and the list of remaining `TODO(user)` items.

## Iron rules

- **Facts come from the session only.** Software versions, OS, file names/sizes, commands — take them from what actually happened. If the environment is still accessible, run a command (e.g. `ffmpeg -version`, `lsb_release -a`, `du -h`) instead of guessing; if not, use facts exactly as recorded in the session — never block on inaccessible verification.
- **Never invent a value.** Anything you cannot know (Google Drive link, licensing choice the user must make, difficulty estimate without a self-test run) → write literally `TODO(user): <what to fill and how>`.
- **Every required field must appear**, even if its value is a TODO. Required fields are marked `*` in the template.
- File sizes matter: flag any file > 1GB and add the Drive/Dropbox link TODO.
- The verification criteria must be **objective and machine-checkable** (exact file paths, formats, numeric thresholds, comparison method against the reference) — no "looks good" language.
- **Reference output:** by default list the session's final verified output as the reference, annotated `(produced in this session — TODO(user): confirm gold-standard quality or replace with your own expert result)`. Do not leave the section empty just because no separate expert file exists.
- **Difficulty Self-Test:** Model = the model powering the current session, Harness = the current harness (both are in your own context — fill them, don't TODO them). Estimated score = a rough judgment from how this session actually went (first-try success → higher bucket; many retries/partial → lower); mark it "informal estimate". Only TODO the score if the session gives no signal at all.
- **Operating System:** pick exactly one of Windows/Linux/MacOS; put distro/version detail in parentheses on the next line and in Starting State.
- **Instance numbering:** check `./ale-submission/` for existing instances of the same task family; otherwise use `_instance_1`.

## Procedure

1. Review the session: what task was completed, starting state, instructions followed, tools/software + versions, OS, input files, produced output files.
2. Gather missing measurable facts with quick commands (versions, file sizes, formats).
3. Choose a `task_short_name`: `<domain>_<software>_instance_N` style, lowercase snake_case, unique.
4. Fill both templates below. Write them under `./ale-submission/<task_short_name>/`.
5. Reply with: file paths, a 3-line summary of the task as submitted, and the outstanding `TODO(user)` list.

## Template 1: submission-draft.md (mirrors the web form)

```markdown
# New Task Submission — <title>

## Industry Domain *
<e.g. Video Production / Mechanical Engineering / Quant Finance>

## Software & Version *
<e.g. FFmpeg 6.1>  (exact version as used in session)

## Operating System *
<Windows | Linux | MacOS>  (one of these three)

## Software Licensing *
<Free / Open Source | Commercial | In-house / Proprietary>
(Note: open-source alternatives are strongly recommended by ALE)

## Task Description *
**Objective:** <one sentence>

**Starting State:** <files/environment the agent starts with, with exact filenames>

**Instructions & Rules:** <what the agent must do, as if assigning to a colleague; include constraints and required output paths/names>

## Task Short Name *
`<task_short_name>`   (reuse the same identifier if resubmitting; number instances in a batch)

## Files
### 📥 Input Materials
- `<file>` (<size>) — <what it is>

### 📤 Reference Output & Evaluation Dependencies
- `<file>` (<size>) — <description> (produced in this session — TODO(user): confirm gold-standard quality or replace with your own expert result)

### Large-file link (only if any single file > 1GB)
TODO(user): upload to Google Drive/Dropbox with open access and paste the link here — or delete this section if all files ≤ 1GB.

## ✅ How should we verify success? *
<strict, objective rules an automated grader can apply against the reference output; enumerate each check: existence/path/format checks, content comparisons, numeric tolerances>

## Difficulty Self-Test (optional, recommended)
- Model: <the model powering this session — always fill>
- Harness: <this harness, e.g. Claude Code — always fill>
- Estimated score: <one of: <1% (last-exam) | 5–10% | 10–20% | 20–40% | 40–60% (near-term) | >60%> — informal estimate from how this session went>
- Evidence: <path to session logs/screenshots, or TODO(user)>

## Confirmations *
- [ ] I confirm this workflow can be used for Agents' Last Exam evaluation.
- [ ] I have read and agree to the Terms and Conditions.
(Check these on the website yourself.)
```

## Template 2: task_card.json (repo format)

Paths inside the card are VM-relative: inputs under `input/`, agent outputs under `output/submission/`. `evaluation` is free-text grading notes; `vm`, `taxonomy`, `requiredSystemPackages` may be left as TODO placeholders for a form-only submission (they are finalized in the code PR).

```json
{
  "taskId": "<category>/<task_short_name>",
  "title": "<Title>",
  "summary": "<one-sentence summary>",
  "category": "<domain, e.g. visual_media>",
  "software": ["<Software>"],
  "taskPrompt": "<full agent-facing prompt: role, task, agent-visible inputs with paths, required submission paths, requirements>",
  "agentMustDo": ["<each concrete requirement / required output, one string per item>"],
  "inputFiles": [
    {"name": "<file>", "format": "<ext>", "path": "input/<file>", "description": "<what it is>"}
  ],
  "referenceFiles": [
    {"name": "<file>", "format": "<ext>", "path": "output/submission/<file>", "description": "<expected output>"}
  ],
  "evaluation": "<grading notes mirroring the verification rules>",
  "vm": {"snapshot": "TODO(user): cpu-free-ubuntu|cpu-free|gpu-free|cpu-license|gpu-license", "timeout": 3600},
  "taxonomy": "TODO(user): filled by ALE maintainers",
  "requiredSystemPackages": ["<software-version, e.g. ffmpeg-6.1>"]
}
```

## Common mistakes

- Guessing a software version instead of checking the session / running `--version`.
- Subjective verification ("video looks natural") — replace with measurable checks (frame comparison, SSIM threshold, file exists at exact path, duration matches ±0.1s).
- TODO-ing the Difficulty Self-Test model/harness — this very session IS a self-test run; its model and harness are always known to you.
- Printing the artifacts only in chat instead of writing the two files.
- Using absolute local paths in `task_card.json` instead of `input/` and `output/submission/` relative paths.
