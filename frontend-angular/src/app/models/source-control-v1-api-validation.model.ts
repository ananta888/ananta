/**
 * Fail-closed guards for Source Control v1 identifiers, cursors, ETags and
 * envelopes, plus the primitive JSON validators shared by the parsers.
 */

import {
  SOURCE_CONTROL_V1_ERROR_SCHEMA,
  SOURCE_CONTROL_V1_RESPONSE_SCHEMA,
  SourceControlV1ContractError,
} from './source-control-v1-api-types.model';
import type {
  ContextPolicyDiagnosticSeverity,
  ContextPolicyState,
  SourceControlAccessDecisionKind,
  SourceControlErrorEnvelope,
  SourceControlJobEventType,
  SourceControlJson,
  SourceControlJsonObject,
  SourceControlMutation,
  SourceControlNextAction,
} from './source-control-v1-api-types.model';

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$/;
const CURSOR = /^[A-Za-z0-9_-]{1,512}$/;
const SHA256 = /^[0-9a-f]{64}$/;
export const NEXT_ACTIONS = new Set<SourceControlNextAction>([
  'refresh',
  'scan',
  'index',
  'activate',
  'grant',
  'disable',
  'rollback',
]);
const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$/;
const ETAG = /^(?:[0-9a-f]{64}|index:[1-9][0-9]*)$/;
const ACTIVE_POINTER_ETAG = /^active:(?:0|[1-9][0-9]*)$/;
export const POLICY_STATES = new Set<ContextPolicyState>([
  'draft',
  'active',
  'superseded',
  'revoked',
]);
export const POLICY_DIAGNOSTIC_SEVERITIES =
  new Set<ContextPolicyDiagnosticSeverity>(['error', 'warning', 'info']);
export const MUTATIONS = new Set<SourceControlMutation>([
  'refresh',
  'disable',
  'reindex',
  'grant_revoke',
]);
export const EVENT_TYPES = new Set<SourceControlJobEventType>([
  'source_refresh',
  'source_scan',
  'source_admission',
  'index_queued',
  'index_started',
  'index_progress',
  'index_completed',
  'index_failed',
  'index_cancelled',
  'index_activated',
  'index_rolled_back',
]);
export const ACCESS_DECISIONS = new Set<SourceControlAccessDecisionKind>([
  'allow',
  'deny',
  'approval_required',
  'unavailable',
]);
const SENSITIVE_RESPONSE_KEYS = new Set([
  'absolute_path',
  'credential',
  'credentials',
  'file_content',
  'private_remote_url',
  'prompt',
  'raw_content',
  'secret',
  'token',
]);

export function assertSourceControlOpaqueId(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !OPAQUE_ID.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlCursor(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !CURSOR.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlSha256(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !SHA256.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlIdempotencyKey(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !IDEMPOTENCY_KEY.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlEtag(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !ETAG.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlActivePointerEtag(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !ACTIVE_POINTER_ETAG.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function parseSourceControlEnvelope<T>(
  value: unknown,
  parseData: (data: unknown, path: string) => T,
): T {
  const envelope = record(value, 'response');
  exactKeys(envelope, ['schema', 'data'], 'response');
  literal(
    envelope['schema'],
    SOURCE_CONTROL_V1_RESPONSE_SCHEMA,
    'response.schema',
  );
  return parseData(envelope['data'], 'response.data');
}

export function parseSourceControlErrorEnvelope(
  value: unknown,
): SourceControlErrorEnvelope {
  const envelope = record(value, 'error_response');
  exactKeys(envelope, ['schema', 'error'], 'error_response');
  literal(
    envelope['schema'],
    SOURCE_CONTROL_V1_ERROR_SCHEMA,
    'error_response.schema',
  );
  const error = record(envelope['error'], 'error_response.error');
  exactKeys(error, ['code'], 'error_response.error');
  assertSourceControlOpaqueId(error['code'], 'error_response.error.code');
  return {
    schema: SOURCE_CONTROL_V1_ERROR_SCHEMA,
    error: { code: error['code'] },
  };
}

export function safeJsonObject(
  value: unknown,
  path: string,
): SourceControlJsonObject {
  const object = record(value, path);
  const result: Record<string, SourceControlJson> = {};
  for (const [key, nestedValue] of Object.entries(object)) {
    if (SENSITIVE_RESPONSE_KEYS.has(key.toLowerCase())) {
      fail(`${path}.${key}_forbidden`);
    }
    result[key] = safeJson(nestedValue, `${path}.${key}`);
  }
  return result;
}

function safeJson(value: unknown, path: string): SourceControlJson {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value as null | boolean | string;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => safeJson(item, `${path}[${index}]`));
  }
  return safeJsonObject(value, path);
}

export function nullableJsonObject(
  value: unknown,
  path: string,
): SourceControlJsonObject | null {
  return value === null ? null : safeJsonObject(value, path);
}

export function opaqueIdArray(value: unknown, path: string): readonly string[] {
  return array(value, path).map((item, index) => {
    assertSourceControlOpaqueId(item, `${path}[${index}]`);
    return item;
  });
}

export function nullableCursor(value: unknown, path: string): string | null {
  if (value === null) {
    return null;
  }
  assertSourceControlCursor(value, path);
  return value;
}

export function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    fail(`${path}_object_required`);
  }
  return value as Record<string, unknown>;
}

export function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    fail(`${path}_array_required`);
  }
  return value;
}

export function exactKeys(
  value: Record<string, unknown>,
  expectedKeys: readonly string[],
  path: string,
): void {
  const expected = new Set(expectedKeys);
  const actual = Object.keys(value);
  if (
    actual.length !== expected.size ||
    actual.some((key) => !expected.has(key))
  ) {
    fail(`${path}_properties_invalid`);
  }
}

export function literal(value: unknown, expected: string, path: string): void {
  if (value !== expected) {
    fail(`${path}_invalid`);
  }
}

export function integer(
  value: unknown,
  path: string,
  minimum: number,
): asserts value is number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    fail(`${path}_invalid`);
  }
}

export function fail(reasonCode: string): never {
  throw new SourceControlV1ContractError(reasonCode);
}
