/**
 * DTO-/Read-Model-Typen für Dashboard, Goal, Governance, Timeline und Benchmark.
 *
 * Spiegeln den API-Vertrag des Hubs und ersetzen schrittweise die bisher `any`-lastigen
 * Konsumsites in DashboardFacade, BenchmarkPanel, TimelinePanel und AutopilotPanel.
 */

export type BenchmarkTaskKind = 'coding' | 'analysis' | 'doc' | 'ops';
export type AutopilotSecurityLevel = 'safe' | 'balanced' | 'aggressive';

export interface AgentResources {
  cpu_percent?: number;
  ram_bytes?: number;
}

export interface AgentLiveness {
  available_for_routing?: boolean;
  status?: string;
  last_seen?: number;
  stale_seconds?: number;
}

export interface AgentEntry {
  name?: string;
  url?: string;
  role?: string;
  status?: string;
  liveness?: AgentLiveness;
  security_level?: string;
  security_tier?: string;
  resources?: AgentResources;
  current_load?: number;
  max_parallel_tasks?: number;
  capabilities?: string[];
  worker_roles?: string[];
  routing_signals?: Record<string, unknown>;
  execution_limits?: Record<string, unknown>;
  registration_validated?: boolean;
  available_for_routing?: boolean;
  success_rate?: number;
  quality_rate?: number;
  metrics?: Record<string, unknown>;
}

export interface TeamMember {
  agent_url: string;
  role_id: string;
}

export interface TeamEntry {
  id: string;
  name: string;
  is_active?: boolean;
  members?: TeamMember[];
}

export interface RoleEntry {
  id: string;
  name: string;
}

export interface BenchmarkFocus {
  suitability_score?: number;
  success_rate?: number;
  quality_rate?: number;
  avg_latency_ms?: number;
  avg_tokens?: number;
}

export interface BenchmarkItem {
  id: string;
  provider: string;
  model: string;
  focus?: BenchmarkFocus;
}

export interface BenchmarkRecommendation {
  current?: { provider?: string; model?: string };
  recommended?: { provider?: string; model?: string; selection_source?: string };
  has_explicit_override?: boolean;
  is_recommendation_active?: boolean;
}

export interface BenchmarksSection {
  task_kind?: BenchmarkTaskKind | string | null;
  updated_at?: number | null;
  items?: BenchmarkItem[];
  recommendation?: BenchmarkRecommendation | null;
}

export interface LlmModelReference {
  provider?: string | null;
  model?: string | null;
  source?: { provider?: string };
  active?: boolean;
}

export interface LlmEffectiveRuntime extends LlmModelReference {
  mode?: string;
  selection_source?: string;
  benchmark_applied?: boolean;
  replaces_configured?: boolean;
  temperature?: number | null;
}

export interface HubCopilotStatus {
  enabled?: boolean;
  active?: boolean;
  strategy_mode?: string;
  effective?: { temperature?: number | null };
}

export interface ContextPolicyStatus {
  effective?: {
    mode?: string;
    compact_max_chunks?: number;
    standard_max_chunks?: number;
  };
}

export interface ArtifactFlowStatus {
  effective?: {
    enabled?: boolean;
    rag_enabled?: boolean;
    rag_top_k?: number;
  };
}

export interface ResearchBackendProvider {
  provider: string;
  selected?: boolean;
  configured?: boolean;
  binary_available?: boolean;
  working_dir?: string;
  working_dir_exists?: boolean;
  mode?: string;
}

export interface ResearchBackendStatus {
  provider?: string;
  enabled?: boolean;
  configured?: boolean;
  review_policy?: { required?: boolean; reason?: string };
  providers?: Record<string, ResearchBackendProvider>;
}

export interface OllamaActiveModel {
  name: string;
  executor?: string;
  context_length?: number;
  num_ctx?: number;
}

export interface OllamaModelDef {
  name: string;
  context_length?: number;
  num_ctx?: number;
  details?: { context_length?: number; num_ctx?: number };
}

export interface OllamaProviderState {
  status?: string;
  reachable?: boolean;
  candidate_count?: number;
  models?: OllamaModelDef[];
  activity?: {
    gpu_active?: boolean;
    executor_summary?: Record<string, number>;
    active_models?: OllamaActiveModel[];
  };
}

export interface LmStudioCandidate {
  id?: string;
  name?: string;
  context_length?: number;
  num_ctx?: number;
  loaded?: boolean;
}

export interface LmStudioProviderState {
  status?: string;
  reachable?: boolean;
  candidate_count?: number;
  candidates?: LmStudioCandidate[];
}

export interface RuntimeTelemetry {
  providers?: {
    ollama?: OllamaProviderState;
    lmstudio?: LmStudioProviderState;
    [key: string]: unknown;
  };
}

export interface LlmConfiguration {
  defaults?: LlmModelReference | null;
  explicit_override?: LlmModelReference | null;
  effective_runtime?: LlmEffectiveRuntime | null;
  hub_copilot?: HubCopilotStatus | null;
  context_bundle_policy?: ContextPolicyStatus | null;
  artifact_flow?: ArtifactFlowStatus | null;
  research_backend?: ResearchBackendStatus | null;
  runtime_telemetry?: RuntimeTelemetry | null;
}

export interface SystemHealthChecks {
  queue?: { depth?: number };
  registration?: { enabled?: boolean; status?: string; attempts?: number };
  scheduler?: { running?: boolean; scheduled_count?: number };
}

export interface SystemHealth {
  status?: string;
  agent?: string;
  checks?: SystemHealthChecks;
}

export interface ContractsStatus {
  version?: string;
  schema_count?: number;
  task_statuses?: { canonical_values?: string[] };
}

export interface DashboardReadModel {
  system_health?: SystemHealth | null;
  contracts?: ContractsStatus | null;
  teams?: { count?: number; items?: TeamEntry[] };
  roles?: { count?: number; items?: RoleEntry[] };
  templates?: { count?: number; items?: unknown[] };
  agents?: { count?: number; items?: AgentEntry[] };
  tasks?: { counts?: Record<string, number>; recent?: unknown[] };
  benchmarks?: BenchmarksSection;
  llm_configuration?: LlmConfiguration;
  context_timestamp?: number;
}

export interface GoalDetailGoal {
  id: string;
  summary?: string;
  goal?: string;
  status?: string;
  updated_at?: number;
  created_at?: number;
}

export interface GoalTaskCostSummary {
  cost_units?: number;
  tokens_total?: number;
}

export interface GoalTaskEntry {
  id: string;
  title?: string;
  status?: string;
  verification_status?: { status?: string };
  cost_summary?: GoalTaskCostSummary;
}

export interface GoalDetail {
  goal?: GoalDetailGoal;
  tasks?: GoalTaskEntry[];
}

export interface GoalGovernanceSummary {
  goal_id?: string;
  verification?: { total?: number; passed?: number; failed?: number; escalated?: number };
  policy?: { approved?: number; blocked?: number };
  cost_summary?: {
    total_cost_units?: number;
    tasks_with_cost?: number;
    total_tokens?: number;
    total_latency_ms?: number;
  };
  summary?: { task_count?: number };
}

export interface GoalListEntry {
  id: string;
  summary?: string;
  goal?: string;
  status?: string;
  updated_at?: number;
  created_at?: number;
}

export interface TimelineGuardrailDetails {
  blocked_tools?: unknown[];
  blocked_reasons?: string[];
  reason?: string;
  output_preview?: string;
}

export interface TimelineEvent {
  event_type: string;
  task_id: string;
  task_status?: string;
  timestamp?: number;
  actor?: string;
  details?: TimelineGuardrailDetails;
}

export interface AutopilotStatus {
  running?: boolean;
  tick_count?: number;
  dispatched_count?: number;
  completed_count?: number;
  failed_count?: number;
  last_tick_at?: number;
  last_error?: string;
  goal?: string;
  team_id?: string;
  budget_label?: string;
  security_level?: AutopilotSecurityLevel;
}

export interface DashboardStatsBlock {
  agents: { total: number; online: number; offline: number };
  tasks: { total: number; completed: number; failed: number; in_progress: number };
  timestamp: number;
  agent_name: string;
  shell_pool?: { total?: number; free?: number; busy?: number };
  resources?: { cpu_percent?: number; ram_bytes?: number };
}
