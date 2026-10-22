#!/usr/bin/env bash
#
# test-milestone-scripts.sh -- offline tests for touches-logic.sh, snapshot-run.sh and ready-issues.py.
#
# Throwaway git repos under a temp dir; a fake `gh` (python) answers `issue view N --json ...` from
# issue-N.json files and `issue edit N --body-file -` by rewriting that file's body.
#
# Run:  bash scripts/test-milestone-scripts.sh   (exit 0 = all pass)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS=0 FAIL=0
_ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
_bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }
eq()   { if [ "$2" = "$3" ]; then _ok "$1"; else _bad "$1 (want [$3] got [$2])"; fi; }
has()  { if grep -qF -- "$3" <<<"$2"; then _ok "$1"; else _bad "$1 (no [$3] in: $2)"; fi; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export TMPDIR=$T
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

# ---------------------------------------------------------------- touches-logic.sh
TL=$HERE/touches-logic.sh
R=$T/tl; git init -q -b main "$R"; cd "$R" || exit 1
mkdir -p src docs; echo a >src/a.py; echo r >README.md; echo d >docs/x.txt
git add -A; git commit -qm init; B0=$(git rev-parse HEAD)

echo r2 >README.md; echo d2 >docs/x.txt; mkdir -p docs/deep; echo y >docs/deep/y.png
git add -A; git commit -qm docs
out=$(bash "$TL" "$B0"); eq "docs-only change: exit 1" "$?" 1; eq "docs-only change: no files listed" "$out" ""

echo b >src/a.py; echo "x" >"src/with space.py"; git add -A; git commit -qm code
out=$(bash "$TL" "$B0"); eq "code change: exit 0" "$?" 0
has "code change lists src/a.py" "$out" "src/a.py"
has "code change lists a name with a space" "$out" "src/with space.py"
if grep -q README <<<"$out"; then _bad "docs file not listed as logic"; else _ok "docs file not listed as logic"; fi

bash "$TL" 0000000000000000000000000000000000000000 >/dev/null 2>&1; eq "all-zero base: exit 0 (run tests)" "$?" 0
bash "$TL" HEAD HEAD >/dev/null; eq "empty range: exit 1" "$?" 1
bash "$TL" >/dev/null 2>&1; eq "no args: exit 2" "$?" 2

mkdir -p .github; printf '# md is read by tests here\ndocs/*   \n\n' >.github/non-logic-paths
git add -A; git commit -qm list; L0=$(git rev-parse HEAD)
echo r3 >README.md; git add -A; git commit -qm readme
out=$(bash "$TL" "$L0"); eq "repo list without *.md: README change exit 0" "$?" 0; has "README listed" "$out" "README.md"
echo d3 >docs/x.txt; git add -A; git commit -qm docs2
bash "$TL" HEAD~1 >/dev/null; eq "repo list: docs/* change exit 1" "$?" 1

# feature branch diverged from main: only the branch's own changes count (from the merge-base)
git switch -qc feat HEAD~1; echo d4 >docs/x.txt; git add -A; git commit -qm feat-docs
bash "$TL" main feat >/dev/null; eq "merge-base: main moved with code, branch docs-only: exit 1" "$?" 1

# one argument: up to the working tree, committed or not
bash "$TL" main >/dev/null; eq "worktree mode, clean tree, docs-only branch: exit 1" "$?" 1
echo d5 >docs/x.txt
bash "$TL" main >/dev/null; eq "worktree mode, uncommitted docs edit: exit 1" "$?" 1
echo c >>src/a.py
out=$(bash "$TL" main); eq "worktree mode, uncommitted code edit: exit 0" "$?" 0; has "uncommitted code edit listed" "$out" "src/a.py"
git checkout -q -- src/a.py; echo n >src/new.py
out=$(bash "$TL" main); eq "worktree mode, untracked new code file: exit 0" "$?" 0; has "untracked file listed" "$out" "src/new.py"
echo 'src/new.py' >>.git/info/exclude
bash "$TL" main >/dev/null; eq "worktree mode, ignored untracked file does not count" "$?" 1
git checkout -q -- docs/x.txt
git mv docs/x.txt src/x.py; git commit -qm rename
out=$(bash "$TL" HEAD~1 HEAD); eq "rename docs -> code: exit 0" "$?" 0; has "renamed-to code file listed" "$out" "src/x.py"
cd "$T" || exit 1

# ---------------------------------------------------------------- snapshot-run.sh
SR=$HERE/snapshot-run.sh
R=$T/sr; git init -q -b main "$R"; cd "$R" || exit 1
printf 'deps/\n' >.gitignore; echo committed >f; git add -A; git commit -qm init
echo modified >f; echo new >untracked; mkdir -p deps; echo dep >deps/d
HEAD0=$(git rev-parse HEAD); IDX0=$(git diff --cached --name-only | wc -l)

bash "$SR" "$T/log1" -- sh -c 'cat f untracked; test -e deps/d && echo has-deps || echo no-deps; echo "dir=$SNAPSHOT_DIR"; exit 3'
eq "snapshot-run passes the exit code through" "$?" 3
log=$(cat "$T/log1")
has "snapshot sees uncommitted edit" "$log" "modified"
has "snapshot sees untracked file" "$log" "new"
has "gitignored dir is not in snapshot" "$log" "no-deps"
has "log ends with the exit line" "$(tail -1 "$T/log1")" "== snapshot-run exit 3 =="
eq "HEAD untouched" "$(git rev-parse HEAD)" "$HEAD0"
eq "index untouched" "$(git diff --cached --name-only | wc -l)" "$IDX0"
eq "temp worktree removed" "$(git worktree list | wc -l)" 1
sdir=$(sed -n 's/^dir=//p' "$T/log1")
if [ ! -e "$sdir" ]; then _ok "snapshot dir deleted"; else _bad "snapshot dir deleted ($sdir)"; fi

bash "$SR" --link deps "$T/log2" -- sh -c 'cat deps/d' >/dev/null; eq "--link: exit 0" "$?" 0
has "--link makes the ignored dir visible" "$(cat "$T/log2")" "dep"
bash "$SR" --link deps/ "$T/log2b" -- sh -c 'cat deps/d' >/dev/null; eq "--link with a trailing slash: exit 0" "$?" 0
bash "$SR" --link nope "$T/log3" -- true 2>/dev/null; eq "--link to a missing path: exit 125" "$?" 125
has "setup failure is written to the log" "$(cat "$T/log3")" "snapshot-run setup failed"
bash "$SR" --link ../sr "$T/log3b" -- true 2>/dev/null; eq "--link with ..: exit 125" "$?" 125
bash "$SR" --link /etc "$T/log3c" -- true 2>/dev/null; eq "--link absolute: exit 125" "$?" 125
bash "$SR" "$T/log4" true 2>/dev/null; eq "missing --: exit 125" "$?" 125

# edits made while the run is going do not reach the snapshot
bash "$SR" "$T/log5" -- sh -c 'while [ ! -e "$TMPDIR/go" ]; do sleep 0.05; done; cat f' &
pid=$!
sleep 0.5; echo edited-during-run >f; touch "$T/go"; wait "$pid"
has "edit during the run is not seen by the snapshot" "$(cat "$T/log5")" "modified"
if grep -q edited-during-run "$T/log5"; then _bad "snapshot isolated from later edits"; else _ok "snapshot isolated from later edits"; fi

# run from a linked worktree: its own uncommitted state, its own index
git add -A; git commit -qm wip
git worktree add -q "$T/sr-wt" -b wt; cd "$T/sr-wt" || exit 1
echo wt-edit >f; WT_HEAD=$(git rev-parse HEAD)
bash "$SR" "$T/log6" -- cat f >/dev/null; eq "linked worktree: exit 0" "$?" 0
has "linked worktree: snapshot sees its uncommitted edit" "$(cat "$T/log6")" "wt-edit"
eq "linked worktree: HEAD untouched" "$(git rev-parse HEAD)" "$WT_HEAD"
eq "linked worktree: index untouched" "$(git diff --cached --name-only | wc -l)" 0
eq "linked worktree: only the two real worktrees remain" "$(git worktree list | wc -l)" 2
cd "$T" || exit 1

# ---------------------------------------------------------------- ready-issues.py
RI=$HERE/ready-issues.py
G=$T/gh; mkdir -p "$G/bin"
cat >"$G/bin/gh" <<'EOF'
#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_GH"]
a = sys.argv[1:]
p = os.path.join(d, f"issue-{a[2]}.json")
if a[:2] == ["issue", "view"]:
    if not os.path.exists(p):
        sys.stderr.write(f"GraphQL: Could not resolve to an issue or pull request with the number of {a[2]}.\n"); sys.exit(1)
    print(open(p).read())
elif a[:2] == ["issue", "edit"] and "--body-file" in a:
    j = json.load(open(p)); j["body"] = sys.stdin.read(); json.dump(j, open(p, "w"))
else:
    sys.exit(f"fake gh: unexpected {a}")
EOF
chmod +x "$G/bin/gh"
issue() {  # issue <n> <state> <labels csv> <body> [<stateReason>]
  python3 - "$G/issue-$1.json" "$1" "$2" "$3" "$4" "${5:-}" <<'EOF'
import json, sys
f, n, state, labels, body, reason = sys.argv[1:]
json.dump({"number": int(n), "title": f"t{n}", "state": state, "stateReason": reason or None,
           "labels": [{"name": x} for x in labels.split(",") if x], "body": body}, open(f, "w"))
EOF
}
issue 1 OPEN milestone $'## Points\n- [x] #10 feat: done\n- [ ] #11 feat: ready one\n- [ ] #12 feat: waits on 11\n- [x] #13 infra: claimed\n- [ ] #14 feat: closed but unticked\n- [ ] #15 feat: waits on outside 30\n- [ ] #16 feat: no deps line\n- [ ] text point without an issue\n## Decisions\n- #20 decision text\n'
issue 10 CLOSED feature $'Blocked-by: none'
issue 11 OPEN feature $'Milestone: #1\nBlocked-by: #10'
issue 12 OPEN feature $'Blocked-by: #11 #10'
issue 13 OPEN feature,in-progress $'Blocked-by: none'
issue 14 CLOSED feature $'Blocked-by: none'
issue 15 OPEN feature $'Blocked-by: #30'
issue 16 OPEN feature $'no deps line here'
issue 30 OPEN bug $'Blocked-by: none'
export FAKE_GH=$G
run_ri() { PATH="$G/bin:$PATH" python3 -I "$RI" -R o/r "$@"; }

out=$(run_ri 1 --json); eq "ready-issues: exit 0" "$?" 0
st() { python3 -c 'import json,sys; r={str(x["number"]):x for x in json.load(sys.stdin)["rows"]}; print(r[sys.argv[1]]["status"], " ".join(map(str, r[sys.argv[1]]["blocked_by"])))' "$1" <<<"$out"; }
eq "#10 closed" "$(st 10)" "closed "
eq "#11 ready (its blocker is closed)" "$(st 11)" "ready "
eq "#12 blocked by open #11 only" "$(st 12)" "blocked 11"
eq "#13 in-progress" "$(st 13)" "in-progress "
eq "#15 blocked by an issue outside the checklist" "$(st 15)" "blocked 30"
eq "#16 without a Blocked-by line counts as ready" "$(st 16)" "ready "
eq "text point is no-issue" "$(st None)" "no-issue "
has "warns: #13 ticked but open" "$out" "#13 开着"
has "warns: #14 closed but not ticked" "$out" "#14 已结"
has "warns: #16 has no Blocked-by line" "$out" "#16 没写 Blocked-by"
table=$(run_ri 1); has "table shows the blocker arrow" "$table" "← #11"

run_ri 1 --sync >/dev/null
body=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["body"])' "$G/issue-1.json")
has "--sync ticks closed #14" "$body" "- [x] #14 feat: closed but unticked"
has "--sync unticks open #13" "$body" "- [ ] #13 infra: claimed"
has "--sync keeps text points" "$body" "- [ ] text point without an issue"
has "--sync keeps other sections" "$body" "- #20 decision text"
out=$(run_ri 1 --json)
if grep -q '清单里却' <<<"$out"; then _bad "after --sync no tick mismatch"; else _ok "after --sync no tick mismatch"; fi

issue 2 OPEN milestone $'- [ ] #21 a\n- [ ] #22 b\n- [ ] #23 dangling'
issue 21 OPEN feature $'Blocked-by: #22'
issue 22 OPEN feature $'Blocked-by: #21'
issue 23 OPEN feature $'Blocked-by: #99'
out=$(run_ri 2); eq "cycle / dangling: exit 1" "$?" 1
has "reports the cycle" "$out" "依赖成环"
has "reports the dangling blocker" "$out" "#23 的 Blocked-by #99 不存在"
run_ri 98 >/dev/null 2>&1; eq "missing milestone issue: exit 2" "$?" 2

# cut points, a cut blocker, a stray #N in the text, CRLF body
issue 3 OPEN milestone $'- [ ] feat: build on #7 decision\r\n- [ ] ~~feat: dropped before an issue~~ out of scope\r\n- [ ] ~~#40 feat: dropped~~ user cut\r\n- [ ] #41 feat: waits on dropped\r\n- [ ] #42 feat: closed as not planned\r\n'
issue 7 CLOSED decision $'decision' COMPLETED
issue 40 CLOSED feature $'Blocked-by: none' NOT_PLANNED
issue 41 OPEN feature $'Blocked-by: #40'
issue 42 CLOSED feature $'Blocked-by: none' NOT_PLANNED
out=$(run_ri 3 --json); eq "cut blocker: exit 1" "$?" 1
st3() { python3 -c 'import json,sys; rows=json.load(sys.stdin)["rows"]; r=rows[int(sys.argv[1])]; print(r["status"], r["number"])' "$1" <<<"$out"; }
eq "stray #7 in the text is not the point's issue" "$(st3 0)" "no-issue None"
eq "struck point without an issue is cut" "$(st3 1)" "cut None"
eq "struck point with a not-planned issue is cut" "$(st3 2)" "cut 40"
eq "point blocked by a cut issue is blocked, not ready" "$(st3 3)" "blocked 41"
eq "not-planned issue is cut even when not struck" "$(st3 4)" "cut 42"
has "error names the cut blocker" "$out" "#41 的 Blocked-by #40 被砍掉了"
has "warns: not planned but not struck" "$out" "#42 以 not_planned 关掉了"
run_ri 3 --sync >/dev/null
body=$(python3 -c 'import json,sys; print(repr(json.load(open(sys.argv[1]))["body"]))' "$G/issue-3.json")
has "--sync keeps CRLF and ticks struck lines" "$body" '- [x] ~~feat: dropped before an issue~~ out of scope\r\n'
has "--sync leaves the stray-#7 line unticked" "$body" '- [ ] feat: build on #7 decision\r\n'
has "--sync ticks the not-planned point" "$body" "- [x] #42 feat: closed as not planned"

# struck and still open: moved to another milestone is fine; struck in place warns with both options
issue 4 OPEN milestone $'- [ ] ~~#43 bug: deferred~~ → #9\n- [ ] ~~#44 feat: struck but still open~~ cut'
issue 43 OPEN bug $'Milestone: #9\nBlocked-by: none'
issue 44 OPEN feature $'Milestone: #4\nBlocked-by: none'
out=$(run_ri 4); eq "moved point: exit 0" "$?" 0
if grep -q '#43 清单里划掉' <<<"$out"; then _bad "no warning for a point moved to another milestone"; else _ok "no warning for a point moved to another milestone"; fi
has "struck but open in this milestone warns" "$out" "#44 清单里划掉了"
has "the warning offers moving it as well as closing it" "$out" "Milestone: 行改成新的"
issue 5 OPEN milestone $'- [ ] ~~#43 bug: deferred~~ → #9\n- [ ] #45 feat: waits on the deferred bug'
issue 45 OPEN feature $'Milestone: #5\nBlocked-by: #43'
out=$(run_ri 5); has "blocked by an issue moved to another milestone: warns" "$out" "#45 等的 #43 挪到别的 milestone 了"

# ---------------------------------------------------------------- failed-test ID extraction (as documented)
printf '%s\n' 'tests/a.py::test_p[a b] FAILED   [ 50%]' 'FAILED tests/a.py::test_p[a b] - AssertionError: x' \
  'ERROR tests/b.py::test_q - RuntimeError' >"$T/ids.log"
mapfile -t ids < <(sed -nE '/^(FAILED|ERROR) /{s/^(FAILED|ERROR) //;s/ - .*$//;p}' "$T/ids.log")
eq "failed-ID extraction keeps spaces in parametrized IDs" "${ids[0]}" "tests/a.py::test_p[a b]"
eq "failed-ID extraction takes ERROR lines too, skips the -v progress line" "${#ids[@]}:${ids[1]}" "2:tests/b.py::test_q"

printf '\nPassed: %d, Failed: %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
