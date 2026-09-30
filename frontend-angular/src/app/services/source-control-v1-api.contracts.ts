/**
 * Request/response contracts and port interfaces of the Source Control v1
 * HTTP client, plus its transport error type.
 */

import type { Observable } from 'rxjs';

import type {
  ContextPolicyLintResult,
  ContextPolicyPreview,
  ContextPolicySummaryPage,
  ContextPolicyVersion,
  ContextPolicyVersionDetail,
  ContextPolicyVersionPage,
  SourceControlAccessDecision,
  SourceControlAccessMatrix,
  SourceControlBulkPlan,
  SourceControlBulkResult,
  SourceControlBulkTarget,
  SourceControlConnectionCreation,
  SourceControlConnectionValidation,
  SourceControlExplorationResult,
  SourceControlIndexComparison,
  SourceControlJobEventPage,
  SourceControlLifecycleAcknowledgement,
  SourceControlMutation,
  SourceControlOperationReceipt,
  SourceControlProjectionDetail,
  SourceControlProjectionPage,
  SourceControlRunPage,
} from '../models/source-control-v1-api.model';

export interface SourceControlConnectionQuery {
  readonly cursor?: string;
  readonly limit?: number;
  readonly state?: string;
  readonly connector_type?: string;
  readonly owner_id?: string;
  readonly sensitivity?: string;
}

export interface SourceControlPageQuery {
  readonly cursor?: string;
  readonly limit?: number;
}

export interface SourceControlEventQuery {
  readonly after_sequence?: number;
  readonly limit?: number;
}

export interface SourceControlMutationGuard {
  readonly etag: string;
  readonly idempotencyKey: string;
}

export interface SourceControlAccessPreviewRequest {
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
}

export interface SourceControlAccessMatrixRequest {
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly source_cursor?: string;
  readonly destination_cursor?: string;
  readonly source_limit?: number;
  readonly destination_limit?: number;
}

interface SourceControlConnectionIntentBase {
  readonly display_name: string;
  readonly sensitivity: string;
}

export type SourceControlConnectionIntent =
  | (SourceControlConnectionIntentBase & {
      readonly connector_type: 'registered_workspace' | 'local_directory';
      readonly workspace_id: string;
      readonly relative_path?: string;
    })
  | (SourceControlConnectionIntentBase & {
      readonly connector_type: 'git' | 'github';
      readonly remote_id: string;
    });

export interface CodeHugMutationResult {
  readonly schema: 'ananta.codehug.mutation-result.v1';
  readonly status: string;
  readonly operation_id: string | null;
  readonly binding_digest: string | null;
}

interface SourceControlGraphQueryBase {
  readonly cursor?: string;
  readonly limit?: number;
  readonly maxEdges?: number;
}

export type SourceControlGraphQuery = SourceControlGraphQueryBase & (
  | {
      readonly view: 'topology';
      readonly domainScope?: string;
      readonly includeSubdomains?: boolean;
      readonly stage?: never;
    }
  | {
      readonly view: 'staged';
      readonly domainScope?: string;
      readonly includeSubdomains?: boolean;
      readonly stage?: 'nodes' | 'edges';
    }
  | {
      readonly view?: string;
      readonly domainScope?: never;
      readonly includeSubdomains?: never;
      readonly stage?: never;
    }
);

export interface SourceControlQueryRequest {
  readonly query: string;
  readonly limit?: number;
}

export interface ContextPolicyDraftRequest {
  readonly document: unknown;
  readonly expected_latest_version: number | null;
}

export interface ContextPolicyPreviewRequest {
  readonly version: number;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly operation: string;
  readonly transformation: string;
}

export interface ContextPolicyRollbackRequest {
  readonly target_version: number;
  readonly expected_latest_version: number;
}

export interface SourceControlReadApi {
  validateConnection(
    intent: SourceControlConnectionIntent,
  ): Observable<SourceControlConnectionValidation>;
  createConnection(
    intent: SourceControlConnectionIntent,
    idempotencyKey: string,
  ): Observable<SourceControlConnectionCreation>;
  listConnections(
    query?: SourceControlConnectionQuery,
  ): Observable<SourceControlProjectionPage>;
  getConnection(connectionId: string): Observable<SourceControlProjectionDetail>;
  listRuns(
    connectionId: string,
    query?: SourceControlPageQuery,
  ): Observable<SourceControlRunPage>;
  compareIndices(
    leftIndexId: string,
    rightIndexId: string,
  ): Observable<SourceControlIndexComparison>;
  loadGraph(
    connectionId: string,
    query?: SourceControlGraphQuery,
  ): Observable<SourceControlExplorationResult>;
  queryConnection(
    connectionId: string,
    request: SourceControlQueryRequest,
  ): Observable<SourceControlExplorationResult>;
  getArtifactStatus(
    connectionId: string,
    artifactId: string,
  ): Observable<SourceControlExplorationResult>;
}

export interface SourceControlLifecycleApi {
  refreshConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt>;
  scanConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt>;
  startIndexRun(
    connectionId: string,
    indexProfileId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt>;
  activateIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement>;
  rollbackIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement>;
  disableConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement>;
  tombstoneIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement>;
  purgeIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement>;
}

export interface ContextPolicyLifecycleApi {
  listContextPolicies(
    query?: SourceControlPageQuery,
  ): Observable<ContextPolicySummaryPage>;
  listContextPolicyVersions(
    policyId: string,
    query?: SourceControlPageQuery,
  ): Observable<ContextPolicyVersionPage>;
  getContextPolicyVersion(
    policyId: string,
    version: number,
  ): Observable<ContextPolicyVersionDetail>;
  getActiveContextPolicy(
    policyId: string,
  ): Observable<ContextPolicyVersionDetail>;
  createContextPolicyDraft(
    policyId: string,
    request: ContextPolicyDraftRequest,
    idempotencyKey: string,
  ): Observable<ContextPolicyVersion>;
  lintContextPolicy(
    policyId: string,
    version: number,
  ): Observable<ContextPolicyLintResult>;
  previewContextPolicy(
    policyId: string,
    request: ContextPolicyPreviewRequest,
  ): Observable<ContextPolicyPreview>;
  activateContextPolicy(
    policyId: string,
    version: number,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion>;
  revokeContextPolicy(
    policyId: string,
    version: number,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion>;
  rollbackContextPolicy(
    policyId: string,
    request: ContextPolicyRollbackRequest,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion>;
}

export interface SourceControlBulkApi {
  planBulk(
    mutation: SourceControlMutation,
    targets: readonly SourceControlBulkTarget[],
  ): Observable<SourceControlBulkPlan>;
  executeBulk(
    plan: SourceControlBulkPlan,
    suppliedPlanDigest: string,
    idempotencyKey: string,
  ): Observable<SourceControlBulkResult>;
}

export interface SourceControlEventApi {
  listEvents(
    query?: SourceControlEventQuery,
  ): Observable<SourceControlJobEventPage>;
}

export interface SourceControlAccessApi {
  previewAccess(
    request: SourceControlAccessPreviewRequest,
  ): Observable<SourceControlAccessDecision>;
  loadAccessMatrix(
    request: SourceControlAccessMatrixRequest,
  ): Observable<SourceControlAccessMatrix>;
  dispatchCodeHugMutation(
    mutationIntentId: string,
    idempotencyKey: string,
  ): Observable<CodeHugMutationResult>;
}

export class SourceControlV1HttpError extends Error {
  constructor(
    readonly status: number,
    readonly reasonCode: string,
  ) {
    super(reasonCode);
    this.name = 'SourceControlV1HttpError';
  }
}
