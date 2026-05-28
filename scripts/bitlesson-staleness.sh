#!/usr/bin/env bash
#
# bitlesson-staleness.sh
#
# Advisory scan of a BitLesson knowledge base for entries whose cited file
# references no longer resolve under the project root. Lesson *content* usually
# stays valid across refactors, but the *references* it cites drift when code
# moves. The stop gate validates only the Delta *format*, so a lesson can
# silently rot; this script surfaces it.
#
# Precision over recall (a noisy advisory gets ignored). Detection is anchored
# on a known file extension, because prose almost never produces `word.ext`
# tokens whereas "GO/NO-GO", "validators/gates", or "248/275" are common:
#   - `dir/sub/file.py`  -> checked verbatim against the project root
#   - bare `file.py`     -> checked anywhere under the root
#   - tokens inside ``` fenced blocks ```, glob/brace tokens, ellipsis
#     placeholders, and extensionless prose are ignored
#   - entries marked `Status: deprecated` are skipped
#
# Trade-off: extensionless directory references (e.g. a Scope of `src/foo`) are
# not verified; reference a concrete file when you want it checked.
#
# Exit codes: 0 (advisory, default). With --strict: 2 if any entry has
# unresolved references. 1 on usage/IO error.

set -euo pipefail

usage() {
    cat <<'EOF' >&2
Usage:
  bitlesson-staleness.sh --bitlesson-file <path> [--project-root <path>] [--strict]

Options:
  --bitlesson-file <path>   BitLesson knowledge base (e.g. .humanize/bitlesson.md)
  --project-root <path>     Root to resolve references against (default: derived
                            from the bitlesson file's git toplevel / .humanize parent)
  --strict                  Exit 2 when any entry has unresolved references
EOF
}

BITLESSON_FILE=""
PROJECT_ROOT=""
STRICT="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bitlesson-file)
            BITLESSON_FILE="${2:-}"
            shift 2
            ;;
        --project-root)
            PROJECT_ROOT="${2:-}"
            shift 2
            ;;
        --strict)
            STRICT="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ -z "$BITLESSON_FILE" ]]; then
    echo "Error: --bitlesson-file is required" >&2
    usage
    exit 1
fi

if [[ ! -f "$BITLESSON_FILE" ]]; then
    echo "Error: BitLesson file not found: $BITLESSON_FILE" >&2
    exit 1
fi

# Derive project root the same way bitlesson-select.sh does.
if [[ -z "$PROJECT_ROOT" ]]; then
    BITLESSON_DIR="$(cd "$(dirname "$BITLESSON_FILE")" && pwd -P)"
    if git -C "$BITLESSON_DIR" rev-parse --show-toplevel >/dev/null 2>&1; then
        PROJECT_ROOT="$(git -C "$BITLESSON_DIR" rev-parse --show-toplevel)"
    elif [[ "$(basename "$BITLESSON_DIR")" == ".humanize" ]]; then
        PROJECT_ROOT="$(cd "$BITLESSON_DIR/.." && pwd -P)"
    else
        PROJECT_ROOT="$BITLESSON_DIR"
    fi
fi

if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "Error: Project root is not a directory: $PROJECT_ROOT" >&2
    exit 1
fi

# Extract candidate file references per lesson block. Emits tab-separated records:
#   META <key> <deprecated:0|1>
#   CAND <type:S|F> <key> <token>
# where S = slash-bearing path (checked verbatim), F = bare filename (found anywhere).
extract_candidates() {
    awk '
        BEGIN {
            EXT = "(py|sh|md|json|js|ts|tsx|jsx|yaml|yml|toml|txt|sql|cfg|ini|c|cc|cpp|h|hpp|go|rs|rb|java)"
            in_fence = 0
        }

        function flush(    i) {
            if (label == "") return
            key = (id != "" ? id : label)
            printf "META\t%s\t%d\n", key, dep
            if (!dep) {
                for (i = 1; i <= nc; i++) {
                    printf "CAND\t%s\t%s\t%s\n", ctype[i], key, ctok[i]
                }
            }
            delete ctok; delete ctype; delete seen
            nc = 0; dep = 0; id = ""; label = ""
        }

        /^```/ { in_fence = !in_fence; next }
        /^~~~/ { in_fence = !in_fence; next }
        in_fence { next }

        /^##[[:space:]]*Lesson:/ {
            flush()
            label = $0
            sub(/^##[[:space:]]*Lesson:[[:space:]]*/, "", label)
            next
        }

        label != "" {
            if ($0 ~ /^Lesson ID:/) {
                id = $0
                sub(/^Lesson ID:[[:space:]]*/, "", id)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", id)
            }
            if (tolower($0) ~ /^status:[[:space:]]*deprecated/) {
                dep = 1
            }

            line = $0
            # split on markdown/punctuation delimiters (backtick, parens, comma,
            # double-quote, angle brackets, semicolon, apostrophe=\047)
            gsub(/[`(),"<>;\047]/, " ", line)
            n = split(line, toks, /[[:space:]]+/)
            for (j = 1; j <= n; j++) {
                t = toks[j]
                sub(/:[0-9]+(-[0-9]+)?$/, "", t)   # strip trailing :line / :line-range
                sub(/[.,:;]+$/, "", t)             # strip trailing punctuation
                sub(/^[.,:;]+/, "", t)             # strip leading punctuation
                if (t == "") continue
                if (index(t, "...") > 0) continue  # ellipsis / abbreviated example path
                if (index(t, "//") > 0) continue   # URL-ish
                if (t ~ /^-/) continue             # flag-like
                if (index(t,"*")||index(t,"?")||index(t,"[")||index(t,"]")|| \
                    index(t,"{")||index(t,"}")||index(t,"=")||index(t,"|")|| \
                    index(t,"$")||index(t,"@")||index(t,"!")) continue
                # require a known file extension at the END only; a known
                # extension followed by "/" means prose joined files (score.py/labeler.py)
                if (t ~ ("\\." EXT "/")) continue
                if (t !~ ("\\." EXT "$")) continue
                if (t !~ /^[A-Za-z0-9._\/-]+$/) continue

                ttype = (index(t, "/") > 0) ? "S" : "F"
                if ((ttype "\t" t) in seen) continue
                seen[ttype "\t" t] = 1
                nc++
                ctype[nc] = ttype
                ctok[nc] = t
            }
        }

        END { flush() }
    ' "$BITLESSON_FILE"
}

declare -A FILE_CACHE

file_exists_somewhere() {
    local name="$1" hit
    if [[ -n "${FILE_CACHE[$name]+x}" ]]; then
        [[ "${FILE_CACHE[$name]}" == "1" ]]
        return $?
    fi
    hit=$(find "$PROJECT_ROOT" -path '*/.git' -prune -o -type f -name "$name" -print 2>/dev/null | head -n1 || true)
    if [[ -n "$hit" ]]; then
        FILE_CACHE[$name]=1
        return 0
    fi
    FILE_CACHE[$name]=0
    return 1
}

TOTAL=0
DEPRECATED=0
STALE_LESSONS=0
CURRENT_KEY=""
CURRENT_UNRESOLVED=""

emit_lesson_report() {
    [[ -n "$CURRENT_KEY" ]] || return 0
    if [[ -n "$CURRENT_UNRESOLVED" ]]; then
        STALE_LESSONS=$((STALE_LESSONS + 1))
        echo "STALE: $CURRENT_KEY"
        # shellcheck disable=SC2001
        echo "$CURRENT_UNRESOLVED" | sed 's/^/  - /'
    fi
    CURRENT_KEY=""
    CURRENT_UNRESOLVED=""
}

while IFS=$'\t' read -r rec a b c; do
    case "$rec" in
        META)
            # a=key, b=deprecated
            emit_lesson_report
            TOTAL=$((TOTAL + 1))
            [[ "$b" == "1" ]] && DEPRECATED=$((DEPRECATED + 1))
            CURRENT_KEY="$a"
            ;;
        CAND)
            # a=type, b=key, c=token
            resolved=0
            if [[ "$a" == "S" ]]; then
                if [[ -e "$PROJECT_ROOT/$c" || -e "$c" ]]; then
                    resolved=1
                fi
            else
                if file_exists_somewhere "$c"; then
                    resolved=1
                fi
            fi
            if [[ "$resolved" -eq 0 ]]; then
                if [[ -n "$CURRENT_UNRESOLVED" ]]; then
                    CURRENT_UNRESOLVED="$CURRENT_UNRESOLVED"$'\n'"$c"
                else
                    CURRENT_UNRESOLVED="$c"
                fi
            fi
            ;;
    esac
done < <(extract_candidates)
emit_lesson_report

echo ""
echo "BitLesson staleness: scanned $TOTAL entr$([[ "$TOTAL" -eq 1 ]] && echo y || echo ies) ($DEPRECATED deprecated, skipped); $STALE_LESSONS with unresolved references."

if [[ "$STRICT" == "true" && "$STALE_LESSONS" -gt 0 ]]; then
    exit 2
fi
exit 0
