import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { map, Observable, throwError, timeout } from 'rxjs';

import {
  UnslothMutationCommand,
  UnslothMutationOperation,
  UnslothMutationResult,
} from '../model-training.models';

const MUTATION_TIMEOUT_MS = 120_000;
const MUTATION_ROUTE = '/api/ml-intern-training/unsloth/mutations';
const SUPPORTED_OPERATIONS: readonly UnslothMutationOperation[] = [
  'export',
  'runtime_handoff',
  'mcp',
  'cleanup',
];
const OPAQUE_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const OPAQUE_RESOURCE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;

@Injectable({ providedIn: 'root' })
export class UnslothHubMutationService {
  private readonly http = inject(HttpClient);

  submit(
    hubUrl: string,
    command: UnslothMutationCommand,
    idempotencyKey: string,
  ): Observable<UnslothMutationResult> {
    if (!SUPPORTED_OPERATIONS.includes(command.operation)) {
      return throwError(() => new Error('unsupported_unsloth_operation'));
    }
    if (!OPAQUE_KEY.test(idempotencyKey)) {
      return throwError(() => new Error('invalid_idempotency_key'));
    }
    if (
      command.operation === 'runtime_handoff' &&
      (!command.provider_descriptor ||
        !command.endpoint_descriptor ||
        containsDirectTarget(command.provider_descriptor) ||
        containsDirectTarget(command.endpoint_descriptor))
    ) {
      return throwError(() => new Error('invalid_runtime_handoff_descriptor'));
    }
    if (
      command.operation === 'cleanup' &&
      (!Array.isArray(command.artifact_ids) ||
        command.artifact_ids.length === 0 ||
        command.artifact_ids.length > 500 ||
        new Set(command.artifact_ids).size !== command.artifact_ids.length ||
        command.artifact_ids.some((value) => !OPAQUE_RESOURCE.test(value)) ||
        typeof command.expected_catalog_revision !== 'number' ||
        !Number.isSafeInteger(command.expected_catalog_revision) ||
        Number(command.expected_catalog_revision) < 0 ||
        (command.retention_before !== undefined &&
          (!Number.isSafeInteger(command.retention_before) || command.retention_before <= 0)))
    ) {
      return throwError(() => new Error('invalid_storage_cleanup_contract'));
    }

    let hub: URL;
    try {
      hub = new URL(hubUrl);
    } catch {
      return throwError(() => new Error('invalid_hub_url'));
    }
    if (
      (hub.protocol !== 'http:' && hub.protocol !== 'https:') ||
      hub.username ||
      hub.password ||
      hub.search ||
      hub.hash
    ) {
      return throwError(() => new Error('invalid_hub_url'));
    }

    const baseUrl = hubUrl.replace(/\/+$/, '');
    const endpoint = `${baseUrl}${MUTATION_ROUTE}/${command.operation}`;
    const headers = new HttpHeaders({ 'Idempotency-Key': idempotencyKey });

    return this.http
      .post<{ data: unknown }>(endpoint, command, { headers })
      .pipe(
        timeout(MUTATION_TIMEOUT_MS),
        map((response) => normalizeMutationResult(response.data, command.operation)),
      );
  }
}

function normalizeMutationResult(
  value: unknown,
  operation: UnslothMutationOperation,
): UnslothMutationResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('invalid_unsloth_mutation_response');
  }
  const source = value as Record<string, unknown>;
  const accepted = source['accepted'];
  if (typeof accepted !== 'boolean' || source['operation'] !== operation) {
    throw new Error('invalid_unsloth_mutation_response');
  }
  const opaque = (
    candidate: unknown,
    pattern: RegExp = OPAQUE_RESOURCE,
  ): string | undefined => {
    const normalized = typeof candidate === 'string' ? candidate.trim() : '';
    return pattern.test(normalized) ? normalized : undefined;
  };
  return {
    accepted,
    operation,
    resource_id: opaque(source['resource_id']),
    dry_run: typeof source['dry_run'] === 'boolean' ? source['dry_run'] : undefined,
    reason_code: opaque(source['reason_code']),
    confirmation_id: opaque(source['confirmation_id'], OPAQUE_KEY),
    replayed: typeof source['replayed'] === 'boolean' ? source['replayed'] : undefined,
  };
}

function containsDirectTarget(value: unknown, key = ''): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => containsDirectTarget(item, key));
  }
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).some(([childKey, child]) => {
      const normalizedKey = childKey.toLowerCase().replaceAll('-', '_');
      return (
        normalizedKey === 'url' ||
        normalizedKey.endsWith('_url') ||
        normalizedKey === 'host' ||
        normalizedKey === 'filesystem_path' ||
        containsDirectTarget(child, normalizedKey)
      );
    });
  }
  if (typeof value !== 'string') {
    return false;
  }
  const normalized = value.trim().toLowerCase();
  return (
    normalized.startsWith('http://') ||
    normalized.startsWith('https://') ||
    normalized.startsWith('file://') ||
    (key !== 'model_id' && normalized.startsWith('/'))
  );
}
