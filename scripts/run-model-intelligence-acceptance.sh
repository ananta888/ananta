#!/usr/bin/env bash
set -euo pipefail

profile="${1:-core}"
if [[ "$profile" != "core" && "$profile" != "extended" ]]; then
  printf 'usage: %s [core|extended]\n' "$0" >&2
  exit 64
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${MODEL_INTELLIGENCE_GATE_DIR:-$repo_root/artifacts/test-gates/model-intelligence}"
mkdir -p "$artifact_dir"
junit="$(mktemp "${TMPDIR:-/tmp}/ananta-model-intelligence-${profile}-junit.XXXXXX.xml")"
trap 'rm -f "$junit"' EXIT
report="$artifact_dir/${profile}-acceptance.json"

core_tests=(
  tests/test_model_intelligence_contracts.py
  tests/test_model_intelligence_snapshot_admission.py
  tests/test_model_intelligence_static_analyzers.py
  tests/test_model_intelligence_graph_service.py
  tests/worker/test_model_analysis_executor.py
  tests/test_model_analysis_job_service.py
  tests/test_model_intelligence_execution_contracts.py
  tests/services/test_model_intelligence_artifact_store.py
  tests/services/test_model_intelligence_report_service.py
  tests/test_model_intelligence_cli.py
)

extended_tests=(
  tests/test_model_intelligence_trace_capture.py
  tests/worker/test_model_intelligence_lora_delta_analyzer.py
  tests/test_model_intelligence_evaluation_service.py
)

selected=("${core_tests[@]}")
if [[ "$profile" == "extended" ]]; then
  selected+=("${extended_tests[@]}")
fi

(
  cd "$repo_root"
  python -m pytest "${selected[@]}" --junitxml="$junit"
)

tool_digest="$(
  cd "$repo_root"
  {
    sha256sum ananta_contracts/model_intelligence.py
    sha256sum ananta_contracts/model_intelligence_execution.py
    find agent worker schemas/model-intelligence -type f \
      \( -name '*model_intelligence*' -o -path 'worker/model_intelligence/*' -o -path 'schemas/model-intelligence/*' \) \
      -print0 | sort -z | xargs -0 sha256sum
  } | sha256sum | cut -d' ' -f1
)"

evidence_args=()
if [[ -n "${MODEL_INTELLIGENCE_SOURCE_ID:-}" ]]; then
  evidence_args+=(--source-id "$MODEL_INTELLIGENCE_SOURCE_ID")
fi
if [[ -n "${MODEL_INTELLIGENCE_RUN_ID:-}" ]]; then
  evidence_args+=(--run-id "$MODEL_INTELLIGENCE_RUN_ID")
fi
if [[ -n "${MODEL_INTELLIGENCE_CONTAINER_DIGEST:-}" ]]; then
  evidence_args+=(--container-digest "$MODEL_INTELLIGENCE_CONTAINER_DIGEST")
fi
if [[ "${MODEL_INTELLIGENCE_REQUIRE_RELEASE_EVIDENCE:-0}" == "1" ]]; then
  evidence_args+=(--require-release-evidence)
fi

python "$repo_root/scripts/model_intelligence_acceptance_report.py" \
  --profile "$profile" \
  --junit "$junit" \
  --output "$report" \
  --tool-digest "$tool_digest" \
  "${evidence_args[@]}"

printf 'model-intelligence %s gate: %s\n' "$profile" "$report"
