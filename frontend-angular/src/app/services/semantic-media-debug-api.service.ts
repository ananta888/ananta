import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { SemanticDebugEventView } from '../features/pair-view/semantic-debug-panel.component';
import { HubApiCoreService } from './hub-api-core.service';

export interface SemanticDebugPage {
  readonly items: readonly SemanticDebugEventView[];
  readonly nextCursor: string | null;
}

@Injectable({ providedIn: 'root' })
export class SemanticMediaDebugApiService {
  private readonly core = inject(HubApiCoreService);

  page(
    hubUrl: string,
    logicalScope: string,
    cursor: string | null = null,
    limit = 50,
  ): Observable<SemanticDebugPage> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      scope: scope(logicalScope),
      limit: String(boundedInteger(limit, 1, 100)),
    });
    if (cursor) query.set('cursor', identifier(cursor));
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/semantic-media/debug/events?${query.toString()}`,
      base,
    ).pipe(map(parsePage));
  }
}

export function parseSemanticDebugEvent(value: unknown): SemanticDebugEventView {
  const row = record(value);
  const expected = new Set([
    'event_id', 'tenant_digest', 'scope_digest', 'event_type', 'transition',
    'reason_code', 'epoch', 'contract_ref', 'lease_ref', 'job_ref',
    'created_at_ms', 'expires_at_ms',
  ]);
  if (Object.keys(row).some((key) => !expected.has(key)) || Object.keys(row).length !== expected.size) {
    throw new Error('semantic_debug_event_shape_invalid');
  }
  digest(row['tenant_digest']); // Tenant binding is verified but never rendered.
  const created = boundedInteger(row['created_at_ms'], 1, Number.MAX_SAFE_INTEGER);
  const expires = boundedInteger(row['expires_at_ms'], created + 1, Number.MAX_SAFE_INTEGER);
  return Object.freeze({
    event_id: identifier(row['event_id']),
    scope_digest: digest(row['scope_digest']),
    event_type: identifier(row['event_type']),
    transition: identifier(row['transition']),
    reason_code: identifier(row['reason_code']),
    epoch: boundedInteger(row['epoch'], 1, 2_147_483_647),
    contract_ref: optionalDigest(row['contract_ref']),
    lease_ref: optionalDigest(row['lease_ref']),
    job_ref: optionalDigest(row['job_ref']),
    created_at_ms: created,
    expires_at_ms: expires,
  });
}

function parsePage(value: unknown): SemanticDebugPage {
  const envelope = record(value);
  if (envelope['ok'] !== true) throw new Error('semantic_debug_response_invalid');
  const data = record(envelope['data']);
  if (data['read_only'] !== true || !Array.isArray(data['items']) || data['items'].length > 100) {
    throw new Error('semantic_debug_page_invalid');
  }
  const cursor = data['next_cursor'];
  return Object.freeze({
    items: Object.freeze(data['items'].map(parseSemanticDebugEvent)),
    nextCursor: cursor == null ? null : identifier(cursor),
  });
}

function normalizeHubUrl(value: string): string {
  const normalized = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(normalized)) throw new Error('semantic_debug_hub_url_invalid');
  return normalized;
}

function scope(value: string): string {
  const normalized = String(value || '').trim();
  if (
    normalized.length < 8 || normalized.length > 256 || /\s/.test(normalized)
    || !/^(semantic-contract|semantic-media-session|speech-job):[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(normalized)
  ) throw new Error('semantic_debug_scope_invalid');
  return normalized;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('semantic_debug_response_invalid');
  }
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  const normalized = String(value ?? '');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(normalized)) {
    throw new Error('semantic_debug_identifier_invalid');
  }
  return normalized;
}

function digest(value: unknown): string {
  const normalized = String(value ?? '');
  if (!/^[a-f0-9]{64}$/.test(normalized)) throw new Error('semantic_debug_digest_invalid');
  return normalized;
}

function optionalDigest(value: unknown): string | null {
  return value == null ? null : digest(value);
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new Error('semantic_debug_integer_invalid');
  }
  return Number(value);
}
