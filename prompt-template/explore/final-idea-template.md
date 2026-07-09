# <TITLE>

## Run Context

- Run ID: <RUN_ID>
- Directions JSON: <DIRECTIONS_JSON_FILE>
- Explore Report: <REPORT_PATH>
- Final Idea: <FINAL_IDEA_PATH>

## Final Recommendation

<FINAL_RECOMMENDATION>

## Rationale

<RATIONALE>

## Approach Summary

<APPROACH_SUMMARY>

## Objective Evidence

<OBJECTIVE_EVIDENCE>

## Explore Outcomes

<EXPLORE_OUTCOMES>

## Constraints

<CONSTRAINTS>

## Known Risks

<KNOWN_RISKS>

## Cross-Direction Learnings

<CROSS_DIRECTION_LEARNINGS>

## Suggested Productization Flow

Review and discuss this final idea in the session so the requirements are clarified in the conversation, then run:

```bash
/humanize:gen-plan --output <plan-path>
/humanize:start-rlcr-loop <plan-path>
```
