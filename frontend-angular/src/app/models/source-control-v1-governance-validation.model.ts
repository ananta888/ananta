/** Shared fail-closed validators of the Source Control v1 governance parsers. */

import {
  SourceControlV1ContractError,
  assertSourceControlOpaqueId,
} from './source-control-v1-api.model';
import type {
  SourceControlJson,
  SourceControlJsonObject,
} from './source-control-v1-api.model';

export const SENSITIVE_KEYS = new Set([
  'absolute_path',
  'clone_url',
  'credential',
  'credential_ref',
  'credentials',
  'file_content',
  'private_remote_url',
  'prompt',
  'raw_content',
  'remote_url',
  'secret',
  'token',
]);

export function positiveInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) {
    fail(`${path}_invalid`);
  }
  return value as number;
}

export function requireCapability(
  capabilities: Record<string, unknown>,
  key: string,
  expected: boolean,
  path: string,
): void {
  if (
    typeof capabilities[key] !== 'boolean'
    || capabilities[key] !== expected
  ) {
    fail(`${path}.${key}_invalid`);
  }
}

export function nullableOpaqueId(value: unknown, path: string): string | null {
  if (value === null) return null;
  assertSourceControlOpaqueId(value, path);
  return value;
}

export function nonNegativeInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    fail(`${path}_invalid`);
  }
  return Number(value);
}

export function safeObject(value: unknown, path: string): SourceControlJsonObject {
  const input = objectValue(value, path);
  const output: Record<string, SourceControlJson> = {};
  for (const [key, item] of Object.entries(input)) {
    if (SENSITIVE_KEYS.has(key.toLowerCase())) {
      fail(`${path}.${key}_forbidden`);
    }
    output[key] = safeValue(item, `${path}.${key}`);
  }
  return output;
}

function safeValue(value: unknown, path: string): SourceControlJson {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value as null | boolean | string;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    return value.map((item, index) => safeValue(item, `${path}[${index}]`));
  }
  return safeObject(value, path);
}

export function cursor(value: unknown, path: string): string | null {
  if (value === null) return null;
  if (
    typeof value !== 'string'
    || !/^[A-Za-z0-9_-]{1,512}$/.test(value)
  ) fail(`${path}_invalid`);
  return value;
}

export function text(
  value: unknown,
  path: string,
  maximum: number,
  allowEmpty = false,
): string {
  if (typeof value !== 'string') fail(`${path}_invalid`);
  const normalized = value.trim();
  if ((!allowEmpty && !normalized) || normalized.length > maximum) {
    fail(`${path}_invalid`);
  }
  return normalized;
}

export function integer(value: unknown, path: string, minimum: number): number {
  if (!Number.isInteger(value) || Number(value) < minimum) {
    fail(`${path}_invalid`);
  }
  return Number(value);
}

export function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') fail(`${path}_invalid`);
  return value;
}

export function objectValue(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${path}_object_required`);
  }
  return value as Record<string, unknown>;
}

export function arrayValue(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) fail(`${path}_array_required`);
  return value;
}

export function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  path: string,
): void {
  const keys = new Set(expected);
  if (
    Object.keys(value).length !== keys.size
    || Object.keys(value).some((key) => !keys.has(key))
  ) fail(`${path}_properties_invalid`);
}

export function fail(reasonCode: string): never {
  throw new SourceControlV1ContractError(reasonCode);
}
