/**
 * Pure request builders of the Source Control v1 HTTP client.
 *
 * Every identifier, cursor, ETag, idempotency key and bounded integer is
 * validated before a request is formed; headers, query parameters and
 * payloads are built here so the client only orchestrates HTTP calls.
 */

import { HttpHeaders, HttpParams } from '@angular/common/http';

import {
  SourceControlV1ContractError,
  assertSourceControlActivePointerEtag,
  assertSourceControlCursor,
  assertSourceControlEtag,
  assertSourceControlIdempotencyKey,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
} from '../models/source-control-v1-api.model';
import type {
  SourceControlBulkTarget,
  SourceControlMutation,
} from '../models/source-control-v1-api.model';
import type {
  SourceControlAccessMatrixRequest,
  SourceControlAccessPreviewRequest,
  SourceControlConnectionIntent,
  SourceControlConnectionQuery,
  SourceControlEventQuery,
  SourceControlGraphQuery,
  SourceControlMutationGuard,
  SourceControlPageQuery,
} from './source-control-v1-api.contracts';

const BULK_MUTATIONS = new Set<SourceControlMutation>([
  'refresh',
  'disable',
  'reindex',
  'grant_revoke',
]);

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

export function sourceControlMutationHeaders(guard: SourceControlMutationGuard): HttpHeaders {
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

export function sourceControlActivePointerMutationHeaders(
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

export function sourceControlIdempotencyHeaders(idempotencyKey: string): HttpHeaders {
  assertSourceControlIdempotencyKey(
    idempotencyKey,
    'idempotency_key',
  );
  return new HttpHeaders({ 'Idempotency-Key': idempotencyKey });
}

export function sourceControlConnectionIntentPayload(
  intent: SourceControlConnectionIntent,
): SourceControlConnectionIntent {
  assertSourceControlOpaqueId(intent.connector_type, 'connector_type');
  assertSourceControlOpaqueId(intent.sensitivity, 'sensitivity');
  const displayName = sourceControlNonEmptyText(
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

export function sourceControlAccessPayload(
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
    ...sourceControlAccessIntent(request),
  };
}

export function sourceControlAccessIntent(request: {
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

export function sourceControlConnectionParams(query: SourceControlConnectionQuery): HttpParams {
  let params = sourceControlPageParams(query);
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

export function sourceControlPageParams(query: SourceControlPageQuery): HttpParams {
  let params = new HttpParams();
  if (query.cursor !== undefined) {
    assertSourceControlCursor(query.cursor, 'cursor');
    params = params.set('cursor', query.cursor);
  }
  if (query.limit !== undefined) {
    assertSourceControlInteger(query.limit, 'limit', 1, 200);
    params = params.set('limit', query.limit);
  }
  return params;
}

export function sourceControlPathId(value: string, name: string): string {
  assertSourceControlOpaqueId(value, name);
  return encodeURIComponent(value);
}

export function sourceControlPolicyVersion(value: number): number {
  assertSourceControlInteger(value, 'version', 1, 2_147_483_647);
  return value;
}

export function sourceControlNonEmptyText(
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

export function assertSourceControlInteger(
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

export function sourceControlGraphParams(
  query: SourceControlGraphQuery,
): HttpParams {
  let params = new HttpParams();
  if (query.cursor !== undefined) {
    assertSourceControlCursor(query.cursor, 'cursor');
    params = params.set('cursor', query.cursor);
  }
  if (query.limit !== undefined) {
    assertSourceControlInteger(query.limit, 'limit', 1, 500);
    params = params.set('limit', query.limit);
  }
  if (query.view !== undefined) {
    assertSourceControlOpaqueId(query.view, 'view');
    params = params.set('view', query.view);
  }
  if (query.maxEdges !== undefined) {
    assertSourceControlInteger(query.maxEdges, 'max_edges', 1, 2000);
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
  return params;
}

export function sourceControlEventParams(
  query: SourceControlEventQuery,
): HttpParams {
  let params = new HttpParams();
  if (query.after_sequence !== undefined) {
    assertSourceControlInteger(query.after_sequence, 'after_sequence', 0);
    params = params.set('after_sequence', query.after_sequence);
  }
  if (query.limit !== undefined) {
    assertSourceControlInteger(query.limit, 'limit', 1, 500);
    params = params.set('limit', query.limit);
  }
  return params;
}

export function sourceControlAccessMatrixPayload(
  request: SourceControlAccessMatrixRequest,
): Record<string, string | number> {
  const payload: Record<string, string | number> = sourceControlAccessIntent(request);
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
    assertSourceControlInteger(request.source_limit, 'source_limit', 1, 50);
    payload['source_limit'] = request.source_limit;
  }
  if (request.destination_limit !== undefined) {
    assertSourceControlInteger(
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
  return payload;
}

export function validatedSourceControlBulkTargets(
  mutation: SourceControlMutation,
  targets: readonly SourceControlBulkTarget[],
): readonly SourceControlBulkTarget[] {
  if (!BULK_MUTATIONS.has(mutation)) {
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
  return validatedTargets;
}
