# Umgebungsvariablen — generierte Referenz (CLN-065)

GENERIERT via `python scripts/generate_env_reference.py`; nicht handpflegen.
Quellen: `os.getenv`/`os.environ`-Sweeps + `validation_alias`-Deklarationen.

| Variable | Quelldateien |
|---|---|
| `ACCEPTANCE_WORKERS_JSON` | `scripts/first_goal_acceptance_runner.py` |
| `ADM0_A3` | `scripts/build_geomap_assets.py` |
| `AGENTS_PATH` | `agent/routes/system.py`, `agent/services/agent_health_monitor_service.py` |
| `AGENT_CONFIG` | `agent/ai_agent.py`, `agent/bootstrap/harness_evolution.py`, `agent/bootstrap/pi_dispatch.py` (+134) |
| `AGENT_EXTENSIONS` | `agent/config.py` |
| `AGENT_NAME` | `agent/auth.py`, `agent/config.py`, `agent/llm_strategies/standard.py` (+17) |
| `AGENT_OFFLINE_TIMEOUT` | `agent/config.py`, `agent/services/workflow_runtime_health_service.py` |
| `AGENT_PLUGINS` | `agent/config.py` |
| `AGENT_PLUGIN_DIRS` | `agent/config.py` |
| `AGENT_ROLE` | `agent/services/memory_runtime.py` |
| `AGENT_TOKEN` | `agent/auth_token_files.py`, `agent/config.py`, `agent/services/autopilot_support_service.py` |
| `AGENT_TOKEN_FILE` | `agent/ai_agent.py`, `agent/auth_token_files.py`, `agent/config.py` (+4) |
| `AGENT_TOKEN_PERSISTENCE` | `agent/config.py` |
| `AGENT_URL` | `agent/config.py`, `agent/services/task_execution_tracking_service.py`, `agent/services/terminal_target_service.py` (+7) |
| `AIDER_DEFAULT_MODEL` | `agent/config.py` |
| `AIDER_PATH` | `agent/config.py` |
| `AI_AGENT_LOG_JSON` | `agent/config.py` |
| `ANANTA_AGENT_CONTAINERS` | `scripts/run_live_click_dual_benchmark.py` |
| `ANANTA_AGENT_SAFETY_MANAGED_SANDBOXES` | `agent/config.py` |
| `ANANTA_AGENT_SAFETY_RETENTION_INTERVAL_SECONDS` | `agent/services/background/agent_safety_retention_reconciler.py` |
| `ANANTA_AGENT_SAFETY_RUNTIME_ADAPTER` | `agent/config.py` |
| `ANANTA_AGENT_SAFETY_SNAPSHOT_ROOT` | `agent/config.py` |
| `ANANTA_AGENT_SAFETY_STATE` | `agent/bootstrap/agent_safety.py`, `agent/config.py` |
| `ANANTA_AI_SNAKE_PROFILE_ROUTING` | `agent/routes/snakes_worker_routing.py` |
| `ANANTA_ALPHA_CONTAINER` | `scripts/test_env_cleanup.py` |
| `ANANTA_ANGULAR_URL` | `client_surfaces/operator_tui/windowing/external_window_controller.py` |
| `ANANTA_API_TOKEN` | `agent/cli/model_intelligence.py` |
| `ANANTA_AUTH_TOKEN` | `agent/cli/api_client.py`, `client_surfaces/common/profile_auth.py`, `client_surfaces/operator_tui/_interactive_template.py` (+11) |
| `ANANTA_BASE_URL` | `agent/cli/api_client.py`, `agent/cli/commands/config.py`, `agent/cli/commands/goal.py` (+17) |
| `ANANTA_BETA_CONTAINER` | `scripts/test_env_cleanup.py` |
| `ANANTA_BLENDER_ALLOW_INSECURE_HTTP` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_ENDPOINT` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_MAX_CONTEXT_OBJECTS` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_MAX_PAYLOAD_BYTES` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_PROFILE` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_TIMEOUT_SECONDS` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_TOKEN` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_TOKEN_REF` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLENDER_TRANSPORT` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_BLOCKED_PATH_FRAGMENTS` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_BPMN_EXECUTION_ENABLED` | `agent/services/workflow_runtime_selection_composition.py` |
| `ANANTA_CHILD_SOURCE_SHA256` | `scripts/run_sfu_broadcast_release_gate.py` |
| `ANANTA_CLI_BACKENDS_DIR` | `agent/cli_backends/provisioning.py` |
| `ANANTA_CODECOMPASS_DISABLED_FORMATS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_ENABLED_FORMATS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_FILE_TYPE_PRIORITIES` | `agent/config_codecompass_settings.py`, `scripts/setup_codecompass_index.py` |
| `ANANTA_CODECOMPASS_INDEX_WAIT_SECONDS` | `scripts/setup_codecompass_index.py` |
| `ANANTA_CODECOMPASS_LAYER_PROFILE` | `scripts/codecompass_layer_sync.py` |
| `ANANTA_CODECOMPASS_MAX_CSV_COLUMNS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_CSV_ROWS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_FILE_BYTES` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_LINES` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_NOTEBOOK_CELLS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_NOTEBOOK_CELL_CHARS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_NOTEBOOK_OUTPUT_BYTES` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_OUTPUT_RECORDS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_XML_DEPTH` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_XML_NODES` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_MAX_YAML_ALIASES` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_OUTPUT_DIR` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_CODECOMPASS_PARSER_TIMEOUT_MS` | `agent/config_codecompass_settings.py` |
| `ANANTA_CODECOMPASS_RANKING_OVERRIDE_JSON` | `agent/services/codecompass_universal_ranking_profile_service.py` |
| `ANANTA_CODECOMPASS_REPOSITORY_RANKER` | `agent/services/codecompass_universal_ranking_profile_service.py` |
| `ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED` | `agent/services/_instruction_layer_compiler_helpers.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ADAPTERS` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_LANGUAGES` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_MAX_GRAPH_RECORDS` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_OUTPUT_DIR` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_TARGETS` | `agent/codecompass/semantic_translation/config.py` |
| `ANANTA_CODECOMPASS_SYMBOL_PATHS` | `worker/retrieval/codecompass_channel_providers.py` |
| `ANANTA_CODECOMPASS_VECTOR_HUB_QDRANT_READ_ENABLED` | `agent/services/codecompass_vector_runtime_service.py` |
| `ANANTA_CODECOMPASS_VECTOR_PROFILE_NAME` | `agent/services/codecompass_vector_runtime_service.py` |
| `ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID` | `agent/services/codecompass_vector_runtime_service.py` |
| `ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID` | `agent/services/codecompass_vector_runtime_service.py` |
| `ANANTA_CODECOMPASS_X86_BINARY_METADATA` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_CFG` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_DEFAULT_PROFILE` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_DISASSEMBLER_EXPORT` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_ENABLED` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_EXPERIMENTAL_ADAPTER` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_MAX_BASIC_BLOCKS` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_MAX_FUNCTIONS` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_MAX_INSTRUCTIONS` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_MAX_STRINGS` | `agent/codecompass/x86/config.py` |
| `ANANTA_CODECOMPASS_X86_RAW_ASSEMBLY` | `agent/codecompass/x86/config.py` |
| `ANANTA_COLIBRI_DIR` | `scripts/colibri-ananta-gateway.py` |
| `ANANTA_COLLABORATION_AUTO_APPROVED_TOOLS` | `agent/bootstrap/collaboration_workspace.py` |
| `ANANTA_COLLABORATION_COMMAND_POLICY_REVISION` | `agent/bootstrap/collaboration_workspace.py` |
| `ANANTA_COLLABORATION_WORKSPACE_ENABLED` | `agent/bootstrap/collaboration_workspace.py`, `agent/config.py` |
| `ANANTA_COLLABORATION_WORKSPACE_STATE` | `agent/bootstrap/collaboration_workspace.py`, `agent/config.py` |
| `ANANTA_CONTEXT_PROFILE` | `agent/config.py` |
| `ANANTA_CONTEXT_TOKENS` | `agent/config.py` |
| `ANANTA_CPU_FALLBACK` | `worker/meet_media/lease_guard.py`, `worker/meet_media/piper_speech.py`, `worker/meet_media/snake_avatar.py` (+1) |
| `ANANTA_DENDRITIC_MEMORY_ARTIFACT_ROOT` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_AUTOMATIC_ACTIVATION_ENABLED` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_ENABLED` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_MODE` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_POLICY_PATH` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_RUNTIME_ENABLED` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DENDRITIC_MEMORY_STATE` | `agent/bootstrap/dendritic_memory.py`, `agent/config.py` |
| `ANANTA_DEVICE_FINGERPRINT` | `agent/cli/commands/share.py` |
| `ANANTA_DEVICE_KEY_DIR` | `client_surfaces/operator_tui/device_keys.py` |
| `ANANTA_DISABLE_BACKGROUND_THREADS` | `agent/lifecycle.py`, `agent/routes/snakes_chat_reply_spawner.py`, `agent/services/autopilot/auto_planner_engine.py` (+2) |
| `ANANTA_DISABLE_HTTP_AUDIT` | `agent/bootstrap/audit_middleware.py` |
| `ANANTA_DOCKER_CLEAN_PATH` | `scripts/build_eclipse_runtime_plugin.py`, `scripts/build_eclipse_update_site.py`, `scripts/release_gate.py` (+1) |
| `ANANTA_DOCKER_OPS_BOUNDARY` | `agent/services/docker_engine_service.py` |
| `ANANTA_DOCKER_OPS_ENV_FILE` | `agent/services/ops_registry_service.py` |
| `ANANTA_DOCKER_OPS_MANAGED_CONTAINERS` | `agent/services/ops_registry_service.py` |
| `ANANTA_DSPY_OPTIMIZATION_ARTIFACT_ROOT` | `agent/bootstrap/dspy_optimization.py`, `agent/config.py` |
| `ANANTA_DSPY_OPTIMIZATION_ENABLED` | `agent/bootstrap/dspy_optimization.py`, `agent/config.py` |
| `ANANTA_DSPY_OPTIMIZATION_MODE` | `agent/bootstrap/dspy_optimization.py`, `agent/config.py` |
| `ANANTA_DSPY_OPTIMIZATION_POLICY_PATH` | `agent/bootstrap/dspy_optimization.py`, `agent/config.py` |
| `ANANTA_DSPY_OPTIMIZATION_STATE` | `agent/bootstrap/dspy_optimization.py`, `agent/config.py` |
| `ANANTA_ENABLE_LLAMAINDEX_EMBEDDINGS` | `agent/semantic_search_engine.py` |
| `ANANTA_FORCE_KITTY` | `client_surfaces/operator_tui/visual/capabilities/terminal_detector.py`, `scripts/operator_tui_terminal_graphics_smoke.py` |
| `ANANTA_FORCE_SIXEL` | `client_surfaces/operator_tui/visual/capabilities/terminal_detector.py`, `scripts/operator_tui_terminal_graphics_smoke.py` |
| `ANANTA_FRONTEND_DIST_DIR` | `client_surfaces/operator_tui/windowing/external_window_controller.py` |
| `ANANTA_FRONTEND_URL` | `scripts/firefox_automation/_config.py` |
| `ANANTA_GAME_DRAGON_CONFIG` | `agent/routes/game_dragon.py` |
| `ANANTA_GIT_REMOTE_ALLOWED_HOSTS` | `agent/services/git_remote_policy_service.py` |
| `ANANTA_GRYPE_BIN` | `scripts/generate_semantic_media_supply_chain_reports.py` |
| `ANANTA_HRM_CUDA_VERSION` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_ENGINE_VERSION` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_EXPERIMENT_WORKER_ENABLED` | `agent/ai_agent.py` |
| `ANANTA_HRM_FLASH_ATTENTION_VERSION` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_GPU_DEVICE_IDS` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_HUB_TOKEN_FILE` | `worker/hrm_experiments/task_handler.py` |
| `ANANTA_HRM_HUB_URL` | `worker/hrm_experiments/task_handler.py` |
| `ANANTA_HRM_ISOLATION_ATTESTED` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_ISOLATION_PROFILE_VERSION` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_RUNNER_SOCKET` | `worker/hrm_experiments/healthcheck.py`, `worker/hrm_experiments/runner_server.py`, `worker/hrm_experiments/task_handler.py` |
| `ANANTA_HRM_RUNTIME_IMAGE_DIGEST` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_TORCH_VERSION` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HRM_VRAM_BYTES` | `worker/hrm_experiments/runner.py` |
| `ANANTA_HUB_CONTAINER` | `scripts/firefox_automation/_config.py`, `scripts/run_live_click_dual_benchmark.py`, `scripts/test_env_cleanup.py` |
| `ANANTA_HUB_ENDPOINT` | `client_surfaces/blender/addon/settings.py` |
| `ANANTA_HUB_EVIDENCE_ASSIGNMENT` | `scripts/benchmark/ornith_benchmark_support.py` |
| `ANANTA_HUB_TOKEN` | `client_surfaces/blender/addon/settings.py`, `scripts/codecompass_layer_sync.py` |
| `ANANTA_HUB_TOKEN_FILE` | `scripts/ananta-self-develop.py` |
| `ANANTA_HUB_URL` | `scripts/codecompass_layer_sync.py`, `scripts/setup_codecompass_index.py` |
| `ANANTA_INSTALL_DIR` | `agent/cli/update.py` |
| `ANANTA_KANBAN_API_ENABLED` | `agent/services/kanban_feature_flags.py` |
| `ANANTA_KANBAN_EVENT_BUFFER_SIZE` | `agent/services/kanban_event_stream_service.py` |
| `ANANTA_KANBAN_WRITE_ENABLED` | `agent/services/kanban_feature_flags.py` |
| `ANANTA_KAT_ENDPOINT` | `agent/services/local_model_runtime_composition.py`, `scripts/local-model-runtime-soak.py` |
| `ANANTA_KNOWLEDGE_EXPERTS_ENABLED` | `agent/config.py` |
| `ANANTA_KNOWLEDGE_EXPERTS_POLICY_PROFILE` | `agent/config.py` |
| `ANANTA_KNOWLEDGE_EXPERTS_ROLLOUT` | `agent/config.py` |
| `ANANTA_KNOWLEDGE_EXPERTS_ROLLOUT_STATE` | `agent/bootstrap/knowledge_expert_rollout.py`, `agent/config.py` |
| `ANANTA_LANGGRAPH_HUB_TOKEN_FILE` | `worker/adapters/langgraph_checkpoint_adapter.py`, `worker/runtime/native_graph/artifact_http.py`, `worker/runtime/workflow_hub_gateway.py` |
| `ANANTA_LANGGRAPH_HUB_URL` | `worker/adapters/langgraph_checkpoint_adapter.py`, `worker/runtime/native_graph/artifact_http.py`, `worker/runtime/workflow_hub_gateway.py` |
| `ANANTA_LANGGRAPH_WORKER_HEALTH_URL` | `agent/services/workflow_runtime_health_service.py` |
| `ANANTA_LFM_ENDPOINT` | `agent/services/local_model_runtime_composition.py`, `scripts/local-model-runtime-soak.py` |
| `ANANTA_LOCAL_MODEL_API_KEY` | `agent/services/local_model_runtime_status_service.py`, `scripts/local-model-runtime-soak.py` |
| `ANANTA_LOCAL_MODEL_CONTROL_BIND_HOST` | `scripts/local-model-runtime-control.py` |
| `ANANTA_LOCAL_MODEL_CONTROL_PORT` | `scripts/local-model-runtime-control.py` |
| `ANANTA_LOCAL_MODEL_CONTROL_TOKEN` | `agent/services/local_model_runtime_composition.py`, `scripts/local-model-runtime-control.py` |
| `ANANTA_LOCAL_MODEL_CONTROL_URL` | `agent/services/local_model_runtime_composition.py` |
| `ANANTA_LOCAL_MODEL_RUNTIME_ENABLED` | `agent/bootstrap/local_model_runtime.py` |
| `ANANTA_LOCAL_MODEL_STATE_DB` | `agent/services/local_model_runtime_composition.py` |
| `ANANTA_LOCAL_MODEL_STATE_DIR` | `scripts/local-model-runtime-control.py`, `scripts/local-model-runtime-soak.py` |
| `ANANTA_LOCAL_RUNTIME_CAPABILITY_CACHE` | `agent/services/local_runtime_capability_composition.py` |
| `ANANTA_LOCAL_RUNTIME_WORKER_CACHE` | `worker/local_runtime_capability_handler.py` |
| `ANANTA_LORA_INFERENCE_ALLOWED_ENDPOINTS` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_lora_inference_worker_port.py` |
| `ANANTA_LORA_INFERENCE_TIMEOUT_SECONDS` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_lora_inference_worker_port.py` |
| `ANANTA_LORA_INFERENCE_TOKEN` | `agent/services/ml_intern_lora_inference_worker_port.py` |
| `ANANTA_LORA_INFERENCE_TOKEN_FILE` | `agent/services/ml_intern_lora_inference_worker_port.py` |
| `ANANTA_LORA_INFERENCE_WORKER_URL` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_lora_inference_worker_port.py` |
| `ANANTA_LORA_TRAINING_ALLOWED_ENDPOINTS` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_lora_inference_worker_port.py`, `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_ARTIFACT_ROOT` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/unsloth_worker_result_service.py` |
| `ANANTA_LORA_TRAINING_DATASET_ROOT` | `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_DEFAULT_BACKEND` | `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_ENABLED` | `agent/routes/ml_intern_training_route_support.py` |
| `ANANTA_LORA_TRAINING_GPU_PROFILE` | `worker/training/runtime.py`, `worker/training/vram_admission.py` |
| `ANANTA_LORA_TRAINING_HOST` | `worker/runtime/lora_training_app.py` |
| `ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON` | `agent/routes/ml_intern_training_route_support.py`, `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_MODEL_ROOT` | `worker/runtime/lora_training_app.py` |
| `ANANTA_LORA_TRAINING_RESOURCE_PROFILE` | `agent/services/ml_intern_training_worker_port.py`, `worker/runtime/lora_training_app.py`, `worker/training/vram_admission.py` |
| `ANANTA_LORA_TRAINING_SMOKE_MODEL_DIR` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_LORA_TRAINING_SMOKE_PROFILE` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_LORA_TRAINING_TOKEN` | `agent/services/ml_intern_lora_inference_worker_port.py`, `agent/services/ml_intern_training_worker_support.py`, `worker/runtime/lora_training_app.py` |
| `ANANTA_LORA_TRAINING_TOKEN_FILE` | `agent/services/ml_intern_lora_inference_worker_port.py`, `agent/services/ml_intern_training_worker_support.py` |
| `ANANTA_LORA_TRAINING_WORKER_BACKENDS` | `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_WORKER_URL` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_lora_inference_worker_port.py`, `agent/services/ml_intern_training_worker_port.py` |
| `ANANTA_LORA_TRAINING_WORKSPACE_ROOT` | `agent/services/ml_intern_lora_inference_service.py`, `agent/services/ml_intern_training_worker_port.py`, `worker/runtime/lora_training_app.py` |
| `ANANTA_LORA_WORKER_IMAGE_SHA256` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_MAIL_CIRCUIT_STATE_PATH` | `agent/services/mail_runtime_policy.py` |
| `ANANTA_MAIL_DATA_ROOT` | `worker/mail_task_composition.py` |
| `ANANTA_MAIL_HUB_TOKEN` | `worker/mail_task_composition.py` |
| `ANANTA_MAIL_HUB_URL` | `worker/mail_task_composition.py` |
| `ANANTA_MAIL_POLLING_ENABLED` | `agent/services/mail_polling_scheduler.py` |
| `ANANTA_MAIL_POLLING_POLICY_REF` | `agent/services/mail_polling_scheduler.py` |
| `ANANTA_MAIL_POLLING_WORKSPACE_ID` | `agent/services/mail_polling_scheduler.py` |
| `ANANTA_MAIL_ROLLOUT_PHASE` | `agent/services/mail_runtime_policy.py` |
| `ANANTA_MASTER_LLM_API_KEY` | `agent/services/model_master_default_service.py` |
| `ANANTA_MASTER_LLM_BASE_URL` | `agent/services/model_master_default_service.py` |
| `ANANTA_MASTER_LLM_MODEL` | `agent/services/model_master_default_service.py` |
| `ANANTA_MASTER_LLM_PROVIDER` | `agent/services/model_master_default_service.py` |
| `ANANTA_MEET_BROWSER_PUBLIC_POLICIES` | `agent/bootstrap/meet_browser.py` |
| `ANANTA_MEET_COMPANION_MCP_TOOLS` | `agent/services/meet_companion_tool_contract.py` |
| `ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED` | `agent/bootstrap/meet_companion.py` |
| `ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED` | `agent/bootstrap/meet_companion.py` |
| `ANANTA_MEET_COMPANION_TASK_TOOLS_ENABLED` | `agent/bootstrap/meet_companion.py` |
| `ANANTA_MEET_DIALOG_CAPACITY` | `agent/bootstrap/meet_dialog_capacity.py` |
| `ANANTA_MEET_DIALOG_CAPACITY_POOL` | `agent/bootstrap/meet_dialog_capacity.py` |
| `ANANTA_MEET_DIALOG_ENABLED` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_DIALOG_POLICIES` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED` | `agent/bootstrap/meet_preauthorizations.py` |
| `ANANTA_MEET_DIALOG_RECONNECT` | `agent/bootstrap/meet_recovery.py` |
| `ANANTA_MEET_DIALOG_WORKER_URLS` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_ENABLED` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MACHINE_ENABLED` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MACHINE_ISSUER` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MACHINE_KEY_FILE` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MACHINE_KEY_ID` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MEDIA_ALLOWED_SCOPES` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MEDIA_CAPACITY_POOL` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MEDIA_ENABLED` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MEDIA_KEY_FILE` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_MEDIA_TIMING` | `agent/bootstrap/meet_media_timing.py` |
| `ANANTA_MEET_MEDIA_WORKER_URL` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_ORIGIN` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_PUBLIC_ROOM` | `agent/services/meet_public_room_service.py` |
| `ANANTA_MEET_SPEAKER_FLOOR` | `agent/bootstrap/meet_speaker.py` |
| `ANANTA_MEET_SPEAKER_POLICIES` | `agent/bootstrap/meet_speaker.py` |
| `ANANTA_MEET_SPEECH_MAX_SECONDS` | `agent/bootstrap/meet.py` |
| `ANANTA_MEET_TASK_SCRATCH_DIR` | `worker/meet_media/companion_task_model_selection.py`, `worker/meet_media/companion_task_runtime.py` |
| `ANANTA_ML_TRAINING_RECONCILE_INTERVAL_SECONDS` | `agent/services/background/ml_intern_training_reconciler.py` |
| `ANANTA_MODEL_ROUTING_PATH` | `agent/services/model_invocation_profile_resolver_loader.py`, `agent/services/workflow_provider_selection_service.py` |
| `ANANTA_NATIVE_RESULT_RECOVERY_ENABLED` | `agent/services/native_result_recovery_tick.py` |
| `ANANTA_NATIVE_RESULT_STORE_PATH` | `worker/runtime/native_graph/result_maintenance.py`, `worker/runtime/native_graph/result_store_runtime.py`, `worker/shell/filesystem_isolation.py` |
| `ANANTA_NATIVE_UNCERTAIN_RETRY_POLICY` | `agent/services/native_uncertain_retry_policy.py` |
| `ANANTA_NATIVE_WORKER_HEALTH_URL` | `agent/services/workflow_runtime_health_service.py` |
| `ANANTA_NEEDLE_BIND_HOST` | `scripts/needle-candidate-sidecar.py` |
| `ANANTA_NEEDLE_ENDPOINT` | `agent/services/local_model_runtime_composition.py`, `scripts/local-model-runtime-soak.py` |
| `ANANTA_NEEDLE_PORT` | `scripts/needle-candidate-sidecar.py` |
| `ANANTA_NEEDLE_TOKEN` | `agent/services/local_model_runtime_status_service.py`, `scripts/local-model-runtime-soak.py`, `scripts/needle-candidate-sidecar.py` |
| `ANANTA_NEEDLE_TRAINING_BIN` | `worker/training/backends/needle.py` |
| `ANANTA_NEEDLE_TRAINING_CPU_SET` | `worker/training/backends/needle.py` |
| `ANANTA_NEEDLE_WEIGHTS` | `scripts/needle-candidate-sidecar.py` |
| `ANANTA_NO_BANNER` | `agent/cli/banner.py` |
| `ANANTA_NPM_COMMAND` | `scripts/release_gate.py` |
| `ANANTA_NVIM_FIXTURE` | `client_surfaces/nvim_runtime/ananta_bridge.py` |
| `ANANTA_OBSIDIAN_RANKING_FACTOR` | `agent/config_rag_settings.py` |
| `ANANTA_OBSIDIAN_VAULTS` | `agent/config_rag_settings.py` |
| `ANANTA_OIDC_CLIENT_ID` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_OIDC_ISSUER` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_OLLAMA_CONTAINER` | `scripts/test_env_cleanup.py` |
| `ANANTA_OLLAMA_MAX_PARALLEL` | `agent/config_defaults_env.py` |
| `ANANTA_OPENCODE_EXECUTION_MODE` | `agent/config_defaults.py`, `agent/config_defaults_env.py` |
| `ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE` | `agent/config_defaults.py`, `agent/config_defaults_env.py` |
| `ANANTA_OPENCODE_TARGET_PROVIDER` | `agent/config_defaults_env.py` |
| `ANANTA_OPERATOR_TUI_ENABLED` | `client_surfaces/operator_tui/rollout.py` |
| `ANANTA_OPERATOR_TUI_STAGE` | `client_surfaces/operator_tui/rollout.py` |
| `ANANTA_ORCHESTRATION_BACKEND` | `agent/services/workflow_backend_factory.py` |
| `ANANTA_ORGANIZATION_ADMIN_GRANT` | `client_surfaces/operator_tui/commands_organizations.py` |
| `ANANTA_PARALLEL_DECISION_URL` | `scripts/decision_provider_benchmark.py` |
| `ANANTA_PASSWORD` | `agent/cli/api_client.py`, `agent/cli_goals_support.py`, `agent/services/three_worker_track_executor.py` (+9) |
| `ANANTA_PATTERN_CATALOG_PATH` | `agent/services/pattern_service.py` |
| `ANANTA_PATTERN_RUNTIME_OVERLAY` | `agent/services/pattern_registry.py` |
| `ANANTA_PATTERN_SCHEMA_PATH` | `agent/services/pattern_service.py` |
| `ANANTA_PEER_OVERLAY_COST_BUDGETS_PATH` | `agent/bootstrap/peer_overlay.py`, `agent/config.py` |
| `ANANTA_PEER_OVERLAY_DATA_ENABLED` | `agent/bootstrap/peer_overlay.py`, `agent/config.py` |
| `ANANTA_PEER_OVERLAY_ROLLOUT` | `agent/bootstrap/peer_overlay.py`, `agent/config.py` |
| `ANANTA_PEER_OVERLAY_STATE` | `agent/bootstrap/peer_overlay.py`, `agent/config.py` |
| `ANANTA_PERSONA_GENERATED_SOURCES_ENABLED` | `agent/bootstrap/persona_generated_sources.py` |
| `ANANTA_PERSONA_GENERATION_ENABLED` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_GENERATION_ENVIRONMENT_DIGEST` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_GENERATION_EXECUTION_PROFILE_DIGEST` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_GENERATION_KEY_FILE` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_GENERATION_REPOSITORY_REVISION` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_GENERATION_WORKER_URL` | `agent/bootstrap/persona_generation.py` |
| `ANANTA_PERSONA_IMAGES_ENABLED` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_IMAGE_ENVIRONMENT_DIGEST` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_IMAGE_EXECUTION_PROFILE_DIGEST` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_IMAGE_KEY_FILE` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_IMAGE_REPOSITORY_REVISION` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_IMAGE_WORKER_URL` | `agent/bootstrap/persona_media.py` |
| `ANANTA_PERSONA_VIDEOS_ENABLED` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VIDEO_ENVIRONMENT_DIGEST` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VIDEO_EXECUTION_PROFILE_DIGEST` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VIDEO_KEY_FILE` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VIDEO_REPOSITORY_REVISION` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VIDEO_WORKER_URL` | `agent/bootstrap/persona_videos.py` |
| `ANANTA_PERSONA_VOICES_ENABLED` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PERSONA_VOICE_ENVIRONMENT_DIGEST` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PERSONA_VOICE_EXECUTION_PROFILE_DIGEST` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PERSONA_VOICE_KEY_FILE` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PERSONA_VOICE_REPOSITORY_REVISION` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PERSONA_VOICE_WORKER_URL` | `agent/bootstrap/persona_voices.py` |
| `ANANTA_PI_EVIDENCE_ENABLED` | `agent/bootstrap/pi_dispatch.py` |
| `ANANTA_PLAN_PATTERN_BINDINGS_PATH` | `agent/services/blueprint_planning_adapter.py` |
| `ANANTA_PLAN_PATTERN_BINDING_SCHEMA_PATH` | `agent/services/blueprint_planning_adapter.py` |
| `ANANTA_PUBLIC_RENDEZVOUS_ENABLED` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_QDRANT_API_KEY` | `scripts/benchmark/qdrant_vector_store.py` |
| `ANANTA_QDRANT_TLS_CA_FILE` | `scripts/benchmark/qdrant_vector_store.py` |
| `ANANTA_QDRANT_URL` | `scripts/benchmark/qdrant_vector_store.py` |
| `ANANTA_QUICKSTART_MODE` | `agent/services/source_access_manifest_keyring.py` |
| `ANANTA_RENDEZVOUS_URL` | `client_surfaces/operator_tui/hub_loader.py`, `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_REPO_ROOT` | `agent/routes/codecompass_domain_scope.py`, `agent/services/background/mail_polling_scheduler.py`, `worker/mail_task_composition.py` |
| `ANANTA_REQUIRE_E2E_PAYLOAD_ENCRYPTION` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_RESEARCH_TRAINING_ALLOWED_LICENSES` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_ARTIFACT_ROOT` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_DATASET_ROOT` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_ENABLED` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_MODE` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_POLICY_PATH` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_RESULT_ROOT` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_ROLLOUT_PATH` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_SAFETY_PATH` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESEARCH_TRAINING_STATE` | `agent/bootstrap/research_training.py`, `agent/config_feature_settings.py` |
| `ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS` | `agent/services/restricted_inference_management_service.py`, `agent/services/restricted_model_inference_service.py`, `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_CIRCUIT_OPEN_SECONDS` | `agent/services/restricted_inference_management_circuit_breaker.py` |
| `ANANTA_RESTRICTED_INFERENCE_CONNECT_TIMEOUT_SECONDS` | `agent/services/restricted_model_inference_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_DEVICE` | `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_ENGINE` | `agent/services/restricted_model_inference_service.py`, `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_ENGINE_SCORE_CHOICES` | `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_HOST` | `worker/runtime/restricted_inference_app.py` |
| `ANANTA_RESTRICTED_INFERENCE_LEGACY_LOCAL` | `agent/services/restricted_model_inference_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_MANIFEST_ID` | `agent/services/restricted_model_inference_service.py`, `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT` | `worker/runtime/restricted_inference_app.py` |
| `ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES` | `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RESTRICTED_INFERENCE_MAX_REQUEST_BYTES` | `worker/runtime/restricted_inference_app.py` |
| `ANANTA_RESTRICTED_INFERENCE_PORT` | `worker/runtime/restricted_inference_app.py` |
| `ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT` | `worker/runtime/restricted_inference_app.py` |
| `ANANTA_RESTRICTED_INFERENCE_TOKEN` | `agent/services/restricted_inference_management_service.py`, `agent/services/restricted_model_inference_service.py`, `agent/services/voice_restricted_choice_service.py` (+1) |
| `ANANTA_RESTRICTED_INFERENCE_URL` | `agent/services/restricted_inference_management_service.py`, `agent/services/restricted_model_inference_service.py`, `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_RUNTIME_MODE` | `agent/cli/init_wizard.py` |
| `ANANTA_RUNTIME_PROFILE` | `agent/runtime_profiles.py`, `agent/services/source_access_manifest_keyring.py` |
| `ANANTA_RUN_ECLIPSE_HEADLESS_SMOKE` | `scripts/smoke_client_golden_paths.py` |
| `ANANTA_RUN_SPREADSHEET_GPU_GATE` | `scripts/run_spreadsheet_studio_release_gate.py` |
| `ANANTA_SCRUM_IMPROVEMENT_STATE` | `agent/bootstrap/scrum_continuous_improvement.py`, `agent/config.py` |
| `ANANTA_SELENIUM_URL` | `scripts/firefox_automation/_config.py` |
| `ANANTA_SELF_DEVELOPMENT_POLICY_FILE` | `agent/bootstrap/self_development.py`, `agent/services/self_development/runtime.py` |
| `ANANTA_SEMANTIC_COMPUTE_HUB_TOKEN_FILE` | `worker/semantic_media/compute_task_handler.py` |
| `ANANTA_SEMANTIC_COMPUTE_HUB_URL` | `worker/semantic_media/compute_task_handler.py` |
| `ANANTA_SEMANTIC_COMPUTE_MAX_PEER_CAPACITY` | `agent/repositories/semantic_compute_candidate_repository.py` |
| `ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED` | `agent/services/semantic_compute_execution_service.py` |
| `ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY` | `agent/services/semantic_contract_service.py`, `agent/services/semantic_task_lease_authority.py` |
| `ANANTA_SEMANTIC_COMPUTE_WORKER_ENABLED` | `agent/ai_agent.py` |
| `ANANTA_SEMANTIC_MEDIA_AUDIT_RETENTION_BATCH_SIZE` | `agent/services/background/semantic_media_audit_reconciler.py` |
| `ANANTA_SEMANTIC_MEDIA_AUDIT_RETENTION_INTERVAL_SECONDS` | `agent/services/background/semantic_media_audit_reconciler.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_API_KEY` | `agent/services/semantic_sfu_admission_service.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET` | `agent/services/semantic_sfu_admission_service.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_CONTAINER` | `scripts/benchmark/semantic_sfu_load.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_ENABLED` | `agent/services/semantic_sfu_admission_service.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL` | `agent/services/semantic_sfu_admission_service.py` |
| `ANANTA_SEMANTIC_MEDIA_SFU_TOKEN_TTL_SECONDS` | `agent/services/semantic_sfu_admission_service.py` |
| `ANANTA_SFU_BACKGROUND_CONFIG` | `agent/bootstrap/semantic_media_services.py` |
| `ANANTA_SFU_NODE_TRUST_CONFIG` | `agent/bootstrap/semantic_media_services.py` |
| `ANANTA_SFU_OBSERVATION_COLLECTOR_ID` | `agent/services/sfu_node_observation_ingestion_service.py` |
| `ANANTA_SFU_OBSERVATION_COLLECTOR_TOKEN` | `agent/bootstrap/semantic_media_services.py` |
| `ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_B64URL` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_FILE` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_SIGNING_KEY_ID` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_SIGNING_KEY_VERSION` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_SIGNING_MODE` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_FILE` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_JSON` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SFU_TRAFFIC_PROJECTION_CONFIG` | `agent/bootstrap/sfu_broadcast_services.py` |
| `ANANTA_SGPT_ROUTING_DEFAULT_BACKEND` | `agent/config_defaults_env.py` |
| `ANANTA_SGPT_ROUTING_TASK_KIND_BACKEND` | `agent/config_defaults_env.py` |
| `ANANTA_SIGNALING_URL` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_SNAKE_LOCAL_DEV_AUTH_BYPASS` | `agent/config.py` |
| `ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED` | `agent/bootstrap/source_control_bootstrap_settings.py` |
| `ANANTA_SOURCE_REVISION` | `agent/services/workflow_runtime_rollout_runtime.py`, `agent/services/workflow_runtime_selection_composition.py` |
| `ANANTA_SPECIALIST_INFERENCE_SIGNING_KEY_FILE` | `agent/services/specialist_inference_grant_service.py` |
| `ANANTA_SPEECH_ADAPTER_EXPORT_KEY_FILE` | `agent/services/ml_intern_speech_adapter_export.py` |
| `ANANTA_SPEECH_ADAPTER_REGISTRY_PATH` | `agent/routes/ml_intern_speech_adapters.py`, `agent/services/ml_intern_speech_revocation_service.py` |
| `ANANTA_SPEECH_EVIDENCE_RETENTION_BATCH_SIZE` | `agent/services/background/speech_evidence_retention_reconciler.py` |
| `ANANTA_SPEECH_EVIDENCE_RETENTION_INTERVAL_SECONDS` | `agent/services/background/speech_evidence_retention_reconciler.py` |
| `ANANTA_SPEECH_RECONCILIATION_ALLOWED_ENDPOINTS` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_ALLOWED_MODELS` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_CHARGING` | `agent/services/background/speech_reconciliation_queue_pump.py`, `agent/services/background/speech_reconciliation_result_collector.py` |
| `ANANTA_SPEECH_RECONCILIATION_CHECKPOINT_ROOT` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_KEYRING_PATH` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_LANGUAGE` | `agent/services/speech_reconciliation_production_composition.py` |
| `ANANTA_SPEECH_RECONCILIATION_MAX_WORKERS` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_PORT` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_QUIET_HOURS` | `agent/services/background/speech_reconciliation_queue_pump.py`, `agent/services/background/speech_reconciliation_result_collector.py` |
| `ANANTA_SPEECH_RECONCILIATION_RECONCILE_INTERVAL_SECONDS` | `agent/services/background/speech_reconciliation_reconciler.py` |
| `ANANTA_SPEECH_RECONCILIATION_RESOURCE_MODE` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_TOKEN` | `agent/services/background/speech_reconciliation_queue_pump.py`, `scripts/run_semantic_media_supply_chain_gate.py`, `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_RECONCILIATION_TRAINING_PROFILE_JSON` | `agent/services/speech_reconciliation_training_delegate.py` |
| `ANANTA_SPEECH_RECONCILIATION_WORKER_CAPACITY_JSON` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_WORKER_ID` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_WORKER_LOCATION` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_WORKER_URL` | `agent/services/background/speech_reconciliation_queue_pump.py` |
| `ANANTA_SPEECH_RECONCILIATION_WORKSPACE_ROOT` | `worker/runtime/speech_reconciliation_app.py` |
| `ANANTA_SPEECH_TRAINER_LOCATION` | `agent/services/speech_adaptation_production_composition.py` |
| `ANANTA_SPEECH_TRAINING_ALLOWED_ENDPOINTS` | `agent/services/speech_adaptation_production_composition.py` |
| `ANANTA_SPEECH_TRAINING_ARTIFACT_ROOT` | `agent/services/ml_intern_speech_adapter_export.py`, `agent/services/speech_adaptation_production_composition.py` |
| `ANANTA_SPEECH_TRAINING_CALLBACK_TOKEN` | `agent/services/speech_adaptation_production_composition.py`, `scripts/run_semantic_media_supply_chain_gate.py`, `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_CI_MOCK` | `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_HUB_ALLOWED_ENDPOINTS` | `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_HUB_CALLBACK_URL` | `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_MOCK_ENABLED` | `agent/services/speech_adaptation_production_composition.py` |
| `ANANTA_SPEECH_TRAINING_PORT` | `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_TOKEN` | `agent/services/speech_adaptation_production_composition.py`, `scripts/run_semantic_media_supply_chain_gate.py`, `worker/runtime/speech_training_app.py` |
| `ANANTA_SPEECH_TRAINING_WORKER_URL` | `agent/services/speech_adaptation_production_composition.py` |
| `ANANTA_SPEECH_TRAINING_WORKSPACE_ROOT` | `worker/runtime/speech_training_app.py` |
| `ANANTA_SPREADSHEET_ALLOWED_ENDPOINTS` | `agent/services/spreadsheet_worker_port.py` |
| `ANANTA_SPREADSHEET_HUB_ENDPOINT` | `worker/runtime/spreadsheet_queue_worker.py` |
| `ANANTA_SPREADSHEET_NETWORK_ISOLATED` | `worker/runtime/spreadsheet_queue_worker.py`, `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_STALE_JOB_SECONDS` | `agent/bootstrap/spreadsheet_studio.py` |
| `ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED` | `agent/bootstrap/spreadsheet_studio.py`, `agent/config.py` |
| `ANANTA_SPREADSHEET_STUDIO_ENABLED` | `agent/bootstrap/spreadsheet_studio.py`, `agent/config.py` |
| `ANANTA_SPREADSHEET_STUDIO_MODE` | `agent/bootstrap/spreadsheet_studio.py`, `agent/config.py` |
| `ANANTA_SPREADSHEET_STUDIO_STATE` | `agent/bootstrap/spreadsheet_studio.py`, `agent/config.py` |
| `ANANTA_SPREADSHEET_WORKER_FILE_BYTES` | `worker/runtime/spreadsheet_queue_worker.py`, `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_WORKER_HOST` | `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_WORKER_ID` | `agent/bootstrap/spreadsheet_studio.py`, `agent/routes/spreadsheet_studio.py`, `worker/runtime/spreadsheet_queue_worker.py` |
| `ANANTA_SPREADSHEET_WORKER_JOB_TIMEOUT_SECONDS` | `worker/runtime/spreadsheet_queue_worker.py`, `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_WORKER_MEMORY_BYTES` | `worker/runtime/spreadsheet_queue_worker.py`, `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_WORKER_PORT` | `worker/runtime/spreadsheet_worker_app.py` |
| `ANANTA_SPREADSHEET_WORKER_TIMEOUT_SECONDS` | `agent/services/spreadsheet_worker_port.py` |
| `ANANTA_SPREADSHEET_WORKER_TOKEN` | `agent/routes/spreadsheet_studio.py`, `agent/services/spreadsheet_worker_port.py`, `worker/runtime/spreadsheet_queue_worker.py` (+1) |
| `ANANTA_SPREADSHEET_WORKER_TOKEN_FILE` | `agent/services/spreadsheet_worker_port.py` |
| `ANANTA_SPREADSHEET_WORKER_URL` | `agent/services/spreadsheet_worker_port.py` |
| `ANANTA_SQLITE_POOL_SIZE` | `agent/config.py` |
| `ANANTA_SSH_GOAL_ID` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_OPERATION` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_PRINCIPAL` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_SESSION_ID` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_TARGET_ID` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_TARGET_TYPE` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_TASK_ID` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_USER_ID` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SSH_WORKSPACE` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_SUBWORKER_MAX_CHILDREN` | `agent/config_defaults_env.py` |
| `ANANTA_SYFT_BIN` | `scripts/generate_semantic_media_supply_chain_reports.py` |
| `ANANTA_TEMPORAL_ADDRESS` | `agent/services/workflow_backend_factory.py`, `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_API_KEY_FILE` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_HUB_IDENTITY` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_NAMESPACE` | `agent/services/workflow_backend_factory.py`, `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_PERFORMANCE_SAMPLES` | `worker/temporal/performance.py` |
| `ANANTA_TEMPORAL_RECOVERY_PROBE_ID` | `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_SMOKE_MODE` | `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_SMOKE_TIMEOUT_SECONDS` | `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_TASK_QUEUE` | `agent/services/workflow_backend_factory.py`, `worker/temporal/smoke.py` |
| `ANANTA_TEMPORAL_TLS_CA_FILE` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_TLS_CERT_FILE` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_TLS_KEY_FILE` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_TLS_SERVER_NAME` | `agent/services/temporal_client_connection.py` |
| `ANANTA_TEMPORAL_UI_URL` | `agent/services/workflow_backend_factory.py` |
| `ANANTA_TEMPORAL_WORKER_HEALTH_URL` | `agent/services/workflow_runtime_health_service.py` |
| `ANANTA_TEMPORAL_WORKFLOW_TYPE` | `agent/services/workflow_backend_factory.py` |
| `ANANTA_THREE_WORKER_RUN_CONFIG` | `agent/services/three_worker_run_config_service.py` |
| `ANANTA_TINY_ROUTER_MODE` | `agent/config_defaults_env.py` |
| `ANANTA_TINY_ROUTER_PROFILES` | `agent/config_defaults_env.py` |
| `ANANTA_TOKEN` | `agent/cli/commands/ssh.py` |
| `ANANTA_TUI_3D` | `client_surfaces/operator_tui/animation3d/capabilities.py`, `client_surfaces/operator_tui/app.py` |
| `ANANTA_TUI_3D_DURATION_MS` | `client_surfaces/operator_tui/animation3d/capabilities.py` |
| `ANANTA_TUI_3D_FPS` | `client_surfaces/operator_tui/animation3d/capabilities.py` |
| `ANANTA_TUI_3D_PRESET` | `client_surfaces/operator_tui/animation3d/capabilities.py`, `client_surfaces/operator_tui/app.py` |
| `ANANTA_TUI_3D_RENDERER` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_3D_SCENE` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_AI_SNAKE_CLOUD_ALLOWED` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_AI_SNAKE_MAX_LATENCY_MS` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_AI_SNAKE_MODEL` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_AI_SNAKE_PROVIDER` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_AUTO_BUILD_CODECOMPASS` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_AUTO_REBUILD_MAX_AGE_SECONDS` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_BROWSER_RENDER_MODE` | `client_surfaces/operator_tui/_renderer_content.py` |
| `ANANTA_TUI_BROWSER_WIDE_LAYOUT` | `client_surfaces/operator_tui/_interactive_window.py`, `client_surfaces/operator_tui/renderer.py` |
| `ANANTA_TUI_CELL_HEIGHT_PX` | `client_surfaces/operator_tui/logo_renderer/pixel_geometry.py` |
| `ANANTA_TUI_CELL_WIDTH_PX` | `client_surfaces/operator_tui/logo_renderer/pixel_geometry.py` |
| `ANANTA_TUI_CHAT_ANSWER_CHARS` | `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_API_BASE_URL` | `client_surfaces/operator_tui/_ai_snake_config_helpers.py`, `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/header_snake_mixin.py` (+1) |
| `ANANTA_TUI_CHAT_ARCHITECTURE_ANALYSIS_MODE` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_ASK_TIMEOUT` | `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/commands_ai.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_BACKEND` | `client_surfaces/operator_tui/chat_ask_support.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_BACKENDS` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_BACKEND_FALLBACK` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_CODE_QUESTIONS_REPO_FIRST` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_CONTEXT_CHARS` | `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_CONTROL_MODE` | `client_surfaces/operator_tui/chat_control_config.py`, `scripts/e2e/snake_chat_control_e2e.py` |
| `ANANTA_TUI_CHAT_EMBEDDING_API_MAX_RECORDS` | `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `ANANTA_TUI_CHAT_EMBEDDING_MODEL` | `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `ANANTA_TUI_CHAT_INCLUDE_LOCAL_PROJECT` | `client_surfaces/operator_tui/chat_history_manager.py` |
| `ANANTA_TUI_CHAT_INCLUDE_WIKIPEDIA` | `client_surfaces/operator_tui/chat_history_manager.py` |
| `ANANTA_TUI_CHAT_MAX_TOKENS` | `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_MODEL` | `client_surfaces/operator_tui/_ai_snake_config_helpers.py`, `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/header_snake_mixin.py` (+1) |
| `ANANTA_TUI_CHAT_RAG_TOP_K` | `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/chat_ask_support.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_RETRIEVAL_DOMAIN_HINT` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_RETRIEVAL_PROFILE` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_CHAT_SOURCE_PACK` | `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/chat_history_manager.py` |
| `ANANTA_TUI_CHAT_STREAMING` | `client_surfaces/operator_tui/chat_message_formatter.py` |
| `ANANTA_TUI_CHAT_SYSTEM_PROMPT` | `client_surfaces/operator_tui/chat_ask_support.py`, `client_surfaces/operator_tui/chat_message_formatter.py` |
| `ANANTA_TUI_CHAT_USE_CODECOMPASS` | `client_surfaces/operator_tui/chat_history_manager.py` |
| `ANANTA_TUI_CHAT_USE_EMBEDDING_API` | `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `ANANTA_TUI_CODECOMPASS_BUILD_TIMEOUT` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_CODECOMPASS_OUTPUT_DIR` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_DISABLE_KITTY` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_DISABLE_SIXEL` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_DOC_DIAGNOSTICS` | `client_surfaces/operator_tui/_renderer_content.py` |
| `ANANTA_TUI_E2E_CAST_COMMAND` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_CAST_HEIGHT` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_CAST_SECONDS` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_CAST_WIDTH` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_OIDC_CLIENT_SECRET` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_OIDC_PASSWORD` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_OIDC_TOKEN` | `client_surfaces/operator_tui/_snake_tick_share.py`, `client_surfaces/operator_tui/hub_loader.py`, `client_surfaces/operator_tui/interactive.py` (+1) |
| `ANANTA_TUI_E2E_OIDC_USERNAME` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_E2E_SHARE_AUTORUN` | `client_surfaces/operator_tui/_snake_tick_share.py` |
| `ANANTA_TUI_E2E_SHARE_ONLY_NAV` | `client_surfaces/operator_tui/_renderer_layout.py`, `client_surfaces/operator_tui/header_snake_mixin.py`, `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_E2E_SHARE_TITLE` | `client_surfaces/operator_tui/_snake_tick_share.py` |
| `ANANTA_TUI_E2E_USE_PUBLIC_OIDC` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_ENABLE_3D` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_ENABLE_KITTY` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_ENABLE_SIXEL` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_FORCE_PIXEL_GRAPHICS` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_FORCE_SIXEL` | `client_surfaces/operator_tui/_interactive_visual.py` |
| `ANANTA_TUI_FRAME_HEIGHT` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_FRAME_WIDTH` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_GFX_DEBUG` | `client_surfaces/operator_tui/_renderer_layout.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_GRAPHICS` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_HEADER_3D` | `client_surfaces/operator_tui/_renderer_layout.py`, `client_surfaces/operator_tui/interactive.py` |
| `ANANTA_TUI_HEADER_3D_FPS` | `client_surfaces/operator_tui/interactive.py` |
| `ANANTA_TUI_HEADER_3D_SPEED` | `client_surfaces/operator_tui/_renderer_layout.py` |
| `ANANTA_TUI_HEADER_ANIM` | `client_surfaces/operator_tui/_renderer_layout.py` |
| `ANANTA_TUI_HEADER_SNAKE` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_HEADER_SNAKE_ACCEL` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_HEADER_SNAKE_MAX_SPEED` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_HEADER_SNAKE_TPS` | `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_KANBAN_ENABLED` | `client_surfaces/operator_tui/dashboard_surfaces.py` |
| `ANANTA_TUI_KEYBINDINGS_FILE` | `client_surfaces/operator_tui/keybindings_config.py` |
| `ANANTA_TUI_LLM_API_BASE` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_LLM_API_TOKEN` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_LLM_HEALTH_INTERVAL_SECS` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_LLM_MODEL` | `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_LOGO` | `agent/cli/logo_assets.py`, `agent/cli/logo_layout.py`, `client_surfaces/operator_tui/_renderer_layout.py` (+2) |
| `ANANTA_TUI_LOGO_ANIMATION` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_LOGO_DEBUG` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_LOGO_FPS` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_LOGO_OVERSAMPLING` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/frame_cache.py` |
| `ANANTA_TUI_LOGO_QUALITY` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py`, `client_surfaces/operator_tui/logo_renderer/frame_cache.py` |
| `ANANTA_TUI_LOGO_RENDERER` | `client_surfaces/operator_tui/_renderer_layout.py`, `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_LOGO_STREAM_INLINE` | `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_MODEL_MENU_ENABLED` | `client_surfaces/operator_tui/dashboard_surfaces.py` |
| `ANANTA_TUI_MOUSE` | `client_surfaces/operator_tui/mouse.py` |
| `ANANTA_TUI_MOUSE_FOLLOW_MAX_SPEED` | `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_MOUSE_FOLLOW_SMOOTHING` | `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_OIDC_TOKEN` | `client_surfaces/operator_tui/_snake_tick_share.py`, `client_surfaces/operator_tui/hub_loader.py`, `client_surfaces/operator_tui/interactive.py` |
| `ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE` | `client_surfaces/operator_tui/_renderer_content.py` |
| `ANANTA_TUI_PTY_TRACE` | `scripts/e2e/tui_kanban_pty_resize.py` |
| `ANANTA_TUI_RAG_SCOPE_FILTER` | `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `ANANTA_TUI_SNAKE_AI_API_BASE_URL` | `client_surfaces/operator_tui/_ai_snake_config_helpers.py`, `client_surfaces/operator_tui/ai_snake_config_view.py`, `client_surfaces/operator_tui/header_snake_mixin.py` (+3) |
| `ANANTA_TUI_SNAKE_AI_API_TOKEN` | `client_surfaces/operator_tui/tutorial_ai_engine.py`, `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `ANANTA_TUI_SNAKE_AI_ASYNC` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_BACKEND` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_MODEL` | `client_surfaces/operator_tui/_ai_snake_config_helpers.py`, `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/header_snake_mixin.py` (+3) |
| `ANANTA_TUI_SNAKE_AI_PROMPT_TEMPLATE` | `client_surfaces/operator_tui/header_snake_mixin.py`, `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_REFRESH` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_TIMEOUT` | `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/commands_ai.py`, `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_TRAINING` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_WORKER_PROVIDER` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_WORKER_STRATEGY` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_AI_WORKER_TOKEN` | `client_surfaces/operator_tui/chat_message_formatter.py`, `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_SNAKE_DEMO_PEERS` | `client_surfaces/operator_tui/_snake_ops_tutor.py` |
| `ANANTA_TUI_SNAKE_MARK_TTL` | `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_SNAKE_MESSAGE_EFFECT` | `client_surfaces/operator_tui/_renderer_snake_overlay.py` |
| `ANANTA_TUI_SNAKE_MODE` | `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_SNAKE_MOUSE_DWELL` | `client_surfaces/operator_tui/interactive.py` |
| `ANANTA_TUI_SNAKE_OIDC_PROVIDER` | `client_surfaces/operator_tui/header_snake_mixin.py`, `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_SNAKE_PSEUDONYM` | `client_surfaces/operator_tui/header_snake_mixin.py`, `client_surfaces/operator_tui/snake_tick_mixin.py` |
| `ANANTA_TUI_SNAKE_RAG_MAX_RECORDS_PER_FILE` | `client_surfaces/operator_tui/chat_history_manager.py`, `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_SNAKE_RAG_TOP_K` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `ANANTA_TUI_SNAKE_SELECT_DELAY` | `client_surfaces/operator_tui/_snake_ops_tutor.py` |
| `ANANTA_TUI_SNAKE_TRAIL_SPEED` | `client_surfaces/operator_tui/_renderer_snake_overlay.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_SNAKE_TRAIL_WINDOW` | `client_surfaces/operator_tui/_renderer_snake_overlay.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_SNAKE_TUTORIAL_AI` | `client_surfaces/operator_tui/_snake_ops_tutor.py`, `client_surfaces/operator_tui/header_snake_mixin.py` |
| `ANANTA_TUI_SNAPSHOT_DIR` | `client_surfaces/operator_tui/tui_snapshot.py`, `scripts/e2e/tui_demo_recorder.py` |
| `ANANTA_TUI_SPLASH` | `agent/cli/splash.py`, `client_surfaces/operator_tui/animation3d/capabilities.py`, `client_surfaces/operator_tui/app.py` |
| `ANANTA_TUI_SPLASH_DEBUG` | `client_surfaces/operator_tui/app.py` |
| `ANANTA_TUI_SPLASH_DEBUG_PATH` | `client_surfaces/operator_tui/app.py` |
| `ANANTA_TUI_TARGET_FPS` | `client_surfaces/operator_tui/app.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` |
| `ANANTA_TUI_VERBOSE` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS` | `client_surfaces/operator_tui/tutorial_ai_engine.py` |
| `ANANTA_TUI_VISUAL_DEFAULT_ADAPTER` | `client_surfaces/operator_tui/_interactive_visual.py` |
| `ANANTA_TUI_VISUAL_DEFAULT_RENDERER` | `client_surfaces/operator_tui/_interactive_visual.py` |
| `ANANTA_TUI_VISUAL_DEFAULT_VIEW` | `client_surfaces/operator_tui/_interactive_visual.py` |
| `ANANTA_TUI_VISUAL_OPENGL` | `client_surfaces/operator_tui/_interactive_visual.py`, `scripts/operator_tui_visual_smoke.py` |
| `ANANTA_TUI_VISUAL_VIEWPORT_ENABLED` | `client_surfaces/operator_tui/_interactive_visual.py` |
| `ANANTA_TUI_WSL2` | `client_surfaces/operator_tui/commands_visual.py`, `client_surfaces/operator_tui/visual/markdown/render_policy.py` |
| `ANANTA_TURN_TEST_PASS` | `agent/routes/network_profiles.py` |
| `ANANTA_TURN_TEST_USER` | `agent/routes/network_profiles.py` |
| `ANANTA_TURN_URL` | `client_surfaces/operator_tui/network_profile.py` |
| `ANANTA_UNSLOTH_COMPATIBILITY_ENTRY` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_UNSLOTH_COMPATIBILITY_MATRIX` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_UNSLOTH_MODEL_CATALOG_PATH` | `agent/services/unsloth_model_catalog_service.py` |
| `ANANTA_UNSLOTH_MODEL_NETWORK_ENABLED` | `worker/training/unsloth_worker_runtime.py` |
| `ANANTA_UNSLOTH_REPEAT_COUNT` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_UNSLOTH_RUN_IDS` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_UNSLOTH_SRC_IDS` | `scripts/run_lora_training_smoke.py` |
| `ANANTA_UNSLOTH_STUDIO_ALLOWED_HOSTS` | `agent/routes/ml_intern_training_route_support.py`, `worker/training/unsloth_mcp_control.py` |
| `ANANTA_UNSLOTH_STUDIO_ALLOWED_IP_CIDRS` | `agent/routes/ml_intern_training_route_support.py`, `worker/training/unsloth_mcp_control.py` |
| `ANANTA_UNSLOTH_STUDIO_ENABLED` | `agent/routes/ml_intern_training_route_support.py` |
| `ANANTA_UNSLOTH_STUDIO_EXPECTED_VERSION` | `agent/routes/ml_intern_training_route_support.py`, `worker/training/unsloth_mcp_control.py` |
| `ANANTA_UNSLOTH_STUDIO_MCP_ENABLED` | `agent/routes/ml_intern_training_route_support.py` |
| `ANANTA_UNSLOTH_STUDIO_MCP_TOKEN` | `worker/training/unsloth_mcp_control.py` |
| `ANANTA_UNSLOTH_STUDIO_URL` | `agent/routes/ml_intern_training_route_support.py`, `worker/training/unsloth_mcp_control.py` |
| `ANANTA_UNSLOTH_WORKER_MODE` | `worker/training/unsloth_worker_runtime.py` |
| `ANANTA_USER` | `agent/cli/api_client.py`, `agent/cli_goals_support.py`, `agent/services/three_worker_track_executor.py` (+4) |
| `ANANTA_USER_JSON` | `agent/routes/ai_snake_config.py`, `client_surfaces/operator_tui/config/user_config_manager.py` |
| `ANANTA_USER_TOKEN` | `agent/cli/commands/ssh.py` |
| `ANANTA_VACGE_APPROVAL_SECRET` | `agent/services/config_graph_approval_service.py` |
| `ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT` | `agent/services/vector_index_input_artifact_service.py` |
| `ANANTA_VECTOR_INDEX_INPUT_ROOTS` | `worker/retrieval/vector_index_input_loader.py` |
| `ANANTA_VECTOR_INDEX_TASK_AUDIENCE` | `worker/retrieval/vector_index_job_handler.py` |
| `ANANTA_VECTOR_INDEX_TASK_DISPATCH_TTL_SECONDS` | `agent/services/vector_index_task_lifecycle_support.py` |
| `ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE` | `worker/retrieval/vector_index_replay_guard.py` |
| `ANANTA_VECTOR_INDEX_TASK_REPLAY_RECEIPT_RETENTION_SECONDS` | `worker/retrieval/vector_index_replay_guard.py` |
| `ANANTA_VECTOR_STORE_ROLLOUT_STATE_PATH` | `agent/services/vector_store_rollout_service.py` |
| `ANANTA_VERSION` | `agent/config.py` |
| `ANANTA_VOICE_RESTRICTED_CHOICE_MANIFEST_ID` | `agent/services/voice_restricted_choice_service.py` |
| `ANANTA_WEBRTC_EDGE_NETWORK` | `scripts/provision_webrtc_room_edge.py` |
| `ANANTA_WEBRTC_ROOM_CONTAINER` | `scripts/provision_webrtc_room_edge.py` |
| `ANANTA_WEBRTC_TLS_CERT_FILE` | `scripts/provision_webrtc_room_edge.py` |
| `ANANTA_WEBRTC_TLS_KEY_FILE` | `scripts/provision_webrtc_room_edge.py` |
| `ANANTA_WEB_URL` | `agent/cli/main.py` |
| `ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED` | `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WIKI_VECTOR_MANIFEST_HASH` | `agent/services/retrieval_vector_runtime_scope_service.py`, `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WIKI_VECTOR_PROFILE_NAME` | `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WIKI_VECTOR_RETRIEVAL_CACHE_STATE` | `agent/services/retrieval_vector_runtime_scope_service.py`, `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WIKI_VECTOR_SOURCE_ID` | `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WIKI_VECTOR_WORKSPACE_ID` | `agent/services/wiki_vector_runtime_service.py` |
| `ANANTA_WINDOW_OPEN_CMD` | `client_surfaces/operator_tui/windowing/backends/wslg_webview_backend.py` |
| `ANANTA_WORKER_CONTEXT_FILES_PER_BATCH` | `agent/config.py` |
| `ANANTA_WORKER_CONTEXT_LINE_WINDOW` | `agent/config.py` |
| `ANANTA_WORKER_CONTEXT_MAX_ITERATIONS` | `agent/config.py` |
| `ANANTA_WORKER_CONTEXT_MAX_SNIPPET_CHARS` | `agent/config.py` |
| `ANANTA_WORKER_CONTEXT_PER_FILE_CHARS` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_ENABLED` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_FILES_PER_BATCH` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_MAX_BATCHES` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_MAX_REF_CHARS` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_MAX_TOTAL_REF_COUNT` | `agent/config.py` |
| `ANANTA_WORKER_FULL_SCAN_SUMMARY_CHARS` | `agent/config.py` |
| `ANANTA_WORKER_MAX_PARALLEL_TASKS` | `agent/config_defaults_env.py` |
| `ANANTA_WORKER_POOL_ENABLED` | `agent/config_defaults_env.py` |
| `ANANTA_WORKFLOW_ADAPTER_LEASE_SECONDS` | `agent/services/workflow_adapter_task_queue_composition.py` |
| `ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING` | `agent/services/workflow_hub_task_gateway_runtime.py` |
| `ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE` | `agent/services/workflow_hub_task_gateway_runtime.py` |
| `ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE` | `worker/runtime/native_graph/authorization.py` |
| `ANANTA_WORKFLOW_BACKEND` | `agent/services/workflow_backend_factory.py` |
| `ANANTA_WORKFLOW_DEFAULT_GATE` | `agent/services/workflow_settings.py` |
| `ANANTA_WORKFLOW_HUB_TOKEN_FILE` | `worker/hrm_experiments/task_handler.py`, `worker/runtime/native_graph/artifact_http.py`, `worker/runtime/workflow_hub_gateway.py` (+1) |
| `ANANTA_WORKFLOW_HUB_URL` | `worker/hrm_experiments/task_handler.py`, `worker/runtime/native_graph/artifact_http.py`, `worker/runtime/workflow_hub_gateway.py` (+1) |
| `ANANTA_WORKFLOW_MODE` | `agent/services/workflow_settings.py` |
| `ANANTA_WORKFLOW_OTEL_ENABLED` | `agent/services/workflow_runtime/telemetry_runtime.py` |
| `ANANTA_WORKFLOW_OTEL_ENDPOINT` | `agent/services/workflow_runtime/telemetry_runtime.py` |
| `ANANTA_WORKFLOW_OTEL_HEADERS_FILE` | `agent/services/workflow_runtime/telemetry_runtime.py` |
| `ANANTA_WORKFLOW_RECONCILE_INTERVAL_SECONDS` | `agent/services/background/workflow_runtime_reconciler.py` |
| `ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH` | `agent/config.py` |
| `ANANTA_WORKFLOW_RUNTIME_PERFORMANCE_EVIDENCE_FILE` | `agent/services/workflow_runtime_rollout_runtime.py` |
| `ANANTA_WORKFLOW_RUNTIME_SHADOW_EVIDENCE_FILE` | `agent/services/workflow_runtime_rollout_runtime.py` |
| `ANANTA_WORKFLOW_RUNTIME_TEST_CONTEXT` | `agent/config.py` |
| `ANANTA_WORKSPACE_BASE` | `agent/services/ssh_terminal_wrapper.py` |
| `ANANTA_WORKSPACE_MUTATION_LLM_BACKEND` | `agent/config_defaults_env.py` |
| `ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT` | `agent/config_defaults_env.py` |
| `ANANTA_WORKSPACE_ROOT` | `agent/bootstrap/source_control_workspace_bootstrap.py`, `agent/config.py`, `agent/config_defaults_worker.py` (+2) |
| `ANANTA_WORKSPACE_ROOTS` | `agent/services/ssh_terminal_wrapper.py` |
| `ANTHROPIC_API_KEY` | `agent/cli_backends/claude_runtime.py`, `agent/cli_backends/claude_write_armed.py`, `agent/config.py` (+5) |
| `ANTHROPIC_URL` | `agent/config.py` |
| `API_BASE_URL` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `APP_STARTED_AT` | `agent/services/system_health_service.py` |
| `ARCHIVED_TASKS_RETENTION_DAYS` | `agent/config.py` |
| `AUTH_ACCESS_TOKEN_TTL_SECONDS` | `agent/config.py` |
| `AUTH_IP_BAN_DURATION_SECONDS` | `agent/config.py` |
| `AUTH_MFA_BACKUP_CODE_COUNT` | `agent/config.py` |
| `AUTH_PASSWORD_HISTORY_LIMIT` | `agent/config.py` |
| `AUTH_PASSWORD_MIN_LENGTH` | `agent/config.py` |
| `AUTH_PROVIDER` | `agent/config.py` |
| `AUTH_RATE_LIMIT_MAX_ATTEMPTS_LONG` | `agent/config.py` |
| `AUTH_RATE_LIMIT_MAX_ATTEMPTS_SHORT` | `agent/config.py` |
| `AUTH_RATE_LIMIT_WINDOW_LONG_SECONDS` | `agent/config.py` |
| `AUTH_RATE_LIMIT_WINDOW_SHORT_SECONDS` | `agent/config.py` |
| `AUTH_REFRESH_TOKEN_TTL_SECONDS` | `agent/config.py` |
| `AUTH_TEST_ENDPOINTS_ENABLED` | `agent/config.py` |
| `AUTH_USER_LOCKOUT_DURATION_SECONDS` | `agent/config.py` |
| `AUTH_USER_LOCKOUT_THRESHOLD` | `agent/config.py` |
| `AUTO_UPDATE_DOTENV` | `agent/config.py` |
| `BACKUPS_RETENTION_DAYS` | `agent/config.py` |
| `CACHE_LENGTH` | `agent/sgpt/handlers/handler.py` |
| `CACHE_PATH` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `CHAT_CACHE_LENGTH` | `agent/sgpt/config.py`, `agent/sgpt/handlers/chat_handler.py`, `agent/sgpt/handlers/default_handler.py` |
| `CHAT_CACHE_PATH` | `agent/sgpt/config.py`, `agent/sgpt/handlers/chat_handler.py`, `agent/sgpt/handlers/default_handler.py` |
| `CLAUDE_CODE_AUTH_MODE` | `agent/config.py` |
| `CLAUDE_CODE_DEFAULT_MODEL` | `agent/config.py` |
| `CLAUDE_CODE_MAX_CONCURRENT_RUNS` | `agent/config.py` |
| `CLAUDE_CODE_PATH` | `agent/config.py` |
| `CLAUDE_CODE_PERMISSION_MODE` | `agent/config.py` |
| `CLAUDE_CODE_TIMEOUT_SECONDS` | `agent/config.py` |
| `CODECOMPASS_AGENT_FEATURE_ALLOWED_PATHS` | `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_AGENT_FEATURE_ALLOWED_PROVIDER_IDS` | `agent/config_codecompass_settings.py`, `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_AGENT_FEATURE_ENABLED` | `agent/config_codecompass_settings.py`, `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_AGENT_FEATURE_EXTERNAL_CALLS_ALLOWED` | `agent/config_codecompass_settings.py`, `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_AGENT_FEATURE_MAX_INPUT_CHARS` | `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_AGENT_FEATURE_NO_WRITE_MODE` | `agent/services/agent_feature_provider.py` |
| `CODECOMPASS_DOMAIN_ARTIFACT_PATH` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_DOMAIN_DESCRIPTOR_ROOT` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_DOMAIN_SCOPE_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_FTS_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_GRAPH_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_OUTPUT_DIR` | `client_surfaces/operator_tui/tutorial_ai_mixin.py` |
| `CODECOMPASS_QUERY_MAX_DEPTH` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_QUERY_MAX_NODES` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_QUERY_MAX_PATHS_PER_RESULT` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_QUERY_MAX_RESULTS` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_RELATION_EXPANSION_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SCOPE_ALLOW_RELATION_EXPANSION` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SCOPE_MAX_EXTERNAL_REFERENCE_CHUNKS` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SCOPE_STRICT_MODE` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_ENRICHMENT_MODEL` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_LAYER_ROOT` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_LOCAL_MODELS_ONLY` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_MODE` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_OFFLINE_ENRICHMENT_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_ONLINE_EXPANSION_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_QUERY_MODEL` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_RERANKER_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_RERANK_MODEL` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_ROLLOUT_STATE` | `agent/bootstrap/codecompass_sira_rollout.py`, `agent/config_codecompass_settings.py` |
| `CODECOMPASS_SIRA_SNAPSHOT_ROOT` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_TRANSFORMER_FEATURE_LOCAL_ONLY` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_TRANSFORMER_FEATURE_MAX_INPUT_TOKENS` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_TRANSFORMER_FEATURE_MODE` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_TRANSFORMER_FEATURE_MODEL` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_EMBEDDING_RECORDS_PATH` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_EMBEDDING_TEXT_PROFILE` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_ENABLED` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_ENCODING_BLOCK_SIZE` | `agent/config_codecompass_settings.py`, `worker/retrieval/vector_encoding.py` |
| `CODECOMPASS_VECTOR_ENCODING_FALLBACK_POLICY` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_ENCODING_MODE` | `agent/config_codecompass_settings.py`, `worker/retrieval/vector_encoding.py` |
| `CODECOMPASS_VECTOR_ENCODING_SEED` | `agent/config_codecompass_settings.py`, `worker/retrieval/vector_encoding.py` |
| `CODECOMPASS_VECTOR_ENCODING_STORE_ORIGINAL` | `agent/config_codecompass_settings.py`, `worker/retrieval/vector_encoding.py` |
| `CODECOMPASS_VECTOR_ENCODING_TARGET_BITS` | `agent/config_codecompass_settings.py`, `worker/retrieval/vector_encoding.py` |
| `CODECOMPASS_VECTOR_FAIL_MODE` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_INDEX_PATH` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_VECTOR_MANIFEST_PATH` | `agent/config_codecompass_settings.py` |
| `CODECOMPASS_WIKI_INDEX_PATH` | `agent/config_codecompass_settings.py` |
| `CODEX_AUTH_MODE` | `agent/config.py` |
| `CODEX_DEFAULT_MODEL` | `agent/config.py` |
| `CODEX_PATH` | `agent/config.py` |
| `CODEX_REQUIRE_API_KEY` | `agent/config.py` |
| `CODE_THEME` | `agent/sgpt/config.py`, `agent/sgpt/handlers/chat_handler.py`, `agent/sgpt/handlers/handler.py` |
| `COGNITIVE_STYLE_ROUTING_WEIGHT` | `agent/services/model_invocation_profile_resolver_loader.py` |
| `COLORTERM` | `client_surfaces/operator_tui/logo_renderer/animated_header.py`, `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `COMMAND_TIMEOUT` | `agent/config.py` |
| `COMPOSE_PROFILES` | `agent/cli/init_wizard.py`, `agent/runtime_profiles.py` |
| `COMPOSITE_RISK_REVIEW_ENABLED` | `agent/config_feature_settings.py` |
| `COMPOSITE_RISK_REVIEW_EXPLICIT_ONLY` | `agent/config_feature_settings.py` |
| `COMPOSITE_RISK_REVIEW_WARNING_TEXT` | `agent/config_feature_settings.py` |
| `COMSPEC` | `agent/services/live_terminal_session_service.py`, `agent/ws_terminal.py` |
| `CONTEXT_COMPRESSION_ADAPTER` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_CCR_ENABLED` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_CCR_PATH` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_CCR_TTL_HOURS` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_EMIT_EVENTS` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_ENABLED` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_EXTERNAL_HEADROOM_ENABLED` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_EXTERNAL_HEADROOM_TRANSPORT` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_FALLBACK_ON_QUALITY_RISK` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_MAX_INPUT_TOKENS` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_MIN_QUALITY_SCORE` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_MODE` | `agent/config_context_compression_settings.py` |
| `CONTEXT_COMPRESSION_TARGET_REDUCTION_PERCENT` | `agent/config_context_compression_settings.py` |
| `CONTINENT` | `scripts/build_geomap_assets.py` |
| `CONTROLLER_URL` | `agent/config.py` |
| `CORS_ORIGINS` | `agent/config.py` |
| `CUDA_VISIBLE_DEVICES` | `worker/training/runtime_artifact_service.py` |
| `DATABASE_URL` | `agent/config.py`, `scripts/migrate_memory_security.py` |
| `DATA_DIR` | `agent/config.py`, `agent/routes/config/benchmarks.py`, `agent/routes/config/shared.py` (+10) |
| `DEFAULT_COLOR` | `agent/sgpt/config.py`, `agent/sgpt/handlers/chat_handler.py`, `agent/sgpt/handlers/handler.py` |
| `DEFAULT_EXECUTE_SHELL_CMD` | `agent/sgpt/app.py`, `agent/sgpt/config.py` |
| `DEFAULT_MODEL` | `agent/config.py`, `agent/routes/config/read_models.py`, `agent/services/model_invocation_profile_resolver_loader.py` (+4) |
| `DEFAULT_PROVIDER` | `agent/config.py`, `agent/routes/config/read_models.py`, `agent/services/model_invocation_profile_resolver_loader.py` (+2) |
| `DISABLE_INITIAL_ADMIN` | `agent/config.py` |
| `DISABLE_LLM_CHECK` | `agent/config.py` |
| `DISABLE_STREAMING` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `DISPLAY` | `client_surfaces/operator_tui/windowing/backends/wslg_webview_backend.py`, `worker/meet_media/browser_source_probe.py`, `worker/meet_media/publication_smoke.py` |
| `DOCKER_OPS_BOUNDARY` | `agent/services/docker_engine_service.py` |
| `E2E_ADMIN_PASSWORD` | `scripts/firefox_automation/_config.py`, `scripts/run_live_click_dual_benchmark.py` |
| `E2E_ADMIN_USER` | `scripts/firefox_automation/_config.py`, `scripts/run_live_click_dual_benchmark.py` |
| `E2E_ALPHA_PORT` | `scripts/e2e/semantic_media_e2e_report.py` |
| `E2E_BETA_PORT` | `scripts/e2e/semantic_media_e2e_report.py` |
| `E2E_FRONTEND_URL` | `scripts/e2e/semantic_media_e2e_report.py` |
| `E2E_HUB_PORT` | `scripts/e2e/semantic_media_e2e_report.py` |
| `E2E_LMSTUDIO_URL` | `client_surfaces/operator_tui/_ai_snake_config_helpers.py` |
| `E2E_PORT` | `scripts/e2e/semantic_media_e2e_report.py` |
| `E2E_VOICE_RUNTIME_PORT` | `scripts/e2e/semantic_media_e2e_report.py` |
| `EDITOR` | `agent/sgpt/utils.py`, `client_surfaces/operator_tui/plugins.py` |
| `ELEMENT` | `scripts/firefox_automation/_webdriver.py` |
| `ENABLE_ADVANCED_COMMAND_ANALYSIS` | `agent/config.py` |
| `EVOLVER_ALLOWED_HOSTS` | `agent/config.py` |
| `EVOLVER_ANALYZE_PATH` | `agent/config.py` |
| `EVOLVER_BASE_URL` | `agent/config.py` |
| `EVOLVER_BEARER_TOKEN` | `agent/config.py` |
| `EVOLVER_CONNECT_TIMEOUT_SECONDS` | `agent/config.py` |
| `EVOLVER_DEFAULT` | `agent/config.py` |
| `EVOLVER_ENABLED` | `agent/config.py` |
| `EVOLVER_FORCE_ANALYZE_ONLY` | `agent/config.py` |
| `EVOLVER_HEADERS` | `agent/config.py` |
| `EVOLVER_HEALTH_PATH` | `agent/config.py` |
| `EVOLVER_MAX_RESPONSE_BYTES` | `agent/config.py` |
| `EVOLVER_READ_TIMEOUT_SECONDS` | `agent/config.py` |
| `EVOLVER_RETRY_BACKOFF_SECONDS` | `agent/config.py` |
| `EVOLVER_RETRY_COUNT` | `agent/config.py` |
| `EVOLVER_TIMEOUT_SECONDS` | `agent/config.py` |
| `EVOLVER_VERSION` | `agent/config.py` |
| `FAIL_SECURE_LLM_ANALYSIS` | `agent/config.py` |
| `FEATURE_ANGULAR_KANBAN_ENABLED` | `agent/config.py` |
| `FEATURE_ANGULAR_MODEL_DASHBOARD_ENABLED` | `agent/config.py` |
| `FEATURE_HISTORY_ENABLED` | `agent/config.py` |
| `FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED` | `agent/config.py` |
| `FEATURE_LOAD_BALANCING_ENABLED` | `agent/config.py` |
| `FEATURE_MODEL_CATALOG_V2_ENABLED` | `agent/config.py` |
| `FEATURE_MODEL_ROUTING_EDITOR_ENABLED` | `agent/config.py` |
| `FEATURE_ROBUST_HTTP_ENABLED` | `agent/config.py` |
| `FEATURE_TUI_KANBAN_ENABLED` | `agent/config.py` |
| `FEATURE_TUI_MODEL_MENU_ENABLED` | `agent/config.py` |
| `FLASK_DEBUG` | `agent/lifecycle.py` |
| `GAME_DRAGON_CONFIG` | `agent/routes/game_dragon.py` |
| `GENERATIVE_CORRECTOR_ALLOWED_HUB_ORIGINS` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_DEVICE` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_ENGINE` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_EXTERNAL_PROVIDERS` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_HOST` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_INTERNAL_TOKEN` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_MODEL_CATALOG` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_CORRECTOR_MODEL_ROOT` | `worker/runtime/generative_corrector_app.py` |
| `GENERATIVE_JUDGE_ALLOWED_HUB_ORIGINS` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_DEVICE` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_ENGINE` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_HOST` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_INTERNAL_TOKEN` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_LOOPBACK_ENDPOINT` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_LOOPBACK_TOKEN` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_MODEL_PATH` | `worker/runtime/generative_judge_app.py` |
| `GENERATIVE_JUDGE_MODEL_ROOT` | `worker/runtime/generative_judge_app.py` |
| `GITHUB_EVENT_PATH` | `scripts/update_readme_release.py` |
| `GITHUB_REPOSITORY` | `scripts/update_readme_release.py` |
| `GITHUB_RUN_ID` | `scripts/run-workflow-runtime-release-gate.py` |
| `GITHUB_SHA` | `scripts/benchmark/qdrant_vector_store.py`, `scripts/run-workflow-runtime-release-gate.py` |
| `GITHUB_TOKEN` | `scripts/update_readme_release.py` |
| `GOAL_WORKFLOW_ENABLED` | `agent/config.py` |
| `HERMES_API_KEY_ENV` | `worker/core/hermes_default_config.py` |
| `HERMES_BASE_URL` | `worker/core/hermes_default_config.py` |
| `HERMES_MAX_CONTEXT_CHARS` | `worker/core/hermes_default_config.py` |
| `HERMES_MAX_OUTPUT_TOKENS` | `worker/core/hermes_default_config.py` |
| `HERMES_MAX_RETRIES` | `worker/core/hermes_default_config.py` |
| `HERMES_MODEL` | `worker/core/hermes_default_config.py` |
| `HERMES_TEMPERATURE` | `worker/core/hermes_default_config.py` |
| `HERMES_TIMEOUT_SECONDS` | `worker/core/hermes_default_config.py` |
| `HF_HUB_OFFLINE` | `worker/decision/jeff_runtime.py` |
| `HRM_EXPERIMENTS_ENABLED` | `agent/config.py` |
| `HTTP_AUTHORIZATION` | `agent/ws_terminal.py`, `agent/ws_voice.py` |
| `HTTP_TIMEOUT` | `agent/config.py` |
| `HUB_BASE_URL` | `scripts/bench_models_live.py`, `scripts/firefox_automation/_config.py`, `scripts/run_live_click_dual_benchmark.py` (+1) |
| `HUB_CAN_BE_WORKER` | `agent/config.py` |
| `HUB_GIT_BUDGETS` | `agent/bootstrap/source_control_api.py`, `agent/sources/hub_git_persistent_composition.py` |
| `HUB_GIT_GITHUB_APP_ID` | `agent/services/hub_git_github_authorization_provider.py` |
| `HUB_GIT_GITHUB_APP_PRIVATE_KEY_REF` | `agent/services/hub_git_github_authorization_provider.py` |
| `HUB_REGISTRATION_PUBLIC_BASE_URL` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_FROM` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_HOST` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_PASSWORD` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_PORT` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_STARTTLS` | `agent/config.py` |
| `HUB_REGISTRATION_SMTP_USERNAME` | `agent/config.py` |
| `HUB_REGISTRATION_VERIFICATION_TTL_SECONDS` | `agent/config.py` |
| `HUB_SELF_REGISTRATION_ENABLED` | `agent/config.py` |
| `HUB_SELF_REGISTRATION_REQUIRE_ADMIN_APPROVAL` | `agent/config.py` |
| `HUB_URL` | `agent/config.py`, `worker/hrm_experiments/task_handler.py`, `worker/mail_task_composition.py` (+1) |
| `INITIAL_ADMIN_PASSWORD` | `agent/cli/api_client.py`, `agent/cli_goals_support.py`, `agent/config.py` (+10) |
| `INITIAL_ADMIN_USER` | `agent/cli/api_client.py`, `agent/cli_goals_support.py`, `agent/config.py` (+7) |
| `JEFF_API_KEY_FILE` | `worker/decision/jeff_runtime.py` |
| `JEFF_DEVICE` | `worker/decision/jeff_runtime.py` |
| `JEFF_ENCODER_TOKEN_LIMIT` | `worker/decision/jeff_runtime.py` |
| `JEFF_HUB_PUBLIC_KEY_FILE` | `worker/decision/jeff_runtime.py` |
| `JEFF_MODEL` | `worker/decision/jeff_runtime.py` |
| `JEFF_MODEL_MANIFEST` | `worker/decision/jeff_runtime.py` |
| `JEFF_REQUIRE_ASSIGNMENT` | `worker/decision/jeff_runtime.py` |
| `KANBAN_API_ENABLED` | `agent/services/kanban_feature_flags.py` |
| `KANBAN_FEATURE_FLAGS` | `agent/services/kanban_feature_flags.py` |
| `KANBAN_WRITE_ENABLED` | `agent/services/kanban_feature_flags.py` |
| `KITTY_WINDOW_ID` | `client_surfaces/operator_tui/_interactive_visual.py`, `client_surfaces/operator_tui/capabilities.py`, `client_surfaces/operator_tui/logo_renderer/detect.py` (+3) |
| `KUBERNETES_SERVICE_HOST` | `agent/cli/init_wizard.py` |
| `LAYA_API_KEY` | `agent/services/decision_providers/laya.py` |
| `LAYA_API_KEY_FILE` | `agent/services/decision_providers/laya.py` |
| `LIVEKIT_KEYS` | `scripts/run_semantic_media_supply_chain_gate.py` |
| `LLM_INVOKE_TIMEOUT_SECONDS` | `agent/config.py` |
| `LMSTUDIO_API_MODE` | `agent/config.py` |
| `LMSTUDIO_MAX_CONTEXT_TOKENS` | `agent/config.py` |
| `LMSTUDIO_MODEL_CONTEXTS` | `agent/config.py` |
| `LMSTUDIO_THINKING_MODE` | `agent/llm_strategies/lmstudio.py` |
| `LMSTUDIO_URL` | `agent/cli/commands/config.py`, `agent/cli/commands/llm.py`, `agent/config.py` (+2) |
| `LOCAL_RANK` | `worker/training/research/distributed.py` |
| `LOG_LEVEL` | `agent/config.py` |
| `MAX_PROMPT_TOKENS` | `agent/config.py` |
| `MEET_ASR_SMOKE_PROFILE` | `worker/meet_media/asr_smoke.py` |
| `MEET_ASSIST_MAX_CHARS` | `worker/meet_media/assist.py` |
| `MEET_AVATAR_PNG` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_ASSIGNED_MODEL_PROFILE` | `agent/services/meet_companion_model_policy.py` |
| `MEET_COMPANION_AVATAR_REFRESH` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_LOG` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_NATIVE_SEQUENCE` | `worker/meet_media/companion_task_runtime.py` |
| `MEET_COMPANION_PROJECT` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_PUBLIC_ROOM_CHECK` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_RENEW` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_ROOM_JSON` | `worker/meet_media/companion.py` |
| `MEET_COMPANION_STOP` | `worker/meet_media/companion.py` |
| `MEET_DIALOG_DIAGNOSTICS_ENABLED` | `worker/meet_media/dialog_runtime.py` |
| `MEET_DIALOG_ENABLED` | `worker/meet_media/server.py` |
| `MEET_HUB_ASSIST_TOOL_URL` | `worker/meet_media/assist.py` |
| `MEET_HUB_ASSIST_URL` | `worker/meet_media/assist.py` |
| `MEET_HUB_COMPANION_TASK_URL` | `worker/meet_media/companion_task_runtime.py` |
| `MEET_HUB_COMPANION_URL` | `worker/meet_media/companion.py` |
| `MEET_HUB_DIALOG_URL` | `worker/meet_media/dialog_client.py` |
| `MEET_HUB_LEASE_URL` | `worker/meet_media/lease_guard.py` |
| `MEET_HUB_PUBLIC_ROOM_URL` | `worker/meet_media/companion.py` |
| `MEET_LLM_BACKEND` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_DIGEST` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_MODEL` | `worker/meet_media/companion.py`, `worker/meet_media/companion_assigned_dialog.py`, `worker/meet_media/llm_backends.py` (+2) |
| `MEET_LLM_NUM_CTX` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_NUM_GPU` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_OPENAI_API_KEY` | `worker/meet_media/openai_http.py` |
| `MEET_LLM_OPENAI_BASE_URL` | `worker/meet_media/openai_http.py` |
| `MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_OPENAI_REASONING_EFFORT` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_TEMPERATURE` | `worker/meet_media/llm_backends.py` |
| `MEET_LLM_TOOLSET` | `worker/meet_media/llm_tools.py` |
| `MEET_LLM_TOOL_CALLS` | `worker/meet_media/llm_tools.py` |
| `MEET_LLM_TOOL_RESULT_CHARS` | `worker/meet_media/llm_tools.py` |
| `MEET_LLM_TOOL_ROUNDS` | `worker/meet_media/llm_tools.py` |
| `MEET_OLLAMA_URL` | `worker/meet_media/ollama_http.py` |
| `MEET_ORIGIN` | `worker/meet_media/companion.py` |
| `MEET_PIPER_MODEL` | `worker/meet_media/piper_assets.py` |
| `MEET_PIPER_MODELS_DIR` | `worker/meet_media/piper_assets.py` |
| `MEET_PUBLIC_ROOM_TITLE` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_CLIENT_ID` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_CLIENT_SECRET` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_PASSWORD` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_SCOPE` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_SERVER_URL` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_TIMEOUT` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_TOKEN_URL` | `agent/services/meet_public_room_service.py` |
| `MEET_ROOM_USERNAME` | `agent/services/meet_public_room_service.py` |
| `MEET_WORKER_KEY_FILE` | `worker/meet_media/assist.py`, `worker/meet_media/companion.py`, `worker/meet_media/dialog_client.py` (+2) |
| `MEMORY_VAULT_EXPORT_ROOT` | `agent/services/memory_vault_export_service.py` |
| `MFA_ENCRYPTION_KEY` | `agent/config.py` |
| `MISTRAL_API_KEY` | `agent/cli_backends/simple_command_runners.py`, `agent/config.py` |
| `MISTRAL_CODE_DEFAULT_MODEL` | `agent/config.py` |
| `MISTRAL_CODE_PATH` | `agent/config.py` |
| `MOCK_URL` | `agent/config.py` |
| `MODEL_INTELLIGENCE_ARTIFACT_ROOT` | `agent/routes/model_intelligence.py` |
| `MODEL_INTELLIGENCE_CORRELATION_SECRET` | `agent/routes/model_intelligence_events.py` |
| `MODEL_INTELLIGENCE_MAX_GRAPH_EDGES` | `agent/routes/model_intelligence.py` |
| `MODEL_INTELLIGENCE_MAX_GRAPH_NODES` | `agent/routes/model_intelligence.py` |
| `MODEL_OVERRIDE_ID` | `agent/services/model_profile_resolver.py` |
| `MODEL_PROFILE` | `agent/services/model_profile_resolver.py` |
| `MODEL_PROFILES_PATH` | `agent/routes/config/cognitive_styles.py`, `agent/routes/config/config_update_policy_normalizers.py`, `agent/routes/config/providers.py` (+3) |
| `MODEL_ROUTING_PATH` | `agent/services/model_invocation_profile_resolver_loader.py`, `agent/services/workflow_provider_selection_service.py`, `scripts/ananta_backup/compose.py` |
| `MODEL_ROUTING_RELEASE_EVIDENCE_PATH` | `agent/services/model_routing_legacy_migration_service.py` |
| `NAME` | `scripts/build_geomap_assets.py` |
| `NAME_LONG` | `scripts/build_geomap_assets.py` |
| `NATIVE_SSH_ENABLED` | `agent/config.py` |
| `NCPU` | `agent/services/docker_engine_service.py` |
| `NO_COLOR` | `agent/cli/banner.py`, `agent/cli/logo_assets.py`, `client_surfaces/operator_tui/animation3d/capabilities.py` (+3) |
| `NVIDIA_VISIBLE_DEVICES` | `worker/training/runtime.py` |
| `OIDC_ALLOWED_ALGORITHMS` | `agent/config.py` |
| `OIDC_AUDIENCE` | `agent/config.py` |
| `OIDC_CLIENT_ID` | `agent/config.py` |
| `OIDC_ENABLED` | `agent/config.py` |
| `OIDC_ISSUER_URL` | `agent/config.py` |
| `OIDC_JWKS_CACHE_SECONDS` | `agent/config.py` |
| `OIDC_JWKS_URL` | `agent/config.py` |
| `OIDC_REGISTRATION_ALLOWED` | `agent/config.py` |
| `OLLAMA_URL` | `agent/cli/commands/llm.py`, `agent/config.py` |
| `OPENAI_API_BASE` | `client_surfaces/operator_tui/tutorial_ai_engine.py`, `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `OPENAI_API_KEY` | `agent/cli_backends/opencode.py`, `agent/cli_backends/sgpt.py`, `agent/config.py` (+11) |
| `OPENAI_BASE_URL` | `client_surfaces/operator_tui/tutorial_ai_engine.py`, `client_surfaces/operator_tui/tutorial_ai_retrieval.py` |
| `OPENAI_FUNCTIONS_PATH` | `agent/sgpt/config.py`, `agent/sgpt/function.py`, `agent/sgpt/llm_functions/init_functions.py` |
| `OPENAI_URL` | `agent/config.py` |
| `OPENAI_USE_FUNCTIONS` | `agent/sgpt/app.py`, `agent/sgpt/config.py` |
| `OPENCODE_DEFAULT_MODEL` | `agent/config.py`, `client_surfaces/operator_tui/_ai_snake_config_helpers.py` |
| `OPENCODE_PATH` | `agent/config.py` |
| `OPENROUTER_API_KEY` | `agent/routes/config/providers.py` |
| `ORGANIZATION_COMPILE_TOKEN_TTL_SECONDS` | `agent/routes/organization_route_support.py` |
| `ORGANIZATION_LIMIT_PROFILE_REF` | `agent/routes/organization_bundles.py` |
| `OS_NAME` | `agent/sgpt/config.py`, `agent/sgpt/role.py` |
| `PATH` | `agent/cli_backends/pi_provider.py`, `worker/runtime/native_graph/pi_process.py`, `worker/shell/command_executor.py` (+1) |
| `PERSISTED_PLANS_ENABLED` | `agent/config.py` |
| `PERSONA_GENERATION_HUB_LEASE_URL` | `worker/meet_media/persona_generation_server.py` |
| `PERSONA_GENERATION_WORKER_KEY_FILE` | `worker/meet_media/persona_generation_server.py` |
| `PERSONA_IMAGE_HUB_LEASE_URL` | `worker/meet_media/persona_server.py` |
| `PERSONA_IMAGE_WORKER_KEY_FILE` | `worker/meet_media/persona_server.py` |
| `PERSONA_VIDEO_HUB_LEASE_URL` | `worker/meet_media/persona_video_server.py` |
| `PERSONA_VIDEO_WORKER_KEY_FILE` | `worker/meet_media/persona_video_server.py` |
| `PERSONA_VOICE_HUB_LEASE_URL` | `worker/meet_media/persona_voice_server.py` |
| `PERSONA_VOICE_WORKER_KEY_FILE` | `worker/meet_media/persona_voice_server.py` |
| `PI_CODING_AGENT_DIR` | `worker/runtime/native_graph/pi_process.py` |
| `PLAYWRIGHT_BROWSERS_PATH` | `scripts/run_spreadsheet_studio_release_gate.py` |
| `PORT` | `agent/config.py`, `worker/retrieval/vector_index_job_handler.py` |
| `POSTGRES_DB` | `scripts/ananta_backup/compose.py` |
| `POSTGRES_USER` | `scripts/ananta_backup/compose.py` |
| `PRETTIFY_MARKDOWN` | `agent/sgpt/app.py`, `agent/sgpt/config.py` |
| `PROMPT_TRACE_ALLOWED_RAW_ACCESS_MODES` | `agent/config.py` |
| `PROMPT_TRACE_ENABLED` | `agent/config.py` |
| `PROMPT_TRACE_MAX_RAW_CHARS` | `agent/config.py` |
| `PROMPT_TRACE_REDACT_BY_DEFAULT` | `agent/config.py` |
| `PROMPT_TRACE_RETENTION_DAYS` | `agent/config.py` |
| `PROMPT_TRACE_STORE_RAW_PROMPTS` | `agent/config.py` |
| `PROMPT_TRACE_STORE_RAW_RESPONSES` | `agent/config.py` |
| `PROVIDER_URLS` | `agent/ai_agent.py`, `agent/bootstrap/local_runtime_capabilities.py`, `agent/cli_backends/helpers.py` (+15) |
| `PYTEST_CURRENT_TEST` | `agent/lifecycle.py`, `agent/routes/snakes_chat_reply_spawner.py`, `agent/services/autopilot/auto_planner_engine.py` (+4) |
| `PYTHONPATH` | `scripts/run_freecad_install_smoke.py` |
| `QUERY_STRING` | `agent/ws_terminal.py`, `agent/ws_voice.py` |
| `RAG_AGENTIC_MAX_COMMANDS` | `agent/config_rag_settings.py` |
| `RAG_AGENTIC_TIMEOUT_SECONDS` | `agent/config_rag_settings.py` |
| `RAG_COMPACT_BUDGET_TOKENS` | `agent/config_rag_settings.py` |
| `RAG_DATA_ROOTS` | `agent/config_rag_settings.py` |
| `RAG_DEFAULT_WINDOW_PROFILE` | `agent/config_rag_settings.py` |
| `RAG_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_FULL_BUDGET_TOKENS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_CATALOG_CHARS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_IMPORT_DEPTH` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_INITIAL_MAX_FILES` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_INITIAL_MIN_FILES` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_MAX_SEARCH_CALLS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_MAX_TOOL_CALLS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_SUMMARIZE_READS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_SUMMARY_CHARS` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_SYMBOL_EXPAND_MAX` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_TOOL_CALLS_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_ITERATIVE_TOOL_CHARS_PER_FILE` | `agent/config_rag_settings.py` |
| `RAG_MAX_CHUNKS` | `agent/config_rag_settings.py` |
| `RAG_MAX_CONTEXT_CHARS` | `agent/config_rag_settings.py` |
| `RAG_MAX_CONTEXT_TOKENS` | `agent/config_rag_settings.py` |
| `RAG_PATH_FOCUS_ALIASES` | `agent/config_rag_settings.py` |
| `RAG_PATH_FOCUS_ALIAS_ANCHOR_BOOST` | `agent/config_rag_settings.py` |
| `RAG_QUERY_NORMALIZE_LANG` | `agent/config_rag_settings.py` |
| `RAG_QUERY_NORMALIZE_MODE` | `agent/config_rag_settings.py` |
| `RAG_QUERY_TRANSLATION_DIRECTIONS` | `agent/config_rag_settings.py` |
| `RAG_REDACT_SENSITIVE` | `agent/config_rag_settings.py` |
| `RAG_REPO_ROOT` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR_DEFAULT` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR_DOCS` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_CODE_REPO` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_CODE_SEMANTIC` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_DEFAULT_REPO` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_DEFAULT_SEMANTIC` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_DOCS_REPO` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_DOCS_SEMANTIC` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_FS_AGENTIC` | `agent/config_rag_settings.py` |
| `RAG_ROUTE_QUOTA_FS_REPO` | `agent/config_rag_settings.py` |
| `RAG_SCAN_EXCLUDE_DIRS` | `agent/config_rag_settings.py` |
| `RAG_SEMANTIC_PERSIST_DIR` | `agent/config_rag_settings.py` |
| `RAG_SOURCE_ARTIFACT_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_SOURCE_OPEN_NOTEBOOK_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_SOURCE_REPO_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_SOURCE_TASK_MEMORY_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_SOURCE_WIKI_ENABLED` | `agent/config_rag_settings.py` |
| `RAG_STANDARD_BUDGET_TOKENS` | `agent/config_rag_settings.py` |
| `REDACTION_VISIBILITY` | `agent/config.py` |
| `REDIS_URL` | `agent/config.py` |
| `REDUCED_MOTION` | `client_surfaces/operator_tui/animation3d/capabilities.py` |
| `REGISTRATION_TOKEN` | `agent/auth_token_files.py`, `agent/config.py` |
| `REGISTRATION_TOKEN_FILE` | `agent/auth_token_files.py` |
| `REMOTE_ADDR` | `agent/ws_terminal.py` |
| `REQUEST_TIMEOUT` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `RESEARCH_SANDBOX_DOCKER_PATH` | `agent/config.py` |
| `RETRY_BACKOFF` | `agent/config.py` |
| `RETRY_COUNT` | `agent/config.py` |
| `ROLE` | `agent/auth.py`, `agent/bootstrap/agent_safety.py`, `agent/bootstrap/codecompass_layers.py` (+52) |
| `ROLE_STORAGE_PATH` | `agent/sgpt/config.py`, `agent/sgpt/role.py` |
| `RUN_SEMANTIC_MEDIA_LIVE_E2E` | `agent/bootstrap/routes.py` |
| `SECRETS_DIR` | `agent/config.py` |
| `SECRET_KEY` | `agent/bootstrap/spreadsheet_studio.py`, `agent/config.py`, `agent/routes/approvals.py` (+4) |
| `SECRET_KEY_FILE` | `agent/config.py` |
| `SEMANTIC_COMPUTE_FALLBACK_HEALTHY` | `agent/routes/semantic_media_contract_route_authority.py` |
| `SEMANTIC_COMPUTE_SECURITY_CONFIRMED` | `agent/routes/semantic_media_contract_route_authority.py`, `agent/services/semantic_compute_execution_service.py`, `scripts/e2e/semantic_sfu_hub_e2e.py` |
| `SFU_HUB_COMPOSITION_SECRET` | `agent/bootstrap/sfu_broadcast_final_composition.py` |
| `SGPT_CODE_THEME` | `agent/config.py` |
| `SGPT_DEFAULT_COLOR` | `agent/config.py` |
| `SGPT_DEFAULT_MODEL` | `agent/config.py` |
| `SGPT_EXECUTION_BACKEND` | `agent/config.py` |
| `SGPT_PRETTIFY_MARKDOWN` | `agent/config.py` |
| `SGPT_SHELL_INTERACTION` | `agent/config.py` |
| `SGPT_USE_LITELLM` | `agent/config.py` |
| `SHELL` | `agent/sgpt/role.py`, `agent/sgpt/utils.py` |
| `SHELL_INTERACTION` | `agent/sgpt/app.py`, `agent/sgpt/config.py` |
| `SHELL_NAME` | `agent/sgpt/config.py`, `agent/sgpt/role.py` |
| `SHELL_PATH` | `agent/config.py` |
| `SHELL_POOL_SIZE` | `agent/config.py` |
| `SHOW_FUNCTIONS_OUTPUT` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `SIXEL_SUPPORTED` | `client_surfaces/operator_tui/visual/capabilities/terminal_detector.py`, `scripts/operator_tui_markdown_mermaid_visible_smoke.py`, `scripts/operator_tui_terminal_graphics_smoke.py` |
| `SOURCE_CONTROL_HARNESS_TOKEN` | `scripts/source_control_control_center_harness.py` |
| `SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED` | `agent/bootstrap/source_control_bootstrap_settings.py` |
| `SOURCE_CONTROL_ROLLOUT_STAGE` | `agent/bootstrap/source_control_bootstrap_settings.py` |
| `SOURCE_CONTROL_SMOKE_TOKEN` | `scripts/source_control_container_smoke.py` |
| `SSH_AUDIT_ENABLED` | `agent/config.py` |
| `SSH_BROAD_PRINCIPALS_ALLOWED` | `agent/config.py` |
| `SSH_CA_BACKEND` | `agent/config.py` |
| `SSH_CA_STEP_CA_CA_FINGERPRINT` | `agent/config.py` |
| `SSH_CA_STEP_CA_PROVISIONER` | `agent/config.py` |
| `SSH_CA_STEP_CA_URL` | `agent/config.py` |
| `SSH_CERTIFICATE_VALIDITY_SECONDS_HUB` | `agent/config.py` |
| `SSH_CERTIFICATE_VALIDITY_SECONDS_WORKER` | `agent/config.py` |
| `SSH_ORIGINAL_COMMAND` | `agent/services/ssh_terminal_wrapper.py` |
| `SSH_TERMINAL_WRAPPER_REQUIRED` | `agent/config.py` |
| `SSL_CLIENT_CERT` | `agent/routes/webrtc_sfu_node_observations.py` |
| `SSL_CLIENT_VERIFY` | `agent/routes/webrtc_sfu_node_observations.py` |
| `STATS_HISTORY_PATH` | `agent/routes/system.py` |
| `STATS_HISTORY_SIZE` | `agent/config.py` |
| `SURFACE_RATE_LIMITS` | `agent/services/surface_rate_limit_policy.py` |
| `TASKS_PATH` | `agent/utils.py` |
| `TASKS_RETENTION_DAYS` | `agent/config.py` |
| `TASK_ENGINE_DETERMINISTIC_BYPASS_ENABLED` | `agent/config.py` |
| `TASK_ENGINE_ENABLED` | `agent/config.py` |
| `TASK_ENGINE_STRICT_UNKNOWN_TOOL_POLICY` | `agent/config.py` |
| `TERM` | `client_surfaces/operator_tui/_interactive_visual.py`, `client_surfaces/operator_tui/capabilities.py`, `client_surfaces/operator_tui/commands_visual.py` (+7) |
| `TERMINAL_ATTACH_TOKEN_TTL_SECONDS` | `agent/config.py` |
| `TERMINAL_FEATURE_ENABLED` | `agent/config.py`, `agent/config_defaults_env.py` |
| `TERMINAL_HUB_AS_WORKER_TARGET_ENABLED` | `agent/config.py` |
| `TERMINAL_HUB_TARGET_ENABLED` | `agent/config.py` |
| `TERMINAL_IDLE_TIMEOUT_SECONDS` | `agent/config.py` |
| `TERMINAL_MAX_LIFETIME_SECONDS` | `agent/config.py` |
| `TERMINAL_OIDC_AUDIENCE` | `agent/config.py` |
| `TERMINAL_OIDC_BROWSER_ISSUER` | `agent/config.py` |
| `TERMINAL_OIDC_CLIENT_ID` | `agent/config.py` |
| `TERMINAL_OIDC_CLIENT_SECRET` | `agent/config.py` |
| `TERMINAL_OIDC_ENABLED` | `agent/config.py` |
| `TERMINAL_OIDC_FRONTEND_REDIRECT` | `agent/config.py` |
| `TERMINAL_OIDC_ISSUER` | `agent/config.py` |
| `TERMINAL_POLICY_VERSION` | `agent/config.py` |
| `TERMINAL_RECORDING_ENABLED_LOCAL_DEV` | `agent/config.py` |
| `TERMINAL_RECORDING_ENABLED_REMOTE` | `agent/config.py` |
| `TERMINAL_WORKER_TARGET_ENABLED` | `agent/config.py` |
| `TERM_PROGRAM` | `client_surfaces/operator_tui/capabilities.py`, `client_surfaces/operator_tui/commands_visual.py`, `client_surfaces/operator_tui/logo_renderer/animated_header.py` (+4) |
| `TESTING` | `agent/services/planning_timeout_service.py` |
| `TOKEN_PATH` | `agent/ai_agent.py` |
| `TOKEN_ROTATION_DAYS` | `agent/config.py` |
| `TRANSFORMERS_OFFLINE` | `worker/decision/jeff_runtime.py` |
| `TUI_ALLOW_ENVIRONMENT_EDITOR` | `agent/config.py`, `agent/services/tui_tool_registry.py` |
| `TUI_DEFAULT_EDITOR` | `agent/config.py`, `agent/services/tui_tool_registry.py` |
| `TURN_OBSERVATION_SCHEMA_PATH` | `agent/bootstrap/sfu_broadcast_final_composition.py` |
| `TURN_OBSERVER_TRUST_POLICY_PATH` | `agent/bootstrap/sfu_broadcast_final_composition.py` |
| `URL` | `agent/services/docker_compose_service.py` |
| `USE_LITELLM` | `agent/sgpt/config.py`, `agent/sgpt/handlers/handler.py` |
| `VAULT_MOUNT_POINT` | `agent/common/vault_source.py`, `agent/config.py` |
| `VAULT_PATH` | `agent/common/vault_source.py`, `agent/config.py` |
| `VAULT_TOKEN` | `agent/common/vault_source.py`, `agent/config.py` |
| `VAULT_URL` | `agent/common/vault_source.py`, `agent/config.py` |
| `VIRTUAL_ENV` | `agent/cli/update.py` |
| `VISION_DECISION_API_KEY` | `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_API_KEY_FILE` | `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_ENABLED` | `agent/routes/vision_decision.py`, `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_ESCALATION_CHAT` | `agent/services/vision_decision_escalation_executor.py` |
| `VISION_DECISION_ESCALATION_TTL_SECONDS` | `agent/services/vision_decision_escalation_executor.py` |
| `VISION_DECISION_MODELS` | `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_REQUIRE_CACHE_ISOLATION` | `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_SCHEMAS` | `agent/services/vision_decision_provider.py` |
| `VISION_DECISION_URL` | `agent/services/vision_decision_provider.py` |
| `VISUAL` | `client_surfaces/operator_tui/plugins.py` |
| `VISUAL_PROCESS_AI_PATCHES_ENABLED` | `agent/config.py` |
| `VISUAL_PROCESS_AI_PATCH_AUTO_APPROVAL_ENABLED` | `agent/config.py` |
| `VISUAL_PROCESS_ASSISTANT_CHAT_ENABLED` | `agent/config.py` |
| `VISUAL_PROCESS_ASSISTANT_MODEL_TIMEOUT_MS` | `agent/config.py` |
| `VISUAL_PROCESS_ASSISTANT_RETRIEVAL_TIMEOUT_MS` | `agent/config.py` |
| `VISUAL_PROCESS_HOVER_HELP_ENABLED` | `agent/config.py` |
| `VISUAL_PROCESS_REGISTRY_INSPECTOR_ENABLED` | `agent/config.py` |
| `VOICE_AUDIO_DECISION_CONFIRM_TTL_SECONDS` | `agent/services/audio_decision_command_executor.py` |
| `VOICE_DELETION_LEDGER_PATH` | `agent/repositories/voice_deletion_ledger.py` |
| `VOICE_DIRECT_CLIENT_ACCESS` | `agent/config.py` |
| `VOICE_ENABLE_STREAMING` | `agent/config.py` |
| `VOICE_FALLBACK_MODEL` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_HUB_ORIGIN` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_MAX_RESPONSE_BYTES` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_MODELS` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_PROVIDERS` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_TIMEOUT_MS` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_WORKER_ALLOWED_ENDPOINTS` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_WORKER_TOKEN` | `agent/config.py` |
| `VOICE_GENERATIVE_CORRECTOR_WORKER_URL` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_ALLOWED_ENDPOINTS` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_ENDPOINT` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_HUB_ORIGIN` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_MAX_RESPONSE_BYTES` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_TIMEOUT_MS` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_WORKER_ALLOWED_ENDPOINTS` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_WORKER_TOKEN` | `agent/config.py` |
| `VOICE_GENERATIVE_JUDGE_WORKER_URL` | `agent/config.py` |
| `VOICE_HUB_MAX_CONCURRENT_REQUESTS` | `agent/routes/voice_request_support.py` |
| `VOICE_HUB_MAX_INFLIGHT_AUDIO_SECONDS` | `agent/routes/voice_request_support.py` |
| `VOICE_IDEMPOTENCY_TTL_SECONDS` | `agent/services/voice_idempotency_service.py` |
| `VOICE_INTERNAL_SERVICE_TOKEN` | `agent/config.py`, `agent/services/voice_provider.py` |
| `VOICE_MAX_AUDIO_DURATION_SEC` | `agent/routes/voice_request_support.py` |
| `VOICE_MAX_AUDIO_MB` | `agent/config.py`, `agent/routes/config/providers.py`, `agent/routes/voice_request_support.py` |
| `VOICE_MAX_QUEUE_DEPTH` | `agent/routes/voice_request_support.py` |
| `VOICE_MODEL` | `agent/config.py` |
| `VOICE_PERSONALIZATION_ENCRYPTION_KEY` | `agent/services/voice_sensitive_text_codec.py` |
| `VOICE_PROVIDER` | `agent/config.py`, `agent/routes/config/providers.py`, `agent/services/voice_provider.py` |
| `VOICE_RUNTIME_ALLOWED_ORIGINS` | `agent/config.py`, `agent/services/voice_provider.py` |
| `VOICE_RUNTIME_DEVICE` | `agent/config.py` |
| `VOICE_RUNTIME_MAX_RESPONSE_BYTES` | `agent/config.py`, `agent/services/voice_provider.py` |
| `VOICE_RUNTIME_MODEL_PATH` | `agent/config.py` |
| `VOICE_RUNTIME_URL` | `agent/config.py`, `agent/routes/config/providers.py`, `agent/services/voice_provider.py` |
| `VOICE_STORE_AUDIO` | `agent/config.py` |
| `VOICE_TIMEOUT_SEC` | `agent/config.py`, `agent/services/voice_provider.py` |
| `WAYLAND_DISPLAY` | `client_surfaces/operator_tui/windowing/backends/wslg_webview_backend.py`, `worker/meet_media/browser_source_probe.py`, `worker/meet_media/publication_smoke.py` |
| `WERKZEUG_RUN_MAIN` | `agent/lifecycle.py` |
| `WEZTERM_EXECUTABLE` | `client_surfaces/operator_tui/logo_renderer/detect.py` |
| `WINDIR` | `agent/sgpt/utils.py` |
| `WORKER_DEFAULT_EXECUTION_PROFILE` | `agent/config.py` |
| `WORKER_TYPE` | `worker/core/hermes_default_config.py` |
| `WORKFLOW_CALLBACK_SECRET` | `agent/routes/integrations_workflows.py` |
| `WORKFLOW_KNOWN_CORRELATIONS` | `agent/routes/integrations_workflows.py` |
| `WORLD_SIZE` | `worker/training/research/distributed.py` |
| `WSL_DISTRO_NAME` | `client_surfaces/operator_tui/commands_visual.py`, `client_surfaces/operator_tui/visual/markdown/render_policy.py` |
| `WT_SESSION` | `client_surfaces/operator_tui/logo_renderer/detect.py`, `client_surfaces/operator_tui/mouse.py`, `client_surfaces/operator_tui/visual/capabilities/terminal_detector.py` (+1) |
| `XDG_CONFIG_HOME` | `agent/services/tui_tool_registry.py`, `client_surfaces/operator_tui/device_keys.py`, `client_surfaces/operator_tui/share_menu.py` |

Gesamt: 1227 im Code gelesene Env-Variablen.
