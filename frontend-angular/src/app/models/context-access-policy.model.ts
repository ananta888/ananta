export enum Sensitivity {
  public = 'public',
  project_internal = 'project_internal',
  customer_confidential = 'customer_confidential',
  security_sensitive = 'security_sensitive',
  secret = 'secret',
  credential = 'credential',
  regulated_data = 'regulated_data',
  generated_summary = 'generated_summary',
  unknown = 'unknown'
}

export enum ModelScope {
  local_model = 'local_model',
  local_tool_only = 'local_tool_only',
  private_remote = 'private_remote',
  approved_cloud = 'approved_cloud',
  public_cloud = 'public_cloud',
  none = 'none'
}

export enum SourceType {
  codecompass_code = 'codecompass_code',
  codecompass_graph = 'codecompass_graph',
  rag_chunk = 'rag_chunk',
  local_file = 'local_file',
  secret_file = 'secret_file',
  env_file = 'env_file',
  memory = 'memory',
  artifact = 'artifact',
  log = 'log',
  config = 'config',
  docs = 'docs',
  external_source = 'external_source',
  user_prompt = 'user_prompt'
}

export enum Decision {
  allow = 'allow',
  allow_redacted = 'allow_redacted',
  allow_summary_only = 'allow_summary_only',
  deny = 'deny',
  approval_required = 'approval_required',
  unavailable = 'unavailable'
}

export enum ReasonCode {
  secret_blocked = 'secret_blocked',
  cloud_blocked = 'cloud_blocked',
  external_worker_blocked = 'external_worker_blocked',
  worker_not_allowed = 'worker_not_allowed',
  runtime_not_allowed = 'runtime_not_allowed',
  model_scope_not_allowed = 'model_scope_not_allowed',
  provider_location_blocked = 'provider_location_blocked',
  write_not_allowed = 'write_not_allowed',
  unmatched_source_denied = 'unmatched_source_denied',
  approval_required = 'approval_required',
  redaction_required = 'redaction_required',
  policy_error = 'policy_error'
}

export enum RequestedOperation {
  send_to_llm = 'send_to_llm',
  send_to_worker = 'send_to_worker',
  tool_read = 'tool_read',
  tool_write = 'tool_write',
  memory_write = 'memory_write',
  artifact_store = 'artifact_store',
  repair_execute = 'repair_execute',
  review_only = 'review_only'
}

export enum WorkerKind {
  native_ananta_worker = 'native_ananta_worker',
  opencode = 'opencode',
  hermes = 'hermes',
  shellgpt = 'shellgpt',
  remote_worker = 'remote_worker',
  custom_worker = 'custom_worker'
}

export enum RuntimeKind {
  local_process = 'local_process',
  docker_container = 'docker_container',
  docker_compose_service = 'docker_compose_service',
  wsl = 'wsl',
  remote_http_worker = 'remote_http_worker',
  remote_ssh_worker = 'remote_ssh_worker',
  ci_sandbox = 'ci_sandbox',
  cloud_worker = 'cloud_worker',
  custom = 'custom'
}

export interface SourceMatch {
  pattern?: string;
  source_types?: SourceType[];
  sensitivity?: Sensitivity;
  tags?: string[];
}

export interface DestinationConstraint {
  allowed_worker_kinds?: string[];
  denied_worker_kinds?: string[];
  allowed_runtime_kinds?: string[];
  denied_runtime_kinds?: string[];
  allowed_model_scopes?: ModelScope[];
  denied_model_scopes?: string[];
  allowed_provider_locations?: string[];
  denied_provider_locations?: string[];
  allowed_worker_ids?: string[];
  denied_worker_ids?: string[];
  allowed_runtime_target_ids?: string[];
  denied_runtime_target_ids?: string[];
}

export interface ContextAccessRule {
  id: string;
  description: string;
  source_match?: string;
  source_types?: SourceType[];
  sensitivity?: Sensitivity;
  
  // Destination constraints (flattened in backend ContextAccessRule dataclass, 
  // but we can group them if needed, or keep flattened as in backend)
  allowed_worker_kinds?: string[];
  denied_worker_kinds?: string[];
  allowed_runtime_kinds?: string[];
  denied_runtime_kinds?: string[];
  allowed_model_scopes?: ModelScope[];
  denied_model_scopes?: string[];
  allowed_provider_locations?: string[];
  denied_provider_locations?: string[];
  
  read_allowed?: boolean;
  write_allowed?: boolean;
  send_allowed?: boolean;
  cloud_allowed?: boolean;
  external_worker_allowed?: boolean;
  redaction_required?: boolean;
  summarization_allowed?: boolean;
  approval_required?: boolean;
  
  reason_tags?: string[];
  
  // Extension map to preserve unknown fields
  extensions?: { [key: string]: any };
}

export interface ContextAccessPolicy {
  policy_id: string;
  version: number;
  scope: string; // system_default, project, blueprint_role, task
  rules: ContextAccessRule[];
  defaults?: { [key: string]: any };
  precedence?: number;
  created_at?: string;
  updated_at?: string;
  validation_state?: 'todo' | 'in_progress' | 'blocked' | 'done' | 'draft' | 'active' | 'archived';
  
  // Extension map
  extensions?: { [key: string]: any };
}

export interface ContextBlockAccessDecision {
  block_id: string;
  source_ref: string;
  matched_rule_ids: string[];
  decision: Decision;
  reason_code?: ReasonCode;
  reason_detail?: string;
  redaction_profile?: string;
  summarization_profile?: string;
  approval_requirement?: string;
  effective_sensitivity?: Sensitivity;
  allowed_destination?: boolean;
  denied_destination?: boolean;
  policy_version?: number;
  decision_hash?: string;
}

export interface PolicyLintItem {
  rule_id?: string;
  severity: 'info' | 'warning' | 'error';
  message: string;
  suggested_fix?: string;
  code?: string;
}

export interface PolicyLintResult {
  is_valid: boolean;
  errors: PolicyLintItem[];
  warnings: PolicyLintItem[];
  infos: PolicyLintItem[];
}

export interface PolicyTemplate {
  id: string;
  name: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  cloud_allowed: boolean;
  external_worker_allowed: boolean;
  denied_categories: string[];
  policy: Partial<ContextAccessPolicy>;
}

export interface DestinationContextPreview {
  worker_id?: string;
  worker_kind: string;
  runtime_target_id?: string;
  runtime_kind: string;
  model_scope: ModelScope;
  cloud_effective: boolean;
  external_effective: boolean;
}

export interface EffectivePolicyReadModel {
  active_policy: ContextAccessPolicy;
  draft_policy?: ContextAccessPolicy;
  merged_policy: ContextAccessPolicy;
  diagnostics: any; // Will be defined in context-policy-diagnostics.model.ts
}
