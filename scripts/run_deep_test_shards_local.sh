#!/usr/bin/env bash
# Lokaler Runner fuer die Backend-Deep-Test-Matrix aus
# .github/workflows/backend-deep-tests.yml
# Reihenfolge und Shards sind 1:1 uebernommen.
# Args entsprechen dem Workflow (pytest -vv --durations=25 --timeout=180
# --tb=short -m "not live_compose" -k ... --ignore=...).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-./.venv/bin/python}"
RESULTS_DIR="${RESULTS_DIR:-./test-results}"
mkdir -p "$RESULTS_DIR"
rm -f "$RESULTS_DIR"/test-results-*.xml "$RESULTS_DIR"/test-results-*.placeholder

# -k Ausdruck in einer einzigen Zeile, damit pytest ihn parsen kann.
K_EXPR="not test_task_evolution_apply_endpoint_is_explicitly_policy_gated and not test_task_evolution_validate_and_apply_fail_closed_for_analyze_only_evolver and not test_llm_generate_runtime_falls_back_to_ollama_in_routing_metadata and not test_task_execute_auto_records_llm_benchmark and not test_task_propose_accepts_stderr_json_as_fallback_output and not test_task_propose_extracts_embedded_json_after_traceback_output and not test_task_propose_repairs_invalid_output_with_followup_prompt and not test_task_propose_repairs_invalid_output_after_opencode_failure and not test_task_propose_uses_worker_execution_context_and_allowed_tools and not test_task_propose_passes_temperature_to_cli_and_exposes_routing_field and not test_task_propose_reuses_stateful_cli_session_when_enabled and not test_task_propose_creates_live_terminal_session_metadata_when_enabled and not test_task_propose_creates_interactive_terminal_session_metadata_when_enabled and not test_task_propose_interactive_terminal_retries_timeout_with_compact_context_and_returns_error and not test_health_endpoint_marks_lmstudio_unstable_when_reachable_without_models"

COMMON_ARGS=(
  -vv
  --durations=25
  --timeout=180
  --tb=short
  -m "not live_compose"
  -k "$K_EXPR"
  --ignore-glob="tests/**/fixtures/**"
  --ignore=tests/test_autonomous_flow_e2e.py
  --ignore=tests/test_autonomous_scrum_e2e.py
  --ignore=tests/test_autopilot_integration.py
  --ignore=tests/test_deerflow_integration.py
  --ignore=tests/test_e2e_auth.py
  --ignore=tests/test_evolver_live_compose.py
  --ignore=tests/test_hybrid_orchestrator_load.py
  --ignore=tests/test_tasks_opencode_e2e.py
  --ignore=tests/test_triggers_e2e.py
  --ignore=tests/test_worker_client_adapter.py
)

# Shards 1:1 aus backend-deep-tests.yml Matrix.
# Pfade mit Wildcards werden hier bewusst als Bash-Globbing gelassen,
# so dass sie vor pytest zur echten Dateiliste expandiert werden.
declare -a SHARDS=(
  "operator-tui-core|tests/operator_tui/test_chat_transport_share_session.py tests/operator_tui/test_device_keys.py tests/operator_tui/test_oidc_device_flow.py tests/operator_tui/test_semantic_overlay.py tests/operator_tui/test_share_*.py tests/operator_tui/test_shared_viewer.py tests/operator_tui/test_tui_delta_hashing.py"
  "operator-tui-chat-control|tests/operator_tui/chat_control/test_*.py"
  "operator-tui-chat-memory|tests/operator_tui/chat_memory/test_*.py"
  "operator-tui-config-scroll|tests/operator_tui/config/test_*.py tests/operator_tui/scroll/test_*.py"
  "operator-tui-visual|tests/operator_tui/visual/markdown/test_*.py tests/operator_tui/visual/test_*.py"
  "client-core-ai|tests/client_surfaces/operator_tui/test_ai_snake_*.py"
  "client-core-ui|tests/client_surfaces/operator_tui/test_artifact_*.py tests/client_surfaces/operator_tui/test_chat_state_prediction_comments.py tests/client_surfaces/operator_tui/test_center_browser_commands.py tests/client_surfaces/operator_tui/test_doc_open_command.py tests/client_surfaces/operator_tui/test_external_window_*.py tests/client_surfaces/operator_tui/test_mouse_*.py tests/client_surfaces/operator_tui/test_region_hit_map.py tests/client_surfaces/operator_tui/test_terminal.py"
  "client-auth|tests/client_surfaces/operator_tui/auth/test_*.py"
  "client-realtime|tests/client_surfaces/operator_tui/realtime/test_*.py"
  "client-visual|tests/client_surfaces/operator_tui/visual/browser/test_*.py tests/client_surfaces/operator_tui/visual/test_*.py"
  "heuristic-core|tests/heuristic_runtime/test_heuristic_*.py tests/heuristic_runtime/test_shadow_experiment_runner.py tests/heuristic_runtime/test_no_llm_in_fast_path.py tests/heuristic_runtime/test_auto_experimental_leases.py tests/heuristic_runtime/test_heuristic_registry_experimental_live.py"
  "heuristic-dsl|tests/heuristic_runtime/test_dsl_*.py tests/heuristic_runtime/test_decision_*.py tests/heuristic_runtime/test_motion_planner.py tests/heuristic_runtime/test_tui_observation_buffer.py tests/heuristic_runtime/test_llm_heuristic_parser.py"
  "llm-routing|tests/llm_interceptor/test_model_profiles.py tests/llm_interceptor/test_policy_profiles.py tests/llm_interceptor/test_provider_router.py tests/llm_interceptor/test_provider_routing_rules.py tests/llm_interceptor/test_prompt_adapter.py tests/llm_interceptor/test_context_gate.py tests/llm_interceptor/test_opencode_compat.py"
  "llm-streaming|tests/llm_interceptor/test_streaming.py tests/llm_interceptor/test_streaming_validation.py tests/llm_interceptor/test_request_envelope.py tests/llm_interceptor/test_response_validator.py tests/llm_interceptor/test_fail_closed.py tests/llm_interceptor/test_e2e_fake_upstream.py"
  "llm-security|tests/llm_interceptor/test_secret_redactor.py tests/llm_interceptor/test_audit_logger.py tests/llm_interceptor/test_config_schema.py tests/llm_interceptor/test_security_regression_corpus.py tests/llm_interceptor/test_cli_startup.py tests/llm_interceptor/test_policy_engine.py tests/llm_interceptor/test_repair_controller.py"
  "e2e-autopilot-new-project-api|tests/e2e/test_autopilot_new_project_api_smoke.py"
  "e2e-autopilot-new-project-llm|tests/e2e/test_autopilot_new_project_llm_first.py"
  "e2e-autopilot-new-project-fibonacci|tests/e2e/test_autopilot_new_project_fibonacci.py"
  "e2e-autopilot-new-project-fibonacci-flow|tests/e2e/test_autopilot_new_project_fibonacci_full_flow.py"
  "e2e-autopilot-flows|tests/e2e/test_autopilot_full_flow_failure_modes.py tests/e2e/test_autopilot_taskscoped_persist_execute_and_verified_completion.py tests/e2e/test_autopilot_verification_service_full_flow.py"
  "e2e-planning-learning|tests/e2e/test_planning_learning_loop.py tests/e2e/test_parallel_scenario_goal_scoped_config.py tests/e2e/test_llm_assisted_project_creation.py"
  "e2e-tui|tests/e2e/test_tui_*.py tests/e2e/test_shared_tui_view.py tests/e2e/test_cli_golden_path_snapshots.py tests/e2e/test_tui_scripted_smoke.py tests/e2e/test_web_ui_screenshots.py tests/e2e/test_optional_video_capture.py"
  "e2e-worker|tests/e2e/worker/test_*.py tests/e2e/test_command_chain_execution_full_flow.py tests/e2e/worker/test_controlled_loop_flow.py tests/e2e/worker/test_standalone_worker_flow.py tests/e2e/test_parallel_worker_ollama_saturation.py"
  "e2e-rag-runtime|tests/e2e/test_browser_use_mock_e2e.py tests/e2e/test_wiki_rag_fixture_flow.py tests/e2e/test_rag_dogfood_tiny_repo.py tests/e2e/test_bitcoin_mining_citation_evidence.py tests/e2e/test_freecad_runtime_golden_path.py tests/e2e/test_blender_runtime_golden_path.py tests/e2e/test_core_golden_path.py tests/e2e/test_e2e_report_generation.py tests/e2e/test_experimental_live_rollback.py tests/e2e/test_workspace_git_sync_flow.py tests/e2e/test_ai_timeout_fallback.py"
  "worker-planning|tests/worker/test_planner_*.py tests/worker/test_command_planner.py tests/worker/test_patch_planner.py tests/worker/test_retrieval_indexing.py tests/worker/test_retrieval_ranking.py tests/worker/test_rag_file_selection.py tests/worker/test_chunking_strategy.py tests/worker/test_planner_step_graph.py tests/worker/test_planner_state_machine.py tests/worker/test_planner_replan.py tests/worker/test_planner_step_scheduler.py tests/worker/test_planner_checkpoint_resume.py tests/worker/test_planner_observability.py"
  "worker-adapters|tests/worker/test_*_adapter_contract.py tests/worker/test_experimental_coding_adapters.py tests/worker/test_worker_model_provider.py tests/worker/test_native_patch_proposer.py tests/worker/test_shellgpt_adapter_contract.py tests/worker/test_aider_adapter_contract.py tests/worker/test_worker_contract_schemas.py"
  "worker-gates|tests/worker/test_command_execution_approval.py tests/worker/test_patch_apply_approval.py tests/worker/test_shell_command_policy.py tests/worker/test_worker_verification.py tests/worker/test_worker_redaction.py"
  "worker-runtime|tests/worker/test_worker_degradation.py tests/worker/test_worktree_sandbox.py tests/worker/test_coding_prompt_assembly.py tests/worker/test_worker_trace_metadata.py tests/worker/test_patch_failure_handling.py tests/worker/test_semantic_output_correction.py tests/worker/test_no_llm_coding_fallback.py tests/worker/test_controlled_worker_loop.py tests/worker/test_standalone_runtime.py"
  "smoke-cli|tests/smoke/test_*.py tests/cli/test_*.py"
  "benchmarks|tests/benchmarks/codecompass_worker/test_*.py tests/benchmarks/retrieval/test_*.py tests/benchmarks/wiki_rag/test_*.py"
)

SUMMARY="$RESULTS_DIR/deep-shards-summary.tsv"
: > "$SUMMARY"
printf 'shard\texit_code\tduration_sec\ttests_run\n' >> "$SUMMARY"

FAIL=0
for entry in "${SHARDS[@]}"; do
  name="${entry%%|*}"
  paths="${entry#*|}"

  # Pfade mit Wildcards werden via Bash-Globbing expandiert.
  shopt -s nullglob
  IFS=' ' read -r -a path_arr <<< "$paths"
  expanded=()
  for p in "${path_arr[@]}"; do
    # Falls Wildcard drin ist, expandieren
    if [[ "$p" == *"*"* ]]; then
      # Wir nutzen eval, weil die Shell-Globs in $paths literal stehen
      eval "exp=($p)"
      for e in "${exp[@]}"; do
        expanded+=("$e")
      done
    else
      expanded+=("$p")
    fi
  done
  shopt -u nullglob

  if [[ ${#expanded[@]} -eq 0 ]]; then
    echo "  >>> shard $name: KEINE TESTS GEFUNDEN (paths=$paths)" | tee -a "$SUMMARY"
    printf '%s\t%s\t%s\t%s\n' "$name" "NOFILES" "0" "0" >> "$SUMMARY"
    FAIL=$(( FAIL + 1 ))
    continue
  fi

  junit="$RESULTS_DIR/test-results-$name.xml"

  echo
  echo "============================================================"
  echo "  shard: $name"
  echo "  files: ${#expanded[@]}"
  echo "============================================================"
  start=$(date +%s)
  set +e
  "$PY" -m pytest \
    "${expanded[@]}" \
    "${COMMON_ARGS[@]}" \
    --junitxml="$junit" 2>&1 | tail -40
  rc=${PIPESTATUS[0]}
  set -e
  end=$(date +%s)
  dur=$(( end - start ))

  # Anzahl Tests aus XML holen
  tests_run=0
  if [[ -f "$junit" ]]; then
    tests_run=$(grep -oP 'tests="\K[0-9]+' "$junit" | head -1 || echo 0)
  fi

  printf '%s\t%s\t%s\t%s\n' "$name" "$rc" "$dur" "$tests_run" >> "$SUMMARY"
  if [[ $rc -ne 0 ]]; then
    FAIL=$(( FAIL + 1 ))
    echo "  >>> shard FAILED exit=$rc duration=${dur}s tests=${tests_run}"
  else
    echo "  >>> shard PASSED duration=${dur}s tests=${tests_run}"
  fi
done

echo
echo "============================================================"
echo "  Deep-Test Shards Summary"
echo "============================================================"
cat "$SUMMARY"
echo
if [[ $FAIL -gt 0 ]]; then
  echo "$FAIL shard(s) failed."
  exit 1
fi
echo "All shards passed."