import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams, HttpResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, throwError } from 'rxjs';

import {
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
  SourceControlV1ContractError,
  assertSourceControlActivePointerEtag,
  assertSourceControlCursor,
  assertSourceControlEtag,
  assertSourceControlIdempotencyKey,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
  parseContextPolicyDocument,
  parseContextPolicyLintResult,
  parseContextPolicyPreview,
  parseContextPolicySummaryPage,
  parseContextPolicyVersion,
  parseContextPolicyVersionPage,
  parseSourceControlAccessDecision,
  parseSourceControlAccessMatrix,
  parseSourceControlBulkPlan,
  parseSourceControlBulkResult,
  parseSourceControlConnectionCreation,
  parseSourceControlConnectionValidation,
  parseSourceControlEnvelope,
  parseSourceControlErrorEnvelope,
  parseSourceControlExplorationResult,
  parseSourceControlIndexComparison,
  parseSourceControlJobEventPage,
  parseSourceControlLifecycleAcknowledgement,
  parseSourceControlOperationReceipt,
  parseSourceControlProjection,
  parseSourceControlProjectionPage,
  parseSourceControlRunPage,
} from '../models/source-control-v1-api.model';

const BASE_PATH = '/api/source-control/v1';
const MUTATIONS = new Set<SourceControlMutation>([
  'refresh',
  'disable',
  'reindex',
  'grant_revoke',
]);

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

export function normalizeSourceWorkspaceRelativePath(value: string): string | null {
  const normalized = value.trim();
  if (!normalized) {
    return '';
  }
  if (
    normalized.length > 1024
    || normalized.startsWith('/')
    || normalized.endsWith('/')
    || normalized.includes('\\')
    || /[\u0000-\u001f\u007f]/.test(normalized)
  ) {
    return null;
  }
  const segments = normalized.split('/');
  if (
    segments.some(
      (segment) =>
        segment === ''
        || segment === '.'
        || segment === '..'
        || !/^[A-Za-z0-9._@+-]+$/.test(segment),
    )
  ) {
    return null;
  }
  return segments.join('/');
}

function parseCodeHugMutationResult(
  value: unknown,
): CodeHugMutationResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SourceControlV1ContractError(
      'codehug_mutation_result_invalid',
    );
  }
  const result = value as Record<string, unknown>;
  const keys = Object.keys(result).sort();
  const expected = [
    'binding_digest',
    'operation_id',
    'schema',
    'status',
  ];
  if (
    keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || result['schema'] !== 'ananta.codehug.mutation-result.v1'
    || typeof result['status'] !== 'string'
    || (
      result['operation_id'] !== null
      && typeof result['operation_id'] !== 'string'
    )
    || (
      result['binding_digest'] !== null
      && typeof result['binding_digest'] !== 'string'
    )
  ) {
    throw new SourceControlV1ContractError(
      'codehug_mutation_result_invalid',
    );
  }
  if (typeof result['binding_digest'] === 'string') {
    assertSourceControlSha256(
      result['binding_digest'],
      'binding_digest',
    );
  }
  return result as unknown as CodeHugMutationResult;
}

@Injectable({ providedIn: 'root' })
export class SourceControlV1ApiClient
  implements
    SourceControlReadApi,
    SourceControlLifecycleApi,
    SourceControlBulkApi,
    SourceControlEventApi,
    SourceControlAccessApi,
    ContextPolicyLifecycleApi
{
  private readonly http = inject(HttpClient);

  validateConnection(
    intent: SourceControlConnectionIntent,
  ): Observable<SourceControlConnectionValidation> {
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/connections/validate`, {
          ...this.connectionIntent(intent),
          dry_run: true,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlConnectionValidation,
            ),
          ),
        ),
    );
  }

  createConnection(
    intent: SourceControlConnectionIntent,
    idempotencyKey: string,
  ): Observable<SourceControlConnectionCreation> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/connections`,
          {
            ...this.connectionIntent(intent),
            dry_run: false,
          },
          { headers: this.idempotencyHeaders(idempotencyKey) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlConnectionCreation,
            ),
          ),
        ),
    );
  }

  dispatchCodeHugMutation(
    mutationIntentId: string,
    idempotencyKey: string,
  ): Observable<CodeHugMutationResult> {
    assertSourceControlOpaqueId(
      mutationIntentId,
      'mutation_intent_id',
    );
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/codehug/mutations`,
          {
            mutation_intent_id: mutationIntentId,
            dry_run: false,
          },
          { headers: this.idempotencyHeaders(idempotencyKey) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseCodeHugMutationResult),
          ),
        ),
    );
  }

  listConnections(
    query: SourceControlConnectionQuery = {},
  ): Observable<SourceControlProjectionPage> {
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/connections`, {
          params: this.connectionParams(query),
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlProjectionPage),
          ),
        ),
    );
  }

  getConnection(
    connectionId: string,
  ): Observable<SourceControlProjectionDetail> {
    const id = this.pathId(connectionId, 'connection_id');
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/connections/${id}`, {
          observe: 'response',
        })
        .pipe(map((response) => this.projectionDetail(response))),
    );
  }

  listRuns(
    connectionId: string,
    query: SourceControlPageQuery = {},
  ): Observable<SourceControlRunPage> {
    const id = this.pathId(connectionId, 'connection_id');
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/connections/${id}/runs`, {
          params: this.pageParams(query),
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlRunPage),
          ),
        ),
    );
  }

  compareIndices(
    leftIndexId: string,
    rightIndexId: string,
  ): Observable<SourceControlIndexComparison> {
    assertSourceControlOpaqueId(leftIndexId, 'left_index_id');
    assertSourceControlOpaqueId(rightIndexId, 'right_index_id');
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/indices/compare`, {
          left_index_id: leftIndexId,
          right_index_id: rightIndexId,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlIndexComparison),
          ),
        ),
    );
  }

  loadGraph(
    connectionId: string,
    query: SourceControlGraphQuery = {},
  ): Observable<SourceControlExplorationResult> {
    const id = this.pathId(connectionId, 'connection_id');
    let params = new HttpParams();
    if (query.cursor !== undefined) {
      assertSourceControlCursor(query.cursor, 'cursor');
      params = params.set('cursor', query.cursor);
    }
    if (query.limit !== undefined) {
      this.assertInteger(query.limit, 'limit', 1, 500);
      params = params.set('limit', query.limit);
    }
    if (query.view !== undefined) {
      assertSourceControlOpaqueId(query.view, 'view');
      params = params.set('view', query.view);
    }
    if (query.maxEdges !== undefined) {
      this.assertInteger(query.maxEdges, 'max_edges', 1, 2000);
      params = params.set('max_edges', query.maxEdges);
    }
    const hasDomainParameters = query.domainScope !== undefined
      || query.includeSubdomains !== undefined;
    if (
      hasDomainParameters
      && query.view !== 'topology'
      && query.view !== 'staged'
    ) {
      throw new SourceControlV1ContractError('graph_domain_view_invalid');
    }
    if (query.domainScope !== undefined) {
      assertSourceControlOpaqueId(query.domainScope, 'domain_scope');
      params = params.set('domain_scope', query.domainScope);
    }
    if (query.includeSubdomains !== undefined) {
      if (typeof query.includeSubdomains !== 'boolean') {
        throw new SourceControlV1ContractError('include_subdomains_invalid');
      }
      params = params.set(
        'include_subdomains',
        query.includeSubdomains ? 'true' : 'false',
      );
    }
    if (query.stage !== undefined) {
      if (
        (query.stage !== 'nodes' && query.stage !== 'edges')
        || query.view !== 'staged'
      ) {
        throw new SourceControlV1ContractError('graph_stage_invalid');
      }
      params = params.set('stage', query.stage);
    }
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/connections/${id}/graph`, { params })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlExplorationResult,
            ),
          ),
        ),
    );
  }

  queryConnection(
    connectionId: string,
    request: SourceControlQueryRequest,
  ): Observable<SourceControlExplorationResult> {
    const id = this.pathId(connectionId, 'connection_id');
    const query = this.nonEmptyText(request.query, 'query', 4000);
    const payload: { query: string; limit?: number } = { query };
    if (request.limit !== undefined) {
      this.assertInteger(request.limit, 'limit', 1, 100);
      payload.limit = request.limit;
    }
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/connections/${id}/query`, payload)
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlExplorationResult,
            ),
          ),
        ),
    );
  }

  getArtifactStatus(
    connectionId: string,
    artifactId: string,
  ): Observable<SourceControlExplorationResult> {
    const connection = this.pathId(connectionId, 'connection_id');
    const artifact = this.pathId(artifactId, 'artifact_id');
    return this.handle(
      this.http
        .get<unknown>(
          `${BASE_PATH}/connections/${connection}/artifacts/${artifact}/status`,
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlExplorationResult,
            ),
          ),
        ),
    );
  }

  refreshConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt> {
    return this.connectionOperation(
      connectionId,
      'refresh',
      {},
      guard,
    );
  }

  scanConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt> {
    return this.connectionOperation(connectionId, 'scan', {}, guard);
  }

  startIndexRun(
    connectionId: string,
    indexProfileId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt> {
    assertSourceControlOpaqueId(indexProfileId, 'index_profile_id');
    return this.connectionOperation(
      connectionId,
      'runs',
      { index_profile_id: indexProfileId },
      guard,
    );
  }

  activateIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/indices/${this.pathId(indexId, 'index_id')}/activate`,
      guard,
      'active-pointer',
    );
  }

  rollbackIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/indices/${this.pathId(indexId, 'index_id')}/rollback`,
      guard,
      'active-pointer',
    );
  }

  disableConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/connections/${this.pathId(connectionId, 'connection_id')}/disable`,
      guard,
    );
  }

  tombstoneIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/indices/${this.pathId(indexId, 'index_id')}/tombstone`,
      guard,
    );
  }

  purgeIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    const id = this.pathId(indexId, 'index_id');
    const headers = this.mutationHeaders(guard);
    return this.handle(
      this.http
        .request<unknown>('DELETE', `${BASE_PATH}/indices/${id}`, {
          body: { dry_run: false },
          headers,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlLifecycleAcknowledgement,
            ),
          ),
        ),
    );
  }

  planBulk(
    mutation: SourceControlMutation,
    targets: readonly SourceControlBulkTarget[],
  ): Observable<SourceControlBulkPlan> {
    if (!MUTATIONS.has(mutation)) {
      throw new SourceControlV1ContractError('bulk_mutation_invalid');
    }
    if (targets.length < 1 || targets.length > 100) {
      throw new SourceControlV1ContractError('bulk_target_count_invalid');
    }
    const seen = new Set<string>();
    const validatedTargets = targets.map((target, index) => {
      assertSourceControlOpaqueId(
        target.resource_id,
        `targets[${index}].resource_id`,
      );
      assertSourceControlSha256(
        target.expected_etag,
        `targets[${index}].expected_etag`,
      );
      if (seen.has(target.resource_id)) {
        throw new SourceControlV1ContractError('bulk_duplicate_target');
      }
      seen.add(target.resource_id);
      return {
        resource_id: target.resource_id,
        expected_etag: target.expected_etag,
      };
    });
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/bulk/plan`, {
          mutation,
          targets: validatedTargets,
          dry_run: true,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlBulkPlan),
          ),
        ),
    );
  }

  executeBulk(
    plan: SourceControlBulkPlan,
    suppliedPlanDigest: string,
    idempotencyKey: string,
  ): Observable<SourceControlBulkResult> {
    const validatedPlan = parseSourceControlBulkPlan(plan);
    assertSourceControlSha256(
      suppliedPlanDigest,
      'supplied_plan_digest',
    );
    if (suppliedPlanDigest !== validatedPlan.plan_digest) {
      throw new SourceControlV1ContractError(
        'bulk_plan_digest_mismatch',
      );
    }
    assertSourceControlIdempotencyKey(
      idempotencyKey,
      'idempotency_key',
    );
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/bulk/execute`,
          {
            plan: validatedPlan,
            supplied_plan_digest: suppliedPlanDigest,
            dry_run: false,
          },
          {
            headers: new HttpHeaders({
              'Idempotency-Key': idempotencyKey,
            }),
          },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlBulkResult),
          ),
        ),
    );
  }

  listEvents(
    query: SourceControlEventQuery = {},
  ): Observable<SourceControlJobEventPage> {
    let params = new HttpParams();
    if (query.after_sequence !== undefined) {
      this.assertInteger(query.after_sequence, 'after_sequence', 0);
      params = params.set('after_sequence', query.after_sequence);
    }
    if (query.limit !== undefined) {
      this.assertInteger(query.limit, 'limit', 1, 500);
      params = params.set('limit', query.limit);
    }
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/events`, { params })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlJobEventPage),
          ),
        ),
    );
  }

  previewAccess(
    request: SourceControlAccessPreviewRequest,
  ): Observable<SourceControlAccessDecision> {
    const payload = this.accessPayload(request);
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/access/preview`, payload)
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlAccessDecision),
          ),
        ),
    );
  }

  loadAccessMatrix(
    request: SourceControlAccessMatrixRequest,
  ): Observable<SourceControlAccessMatrix> {
    const payload: Record<string, string | number> = this.accessIntent(request);
    if (request.source_cursor !== undefined) {
      assertSourceControlCursor(request.source_cursor, 'source_cursor');
      payload['source_cursor'] = request.source_cursor;
    }
    if (request.destination_cursor !== undefined) {
      assertSourceControlCursor(
        request.destination_cursor,
        'destination_cursor',
      );
      payload['destination_cursor'] = request.destination_cursor;
    }
    if (request.source_limit !== undefined) {
      this.assertInteger(request.source_limit, 'source_limit', 1, 50);
      payload['source_limit'] = request.source_limit;
    }
    if (request.destination_limit !== undefined) {
      this.assertInteger(
        request.destination_limit,
        'destination_limit',
        1,
        50,
      );
      payload['destination_limit'] = request.destination_limit;
    }
    const sourceLimit = request.source_limit ?? 25;
    const destinationLimit = request.destination_limit ?? 25;
    if (sourceLimit * destinationLimit > 625) {
      throw new SourceControlV1ContractError('matrix_limit_invalid');
    }
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/access/matrix`, payload)
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseSourceControlAccessMatrix),
          ),
        ),
    );
  }

  listContextPolicies(
    query: SourceControlPageQuery = {},
  ): Observable<ContextPolicySummaryPage> {
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies`, {
          params: this.pageParams(query),
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicySummaryPage),
          ),
        ),
    );
  }

  listContextPolicyVersions(
    policyId: string,
    query: SourceControlPageQuery = {},
  ): Observable<ContextPolicyVersionPage> {
    const id = this.pathId(policyId, 'policy_id');
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies/${id}/versions`, {
          params: this.pageParams(query),
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyVersionPage),
          ),
        ),
    );
  }

  getContextPolicyVersion(
    policyId: string,
    version: number,
  ): Observable<ContextPolicyVersionDetail> {
    const id = this.pathId(policyId, 'policy_id');
    const normalizedVersion = this.policyVersion(version);
    return this.handle(
      this.http
        .get<unknown>(
          `${BASE_PATH}/context-policies/${id}/versions/${normalizedVersion}`,
          { observe: 'response' },
        )
        .pipe(map((response) => this.policyVersionDetail(response))),
    );
  }

  getActiveContextPolicy(
    policyId: string,
  ): Observable<ContextPolicyVersionDetail> {
    const id = this.pathId(policyId, 'policy_id');
    return this.handle(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies/${id}/active`, {
          observe: 'response',
        })
        .pipe(map((response) => this.policyVersionDetail(response))),
    );
  }

  createContextPolicyDraft(
    policyId: string,
    request: ContextPolicyDraftRequest,
    idempotencyKey: string,
  ): Observable<ContextPolicyVersion> {
    const id = this.pathId(policyId, 'policy_id');
    const document = parseContextPolicyDocument(request.document);
    if (document['policy_id'] !== policyId) {
      throw new SourceControlV1ContractError(
        'policy_document_id_mismatch',
      );
    }
    if (request.expected_latest_version !== null) {
      this.assertInteger(
        request.expected_latest_version,
        'expected_latest_version',
        1,
      );
    }
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/drafts`,
          {
            document,
            expected_latest_version: request.expected_latest_version,
            dry_run: false,
          },
          { headers: this.idempotencyHeaders(idempotencyKey) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyVersion),
          ),
        ),
    );
  }

  lintContextPolicy(
    policyId: string,
    version: number,
  ): Observable<ContextPolicyLintResult> {
    assertSourceControlOpaqueId(policyId, 'policy_id');
    const normalizedVersion = this.policyVersion(version);
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/context-policies/lint`, {
          policy_id: policyId,
          version: normalizedVersion,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyLintResult),
          ),
        ),
    );
  }

  previewContextPolicy(
    policyId: string,
    request: ContextPolicyPreviewRequest,
  ): Observable<ContextPolicyPreview> {
    const id = this.pathId(policyId, 'policy_id');
    const version = this.policyVersion(request.version);
    for (const [name, value] of [
      ['source_revision_id', request.source_revision_id],
      ['destination_id', request.destination_id],
      ['operation', request.operation],
      ['transformation', request.transformation],
    ] as const) {
      assertSourceControlOpaqueId(value, name);
    }
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/context-policies/${id}/preview`, {
          version,
          source_revision_id: request.source_revision_id,
          destination_id: request.destination_id,
          operation: request.operation,
          transformation: request.transformation,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyPreview),
          ),
        ),
    );
  }

  activateContextPolicy(
    policyId: string,
    version: number,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion> {
    return this.contextPolicyTransition(
      policyId,
      version,
      'activate',
      guard,
    );
  }

  revokeContextPolicy(
    policyId: string,
    version: number,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion> {
    return this.contextPolicyTransition(
      policyId,
      version,
      'revoke',
      guard,
    );
  }

  rollbackContextPolicy(
    policyId: string,
    request: ContextPolicyRollbackRequest,
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion> {
    const id = this.pathId(policyId, 'policy_id');
    const targetVersion = this.policyVersion(request.target_version);
    const expectedLatestVersion = this.policyVersion(
      request.expected_latest_version,
    );
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/rollback`,
          {
            target_version: targetVersion,
            expected_latest_version: expectedLatestVersion,
            dry_run: false,
          },
          { headers: this.mutationHeaders(guard) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyVersion),
          ),
        ),
    );
  }

  private lifecyclePost(
    path: string,
    guard: SourceControlMutationGuard,
    etagKind: 'resource' | 'active-pointer' = 'resource',
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}${path}`,
          { dry_run: false },
          {
            headers: etagKind === 'active-pointer'
              ? this.activePointerMutationHeaders(guard)
              : this.mutationHeaders(guard),
          },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlLifecycleAcknowledgement,
            ),
          ),
        ),
    );
  }

  private connectionOperation(
    connectionId: string,
    operation: 'refresh' | 'scan' | 'runs',
    fields: Readonly<Record<string, string>>,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlOperationReceipt> {
    const id = this.pathId(connectionId, 'connection_id');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/connections/${id}/${operation}`,
          { ...fields, dry_run: false },
          { headers: this.mutationHeaders(guard) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(
              body,
              parseSourceControlOperationReceipt,
            ),
          ),
        ),
    );
  }

  private projectionDetail(
    response: HttpResponse<unknown>,
  ): SourceControlProjectionDetail {
    const projection = parseSourceControlEnvelope(
      response.body,
      parseSourceControlProjection,
    );
    const header = response.headers.get('ETag');
    if (header === null) {
      throw new SourceControlV1ContractError('etag_header_required');
    }
    const etag = header.trim().replace(/^"|"$/g, '');
    assertSourceControlSha256(etag, 'etag_header');
    if (etag !== projection.etag) {
      throw new SourceControlV1ContractError('etag_header_mismatch');
    }
    return { projection, etag };
  }

  private mutationHeaders(guard: SourceControlMutationGuard): HttpHeaders {
    assertSourceControlEtag(guard.etag, 'if_match');
    assertSourceControlIdempotencyKey(
      guard.idempotencyKey,
      'idempotency_key',
    );
    return new HttpHeaders({
      'If-Match': `"${guard.etag}"`,
      'Idempotency-Key': guard.idempotencyKey,
    });
  }

  private activePointerMutationHeaders(
    guard: SourceControlMutationGuard,
  ): HttpHeaders {
    assertSourceControlActivePointerEtag(guard.etag, 'if_match');
    assertSourceControlIdempotencyKey(
      guard.idempotencyKey,
      'idempotency_key',
    );
    return new HttpHeaders({
      'If-Match': `"${guard.etag}"`,
      'Idempotency-Key': guard.idempotencyKey,
    });
  }

  private idempotencyHeaders(idempotencyKey: string): HttpHeaders {
    assertSourceControlIdempotencyKey(
      idempotencyKey,
      'idempotency_key',
    );
    return new HttpHeaders({ 'Idempotency-Key': idempotencyKey });
  }

  private connectionIntent(
    intent: SourceControlConnectionIntent,
  ): SourceControlConnectionIntent {
    assertSourceControlOpaqueId(intent.connector_type, 'connector_type');
    assertSourceControlOpaqueId(intent.sensitivity, 'sensitivity');
    const displayName = this.nonEmptyText(
      intent.display_name,
      'display_name',
      256,
    );
    if ('workspace_id' in intent) {
      assertSourceControlOpaqueId(intent.workspace_id, 'workspace_id');
      const relativePath = normalizeSourceWorkspaceRelativePath(
        intent.relative_path ?? '',
      );
      if (relativePath === null) {
        throw new SourceControlV1ContractError('relative_path_invalid');
      }
      return {
        connector_type: intent.connector_type,
        workspace_id: intent.workspace_id,
        ...(relativePath ? { relative_path: relativePath } : {}),
        display_name: displayName,
        sensitivity: intent.sensitivity,
      };
    }
    assertSourceControlOpaqueId(intent.remote_id, 'remote_id');
    return {
      connector_type: intent.connector_type,
      remote_id: intent.remote_id,
      display_name: displayName,
      sensitivity: intent.sensitivity,
    };
  }

  private policyVersionDetail(
    response: HttpResponse<unknown>,
  ): ContextPolicyVersionDetail {
    const policy = parseSourceControlEnvelope(
      response.body,
      parseContextPolicyVersion,
    );
    const header = response.headers.get('ETag');
    if (header === null) {
      throw new SourceControlV1ContractError('etag_header_required');
    }
    const etag = header.trim().replace(/^"|"$/g, '');
    assertSourceControlSha256(etag, 'etag_header');
    if (etag !== policy.etag) {
      throw new SourceControlV1ContractError('etag_header_mismatch');
    }
    return { policy, etag };
  }

  private contextPolicyTransition(
    policyId: string,
    version: number,
    operation: 'activate' | 'revoke',
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion> {
    const id = this.pathId(policyId, 'policy_id');
    const normalizedVersion = this.policyVersion(version);
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/versions/${normalizedVersion}/${operation}`,
          { dry_run: false },
          { headers: this.mutationHeaders(guard) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyVersion),
          ),
        ),
    );
  }

  private accessPayload(
    request: SourceControlAccessPreviewRequest,
  ): Record<string, string> {
    assertSourceControlOpaqueId(
      request.source_revision_id,
      'source_revision_id',
    );
    assertSourceControlOpaqueId(
      request.destination_id,
      'destination_id',
    );
    return {
      source_revision_id: request.source_revision_id,
      destination_id: request.destination_id,
      ...this.accessIntent(request),
    };
  }

  private accessIntent(request: {
    readonly operation: string;
    readonly transformation: string;
    readonly purpose: string;
  }): Record<string, string> {
    assertSourceControlOpaqueId(request.operation, 'operation');
    assertSourceControlOpaqueId(
      request.transformation,
      'transformation',
    );
    assertSourceControlOpaqueId(request.purpose, 'purpose');
    return {
      operation: request.operation,
      transformation: request.transformation,
      purpose: request.purpose,
    };
  }

  private connectionParams(query: SourceControlConnectionQuery): HttpParams {
    let params = this.pageParams(query);
    for (const key of [
      'state',
      'connector_type',
      'owner_id',
      'sensitivity',
    ] as const) {
      const value = query[key];
      if (value !== undefined) {
        assertSourceControlOpaqueId(value, key);
        params = params.set(key, value);
      }
    }
    return params;
  }

  private pageParams(query: SourceControlPageQuery): HttpParams {
    let params = new HttpParams();
    if (query.cursor !== undefined) {
      assertSourceControlCursor(query.cursor, 'cursor');
      params = params.set('cursor', query.cursor);
    }
    if (query.limit !== undefined) {
      this.assertInteger(query.limit, 'limit', 1, 200);
      params = params.set('limit', query.limit);
    }
    return params;
  }

  private pathId(value: string, name: string): string {
    assertSourceControlOpaqueId(value, name);
    return encodeURIComponent(value);
  }

  private policyVersion(value: number): number {
    this.assertInteger(value, 'version', 1, 2_147_483_647);
    return value;
  }

  private nonEmptyText(
    value: string,
    name: string,
    maximum: number,
  ): string {
    const normalized = String(value ?? '').trim();
    if (normalized.length === 0 || normalized.length > maximum) {
      throw new SourceControlV1ContractError(`${name}_invalid`);
    }
    return normalized;
  }

  private assertInteger(
    value: number,
    name: string,
    minimum: number,
    maximum?: number,
  ): void {
    if (
      !Number.isInteger(value) ||
      value < minimum ||
      (maximum !== undefined && value > maximum)
    ) {
      throw new SourceControlV1ContractError(`${name}_invalid`);
    }
  }

  private handle<T>(request: Observable<T>): Observable<T> {
    return request.pipe(
      catchError((error: unknown) =>
        throwError(() => this.toClientError(error)),
      ),
    );
  }

  private toClientError(error: unknown): Error {
    if (error instanceof SourceControlV1ContractError) {
      return error;
    }
    if (!(error instanceof HttpErrorResponse)) {
      return new SourceControlV1HttpError(0, 'transport_error');
    }
    try {
      const envelope = parseSourceControlErrorEnvelope(error.error);
      return new SourceControlV1HttpError(
        error.status,
        envelope.error.code,
      );
    } catch {
      return new SourceControlV1HttpError(
        error.status,
        'invalid_error_contract',
      );
    }
  }
}
