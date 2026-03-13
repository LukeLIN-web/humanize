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
Scope: scripts/validate-refine-plan-io.sh (CMT block counting and required-section detection)
Problem Description: Using grep to detect CMT: occurrences or required section headings causes false positives when content appears inside HTML comments, fenced code blocks, or CMT blocks. Affects both exit code 3 (CMT counting) and exit code 4 (section detection).
Root Cause: grep-based detection does not understand document structure (code fences, HTML comments) and matches content inside ignored regions
Solution: Replace grep with stateful awk scanners (scan_cmt_blocks for CMT counting, scan_sections for section detection) that track NORMAL/IN_FENCE/IN_HTML/IN_CMT states and only match content outside ignored regions
Constraints: Scanners must handle same-line HTML comment spans, multi-backtick fences, tilde fences, nested token precedence, and error context excerpts
Validation Evidence: tests/test-refine-plan.sh passes 182/182 tests including regression cases for markers and headings inside ignored regions
Source Rounds: 0 (CMT problem), 1 (CMT fix), 2 (section detection fix)
