#!/usr/bin/env bash
# touches-logic.sh — 一组改动碰没碰到代码逻辑；没碰到就不跑任何测试。orchestrate 的测试纪律用
# （见 ../SKILL.md「门禁怎么选」、../milestone.md 第 6 步），本地和 CI 用同一个判据：CI 里用的是拷进仓库的同一个脚本。
#
#   touches-logic.sh <base> <head>     两个提交之间：从 merge-base(<base>, <head>) 到 <head>（CI 用这个）
#   touches-logic.sh <base>            到工作树为止：从 merge-base(<base>, HEAD) 起，连同没提交的改动和没 add 的新文件
#                                      （本地跑测试之前用这个，提没提交都算）
#
# 只看路径：改过的文件全落在非逻辑清单里 ⇒ 退出 1（不跑测试）；否则退出 0（要跑），stdout 列出算逻辑的文件。
# 拿不准一律算要跑：<base> 或 <head> 认不出（新分支推送时 github.event.before 是全零）、没有共同祖先、git 出错，
# 都退出 0。用法错退出 2。
#
# 非逻辑清单：仓库根的 .github/non-logic-paths，一行一个 bash 通配（`*` 跨目录，# 起是注释）；
# 没有这个文件就用下面的 DEFAULT。测试会读的 md（prompt、模板、skill 仓库）是逻辑：在清单里去掉 *.md。
set -uo pipefail

usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }

DEFAULT=('*.md' '*.rst' '*.adoc' 'docs/*' 'LICENSE*' 'NOTICE*' '.github/ISSUE_TEMPLATE/*' '.github/PULL_REQUEST_TEMPLATE*')

case ${1:-} in -h|--help|'') usage ;; esac
[ $# -le 2 ] || usage
BASE=$1 HEAD=${2:-HEAD} WORKTREE=0
[ $# = 2 ] || WORKTREE=1
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "要在仓库里跑" >&2; exit 2; }
for c in "$BASE" "$HEAD"; do
  git -C "$ROOT" rev-parse -q --verify "$c^{commit}" >/dev/null || { echo "touches-logic: 认不出 $c，按要跑算" >&2; exit 0; }
done
MB=$(git -C "$ROOT" merge-base "$BASE" "$HEAD") || { echo "touches-logic: $BASE 和 $HEAD 没有共同祖先，按要跑算" >&2; exit 0; }

GLOBS=()
if [ -f "$ROOT/.github/non-logic-paths" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}; line=${line%"${line##*[![:space:]]}"}
    [ -z "$line" ] || GLOBS+=("$line")
  done <"$ROOT/.github/non-logic-paths"
else
  GLOBS=("${DEFAULT[@]}")
fi

# 改过的文件，NUL 分隔。--no-renames：改名拆成删旧加新，新名字按自己的类别算。
changed() {
  if [ "$WORKTREE" = 1 ]; then
    git -C "$ROOT" diff -z --name-only --no-renames "$MB" -- && git -C "$ROOT" ls-files -z -o --exclude-standard
  else
    git -C "$ROOT" diff -z --name-only --no-renames "$MB" "$HEAD" --
  fi
}
LIST=$(mktemp); trap 'rm -f "$LIST"' EXIT
changed >"$LIST" || { echo "touches-logic: git 出错，按要跑算" >&2; exit 0; }

LOGIC=0
while IFS= read -r -d '' f; do
  hit=0
  for g in ${GLOBS[@]+"${GLOBS[@]}"}; do
    # shellcheck disable=SC2053  # $g 不加引号：要它当通配
    if [[ $f == $g ]]; then hit=1; break; fi
  done
  [ "$hit" = 1 ] || { printf '%s\n' "$f"; LOGIC=1; }
done <"$LIST"

[ "$LOGIC" = 1 ] && exit 0
exit 1
