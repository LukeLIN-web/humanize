#!/usr/bin/env bash
# todo-dispatch 每轮的推送：共享工作树的本地默认分支，并上 origin 后在「已提交的内容」上过门禁，再非强制地推上去。
# 门禁跑在 HEAD 的临时 detached worktree 里，查不到别的会话没提交的改动。在仓库里跑：
#
#   push-shared-main.sh [--gate '<命令>']      默认门禁 bash scripts/ci_check.sh
#
# 退出码：0 推上去了或没有要推的；2 推失败但 origin 没前进（看报错）；
#         3 别的会话没提交的改动挡着并 origin（暂存的、没做完的合并、或 origin 要动的文件上有改动；等它清了重跑）；
#         4 门禁红（没推，报出待推区间的提交）；5 并 origin 冲突（已 abort）；6 推被拒（origin 刚前进，重跑）
set -euo pipefail

GATE='bash scripts/ci_check.sh'
case ${1:-} in
  --gate) GATE=${2:?--gate 要一条命令} ;;
  '') ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2 ;;
esac

cd "$(git rev-parse --show-toplevel)"
export GIT_TERMINAL_PROMPT=0
B=$(git symbolic-ref --short HEAD)
git fetch -q origin "$B"

if ! git merge-base --is-ancestor "origin/$B" HEAD; then
  if ! git diff --cached --quiet || git rev-parse -q --verify MERGE_HEAD >/dev/null; then
    echo "index 里有别人暂存的改动或没做完的合并，现在并 origin/$B 会卷进合并提交：等它清了重跑" >&2; exit 3
  fi
  if ! git merge --no-edit -q "origin/$B" -m "并入 origin/$B"; then
    git rev-parse -q --verify MERGE_HEAD >/dev/null || { echo "要动的文件上有别人没提交的改动，git 没并：等它清了重跑" >&2; exit 3; }
    git merge --abort; echo "并 origin/$B 冲突，已 abort" >&2; exit 5
  fi
fi
if git merge-base --is-ancestor HEAD "origin/$B"; then echo "没有要推的"; exit 0; fi

SHA=$(git rev-parse HEAD)
WT=$(mktemp -d)/gate
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$(dirname "$WT")"' EXIT
git worktree add -q --detach "$WT" "$SHA"
if ! (cd "$WT" && bash -c "$GATE"); then
  echo "门禁红在 ${SHA:0:7}，没推。待推区间：" >&2
  git log --format='  %h %<(100,trunc)%s' "origin/$B..$SHA" >&2
  exit 4
fi
N=$(git rev-list --count "origin/$B..$SHA")
if ! err=$(git push -q origin "$SHA:refs/heads/$B" 2>&1); then
  git fetch -q origin "$B"
  git merge-base --is-ancestor "origin/$B" "$SHA" || { echo "推被拒：origin/$B 刚前进，重跑" >&2; exit 6; }
  echo "推失败（不是因为 origin/$B 前进了）：$err" >&2; exit 2
fi
echo "推上去了：origin/$B → ${SHA:0:7}（$N 个提交）"
