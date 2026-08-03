#!/usr/bin/env bash
#
# install-hooks.sh — wire this checkout's hooks into a project, by symlink.
#
# Why symlinks: a hook that is COPIED into a project stops tracking the checkout.
# Two copies of task-codex-review.sh drifted apart here for a month (medium/600
# vs xhigh/1800, different diff scoping) while both were registered, so every
# task completion was reviewed twice by two differently-configured reviewers.
# A symlink cannot drift: edit the checkout, every project has it.
#
# Installs into <project>/.claude/hooks/<name>.sh and registers each hook in
# <project>/.claude/settings.json. Everything it creates is recorded in
# <project>/.humanize/installed-hooks.txt, and uninstall touches only that list.
#
# Usage:
#   scripts/install-hooks.sh <project_path> [--hooks a,b] [--dry-run]
#   scripts/install-hooks.sh <project_path> --uninstall
#   scripts/install-hooks.sh --list
#
# Notes:
#   - The humanize plugin already registers these hooks for every project. This
#     installer is for projects/machines NOT running the plugin. If both are
#     active, the hooks single-flight, but the installer warns about it.
#   - An existing file that this installer did not create is never overwritten.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_SRC="$REPO_ROOT/hooks"
MANIFEST_REL=".humanize/installed-hooks.txt"
MANIFEST_VERSION=1

# hook file : settings.json event : timeout seconds
HOOK_SPECS=(
  "task-codex-review.sh:TaskCompleted:900"
  "goal-monitor-spawn.sh:UserPromptSubmit:20"
)

PROJECT=""
ACTION="install"
DRY_RUN=false
SELECTED=""

die() { echo "install-hooks: $*" >&2; exit 1; }
log() { echo "$*"; }

list_hooks() {
  local spec
  for spec in "${HOOK_SPECS[@]}"; do
    printf '  %-28s %s\n' "${spec%%:*}" "$(printf '%s' "$spec" | cut -d: -f2)"
  done
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \?//'; exit 0 ;;
    --list) log "hooks available in $HOOKS_SRC:"; list_hooks; exit 0 ;;
    --uninstall) ACTION="uninstall"; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --hooks) [ -n "${2:-}" ] || die "--hooks needs a comma-separated list"; SELECTED="$2"; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$PROJECT" ] || die "only one project path"; PROJECT="$1"; shift ;;
  esac
done

[ -n "$PROJECT" ] || die "usage: install-hooks.sh <project_path> [--hooks a,b] [--dry-run|--uninstall]"
PROJECT="$(cd "$PROJECT" 2>/dev/null && pwd)" || die "no such directory: $PROJECT"
command -v jq >/dev/null 2>&1 || die "jq is required"

CLAUDE_DIR="$PROJECT/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"
MANIFEST="$PROJECT/$MANIFEST_REL"

selected_wanted() {  # $1 = hook file name
  [ -z "$SELECTED" ] && return 0
  printf '%s' ",$SELECTED," | grep -q ",$1,"
}

# ---------------------------------------------------------------- uninstall

if [ "$ACTION" = "uninstall" ]; then
  [ -f "$MANIFEST" ] || die "no manifest at $MANIFEST; nothing this installer created"
  while IFS=$'\t' read -r kind name target; do
    [ "$kind" = "hook" ] || continue
    link="$HOOKS_DIR/$name"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
      $DRY_RUN || rm -f "$link"
      log "  removed link $link"
    else
      log "  skipped $link (not the symlink this installer created)"
    fi
    if [ -f "$SETTINGS" ] && ! $DRY_RUN; then
      tmp="$(mktemp)"
      if jq --arg cmd "\"\$CLAUDE_PROJECT_DIR/.claude/hooks/$name\"" '
            if .hooks then
              .hooks |= (
                with_entries(
                  .value |= (
                    map(.hooks |= map(select(.command != $cmd)))
                    | map(select((.hooks | length) > 0))
                  )
                )
                | with_entries(select((.value | length) > 0))
              )
            else . end
          ' "$SETTINGS" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$SETTINGS"
        log "  unregistered $name from settings.json"
      else
        rm -f "$tmp"
        log "  WARNING: could not update $SETTINGS; remove the $name entry manually"
      fi
    fi
  done < <(grep -v '^version' "$MANIFEST" 2>/dev/null)
  $DRY_RUN || mv -f "$MANIFEST" "$MANIFEST.prev"
  log "uninstalled (manifest kept as $MANIFEST_REL.prev)"
  exit 0
fi

# ---------------------------------------------------------------- install

# The plugin registers the same hooks globally; two registrations mean two
# firings of the same event (the hooks single-flight, but say so out loud).
plugin_hooks_json="$HOME/.claude/plugins/cache/PolyArch/humanize"
if [ -d "$plugin_hooks_json" ] && grep -rqs 'task-codex-review.sh' "$plugin_hooks_json"; then
  log "note: the humanize plugin is installed and already registers these hooks for"
  log "      every project. Installing them here too means the same event fires twice;"
  log "      the hooks single-flight so only one review runs, but prefer one or the other."
fi

$DRY_RUN || mkdir -p "$HOOKS_DIR" "$(dirname "$MANIFEST")"

manifest_tmp="$(mktemp)"
printf 'version\t%s\n' "$MANIFEST_VERSION" > "$manifest_tmp"
installed=0

for spec in "${HOOK_SPECS[@]}"; do
  name="${spec%%:*}"
  event="$(printf '%s' "$spec" | cut -d: -f2)"
  timeout="$(printf '%s' "$spec" | cut -d: -f3)"
  src="$HOOKS_SRC/$name"
  link="$HOOKS_DIR/$name"
  cmd="\"\$CLAUDE_PROJECT_DIR/.claude/hooks/$name\""

  selected_wanted "$name" || continue
  [ -r "$src" ] || { log "  skip $name (not in $HOOKS_SRC)"; continue; }

  if [ -e "$link" ] && [ ! -L "$link" ]; then
    log "  REFUSING $name: $link exists and is a real file, not a link this installer made."
    log "            Move it aside first if you want the checkout's version."
    continue
  fi
  if [ -L "$link" ] && [ "$(readlink "$link")" != "$src" ]; then
    log "  relinking $name (was -> $(readlink "$link"))"
  fi

  $DRY_RUN || ln -sfn "$src" "$link"
  printf 'hook\t%s\t%s\n' "$name" "$src" >> "$manifest_tmp"
  installed=$((installed + 1))
  log "  linked   $link -> $src"

  # Register in settings.json only if this exact command is not already there.
  if ! $DRY_RUN; then
    [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
    tmp="$(mktemp)"
    if jq --arg ev "$event" --arg cmd "$cmd" --argjson to "$timeout" '
        .hooks //= {} |
        .hooks[$ev] //= [] |
        if ([.hooks[$ev][]?.hooks[]?.command] | index($cmd)) then .
        else .hooks[$ev] += [{hooks: [{type:"command", command:$cmd, timeout:$to}]}]
        end
      ' "$SETTINGS" > "$tmp" 2>/dev/null; then
      mv "$tmp" "$SETTINGS"
      log "  registered $name on $event in .claude/settings.json"
    else
      rm -f "$tmp"
      log "  WARNING: could not update $SETTINGS (invalid JSON?); register $name manually"
    fi
  fi
done

if $DRY_RUN; then
  rm -f "$manifest_tmp"
  log "(dry-run) $installed hook(s) would be installed into $PROJECT"
else
  mv -f "$manifest_tmp" "$MANIFEST"
  log "installed $installed hook(s) into $PROJECT (manifest: $MANIFEST_REL)"
fi
