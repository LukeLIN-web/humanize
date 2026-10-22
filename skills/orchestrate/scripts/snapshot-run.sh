#!/usr/bin/env bash
# snapshot-run.sh — 在工作树的快照里跑测试，好让人在测试还在跑的时候就开始改代码。orchestrate 的测试纪律
# 用（见 ../SKILL.md「门禁怎么选」）：全量跑、不 fail-fast，日志里第一处失败一出现就开始修。
#
#   snapshot-run.sh [--link <路径>]... <日志> -- <命令> [参数...]
#
# 快照 = 工作树此刻的全部内容：已提交的、改了没提交的、新建没 add 的（被 .gitignore 掉的不算）。
# 不动 HEAD、index 和工作树。快照检出到 $TMPDIR 下一个临时 worktree，<命令> 在那里跑，环境变量
# SNAPSHOT_DIR 指向它；stdout 和 stderr 都写进 <日志>，第一行是快照的提交号，最后一行是
# `== snapshot-run exit <退出码> ==`。跑完删掉临时 worktree。
#
# 退出码：<命令> 的退出码；snapshot-run 自己出错（用法错、建快照失败）是 125，原因写进 stderr，
# <日志> 已经给出时也写进 <日志>（最后一行 `== snapshot-run setup failed: … ==`）。
#
#   --link <路径>   工作树里被 gitignore 掉、<命令> 又要用的东西（node_modules、.venv、构建缓存）软链进快照，
#                   可重复，路径相对仓库根、不许带 ..。软链过去的是共享的：<命令> 往里写，写的就是工作树那一份。
#
# 被测的代码要来自快照：Python 设 PYTHONPATH="$SNAPSHOT_DIR"（src 布局 "$SNAPSHOT_DIR/src"），主工作树的 venv
# 若是 editable install，不设就测成了工作树的源码。快照跑完就删，测试框架写在快照里的缓存（.pytest_cache 等）
# 跟着没了：「只重跑失败的」从日志里取失败的测试 ID。被强杀时临时 worktree 会留下：git worktree list 看，
# git worktree remove --force 删。
#
# 测试命令要逐条打印结果，失败才会在跑的过程中出现在日志里（pytest 加 -v；只打点的 -q 要到跑完才列失败）。
# 典型用法（Bash 工具 run_in_background）：
#   bash snapshot-run.sh "$SCRATCH/test.log" -- sh -c 'PYTHONPATH=$SNAPSHOT_DIR /abs/venv/bin/python -m pytest -v -rfE'
#   第一处失败：grep -n -m1 -E ' (FAILED|ERROR)( |$)' "$SCRATCH/test.log"
#   跑完重跑失败的（测试 ID 可能带空格，一行一个读进数组）：
#     mapfile -t ids < <(sed -nE '/^(FAILED|ERROR) /{s/^(FAILED|ERROR) //;s/ - .*$//;p}' "$SCRATCH/test.log")
#     pytest -v -rfE "${ids[@]}"
set -euo pipefail

LOG=''
fail() {
  echo "snapshot-run: $*" >&2
  [ -z "$LOG" ] || echo "== snapshot-run setup failed: $* ==" >>"$LOG" 2>/dev/null || true
  exit 125
}
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 125; }

LINKS=()
while [ $# -gt 0 ]; do
  case $1 in
    --link)
      [ $# -ge 2 ] || fail "--link 要一个路径"
      l=$2; while [ "${l%/}" != "$l" ]; do l=${l%/}; done
      case /$l/ in //|/./|*/../*) fail "--link 要仓库里的相对路径，不许带 ..：$2" ;; esac
      case $l in /*) fail "--link 要相对路径：$2" ;; esac
      LINKS+=("$l"); shift 2 ;;
    -h|--help) usage ;;
    -*) echo "未知参数 $1" >&2; usage ;;
    *) break ;;
  esac
done
[ $# -ge 3 ] && [ "$2" = -- ] || usage
LOG=$(realpath -m "$1"); shift 2
: >"$LOG" 2>/dev/null || { LOG=''; fail "写不了日志 $1"; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || fail "要在仓库里跑"
for l in ${LINKS[@]+"${LINKS[@]}"}; do
  [ -e "$ROOT/$l" ] || fail "--link $l：工作树里没有"
done

# 用一份临时 index 记下工作树全貌：真 index 不动。先拷真 index 只为快（add -A 能复用 stat 信息）。
IDX=$(mktemp) || fail "mktemp 失败"
SRC_IDX=$(git -C "$ROOT" rev-parse --path-format=absolute --git-path index) || fail "找不到 index"
if [ -f "$SRC_IDX" ]; then cp "$SRC_IDX" "$IDX"; else rm -f "$IDX"; fi
GIT_INDEX_FILE=$IDX git -C "$ROOT" add -A || { rm -f "$IDX"; fail "git add -A（临时 index）失败"; }
TREE=$(GIT_INDEX_FILE=$IDX git -C "$ROOT" write-tree) || { rm -f "$IDX"; fail "git write-tree 失败"; }
rm -f "$IDX"
PARENT=()
if git -C "$ROOT" rev-parse -q --verify HEAD >/dev/null; then PARENT=(-p HEAD); fi
COMMIT=$(GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME:-snapshot-run} GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL:-snapshot-run@localhost} \
  GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME:-snapshot-run} GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL:-snapshot-run@localhost} \
  git -C "$ROOT" commit-tree "$TREE" ${PARENT[@]+"${PARENT[@]}"} -m snapshot-run) || fail "git commit-tree 失败"

DIR=$(mktemp -d "${TMPDIR:-/tmp}/snapshot-run.XXXXXX") || fail "建不了临时目录"
# shellcheck disable=SC2329  # cleanup 由 trap 调
cleanup() {
  git -C "$ROOT" worktree remove --force "$DIR" 2>/dev/null && return
  rm -rf "$DIR"; git -C "$ROOT" worktree prune 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
git -C "$ROOT" worktree add -q --detach "$DIR" "$COMMIT" || fail "git worktree add 失败"
for l in ${LINKS[@]+"${LINKS[@]}"}; do
  if ! { mkdir -p "$(dirname "$DIR/$l")" && rm -rf "${DIR:?}/$l" && ln -s "$ROOT/$l" "$DIR/$l"; }; then fail "--link $l 软链失败"; fi
done

echo "== snapshot-run $COMMIT in $DIR: $* ==" >>"$LOG"
rc=0
(cd "$DIR" && SNAPSHOT_DIR=$DIR "$@") >>"$LOG" 2>&1 || rc=$?
echo "== snapshot-run exit $rc ==" >>"$LOG"
exit "$rc"
