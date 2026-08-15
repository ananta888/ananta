export const CASEFLOW_EDGE_TRACE_QUERY_SCHEMA = 'ananta.caseflow_edge_trace_query.v1';
export const CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA = 'ananta.caseflow_edge_trace_read_model.v1';

export type CaseFlowEdgeActivityStatus = 'active' | 'inactive' | 'unknown';
export type CaseFlowEdgeVerificationStatus = 'verified' | 'unverified';
export type CaseFlowEdgeKind = 'dependency' | 'back_edge';

/** Exact canonical identity of one directed VisualProcess edge. */
export interface CaseFlowEdgeIdentity {
  readonly edge_id: string;
  readonly source_step_id: string;
  readonly target_step_id: string;
}

export interface CaseFlowEdgeTraceMessage {
  readonly content: string;
  readonly role: string | null;
  readonly event_ref: string | null;
  readonly trace_ref: string | null;
  readonly correlation_ref: string | null;
  readonly occurred_at: number | null;
  readonly verification_status: CaseFlowEdgeVerificationStatus;
  readonly truncated: boolean;
}

export type CaseFlowTokenUsageKey =
  | 'input_tokens'
  | 'output_tokens'
  | 'total_tokens'
  | 'prompt_tokens'
  | 'completion_tokens'
  | 'cached_tokens'
  | 'reasoning_tokens'
  | 'cache_read_input_tokens'
  | 'cache_creation_input_tokens';

export type CaseFlowTokenUsage = Readonly<Partial<Record<CaseFlowTokenUsageKey, number>>>;

export interface CaseFlowEdgeTraceTelemetryEntry {
  readonly event_ref: string | null;
  readonly trace_ref: string | null;
  readonly agent_run_ref: string | null;
  readonly correlation_ref: string | null;
  readonly causation_ref: string | null;
  readonly event_type: string;
  readonly step_id: string | null;
  readonly sequence: number | null;
  readonly occurred_at: number | null;
  readonly status: string | null;
  readonly duration_ms: number | null;
  readonly model: string | null;
  readonly provider: string | null;
  readonly token_usage: CaseFlowTokenUsage | null;
  readonly cost_micros: number | null;
  readonly tool: string | null;
  readonly error: string | null;
  readonly redaction_policy: 'user';
}

export interface CaseFlowEdgeTraceLimits {
  readonly messages_truncated: number;
  readonly telemetry_truncated: number;
  readonly event_refs_truncated: number;
  readonly trace_refs_truncated: number;
}

export interface CaseFlowEdgeTraceProjection extends CaseFlowEdgeIdentity {
  readonly edge_kind: CaseFlowEdgeKind;
  readonly activity_status: CaseFlowEdgeActivityStatus;
  readonly verification_status: CaseFlowEdgeVerificationStatus;
  readonly reason_code: string;
  readonly correlation_basis:
    | 'explicit_edge_id'
    | 'explicit_direction'
    | 'unique_dependency_event_sequence'
    | 'unavailable';
  readonly event_refs: readonly string[];
  readonly trace_refs: readonly string[];
  readonly messages: readonly CaseFlowEdgeTraceMessage[];
  readonly telemetry: readonly CaseFlowEdgeTraceTelemetryEntry[];
  readonly limits: CaseFlowEdgeTraceLimits;
}

export interface CaseFlowEdgeTraceProjectionTelemetry {
  readonly source_event_count: number;
  readonly processed_event_count: number;
  readonly rejected_event_count: number;
  readonly truncated_event_count: number;
  readonly correlated_edge_count: number;
  readonly redaction_policy: 'user';
  readonly messages_per_edge_limit: number;
  readonly telemetry_per_edge_limit: number;
}

export interface CaseFlowEdgeTraceReadModel {
  readonly schema: typeof CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA;
  readonly workflow_id: string;
  readonly run_id: string;
  readonly catalog_verification_status: CaseFlowEdgeVerificationStatus;
  readonly verification_status: CaseFlowEdgeVerificationStatus;
  readonly reason_code: string;
  /** Hub order is authoritative and must not be rearranged by clients. */
  readonly edges: readonly CaseFlowEdgeTraceProjection[];
  readonly telemetry: CaseFlowEdgeTraceProjectionTelemetry;
  /**
   * Runtime revision this projection reflects, or null when the Hub could not
   * establish one. Null means freshness is unproven, never proven fresh.
   */
  readonly source_revision: number | null;
}

export interface CaseFlowEdgeTraceScope {
  readonly workflow_id: string;
  readonly run_id: string;
}

export type CaseFlowMessageTelemetryResolution =
  | Readonly<{ status: 'verified'; telemetry_index: number; correlation_ref: string }>
  | Readonly<{ status: 'unverified'; telemetry_index: null; correlation_ref: string | null }>;
