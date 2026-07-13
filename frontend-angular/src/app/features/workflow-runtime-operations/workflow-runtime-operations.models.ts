export type RuntimeHealthFilter = '' | 'healthy' | 'degraded' | 'stale' | 'parity_gap' | 'unverified';

export interface RuntimeCapabilityDescriptorProjection {
  schema: string;
  runtime_id: string;
  runtime_version: string;
  contract_version: string;
  mode: string;
  capabilities: string[];
  restrictions: string[];
  health: {
    status: 'ready' | 'degraded' | 'unavailable' | 'disabled';
    reason_code: string;
  };
  selection: {
    state: 'compatible' | 'incompatible' | 'degraded' | 'blocked';
    reason_code: string;
    missing_capabilities: string[];
  };
}

export interface RuntimeCapabilityMatrixProjection {
  schema: 'ananta.workflow_runtime_capability_matrix.v1';
  matrix_version: string;
  required_capabilities: string[];
  runtimes: RuntimeCapabilityDescriptorProjection[];
}

export interface RuntimeCapabilityView {
  name: string;
  status: string;
  reason_code: string | null;
}

export interface RuntimeFallbackView {
  source_runtime: string;
  target_runtime: string;
  reason_code: string;
  semantic_class: string;
  approved: boolean;
}

export interface RuntimeRecoveryView {
  status: string;
  strategy: string | null;
  attempts: number;
  last_checkpoint_ref: string | null;
  reason_code: string | null;
}

export interface RuntimeGateView {
  gate_id: string;
  label: string;
  status: string;
  approval_id: string | null;
  required_evidence_refs: string[];
  allowed_commands: string[];
  expires_at: number | null;
}

export interface RuntimeEvidenceView {
  evidence_id: string;
  kind: string;
  verification_status: string;
  summary: string;
  source_ref: string | null;
  observed_at: number;
}

export interface RuntimeGapView {
  code: string;
  category: string;
  severity: string;
  summary: string;
}

export interface WorkflowRuntimeOperationRun {
  schema: string;
  run_id: string;
  workflow_id: string | null;
  task_id: string | null;
  runtime: string;
  mode: string;
  status: string;
  outcome_claim: string;
  capabilities: RuntimeCapabilityView[];
  fallbacks: RuntimeFallbackView[];
  cost_micros: number;
  latency_ms: number;
  recovery: RuntimeRecoveryView;
  gates: RuntimeGateView[];
  evidence: RuntimeEvidenceView[];
  parity_gaps: RuntimeGapView[];
  semantic_deviations: RuntimeGapView[];
  open_gate_count: number;
  verified_evidence_count: number;
  degraded: boolean;
  degraded_reasons: string[];
  stale: boolean;
  updated_at: number;
  stale_after_seconds: number;
  source_sequence: number;
}

export interface RuntimeOperationsSummary {
  total_runs: number;
  degraded_runs: number;
  stale_runs: number;
  unverified_successes: number;
  open_gates: number;
  verified_evidence: number;
  total_cost_micros: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  active_recoveries: number;
  parity_gap_runs: number;
}

export interface RuntimeOperationsResponse {
  schema: string;
  generated_at: number;
  filters: Record<string, string | number | null>;
  summary: RuntimeOperationsSummary;
  runs: WorkflowRuntimeOperationRun[];
  count: number;
}

export interface RuntimeOperationsFilters {
  runtime: string;
  mode: string;
  status: string;
  health: RuntimeHealthFilter;
  q: string;
}

export interface RuntimeOperationCommandRequest {
  type: 'pause_run' | 'resume_run' | 'cancel_run' | 'retry_run_or_task';
  approval_id: string;
  evidence_refs: string[];
}

export interface RuntimeOperationCommandResponse {
  status: string;
  command: {
    command_id: string;
    type: string;
    status: string;
    run_id: string | null;
  };
}
