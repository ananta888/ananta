/**
 * Angular client of the canonical Source Control v1 API.
 *
 * ``SourceControlV1ApiClient`` orchestrates the HTTP calls; request
 * validation/formatting lives in ``source-control-v1-request-builders``,
 * response readers and error mapping in ``source-control-v1-response-readers``
 * and the contracts in ``source-control-v1-api.contracts``. All previously
 * exported names stay importable from this module.
 */

import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import {
  SourceControlV1ContractError,
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
  parseSourceControlExplorationResult,
  parseSourceControlIndexComparison,
  parseSourceControlJobEventPage,
  parseSourceControlLifecycleAcknowledgement,
  parseSourceControlOperationReceipt,
  parseSourceControlProjectionPage,
  parseSourceControlRunPage,
} from '../models/source-control-v1-api.model';
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
import { SourceControlV1HttpError } from './source-control-v1-api.contracts';
import type {
  CodeHugMutationResult,
  ContextPolicyDraftRequest,
  ContextPolicyLifecycleApi,
  ContextPolicyPreviewRequest,
  ContextPolicyRollbackRequest,
  SourceControlAccessApi,
  SourceControlAccessMatrixRequest,
  SourceControlAccessPreviewRequest,
  SourceControlBulkApi,
  SourceControlConnectionIntent,
  SourceControlConnectionQuery,
  SourceControlEventApi,
  SourceControlEventQuery,
  SourceControlGraphQuery,
  SourceControlLifecycleApi,
  SourceControlMutationGuard,
  SourceControlPageQuery,
  SourceControlQueryRequest,
  SourceControlReadApi,
} from './source-control-v1-api.contracts';
import {
  assertSourceControlInteger,
  sourceControlAccessMatrixPayload,
  sourceControlAccessPayload,
  sourceControlActivePointerMutationHeaders,
  sourceControlConnectionIntentPayload,
  sourceControlConnectionParams,
  sourceControlEventParams,
  sourceControlGraphParams,
  sourceControlIdempotencyHeaders,
  sourceControlMutationHeaders,
  sourceControlNonEmptyText,
  sourceControlPageParams,
  sourceControlPathId,
  sourceControlPolicyVersion,
  validatedSourceControlBulkTargets,
  normalizeSourceWorkspaceRelativePath,
} from './source-control-v1-request-builders';
import {
  handleSourceControlRequest,
  parseCodeHugMutationResult,
  readContextPolicyVersionDetail,
  readSourceControlProjectionDetail,
} from './source-control-v1-response-readers';

export { SourceControlV1HttpError, normalizeSourceWorkspaceRelativePath };
export type {
  CodeHugMutationResult,
  ContextPolicyDraftRequest,
  ContextPolicyLifecycleApi,
  ContextPolicyPreviewRequest,
  ContextPolicyRollbackRequest,
  SourceControlAccessApi,
  SourceControlAccessMatrixRequest,
  SourceControlAccessPreviewRequest,
  SourceControlBulkApi,
  SourceControlConnectionIntent,
  SourceControlConnectionQuery,
  SourceControlEventApi,
  SourceControlEventQuery,
  SourceControlGraphQuery,
  SourceControlLifecycleApi,
  SourceControlMutationGuard,
  SourceControlPageQuery,
  SourceControlQueryRequest,
  SourceControlReadApi,
} from './source-control-v1-api.contracts';

const BASE_PATH = '/api/source-control/v1';

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
    return handleSourceControlRequest(
      this.http
        .post<unknown>(`${BASE_PATH}/connections/validate`, {
          ...sourceControlConnectionIntentPayload(intent),
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
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/connections`,
          {
            ...sourceControlConnectionIntentPayload(intent),
            dry_run: false,
          },
          { headers: sourceControlIdempotencyHeaders(idempotencyKey) },
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
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/codehug/mutations`,
          {
            mutation_intent_id: mutationIntentId,
            dry_run: false,
          },
          { headers: sourceControlIdempotencyHeaders(idempotencyKey) },
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
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/connections`, {
          params: sourceControlConnectionParams(query),
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
    const id = sourceControlPathId(connectionId, 'connection_id');
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/connections/${id}`, {
          observe: 'response',
        })
        .pipe(map((response) => readSourceControlProjectionDetail(response))),
    );
  }

  listRuns(
    connectionId: string,
    query: SourceControlPageQuery = {},
  ): Observable<SourceControlRunPage> {
    const id = sourceControlPathId(connectionId, 'connection_id');
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/connections/${id}/runs`, {
          params: sourceControlPageParams(query),
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
    return handleSourceControlRequest(
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
    const id = sourceControlPathId(connectionId, 'connection_id');
    const params = sourceControlGraphParams(query);
    return handleSourceControlRequest(
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
    const id = sourceControlPathId(connectionId, 'connection_id');
    const query = sourceControlNonEmptyText(request.query, 'query', 4000);
    const payload: { query: string; limit?: number } = { query };
    if (request.limit !== undefined) {
      assertSourceControlInteger(request.limit, 'limit', 1, 100);
      payload.limit = request.limit;
    }
    return handleSourceControlRequest(
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
    const connection = sourceControlPathId(connectionId, 'connection_id');
    const artifact = sourceControlPathId(artifactId, 'artifact_id');
    return handleSourceControlRequest(
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
      `/indices/${sourceControlPathId(indexId, 'index_id')}/activate`,
      guard,
      'active-pointer',
    );
  }

  rollbackIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/indices/${sourceControlPathId(indexId, 'index_id')}/rollback`,
      guard,
      'active-pointer',
    );
  }

  disableConnection(
    connectionId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/connections/${sourceControlPathId(connectionId, 'connection_id')}/disable`,
      guard,
    );
  }

  tombstoneIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    return this.lifecyclePost(
      `/indices/${sourceControlPathId(indexId, 'index_id')}/tombstone`,
      guard,
    );
  }

  purgeIndex(
    indexId: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlLifecycleAcknowledgement> {
    const id = sourceControlPathId(indexId, 'index_id');
    const headers = sourceControlMutationHeaders(guard);
    return handleSourceControlRequest(
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
    const validatedTargets = validatedSourceControlBulkTargets(
      mutation,
      targets,
    );
    return handleSourceControlRequest(
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
    return handleSourceControlRequest(
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
    const params = sourceControlEventParams(query);
    return handleSourceControlRequest(
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
    const payload = sourceControlAccessPayload(request);
    return handleSourceControlRequest(
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
    const payload = sourceControlAccessMatrixPayload(request);
    return handleSourceControlRequest(
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
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies`, {
          params: sourceControlPageParams(query),
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
    const id = sourceControlPathId(policyId, 'policy_id');
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies/${id}/versions`, {
          params: sourceControlPageParams(query),
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
    const id = sourceControlPathId(policyId, 'policy_id');
    const normalizedVersion = sourceControlPolicyVersion(version);
    return handleSourceControlRequest(
      this.http
        .get<unknown>(
          `${BASE_PATH}/context-policies/${id}/versions/${normalizedVersion}`,
          { observe: 'response' },
        )
        .pipe(map((response) => readContextPolicyVersionDetail(response))),
    );
  }

  getActiveContextPolicy(
    policyId: string,
  ): Observable<ContextPolicyVersionDetail> {
    const id = sourceControlPathId(policyId, 'policy_id');
    return handleSourceControlRequest(
      this.http
        .get<unknown>(`${BASE_PATH}/context-policies/${id}/active`, {
          observe: 'response',
        })
        .pipe(map((response) => readContextPolicyVersionDetail(response))),
    );
  }

  createContextPolicyDraft(
    policyId: string,
    request: ContextPolicyDraftRequest,
    idempotencyKey: string,
  ): Observable<ContextPolicyVersion> {
    const id = sourceControlPathId(policyId, 'policy_id');
    const document = parseContextPolicyDocument(request.document);
    if (document['policy_id'] !== policyId) {
      throw new SourceControlV1ContractError(
        'policy_document_id_mismatch',
      );
    }
    if (request.expected_latest_version !== null) {
      assertSourceControlInteger(
        request.expected_latest_version,
        'expected_latest_version',
        1,
      );
    }
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/drafts`,
          {
            document,
            expected_latest_version: request.expected_latest_version,
            dry_run: false,
          },
          { headers: sourceControlIdempotencyHeaders(idempotencyKey) },
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
    const normalizedVersion = sourceControlPolicyVersion(version);
    return handleSourceControlRequest(
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
    const id = sourceControlPathId(policyId, 'policy_id');
    const version = sourceControlPolicyVersion(request.version);
    for (const [name, value] of [
      ['source_revision_id', request.source_revision_id],
      ['destination_id', request.destination_id],
      ['operation', request.operation],
      ['transformation', request.transformation],
    ] as const) {
      assertSourceControlOpaqueId(value, name);
    }
    return handleSourceControlRequest(
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
    const id = sourceControlPathId(policyId, 'policy_id');
    const targetVersion = sourceControlPolicyVersion(request.target_version);
    const expectedLatestVersion = sourceControlPolicyVersion(
      request.expected_latest_version,
    );
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/rollback`,
          {
            target_version: targetVersion,
            expected_latest_version: expectedLatestVersion,
            dry_run: false,
          },
          { headers: sourceControlMutationHeaders(guard) },
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
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}${path}`,
          { dry_run: false },
          {
            headers: etagKind === 'active-pointer'
              ? sourceControlActivePointerMutationHeaders(guard)
              : sourceControlMutationHeaders(guard),
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
    const id = sourceControlPathId(connectionId, 'connection_id');
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/connections/${id}/${operation}`,
          { ...fields, dry_run: false },
          { headers: sourceControlMutationHeaders(guard) },
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

  private contextPolicyTransition(
    policyId: string,
    version: number,
    operation: 'activate' | 'revoke',
    guard: SourceControlMutationGuard,
  ): Observable<ContextPolicyVersion> {
    const id = sourceControlPathId(policyId, 'policy_id');
    const normalizedVersion = sourceControlPolicyVersion(version);
    return handleSourceControlRequest(
      this.http
        .post<unknown>(
          `${BASE_PATH}/context-policies/${id}/versions/${normalizedVersion}/${operation}`,
          { dry_run: false },
          { headers: sourceControlMutationHeaders(guard) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContextPolicyVersion),
          ),
        ),
    );
  }
}
