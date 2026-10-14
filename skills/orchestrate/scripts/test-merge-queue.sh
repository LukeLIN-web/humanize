#!/usr/bin/env bash
#
# test-merge-queue.sh -- offline tests for merge-queue.sh.
#
# A bare repo stands in for GitHub (PR heads live at refs/pull/N/head, as on GitHub) and a fake `gh`
# answers from pr-N.json files: a PR reads MERGED once its head is reachable from main, which is how
# GitHub treats a manual push of the PR's commits. CI: every PR head shows a green "ci" run (plus a
# "pr-only" run when the file pr_only exists); any other commit shows "ci" with the conclusion in the
# file "ci" ("none" = no run at all).
#
# Run:  bash scripts/test-merge-queue.sh   (exit 0 = all pass; needs jq and flock)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MQ=$HERE/merge-queue.sh
if ! command -v jq >/dev/null || ! command -v flock >/dev/null; then echo "SKIP: needs jq and flock"; exit 0; fi

PASS=0 FAIL=0
_ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
_bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }
eq()   { if [ "$2" = "$3" ]; then _ok "$1"; else _bad "$1 (want [$3] got [$2])"; fi; }
has()  { if grep -qF -- "$3" <<<"$2"; then _ok "$1"; else _bad "$1 (no [$3] in: $2)"; fi; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
O=$T/origin.git
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 FAKE_GH=$T MQ_POLL_SECS=0

mkdir "$T/bin"
cat >"$T/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
O=$FAKE_GH/origin.git jqf=. sha='' args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case ${args[i]} in --jq) jqf=${args[i+1]} ;; --commit) sha=${args[i+1]} ;; esac
done
case "$1 $2" in
  "repo view") json='{"defaultBranchRef":{"name":"main"}}' ;;
  "pr view")
    head=$(git --git-dir="$O" rev-parse "refs/pull/$3/head")
    state=OPEN; git --git-dir="$O" merge-base --is-ancestor "$head" main && state=MERGED
    json=$(jq --arg h "$head" --arg s "$state" '. + {headRefOid: $h, state: $s}' "$FAKE_GH/pr-$3.json") ;;
  "run list")
    run() { jq -n --arg w "$1" --arg c "$2" '{status: "completed", conclusion: $c, workflowName: $w, url: "https://ci/\($w)"}'; }
    if [ -n "$(git --git-dir="$O" for-each-ref --points-at "$sha" refs/pull)" ]; then
      json=$( { run ci success; [ ! -f "$FAKE_GH/pr_only" ] || run pr-only success; } | jq -s .)
    elif [ "$(cat "$FAKE_GH/ci")" = none ]; then json='[]'
    else json=$(run ci "$(cat "$FAKE_GH/ci")" | jq -s .); fi ;;
  *) echo "fake gh: unsupported: $*" >&2; exit 1 ;;
esac
jq -r "$jqf" <<<"$json"
EOF
chmod +x "$T/bin/gh"
export PATH=$T/bin:$PATH

git init -q --bare -b main "$O"
git clone -q "$O" "$T/work" 2>/dev/null
W=$T/work
cd "$W" || exit 1
git checkout -q -b main
printf 'one\ntwo\n' >f.txt; git add f.txt; git commit -qm base; git tag v0.3.1
git push -q origin main v0.3.1
git clone -q "$O" "$T/other"
echo success >"$T/ci"

# mkpr N branch title file content [body] [draft]: branch off origin/main, open PR N
mkpr() {
  git -C "$W" fetch -q origin main
  git -C "$W" checkout -q -B "$2" origin/main
  printf '%s\n' "$5" >"$W/$4"; git -C "$W" add "$4"; git -C "$W" commit -qm "$3"
  git -C "$W" push -q -f origin "$2"
  git --git-dir="$O" update-ref "refs/pull/$1/head" "$(git -C "$W" rev-parse HEAD)"
  jq -n --arg t "$3" --arg b "${6:-}" --arg r "$2" --argjson d "${7:-false}" \
    '{title: $t, body: $b, headRefName: $r, baseRefName: "main", isDraft: $d, isCrossRepository: false}' >"$T/pr-$1.json"
  git -C "$W" checkout -q --detach
}
main_sha() { git --git-dir="$O" rev-parse main; }
branch_on_origin() { git --git-dir="$O" branch --list "$@" | wc -l; }
run() { (cd "$W" && bash "$MQ" "$@" 2>&1); }

# --- batch merge, gate on the merged tree, semver + version-cmd, tag, Closes, branch cleanup ---
mkpr 1 feat-a "feat: add a" a.txt a "adds a. Closes #21"
mkpr 2 fix-b "fix(core): add b" b.txt b
# shellcheck disable=SC2016  # $NEW_VERSION etc. expand inside the version-cmd, not here
out=$(run --gate 'local:test -f a.txt && test -f b.txt' --semver --version-cmd 'echo "$NEW_VERSION $BUMP $PRS" > VERSION' 1 2); rc=$?
eq  "batch of two merges" "$rc" 0
has "reports the new version" "$out" "v0.3.1 → v0.4.0（minor）"
eq  "main carries the version-cmd output" "$(git --git-dir="$O" show main:VERSION)" "0.4.0 minor 1 2"
eq  "first-parent history is merge, merge, release" \
    "$(git --git-dir="$O" log --first-parent --format=%s -3 main | tr '\n' '|')" "release v0.4.0|Merge PR #2: fix(core): add b|Merge PR #1: feat: add a|"
has "merge commit carries the PR's closing keyword" "$(git --git-dir="$O" log --format=%B --grep='Merge PR #1' main)" "Closes #21"
eq  "tag pushed" "$(git --git-dir="$O" rev-parse 'v0.4.0^{commit}')" "$(main_sha)"
eq  "PR 1 reads MERGED" "$(gh pr view 1 --json state --jq .state)" MERGED
eq  "head branches deleted" "$(branch_on_origin feat-a fix-b)" 0
eq  "no refs/mq left" "$(git -C "$W" for-each-ref refs/mq | wc -l)" 0

# --- conflict inside a batch: nothing pushed, worktree left clean ---
mkpr 3 c1 "fix: f one" f.txt 1
mkpr 4 c2 "fix: f two" f.txt 2
before=$(main_sha)
out=$(run --gate local:true 3 4); rc=$?
eq  "conflict exits 3" "$rc" 3
has "names the ejected PR" "$out" "弹出 #4"
has "names the conflicting file" "$out" "f.txt"
eq  "main untouched on conflict" "$(main_sha)" "$before"
eq  "PR 3 stays OPEN (batch is atomic)" "$(gh pr view 3 --json state --jq .state)" OPEN
out=$(run --gate local:true 3); rc=$?
eq  "queue worktree reusable after a conflict" "$rc" 0

# --- red gate, judged on the merged tree ---
mkpr 5 e "fix: e" e.txt e
before=$(main_sha)
out=$(run --gate 'local:test ! -f e.txt' 5); rc=$?
eq  "red gate exits 4" "$rc" 4
eq  "main untouched on red gate" "$(main_sha)" "$before"

# --- main advances while the batch is gated: non-force push refuses, exit 6 ---
out=$(run --gate "local:cd $T/other && git pull -q --ff-only origin main && echo z >> z.txt && git add z.txt && git commit -qm z && git push -q origin HEAD:main" 5); rc=$?
eq  "main moved exits 6" "$rc" 6
eq  "PR 5 still OPEN after main moved" "$(gh pr view 5 --json state --jq .state)" OPEN
out=$(run --gate local:true 5); rc=$?
eq  "rerun after main moved merges" "$rc" 0

# --- PR head moves while gated: exit 5 ---
mkpr 6 g "fix: g" g.txt g
other=$(git --git-dir="$O" rev-parse refs/pull/1/head)
before=$(main_sha)
out=$(run --gate "local:git --git-dir=$O update-ref refs/pull/6/head $other" 6); rc=$?
eq  "head moved exits 5" "$rc" 5
eq  "main untouched when head moved" "$(main_sha)" "$before"

# --- a daemon started by the gate does not keep the queue lock ---
mkpr 15 daemon "fix: daemon" d.txt d
mkpr 16 after "fix: after" e2.txt e2
out=$(run --gate "local:sleep 30 >/dev/null 2>&1 & echo \$! > $T/daemon.pid" 15); rc=$?
eq  "gate with a daemon merges" "$rc" 0
out=$(run --dry-run 16); rc=$?
eq  "next run not blocked by the gate's daemon" "$rc" 0
kill "$(cat "$T/daemon.pid")" 2>/dev/null

# --- CI gate: success, failure, no run, a workflow that ran on the PR head but not on the merge ---
mkpr 7 h "fix: h" h.txt h
out=$(run --gate ci 7); rc=$?
eq  "CI green merges" "$rc" 0
eq  "mq branch deleted after CI gate" "$(git --git-dir="$O" for-each-ref 'refs/heads/mq/*' | wc -l)" 0
mkpr 8 i "fix: i" i.txt i
echo failure >"$T/ci"; before=$(main_sha)
out=$(run --gate ci 8); rc=$?
eq  "CI red exits 4" "$rc" 4
has "CI red lists the run" "$out" "https://ci/ci"
eq  "main untouched on CI red" "$(main_sha)" "$before"
eq  "mq branch deleted after CI red" "$(git --git-dir="$O" for-each-ref 'refs/heads/mq/*' | wc -l)" 0
echo none >"$T/ci"
out=$(MQ_CI_APPEAR_SECS=0 run --gate ci 8); rc=$?
eq  "no CI run exits 4" "$rc" 4
has "no CI run says why" "$out" "等不到 CI run"
echo success >"$T/ci"; touch "$T/pr_only"
out=$(MQ_CI_APPEAR_SECS=0 run --gate ci 8); rc=$?
eq  "workflow missing on the merge exits 4" "$rc" 4
has "names the missing workflow" "$out" "pr-only"
eq  "main untouched when a workflow is missing" "$(main_sha)" "$before"
rm "$T/pr_only"

# --- after the push: cross-repo branch kept, moved branch kept (exit 7), tag collision (exit 7) ---
mkpr 17 j1 "fix: from a fork" x1.txt x1
jq '.isCrossRepository = true' "$T/pr-17.json" >"$T/t.json" && mv "$T/t.json" "$T/pr-17.json"
out=$(run --gate local:true 17); rc=$?
eq  "cross-repo PR merges" "$rc" 0
eq  "same-named branch on origin not deleted for a cross-repo PR" "$(branch_on_origin j1)" 1
mkpr 18 j2 "fix: moved branch" x2.txt x2
out=$(run --gate "local:git --git-dir=$O update-ref refs/heads/j2 $other" 18); rc=$?
eq  "branch moved after the head was read exits 7" "$rc" 7
eq  "moved branch kept (lease)" "$(branch_on_origin j2)" 1
eq  "PR 18 merged anyway" "$(gh pr view 18 --json state --jq .state)" MERGED
mkpr 14 o "fix: o" o.txt o
git -C "$W" tag v0.4.1 "$(git --git-dir="$O" rev-parse refs/pull/14/head)" && git -C "$W" push -q origin v0.4.1 && git -C "$W" tag -d v0.4.1 >/dev/null
out=$(run --gate local:true --semver 14); rc=$?
eq  "tag already taken exits 7" "$rc" 7
has "says how to finish the tag" "$out" "git push origin v0.4.1"
eq  "PR 14 merged anyway" "$(gh pr view 14 --json state --jq .state)" MERGED
eq  "failed tag not left in the local clone" "$(git -C "$W" tag --list v0.4.1 | wc -l)" 0

# --- semver levels (dry-run: no push, no tag) ---
git -C "$W" fetch -q origin main; git -C "$W" tag v9.9.9-rc1 origin/main; git -C "$W" push -q origin v9.9.9-rc1
mkpr 9 j "feat!: drop x" j.txt j
out=$(run --semver --dry-run 9)
has "pre-release tags ignored; 0.x breaking bumps minor" "$out" "v0.4.1 → v0.5.0（major）"
git -C "$W" tag v1.2.3 origin/main; git -C "$W" push -q origin v1.2.3
out=$(run --semver --dry-run 9);  has "breaking bumps major" "$out" "v1.2.3 → v2.0.0（major）"
mkpr 10 k "fix: k" k.txt k "BREAKING CHANGE: none"
out=$(run --semver --dry-run 10); has "body is not a level declaration" "$out" "→ v1.2.4（patch）"
mkpr 11 l "feat(api): l" l.txt l
out=$(run --semver --dry-run 11); has "feat bumps minor" "$out" "→ v1.3.0（minor）"
mkpr 12 m "docs: m" m.txt m
out=$(run --semver --dry-run 12 11); has "batch takes the highest level" "$out" "→ v1.3.0（minor）"
out=$(run --semver --dry-run 12); has "other types bump patch" "$out" "→ v1.2.4（patch）"
eq  "dry-run pushes nothing" "$(git --git-dir="$O" tag --list 'v1.3*' 'v1.2.4' | wc -l)" 0
git -C "$W" tag v5.0.0 origin/main; git -C "$W" tag -d v1.2.3 >/dev/null
out=$(run --semver --dry-run 12); has "only origin's tags count" "$out" "v1.2.3 → v1.2.4"
git -C "$W" tag -d v5.0.0 >/dev/null

# --- refusals ---
git --git-dir="$O" for-each-ref --format='delete %(refname)' refs/tags | git --git-dir="$O" update-ref --stdin
out=$(run --semver --dry-run 12); rc=$?
eq  "no tag on origin refuses --semver" "$rc" 2
has "no tag says what to do" "$out" "打成 tag"
mkpr 13 n "fix: n" n.txt n "" true
out=$(run 13); rc=$?;              eq "draft PR refused" "$rc" 2
out=$(run); rc=$?;                 eq "no PR refused" "$rc" 2
out=$(run --gate foo 12); rc=$?;   eq "bad gate refused" "$rc" 2
out=$(run --version-cmd x 12); rc=$?; eq "version-cmd without semver refused" "$rc" 2
lock=$(git -C "$W" rev-parse --path-format=absolute --git-common-dir)/mq.lock
flock "$lock" sleep 5 & holder=$!
sleep 0.3
out=$(run --gate local:true 12); rc=$?
eq  "second queue on the same clone refused" "$rc" 2
has "says a queue is running" "$out" "已经有一个队列在跑"
kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
