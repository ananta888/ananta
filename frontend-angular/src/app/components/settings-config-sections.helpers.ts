import {
  normalizeArtifactFlowConfigValue, normalizeContextBundlePolicyConfigValue,
  normalizeHubCopilotConfigValue, normalizeModelOverrideMapValue,
  normalizeOpencodeRuntimeConfigValue, normalizeOpenAICompatibleBaseUrlValue,
  normalizeResearchBackendConfigValue, normalizeWorkerRuntimeConfigValue,
} from './settings-config.helpers';

/**
 * Pure normalization of the hub config sections edited on the settings page.
 * Shared by the load path (hub -> form) and the save path (form -> hub) so both
 * apply the same defaults.
 */

export const SETTINGS_POLICY_DEFAULTS = {
  routing_fallback_policy: { enabled: true, allow_static_providers: true, allow_local_backends: true, allow_remote_hubs: true, allow_stateful_cli: true, allow_stateless_generation: true, unavailable_action: 'mark_unavailable' },
  execution_fallback_policy: { allow_hub_worker_fallback: true, escalate_on_fallback_block: true, fallback_block_status: 'blocked', worker_404_hub_fallback_enabled: true, worker_task_sync_from_hub_enabled: true },
  planning: { default_strategy: 'auto' },
  planning_policy: { delegated_planning_enabled: false, require_review: true, max_nodes: 8, max_depth: 8, timeout_seconds: 600, parallel_goal_planning_max_concurrency: 1 },
  goal_plan_limits: { max_plan_nodes: 8, max_plan_depth: 8 },
  proposal_budget: { max_total_seconds: 90, max_llm_calls: 2, max_strategy_attempts: 2, allow_parallel_strategy_race: false },
  ananta_worker_tool_loop: { enabled: false, max_iterations: 6, max_tool_calls: 12, max_tool_result_chars: 8000 },
  ananta_worker_workspace_mutation: { enabled: false, mutation_mode: 'read_only', max_diff_chars: 12000, max_write_file_bytes: 262144 },
  hub_direct_execution: { enabled: false, direct_before_worker: true, fallback_to_worker: true, require_policy_gate: true, confidence_threshold: 0.8 },
  git_workspace: { enabled: false, remote_url: '', branch_strategy: 'goal', merge_strategy: 'squash', auto_commit: false },
  terminal_policy: { enabled: false, allow_read: false, allow_interactive: false, require_authenticated: false, require_admin: true, require_admin_for_interactive: true, max_session_seconds: 1800, idle_timeout_seconds: 300 },
  evolution: { enabled: true, analyze_only: true, validate_allowed: true, apply_allowed: false, auto_triggers_enabled: false, manual_triggers_enabled: true, require_review_before_apply: true },
  memory_tree: { enabled: false, mode: 'safe_readonly', auto_ingest_knowledge_index: false, auto_ingest_result_memory: false, llm_summary_enabled: false },
  result_memory_policy: { enabled: true, create_followup_artifact: true, retrieval_document_max_chars: 2200, raw_history_max_chars: 12000, archive_raw_output: false },
  tool_output_compaction: { enabled: true, fail_open: true, builtin_rules_enabled: true, max_input_chars_for_compaction: 4000, max_output_chars: 2000 },
  propose_policy: { context_compaction_enabled: true, context_compaction_required: false },
  workspace_context_policy: { scope_mode: 'full', max_files: 200 },
  shell_command_policy: { enabled: true, allow_complex_shell_mode: false },
  execution_risk_policy: { enabled: true, default_action: 'deny' },
  review_policy: { enabled: true },
  remote_federation_policy: { enabled: true, max_hops: 3, allow_artifact_access: false, allow_file_access: false },
  hint_routing: { enabled: false, mode: 'compatibility' },
} as const satisfies Record<string, Record<string, any>>;

export function normalizeObjValue(raw: any, defaults: Record<string, any>): any {
  if (!raw || typeof raw !== 'object') return { ...defaults };
  return { ...defaults, ...raw };
}

export function normalizeSgptRoutingValue(raw: any): any {
  const defaults = {
    default_backend: 'ananta-worker',
    task_kind_backend: { coding: 'ananta-worker', analysis: 'ananta-worker', doc: 'ananta-worker', ops: 'ananta-worker', research: 'deerflow' },
  };
  if (!raw || typeof raw !== 'object') return defaults;
  return { ...defaults, ...raw, task_kind_backend: { ...defaults.task_kind_backend, ...(raw.task_kind_backend || {}) } };
}

export function normalizeApprovalLifecycleValue(raw: any): any {
  const defaults = { enabled: false, grant_one_shot: true, default_ttl_seconds: 3600, goal_pre_approvals: { enabled: false, ttl_seconds: 7200 } };
  if (!raw || typeof raw !== 'object') return defaults;
  return { ...defaults, ...raw, goal_pre_approvals: { ...defaults.goal_pre_approvals, ...(raw.goal_pre_approvals || {}) } };
}

export function normalizeMutationGateValue(raw: any): any {
  if (!raw || typeof raw !== 'object') return { enabled: true, global_deny_mutations: false };
  return { enabled: raw.enabled !== false, global_deny_mutations: !!raw.global_deny_mutations };
}

export function normalizeLocalAiValue(raw: any): any {
  const defaults = { enabled: false, provider: 'ollama', base_url: 'http://localhost:11434', model: '', api_key: '' };
  if (!raw || typeof raw !== 'object') return defaults;
  return { ...defaults, ...raw };
}

export function normalizeAutopilotSecurityPoliciesValue(raw: any): any {
  const defaults = {
    allow_file_write: false, allow_shell_exec: false, allow_network_access: false,
    allow_tool_use: true, allow_memory_write: false, max_auto_tasks: 10
  };
  if (!raw || typeof raw !== 'object') return defaults;
  return { ...defaults, ...raw };
}

export function normalizeKnowledgeContextValue(raw: any): any {
  const defaults = { enabled: false, max_chunks: 5, min_score: 0.6, inject_into_planning: true, inject_into_execution: true };
  if (!raw || typeof raw !== 'object') return defaults;
  return { ...defaults, ...raw };
}

/** Values whose normalization differs between the load and the save path. */
export interface SettingsSectionOverrides {
  context_window: any;
  role_model_overrides: Record<string, string>;
  template_model_overrides: Record<string, string>;
}

/**
 * Normalizes every structured config section the settings page edits.
 * Key order is part of the contract (it is the order the hub receives on save).
 */
export function buildNormalizedSettingsSections(cfg: any, overrides: SettingsSectionOverrides): Record<string, any> {
  const d = SETTINGS_POLICY_DEFAULTS;
  return {
    agent_offline_timeout: Number(cfg?.agent_offline_timeout ?? 30),
    http_timeout: Number(cfg?.http_timeout ?? 30),
    command_timeout: Number(cfg?.command_timeout ?? 120),
    hub_copilot: normalizeHubCopilotConfigValue(cfg?.hub_copilot),
    context_bundle_policy: normalizeContextBundlePolicyConfigValue(cfg?.context_bundle_policy),
    context_window: overrides.context_window,
    artifact_flow: normalizeArtifactFlowConfigValue(cfg?.artifact_flow),
    opencode_runtime: normalizeOpencodeRuntimeConfigValue(cfg?.opencode_runtime),
    worker_runtime: normalizeWorkerRuntimeConfigValue(cfg?.worker_runtime),
    role_model_overrides: overrides.role_model_overrides,
    template_model_overrides: overrides.template_model_overrides,
    task_kind_model_overrides: normalizeModelOverrideMapValue(cfg?.task_kind_model_overrides),
    research_backend: normalizeResearchBackendConfigValue(cfg?.research_backend),
    sgpt_routing: normalizeSgptRoutingValue(cfg?.sgpt_routing),
    approval_lifecycle: normalizeApprovalLifecycleValue(cfg?.approval_lifecycle),
    mutation_gate: normalizeMutationGateValue(cfg?.mutation_gate),
    adaptive_model_routing_enabled: cfg?.adaptive_model_routing_enabled !== false,
    adaptive_model_routing_min_samples: Number(cfg?.adaptive_model_routing_min_samples ?? 3),
    adaptive_model_routing_top_k: Number(cfg?.adaptive_model_routing_top_k ?? 3),
    routing_fallback_policy: normalizeObjValue(cfg?.routing_fallback_policy, d.routing_fallback_policy),
    execution_fallback_policy: normalizeObjValue(cfg?.execution_fallback_policy, d.execution_fallback_policy),
    autopilot_security_policies: normalizeAutopilotSecurityPoliciesValue(cfg?.autopilot_security_policies),
    planning: normalizeObjValue(cfg?.planning, d.planning),
    planning_policy: normalizeObjValue(cfg?.planning_policy, d.planning_policy),
    goal_plan_limits: normalizeObjValue(cfg?.goal_plan_limits, d.goal_plan_limits),
    task_propose_timeout_seconds: Number(cfg?.task_propose_timeout_seconds ?? 300),
    proposal_budget: normalizeObjValue(cfg?.proposal_budget, d.proposal_budget),
    ananta_worker_tool_loop: normalizeObjValue(cfg?.ananta_worker_tool_loop, d.ananta_worker_tool_loop),
    ananta_worker_workspace_mutation: normalizeObjValue(cfg?.ananta_worker_workspace_mutation, d.ananta_worker_workspace_mutation),
    hub_direct_execution: normalizeObjValue(cfg?.hub_direct_execution, d.hub_direct_execution),
    git_workspace: normalizeObjValue(cfg?.git_workspace, d.git_workspace),
    terminal_policy: normalizeObjValue(cfg?.terminal_policy, d.terminal_policy),
    evolution: normalizeObjValue(cfg?.evolution, d.evolution),
    local_ai: normalizeLocalAiValue(cfg?.local_ai),
    memory_tree: normalizeObjValue(cfg?.memory_tree, d.memory_tree),
    result_memory_policy: normalizeObjValue(cfg?.result_memory_policy, d.result_memory_policy),
    tool_output_compaction: normalizeObjValue(cfg?.tool_output_compaction, d.tool_output_compaction),
    propose_policy: normalizeObjValue(cfg?.propose_policy, d.propose_policy),
    workspace_context_policy: normalizeObjValue(cfg?.workspace_context_policy, d.workspace_context_policy),
    shell_command_policy: normalizeObjValue(cfg?.shell_command_policy, d.shell_command_policy),
    execution_risk_policy: normalizeObjValue(cfg?.execution_risk_policy, d.execution_risk_policy),
    review_policy: normalizeObjValue(cfg?.review_policy, d.review_policy),
    remote_federation_policy: normalizeObjValue(cfg?.remote_federation_policy, d.remote_federation_policy),
    knowledge_context: normalizeKnowledgeContextValue(cfg?.knowledge_context),
    hint_routing: normalizeObjValue(cfg?.hint_routing, d.hint_routing),
    goal_scoped_config_enabled: cfg?.goal_scoped_config_enabled !== false,
  };
}

/** codex_cli as shown in the form after loading (missing block gets defaults). */
export function normalizeLoadedCodexCliValue(codexCli: any): any {
  if (!codexCli || typeof codexCli !== 'object') {
    return { target_provider: '', base_url: '', api_key_profile: '', prefer_lmstudio: true };
  }
  return {
    target_provider: String(codexCli.target_provider || '').trim().toLowerCase(),
    base_url: normalizeOpenAICompatibleBaseUrlValue(codexCli.base_url),
    api_key_profile: codexCli.api_key_profile || '',
    prefer_lmstudio: codexCli.prefer_lmstudio !== false,
  };
}

/** codex_cli as sent to the hub on save (extra keys preserved, profile trimmed). */
export function normalizeSavedCodexCliValue(codexCli: any): any {
  return {
    ...codexCli,
    target_provider: String(codexCli.target_provider || '').trim().toLowerCase(),
    base_url: normalizeOpenAICompatibleBaseUrlValue(codexCli.base_url),
    api_key_profile: String(codexCli.api_key_profile || '').trim(),
    prefer_lmstudio: codexCli.prefer_lmstudio !== false,
  };
}
