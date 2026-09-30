/** Wire types and the contract error of the Source Control v1 API. */

export const SOURCE_CONTROL_V1_RESPONSE_SCHEMA =
  'ananta.source-control.api-response.v1' as const;
export const SOURCE_CONTROL_V1_ERROR_SCHEMA =
  'ananta.source-control.error.v1' as const;

export type SourceControlJson =
  | null
  | boolean
  | number
  | string
  | readonly SourceControlJson[]
  | SourceControlJsonObject;

export interface SourceControlJsonObject {
  readonly [key: string]: SourceControlJson;
}

export type SourceControlMutation =
  | 'refresh'
  | 'disable'
  | 'reindex'
  | 'grant_revoke';

export type SourceControlNextAction =
  | 'refresh'
  | 'scan'
  | 'index'
  | 'activate'
  | 'grant'
  | 'disable'
  | 'rollback';

export interface SourceControlProjection {
  readonly schema: 'ananta.source-control.projection.v1';
  readonly connection_id: string;
  readonly etag: string;
  readonly connection: SourceControlProjectionConnection;
  readonly revision: SourceControlJsonObject | null;
  readonly admission: SourceControlJsonObject | null;
  readonly index: SourceControlJsonObject | null;
  readonly active_index: SourceControlJsonObject | null;
  readonly stale: boolean;
  readonly grants: readonly SourceControlJsonObject[];
  readonly health: SourceControlJsonObject;
  readonly next_actions: readonly SourceControlNextAction[];
}

export interface SourceControlProjectionConnection
  extends SourceControlJsonObject {
  readonly project_id: string;
}

export interface SourceControlProjectionPage {
  readonly items: readonly SourceControlProjection[];
  readonly next_cursor: string | null;
}

export interface SourceControlProjectionDetail {
  readonly projection: SourceControlProjection;
  readonly etag: string;
}

export interface SourceControlIndexRecord extends SourceControlJsonObject {
  readonly knowledge_index_id: string;
  readonly source_revision_id: string;
  readonly status: string;
}

export interface SourceControlActiveIndex extends SourceControlJsonObject {
  readonly connection_id: string;
  readonly source_revision_id: string;
  readonly knowledge_index_id: string;
  readonly generation: number;
}

export interface SourceControlRunPage {
  readonly items: readonly SourceControlIndexRecord[];
  readonly active: SourceControlActiveIndex | null;
  readonly next_cursor: string | null;
}

export interface SourceControlIndexComparison {
  readonly left: SourceControlIndexRecord;
  readonly right: SourceControlIndexRecord;
  readonly changes: SourceControlJsonObject;
}

export interface SourceControlLifecycleAcknowledgement {
  readonly operation: string;
  readonly resource_id: string;
  readonly result: SourceControlJsonObject;
}

export interface SourceControlBulkTarget {
  readonly resource_id: string;
  readonly expected_etag: string;
}

export interface SourceControlBulkPlanItem {
  readonly resource_id: string;
  readonly expected_etag: string;
  readonly current_etag: string;
  readonly allowed: boolean;
  readonly reason_code: string;
}

export interface SourceControlBulkPlan {
  readonly schema: 'ananta.source-control.bulk-plan.v1';
  readonly tenant_id: string;
  readonly project_id: string;
  readonly actor_id: string;
  readonly mutation: SourceControlMutation;
  readonly items: readonly SourceControlBulkPlanItem[];
  readonly plan_digest: string;
}

export interface SourceControlBulkResultItem extends SourceControlJsonObject {
  readonly resource_id: string;
  readonly status: string;
}

export interface SourceControlBulkResult {
  readonly plan_digest: string;
  readonly results: readonly SourceControlBulkResultItem[];
}

export type SourceControlJobEventType =
  | 'source_refresh'
  | 'source_scan'
  | 'source_admission'
  | 'index_queued'
  | 'index_started'
  | 'index_progress'
  | 'index_completed'
  | 'index_failed'
  | 'index_cancelled'
  | 'index_activated'
  | 'index_rolled_back';

export interface SourceControlJobEvent {
  readonly event_id: string;
  readonly sequence: number;
  readonly resource_id: string;
  readonly job_id: string;
  readonly event_type: SourceControlJobEventType;
  readonly status: string;
  readonly reason_code: string | null;
  readonly trace_id: string;
  readonly occurred_at: string;
}

export interface SourceControlJobEventPage {
  readonly events: readonly SourceControlJobEvent[];
  readonly next_sequence: number;
}

export type SourceControlAccessDecisionKind =
  | 'allow'
  | 'deny'
  | 'approval_required'
  | 'unavailable';

export interface SourceControlAccessDecision {
  readonly schema: 'ananta.source-control.access-decision.v1';
  readonly source_revision_id: string;
  readonly revision_digest: string;
  readonly destination_id: string;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly decision: SourceControlAccessDecisionKind;
  readonly reason_codes: readonly string[];
  readonly matched_rule_path: readonly string[];
  readonly default_applied: boolean;
  readonly approval_requirement: string | null;
  readonly policy_digest: string;
}

export interface SourceControlAccessMatrix {
  readonly items: readonly SourceControlAccessDecision[];
  readonly source_next_cursor: string | null;
  readonly destination_next_cursor: string | null;
}

export interface SourceControlConnection extends SourceControlJsonObject {
  readonly schema: 'ananta.source-control.source-connection.v1';
  readonly authority: string;
  readonly connection_id: string;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly owner_id: string;
  readonly connector_type: string;
  readonly connection_identity_digest: string;
  readonly display_name: string;
  readonly sensitivity: string;
  readonly state: string;
  readonly created_at: string;
}

export interface SourceControlConnectionValidation {
  readonly valid: boolean;
  readonly connection: SourceControlConnection;
}

export interface SourceControlConnectionCreation {
  readonly connection: SourceControlConnection;
  readonly version: number;
}

export interface SourceControlOperationReceipt {
  readonly operation: 'refresh' | 'scan' | 'run';
  readonly connection_id: string;
  readonly receipt: SourceControlJsonObject;
}

export interface SourceControlExplorationResult
  extends SourceControlJsonObject {
  readonly text_alternative: string;
  readonly artifact_status: string | SourceControlJsonObject;
}

export type ContextPolicyState =
  | 'draft'
  | 'active'
  | 'superseded'
  | 'revoked';

export interface ContextPolicySummary {
  readonly policy_id: string;
  readonly latest_version: number;
  readonly state: ContextPolicyState;
  readonly etag: string;
  readonly policy_digest: string;
}

export interface ContextPolicySummaryPage {
  readonly items: readonly ContextPolicySummary[];
  readonly next_cursor: string | null;
}

export interface ContextPolicyVersion {
  readonly policy_id: string;
  readonly version: number;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly state: ContextPolicyState;
  readonly document: SourceControlJsonObject;
  readonly policy_digest: string;
  readonly etag: string;
  readonly created_by: string;
  readonly created_at: string;
}

export interface ContextPolicyVersionDetail {
  readonly policy: ContextPolicyVersion;
  readonly etag: string;
}

export interface ContextPolicyVersionPage {
  readonly items: readonly ContextPolicyVersion[];
  readonly next_cursor: string | null;
}

export type ContextPolicyDiagnosticSeverity = 'error' | 'warning' | 'info';

export interface ContextPolicyDiagnostic {
  readonly severity: ContextPolicyDiagnosticSeverity;
  readonly reason_code: string;
  readonly rule_id: string | null;
}

export interface ContextPolicyLintResult {
  readonly diagnostics: readonly ContextPolicyDiagnostic[];
}

export interface ContextPolicyPreview {
  readonly decision: SourceControlAccessDecisionKind;
  readonly reason_codes: readonly string[];
  readonly matched_rule_path: readonly string[];
  readonly approval_requirement: string | null;
  readonly policy_digest: string;
}

export interface SourceControlErrorEnvelope {
  readonly schema: typeof SOURCE_CONTROL_V1_ERROR_SCHEMA;
  readonly error: {
    readonly code: string;
  };
}

export class SourceControlV1ContractError extends Error {
  readonly status = 422;

  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'SourceControlV1ContractError';
  }
}
