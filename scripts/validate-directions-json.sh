#!/usr/bin/env bash
# validate-directions-json.sh
# Validates a directions.json file against the schema version 1 contract.
#
# Usage: validate-directions-json.sh <path/to/file.directions.json>
#
# Exit codes:
#   0 - Validation passed
#   1 - Missing input file argument or file does not exist
#   2 - jq not available
#   3 - Schema validation failed (jq returned false or file is invalid JSON)

set -euo pipefail

usage() {
    echo "Usage: $0 <path/to/file.directions.json>"
    echo ""
    echo "Validates a directions.json file against schema version 1."
    exit 1
}

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
fi

INPUT_FILE="$1"

if [[ ! -f "$INPUT_FILE" ]]; then
    echo "ERROR: File not found: $INPUT_FILE" >&2
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq is required but not installed" >&2
    exit 2
fi

# Full schema validation using a single jq -e expression.
# Returns false (exit 1) if any rule fails.
if jq -e '
  # schema_version must be 1
  .schema_version == 1

  # required top-level keys
  and has("title")
  and has("original_idea")
  and has("synthesis_notes")
  and has("metadata")
  and has("directions")

  # directions array: 1..10 elements
  and ((.directions | type) == "array")
  and ((.directions | length) >= 1)
  and ((.directions | length) <= 10)

  # exactly one primary direction
  and ((.directions | map(select(.is_primary == true)) | length) == 1)

  # unique direction_id values
  and ((.directions | map(.direction_id) | unique | length) == (.directions | length))

  # unique dir_slug values
  and ((.directions | map(.dir_slug) | unique | length) == (.directions | length))

  # dir_slug values must be lowercase alphanumeric + hyphens (branch/path safe)
  and (.directions | map(.dir_slug) | all(. != null and test("^[a-z0-9-]+$")))

  # unique source_index values
  and ((.directions | map(.source_index) | unique | length) == (.directions | length))

  # display_order values must be integers (number type and equal to floor)
  and (.directions | map(.display_order) | all(. != null and (type == "number") and (. == floor)))

  # metadata.n_returned must equal directions.length
  and (.metadata.n_returned == (.directions | length))

  # confidence must be high, medium, or low for each direction
  and (.directions | map(.confidence) | all(. == "high" or . == "medium" or . == "low"))

  # each direction must have all required fields and correct types
  and (.directions | map(
        has("name")
        and has("rationale")
        and has("raw_phase3_response")
        and has("approach_summary")
        and ((.objective_evidence | type) == "array")
        and ((.known_risks | type) == "array")
      ) | all)
' "$INPUT_FILE" > /dev/null 2>&1; then
    echo "VALIDATION_SUCCESS"
    exit 0
else
    echo "VALIDATION_FAILED: $INPUT_FILE does not conform to directions.json schema version 1" >&2
    exit 3
fi
