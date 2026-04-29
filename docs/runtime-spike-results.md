# Runtime Spike Results — explore-idea

This document records the results of the post-RLCR functional spike for `/humanize:explore-idea`.

## How to Run

After the RLCR loop completes and the PR is merged, execute the following sequence in a real session:

```bash
# Step 1: Generate an idea draft with directions.json companion
/humanize:gen-idea "add undo/redo to the editor"

# Step 2: Run explore-idea with the emitted directions.json
/humanize:explore-idea .humanize/ideas/<slug>-<timestamp>.directions.json \
    --max-worker-iterations 1
```

## Functional Spike Checklist

Record each item as `[x]` (passed) or `[ ]` (failed/skipped) after the spike run.

### Phase 1: IO Validation
- [ ] `validate-explore-idea-io.sh` runs and emits all required keys
- [ ] `DIRECTIONS_JSON_FILE` points to a schema-valid file
- [ ] `RUN_DIR` path is under `.humanize/explore/<RUN_ID>/`

### Phase 2: Confirmation
- [ ] Dispatch plan displayed to user before any side effects
- [ ] User confirmation required (`[y/N]` prompt shown)

### Phase 3: Run State Initialization
- [ ] Run directory created: `.humanize/explore/<RUN_ID>/`
- [ ] `dispatch-prompts/` subdirectory created
- [ ] `manifest.json` written before any workers start
- [ ] Each direction has a per-worker entry with `status: pending` in manifest

### Phase 4: Worker Dispatch
- [ ] Workers dispatched in parallel (single Agent-tool message)
- [ ] Workers run in isolated git worktrees (`isolation: "worktree"`)
- [ ] No branches pushed to remote

### Phase 5: Result Collection
- [ ] `worker-results.jsonl` created with one entry per worker
- [ ] Each entry has valid JSON with all required fields
- [ ] Workers that failed emit coordinator-generated failure rows

### Phase 6: Report Synthesis
- [ ] `report.md` created with two-tier ranking tables
- [ ] Tier 1 ranks by product direction quality
- [ ] Tier 2 ranks by implementation readiness
- [ ] Adoption paths include correct worktree/branch/commit data

## Spike Run Results

| Date | Idea Input | N Directions | Workers Run | Report Path | Notes |
|------|-----------|--------------|-------------|-------------|-------|
| (pending) | | | | | Run post-RLCR loop completion |
