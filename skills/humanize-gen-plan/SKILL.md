---
name: humanize-gen-plan
description: Generate a structured implementation plan from the requirements clarified in the current conversation. Validates the output path, synthesizes a Design Requirements summary, analyzes it for issues, and generates a complete plan.md with acceptance criteria.
type: flow
user-invocable: false
disable-model-invocation: true
---

# Humanize Generate Plan

Transforms the requirements clarified in the current conversation (e.g., through a brainstorm and grill-me style clarification discussion) into a well-structured implementation plan with clear goals, acceptance criteria (AC-X format), path boundaries, and feasibility suggestions. There is no input draft file: the conversation itself is the requirements source, and a Design Requirements (from conversation) summary is archived inside the plan.

> **MANDATORY FIRST STEP — read the papers' `reference/` folder before anything else.**
> Before analyzing the requirements or generating any plan, you MUST read the repository's paper reference folder (`docs/reference/`, e.g. the relevant benchmark/method papers and their notes) to learn **how the papers actually do it** — their evaluation protocol, scoring, and methodology. Ground the plan in what the papers do; do NOT default to repo-internal conventions or your own assumptions. If the `reference/` folder is missing or empty, say so explicitly and ask the user before proceeding.

The installer hydrates this skill with an absolute runtime root path:

```bash
{{HUMANIZE_RUNTIME_ROOT}}
```

```mermaid
flowchart TD
    BEGIN([BEGIN]) --> VALIDATE[Validate output path<br/>Run: {{HUMANIZE_RUNTIME_ROOT}}/scripts/validate-gen-plan-io.sh --output &lt;plan&gt;]
    VALIDATE --> CHECK{Validation passed?}
    CHECK -->|No| REPORT_ERROR[Report validation error<br/>Stop]
    REPORT_ERROR --> END_FAIL([END])
    CHECK -->|Yes| READ_REF[MANDATORY: Read papers' reference folder<br/>docs/reference/ — how the papers do<br/>eval/scoring/methodology]
    READ_REF --> REF_OK{reference/ present<br/>and readable?}
    REF_OK -->|No| ASK_REF[Report missing reference/<br/>Ask user before proceeding]
    ASK_REF --> END_FAIL
    REF_OK -->|Yes| CHECK_CONVO{Conversation contains a<br/>clarified requirements discussion?}
    CHECK_CONVO -->|No| REPORT_NO_REQS[Report: No clarified requirements<br/>Ask user to discuss first<br/>Stop]
    REPORT_NO_REQS --> END_FAIL
    CHECK_CONVO -->|Yes| SYNTH[Synthesize Design Requirements<br/>from conversation summary<br/>no separate draft file]
    SYNTH --> ANALYZE[Analyze requirements for:<br/>- Clarity<br/>- Consistency<br/>- Completeness<br/>- Functionality]
    ANALYZE --> HAS_ISSUES{Issues found?}
    HAS_ISSUES -->|Yes| RESOLVE[Engage user to resolve issues<br/>via AskUserQuestion]
    RESOLVE --> ANALYZE
    HAS_ISSUES -->|No| CHECK_METRICS{Has quantitative<br/>metrics?}
    CHECK_METRICS -->|Yes| CONFIRM_METRICS[Confirm metrics with user:<br/>Hard requirement or trend?]
    CONFIRM_METRICS --> GEN_PLAN
    CHECK_METRICS -->|No| GEN_PLAN[Generate structured plan:<br/>- Goal Description<br/>- Acceptance Criteria with TDD tests<br/>- Path Boundaries<br/>- Feasibility Hints<br/>- Dependencies & Milestones]
    GEN_PLAN --> WRITE[Write plan to output file<br/>using Edit tool to preserve the<br/>Design Requirements section]
    WRITE --> REVIEW[Review complete plan<br/>Check for inconsistencies]
    REVIEW --> INCONSISTENT{Inconsistencies?}
    INCONSISTENT -->|Yes| FIX[Fix inconsistencies]
    FIX --> REVIEW
    INCONSISTENT -->|No| CHECK_LANG{Multiple languages?}
    CHECK_LANG -->|Yes| UNIFY[Ask user to unify language]
    UNIFY --> REPORT_SUCCESS
    CHECK_LANG -->|No| REPORT_SUCCESS[Report success:<br/>- Plan path<br/>- AC count<br/>- Language unified?]
    REPORT_SUCCESS --> END_SUCCESS([END])
```

## Input Requirements

**Required Arguments:**
- `--output <path/to/plan.md>` - Where to write the plan

**Requirements Source:**
The current conversation must already contain a clarified requirements discussion (brainstorm plus grill-me style questions and answers). No input draft file is read or written; the skill synthesizes a Design Requirements (from conversation) summary and archives it at the bottom of the generated plan.

## Plan Structure Output

The generated plan includes:

```markdown
# Plan Title

## Goal Description
Clear description of what needs to be accomplished

## Acceptance Criteria

- AC-1: First criterion
  - Positive Tests (expected to PASS):
    - Test case that should succeed
  - Negative Tests (expected to FAIL):
    - Test case that should fail

## Path Boundaries

### Upper Bound (Maximum Scope)
Most comprehensive acceptable implementation

### Lower Bound (Minimum Scope)  
Minimum viable implementation

### Allowed Choices
- Can use: allowed technologies
- Cannot use: prohibited technologies

## Dependencies and Sequence

### Milestones
1. Milestone 1: Description
   - Phase A: ...
   - Phase B: ...

## Implementation Notes
- Code should NOT contain plan terminology
```

## Validation Exit Codes

| Exit Code | Meaning |
|-----------|---------|
| 0 | Success - continue |
| 3 | Output directory does not exist |
| 4 | Output file already exists |
| 5 | No write permission |
| 6 | Invalid arguments |
| 7 | Plan template file not found |

## Usage

```bash
# Start the flow (after the requirements discussion in this conversation)
/flow:humanize-gen-plan

# The flow will ask for:
# - Output plan file path
```

Or with the skill only (no auto-execution):

```bash
/skill:humanize-gen-plan
```
