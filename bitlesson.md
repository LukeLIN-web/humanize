# BitLesson Knowledge Base

This file is project-specific. Keep entries precise and reusable for future rounds.

## Entry Template (Strict)

Use this exact field order for every entry:

```markdown
## Lesson: <unique-id>
Lesson ID: <BL-YYYYMMDD-short-name>
Scope: <component/subsystem/files>
Problem Description: <specific failure mode with trigger conditions>
Root Cause: <direct technical cause>
Solution: <exact fix that resolved the problem>
Constraints: <limits, assumptions, non-goals>
Validation Evidence: <tests/commands/logs/PR evidence>
Source Rounds: <round numbers where problem appeared and was solved>
```

## Entries

<!-- Add lessons below using the strict template. -->

## Lesson: BL-20260313-validator-grep-vs-scanner
Lesson ID: BL-20260313-validator-grep-vs-scanner
Scope: scripts/validate-refine-plan-io.sh
Problem Description: Using grep to count CMT: occurrences for preflight validation causes false positives when markers appear inside HTML comments or fenced code blocks, or when blocks are malformed (missing ENDCMT, nested CMT)
Root Cause: grep-based counting does not understand document structure (code fences, HTML comments) and cannot detect malformed block syntax
Solution: Replace grep with a stateful awk scanner that tracks NORMAL/IN_FENCE/IN_HTML/IN_CMT states and only counts valid, non-empty, properly-terminated blocks outside ignored regions
Constraints: Scanner must handle same-line HTML comment spans, multi-backtick fences, tilde fences, and nested token precedence
Validation Evidence: tests/test-refine-plan.sh passes 178/178 tests including regression cases for HTML-only, fence-only, empty, unterminated, and nested markers
Source Rounds: 0 (problem), 1 (fix)
