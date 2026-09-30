import { SpeechEvidenceValidationError } from '../../services/speech-evidence-sync.validators';

/** Pure, fail-closed value validators and byte helpers of the peer evidence sync flow. */

export function unique(values: readonly string[]): string[] {
  if (!Array.isArray(values) || values.length > 4096) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  const result = [...new Set(values.map(identifier))];
  if (result.length !== values.length) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  return result;
}

export function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 4096) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  return unique(value.map(identifier));
}

export function object(value: unknown, reasonCode: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new SpeechEvidenceValidationError(reasonCode);
  return value as Record<string, unknown>;
}

export function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)) {
    throw new SpeechEvidenceValidationError('speech_evidence_identifier_invalid');
  }
  return value;
}

export function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) {
    throw new SpeechEvidenceValidationError('speech_evidence_digest_invalid');
  }
  return value;
}

export function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) {
    throw new SpeechEvidenceValidationError('speech_evidence_integer_invalid');
  }
  return Number(value);
}

export function boundedText(value: unknown): string {
  if (typeof value !== 'string' || !value.length || value.length > 32_768) {
    throw new SpeechEvidenceValidationError('speech_evidence_text_invalid');
  }
  return value;
}

export function concatenate(chunks: readonly Uint8Array[]): Uint8Array {
  const size = chunks.reduce((total, value) => total + value.byteLength, 0);
  if (!size || size > 1024 * 1024) throw new SpeechEvidenceValidationError('speech_evidence_group_size_invalid');
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

export async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digestBytes = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digestBytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export async function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

export function bytesToBase64(value: Uint8Array): string {
  let binary = '';
  for (let offset = 0; offset < value.byteLength; offset += 0x8000) {
    binary += String.fromCharCode(...value.subarray(offset, Math.min(value.byteLength, offset + 0x8000)));
  }
  return btoa(binary);
}

export function sameSourceRevisions(
  first: ReadonlyMap<string, number>,
  second: ReadonlyMap<string, number>,
): boolean {
  if (first.size !== second.size) return false;
  for (const [sourceDigest, revision] of first) {
    if (second.get(sourceDigest) !== revision) return false;
  }
  return true;
}

export function reason(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const value = error as { error?: { error?: { code?: unknown } | string }; message?: unknown };
    const nested = value.error?.error;
    if (typeof nested === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(nested)) return nested;
    if (nested && typeof nested === 'object' && typeof nested.code === 'string') return nested.code;
    if (typeof value.message === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(value.message)) return value.message;
  }
  return fallback;
}
