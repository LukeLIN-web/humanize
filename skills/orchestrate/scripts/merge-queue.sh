#!/usr/bin/env bash
# merge-queue.sh — 仓库没有原生 merge queue 时（个人名下的仓库都没有）用的合并队列，
# orchestrate 协作 PR 模式的一部分（见 ../collab-pr.md）。在仓库里跑，PR 都是 origin 上的 PR。
#
# 不变量：默认分支只前进到一个「门禁在合并结果上绿过」的提交；PR 分支一个字节都不改写。
#
#   merge-queue.sh [--gate ci|local:<命令>] [--semver] [--version-cmd <命令>] [--dry-run] PR号...
#
# 一批 PR 依次 --no-ff 合进最新的 origin/<默认分支>（在 .git 下的专用 worktree 里），过门禁，
# 再非强制地推成默认分支——推被拒就说明排队期间默认分支前进了，整批重跑即可。
# GitHub 看到 PR 的 head 进了默认分支，就把 PR 记为已合并；PR 正文里的 Closes #N 抄进合并提交。
#
#   --gate ci            （默认）把合并结果推成 mq/<PR号> 分支，等这个提交上的 CI 跑完：每个在 PR head 上
#                        跑过的 workflow 都要在合并结果上跑一遍并且绿；仓库的 CI 不在 mq/* 的 push 上触发就报错退出
#   --gate local:<命令>  在队列 worktree 里跑 <命令>，退出码 0 算绿
#   --semver             按 PR 标题（Conventional Commits）取这批的最高级别：`type!:` 为 major，`feat` 为 minor，
#                        其余 patch；0.x 期间 major 只升 minor。从 origin 上已并入默认分支的最高 vX.Y.Z tag
#                        算出新版本（没有这样的 tag 就退出：先把现行版本打成 tag），合并后打 tag 推上去
#   --version-cmd <命令> 只配 --semver：在合并结果上跑（环境变量 NEW_VERSION、BUMP、PRS），
#                        它改的文件提交成一个 release 提交，和合并一起过门禁
#   --dry-run            合并、算版本，不跑门禁、不推
#
# 退出码：0 合进去了；2 用法或前提不满足；3 合并冲突（报出 PR 和文件）；4 门禁红；
#         5 某个 PR 的 head 在排队期间变了；6 默认分支在排队期间前进了（直接重跑）；
#         7 合进去了，但打 tag / 删分支 / 核 MERGED 有没做完的（看报错补）。
#         别的退出码先看 origin/<默认分支> 有没有含上合并结果，再决定重跑。
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "要在仓库里跑" >&2; exit 2; }
COMMON=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)
# 一个 clone 上同时只跑一个队列。锁由外层的 flock 进程拿着（-o：不传给子进程），
# 门禁或 version-cmd 起的常驻进程不会把锁一直占着。
if [ -z "${MQ_LOCKED:-}" ]; then
  rc=0; MQ_LOCKED=1 flock -n -o -E 75 "$COMMON/mq.lock" bash "$0" "$@" || rc=$?
  [ "$rc" != 75 ] || { echo "这个 clone 上已经有一个队列在跑" >&2; exit 2; }
  exit "$rc"
fi

GATE=ci SEMVER=0 VERSION_CMD='' DRY=0 PRS=()
while [ $# -gt 0 ]; do
  case $1 in
    --gate) GATE=${2:?}; shift 2 ;;
    --semver) SEMVER=1; shift ;;
    --version-cmd) VERSION_CMD=${2:?}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    -*) echo "未知参数 $1" >&2; usage ;;
    *) [[ $1 =~ ^[0-9]+$ ]] || { echo "PR 号要是数字：$1" >&2; exit 2; }; PRS+=("$1"); shift ;;
  esac
done
[ ${#PRS[@]} -gt 0 ] || usage
case $GATE in ci|local:?*) ;; *) echo "--gate 只能是 ci 或 local:<命令>" >&2; exit 2 ;; esac
[ -z "$VERSION_CMD" ] || [ "$SEMVER" = 1 ] || { echo "--version-cmd 要和 --semver 一起用" >&2; exit 2; }

export GIT_TERMINAL_PROMPT=0
WT=$COMMON/mq-worktree
# gh 只认 origin：多 remote 时 gh 自己挑的默认仓库可能是 upstream
R=$(git -C "$ROOT" remote get-url origin | sed -E 's#^[a-z+]+://([^@/]*@)?github\.com/##; s#^[^@/]*@github\.com:##; s#\.git$##; s#/$##')
BASE=$(gh repo view "$R" --json defaultBranchRef --jq .defaultBranchRef.name)
BATCH=mq/$(IFS=-; echo "${PRS[*]}")
PUSHED_BATCH=0
# shellcheck disable=SC2329  # cleanup 由 trap 调
cleanup() {
  if [ "$PUSHED_BATCH" = 1 ]; then git -C "$ROOT" push -q origin --delete "$BATCH" 2>/dev/null || true; fi
  git -C "$ROOT" for-each-ref --format='delete %(refname)' refs/mq | git -C "$ROOT" update-ref --stdin 2>/dev/null || true
}
trap cleanup EXIT

git -C "$ROOT" fetch -q --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE"
git -C "$ROOT" worktree prune
[ -d "$WT" ] || git -C "$ROOT" worktree add -q --detach "$WT" "origin/$BASE"
git -C "$WT" merge --abort 2>/dev/null || true
git -C "$WT" checkout -q --detach -f "origin/$BASE"
git -C "$WT" clean -qfdx

declare -A TITLE HEAD REF CROSS LEVEL CLOSES
rank() { case $1 in major) echo 3 ;; minor) echo 2 ;; *) echo 1 ;; esac; }
BUMP='patch'
for n in "${PRS[@]}"; do
  meta=$(gh pr view "$n" -R "$R" --json state,baseRefName,isDraft,headRefOid,headRefName,isCrossRepository \
    --jq '"\(.state) \(.baseRefName) \(.isDraft) \(.headRefOid) \(.headRefName) \(.isCrossRepository)"')
  read -r state base draft head ref cross <<<"$meta"
  if [ "$state" != OPEN ] || [ "$base" != "$BASE" ] || [ "$draft" != false ]; then
    echo "#$n 不进队：state=$state base=$base draft=$draft" >&2; exit 2
  fi
  git -C "$ROOT" fetch -q --no-tags origin "+refs/pull/$n/head:refs/mq/pr-$n"
  [ "$(git -C "$ROOT" rev-parse "refs/mq/pr-$n")" = "$head" ] \
    || { echo "#$n 刚拉到的 head 和 GitHub 报的不同（作者正在推），稍后重跑" >&2; exit 5; }
  TITLE[$n]=$(gh pr view "$n" -R "$R" --json title --jq .title)
  CLOSES[$n]=$(gh pr view "$n" -R "$R" --json body --jq .body \
    | grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?) #[0-9]+' || true)
  HEAD[$n]=$head REF[$n]=$ref CROSS[$n]=$cross
  if [[ ${TITLE[$n]} =~ ^[a-z]+(\([^\)]*\))?!: ]]; then LEVEL[$n]=major
  elif [[ ${TITLE[$n]} =~ ^feat(\([^\)]*\))?: ]]; then LEVEL[$n]=minor
  else LEVEL[$n]='patch'; fi
  [ "$(rank "${LEVEL[$n]}")" -le "$(rank "$BUMP")" ] || BUMP=${LEVEL[$n]}
done

for n in "${PRS[@]}"; do
  msg=(-m "Merge PR #$n: ${TITLE[$n]}"); [ -z "${CLOSES[$n]}" ] || msg+=(-m "${CLOSES[$n]}")
  if ! out=$(git -C "$WT" merge --no-ff "${msg[@]}" "refs/mq/pr-$n" 2>&1); then
    files=$(git -C "$WT" diff --name-only --diff-filter=U | tr '\n' ' ')
    git -C "$WT" merge --abort 2>/dev/null || true
    echo "弹出 #$n：和 origin/$BASE（加上这批里排在它前面的 PR）冲突：${files:-$out}" >&2
    exit 3
  fi
done

NEW=''
if [ "$SEMVER" = 1 ]; then
  git -C "$ROOT" fetch -q --no-tags origin '+refs/tags/*:refs/mq/tags/*'
  last=$(git -C "$ROOT" for-each-ref --merged "origin/$BASE" --sort=-v:refname --format='%(refname:strip=3)' refs/mq/tags \
    | awk '/^v[0-9]+\.[0-9]+\.[0-9]+$/ && !f {print; f=1}')
  [ -n "$last" ] || { echo "origin 上 $BASE 里没有 vX.Y.Z tag：先把现行版本（manifest 里写的那个）打成 tag 推上去" >&2; exit 2; }
  IFS=. read -r MA MI PA <<<"${last#v}"
  case $BUMP in
    major) if [ "$MA" = 0 ]; then MI=$((MI+1)) PA=0; else MA=$((MA+1)) MI=0 PA=0; fi ;;
    minor) MI=$((MI+1)) PA=0 ;;
    patch) PA=$((PA+1)) ;;
  esac
  NEW=$MA.$MI.$PA
  if [ -n "$VERSION_CMD" ]; then
    (cd "$WT" && NEW_VERSION=$NEW BUMP=$BUMP PRS="${PRS[*]}" bash -c "$VERSION_CMD")
    git -C "$WT" add -A
    git -C "$WT" diff --cached --quiet || git -C "$WT" commit -q -m "release v$NEW" \
      -m "$(for n in "${PRS[@]}"; do echo "- #$n ${TITLE[$n]} (${LEVEL[$n]})"; done)"
  fi
  echo "版本：$last → v$NEW（$BUMP）"
fi
SHA=$(git -C "$WT" rev-parse HEAD)

if [ "$DRY" = 1 ]; then
  git -C "$WT" log --oneline --first-parent "origin/$BASE..$SHA"
  echo "dry-run：没跑门禁、没推"; exit 0
fi

case $GATE in
  local:*)
    (cd "$WT" && bash -c "${GATE#local:}") || { echo "门禁红（local），合并结果 $SHA" >&2; exit 4; } ;;
  ci)
    # 合并结果上要跑到的 workflow = 各 PR head 上跑过的；一个都没有就至少等到一个
    expected=$(for n in "${PRS[@]}"; do
      gh run list -R "$R" --commit "${HEAD[$n]}" --json workflowName --jq '.[].workflowName'; done | sort -u)
    git -C "$WT" push -q -f origin "$SHA:refs/heads/$BATCH"; PUSHED_BATCH=1
    appear=$((SECONDS + ${MQ_CI_APPEAR_SECS:-300})) deadline=$((SECONDS + ${MQ_CI_TIMEOUT_SECS:-7200})) seen=''
    while :; do
      runs=$(gh run list -R "$R" --commit "$SHA" --json status,conclusion,workflowName,url \
        --jq '.[] | "\(.status)\t\(.conclusion)\t\(.workflowName)\t\(.url)"')
      missing=$(comm -23 <(printf '%s\n' "$expected" | sed '/^$/d') <(cut -f3 <<<"$runs" | sed '/^$/d' | sort -u))
      if [ -z "$runs" ] || [ -n "$missing" ]; then
        if [ $SECONDS -ge $appear ]; then
          echo "$BATCH 上等不到 CI run（${missing:-任何 workflow}）：CI 不在 mq/* 的 push 上触发。" \
               "改用 --gate local:<和 CI 同一条命令>，或给 workflow 加 mq/** 的 push 触发" | tr '\n' ' ' >&2; echo >&2
          exit 4
        fi
      elif ! grep -qv $'^completed\t' <<<"$runs"; then
        # 全部跑完后再看一眼，防止有的 workflow 晚登记
        if [ "$seen" = "$runs" ]; then break; fi
        seen=$runs
      fi
      [ $SECONDS -lt $deadline ] || { echo "CI 超时（MQ_CI_TIMEOUT_SECS），合并结果 $SHA" >&2; exit 4; }
      sleep "${MQ_POLL_SECS:-20}"
    done
    bad=$(grep -vE $'^completed\t(success|skipped|neutral)\t' <<<"$runs" || true)
    [ -z "$bad" ] || { echo "门禁红（CI），合并结果 $SHA：" >&2; echo "$bad" >&2; exit 4; } ;;
esac

for n in "${PRS[@]}"; do
  [ "$(gh pr view "$n" -R "$R" --json headRefOid --jq .headRefOid)" = "${HEAD[$n]}" ] \
    || { echo "#$n 的 head 在排队期间变了（作者又推了），整批重跑" >&2; exit 5; }
done

if ! err=$(git -C "$WT" push -q origin "$SHA:refs/heads/$BASE" 2>&1); then
  git -C "$ROOT" fetch -q --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE"
  if git -C "$ROOT" merge-base --is-ancestor "$SHA" "origin/$BASE"; then
    echo "推 $BASE 报了错，但合并结果已经在 $BASE 上了：$err" >&2
  elif ! git -C "$ROOT" merge-base --is-ancestor "origin/$BASE" "$SHA"; then
    echo "推 $BASE 被拒：排队期间 $BASE 前进了，整批重跑" >&2; exit 6
  else
    echo "推 $BASE 失败（不是因为它前进了）：$err" >&2; exit 2
  fi
fi

# 合进去以后的收尾不中断：没做完的报出来，退出码 7
LEFT=0
if [ -n "$NEW" ]; then
  if ! { git -C "$ROOT" tag -a "v$NEW" -m "v$NEW" "$SHA" && git -C "$ROOT" push -q origin "refs/tags/v$NEW"; }; then
    git -C "$ROOT" tag -d "v$NEW" >/dev/null 2>&1 || true
    echo "tag v$NEW 没推上去：git tag -a v$NEW $SHA && git push origin v$NEW" >&2; LEFT=1
  fi
fi
for n in "${PRS[@]}"; do
  state=''
  for _ in $(seq 15); do
    state=$(gh pr view "$n" -R "$R" --json state --jq .state 2>/dev/null || true)
    [ "$state" = MERGED ] && break
    sleep 2
  done
  if [ "$state" != MERGED ]; then
    echo "#$n 推进了 $BASE，但 GitHub 还没记成 MERGED（state=$state），去看一眼" >&2; LEFT=1
  elif [ "${CROSS[$n]}" != true ]; then
    git -C "$ROOT" push -q --force-with-lease="refs/heads/${REF[$n]}:${HEAD[$n]}" origin ":refs/heads/${REF[$n]}" 2>/dev/null \
      || { echo "#$n 的分支 ${REF[$n]} 没删（已不在 ${HEAD[$n]:0:12}，或推不上去）" >&2; LEFT=1; }
  fi
done
echo "合并：$(printf '#%s ' "${PRS[@]}")→ $BASE @ ${SHA:0:12}${NEW:+，v$NEW}"
exit "$((LEFT ? 7 : 0))"
