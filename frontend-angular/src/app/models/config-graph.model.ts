export interface ConfigGraphNode {
  id: string;
  node_type: string;
  label: string;
  source_file: string | null;
  source_line: number | null;
  runtime_source: string | null;
  runtime_active: boolean;
  stale: boolean;
  data: Record<string, unknown>;
  diagnostics: string[];
}

export interface ConfigGraphEdge {
  source: string;
  target: string;
  edge_type: string;
  priority: number;
  condition: string | null;
  policy_effect: string | null;
  source_ref: string | null;
}

export interface ConfigGraph {
  schema: string;
  snapshot_id: string;
  nodes: Record<string, ConfigGraphNode>;
  edges: ConfigGraphEdge[];
  views: Record<string, string[]>;
  diagnostics: string[];
  generated_at: number;
  node_count: number;
  edge_count: number;
}

export interface EffectiveConfig {
  surface: string;
  task_kind: string | null;
  path: string | null;
  instruction_layers: Array<Record<string, unknown>>;
  agent_profile: Record<string, unknown> | null;
  goal_template: Record<string, unknown> | null;
  effective_ai_modes_allowed: string[];
  effective_ai_modes_blocked: string[];
  tools_allowed: string[];
  policies_active: Array<Record<string, unknown>>;
  merge_trace: Array<Record<string, unknown>>;
  warnings: string[];
  effective_node_ids: string[];
}

export interface PatchOp {
  op: 'set_data' | 'add_edge' | 'remove_edge' | 'remove_node' | 'add_node';
  target: string;
  data: Record<string, unknown>;
}

export interface ValidationResult {
  valid: boolean;
  risk_tier: 'low' | 'medium' | 'high' | 'critical';
  errors: string[];
  warnings: string[];
  requires_approval: boolean;
  risk_score: number;
}

export interface ApplyPatchResult {
  result: {
    success: boolean;
    applied_ops: PatchOp[];
    skipped_ops: PatchOp[];
    errors: string[];
    new_snapshot_id: string;
  };
  graph: ConfigGraph;
}

export const VIEW_IDS = {
  profileActivation: 'profile_activation_view',
  planningFlow: 'planning_flow_view',
  agentRuntime: 'agent_runtime_view',
  policyPath: 'policy_path_view',
  contextPipeline: 'context_pipeline_view',
  effectiveConfig: 'effective_config_view',
} as const;

export type ViewId = typeof VIEW_IDS[keyof typeof VIEW_IDS];

// Color map for node types (used by SVG renderer)
export const NODE_TYPE_COLORS: Record<string, string> = {
  surface: '#4A90D9',
  agent_profile: '#7B4EA0',
  instruction_layer: '#2E7D32',
  goal_template: '#E65100',
  task_kind: '#BF360C',
  subtask_step: '#FF8F00',
  role: '#00838F',
  tool: '#546E7A',
  tool_group: '#37474F',
  policy: '#C62828',
  path_rule: '#AD1457',
  context_source: '#00695C',
  codecompass_profile: '#1565C0',
  rag_profile: '#4527A0',
  embedding_model: '#283593',
  model_provider: '#1A237E',
  restricted_inference_model: '#880E4F',
  worker_backend: '#1B5E20',
  model_profile: '#0D47A1',
  default: '#616161',
};

export function nodeColor(nodeType: string): string {
  return NODE_TYPE_COLORS[nodeType] ?? NODE_TYPE_COLORS['default'];
}
