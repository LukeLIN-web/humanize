#!/usr/bin/env bash
# Restart the Humanize Viz dashboard server.
#
# Usage:
#   viz-restart.sh <project_dir>           # legacy positional
#   viz-restart.sh --project <path>        # matches viz-start.sh / viz-stop.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse the documented --project flag the same way viz-start.sh and
# viz-stop.sh do. The old `"${1:-.}"` form treated the flag name
# itself as a directory and `cd --project` would fail, which broke
# the form printed in the usage string above.
PROJECT_DIR="."
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) PROJECT_DIR="$2"; shift 2 ;;
        --) shift ;;
        *) PROJECT_DIR="$1"; shift ;;
    esac
done
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

bash "$SCRIPT_DIR/viz-stop.sh" --project "$PROJECT_DIR" 2>/dev/null || true
sleep 1
exec bash "$SCRIPT_DIR/viz-start.sh" --project "$PROJECT_DIR"
