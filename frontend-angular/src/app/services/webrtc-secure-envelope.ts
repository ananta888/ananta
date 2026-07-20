/** Closed TypeScript implementation of secure_envelope.v1.json. */

export const SECURE_ENVELOPE_VERSION = 1 as const;
export const MAX_SECURE_CIPHERTEXT_BYTES = 256 * 1024 + 16;
export const MAX_SECURE_SEQUENCE = Number.MAX_SAFE_INTEGER;

export type SecurityTrafficClass = 'control' | 'media' | 'semantic' | 'bulk';
export type SecurePayloadEncoding = 'json' | 'binary';

export interface SecureEnvelopeV1 {
  version: typeof SECURE_ENVELOPE_VERSION;
  scope: { kind: 'session' | 'room'; id: string };
  sender_id: string;
  recipient: { kind: 'peer' | 'group'; id: string };
  epoch: number;
  sequence: number;
  key_id: string;
  payload_type: string;
  expires_at_ms: number;
  nonce_b64: string;
  aad: {
    traffic_class: SecurityTrafficClass;
    content_encoding: SecurePayloadEncoding;
    contract_digest: string;
  };
  ciphertext_b64: string;
}

export class SecureEnvelopeError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const PAYLOAD_RE = /^[a-z][a-z0-9_.-]{0,63}$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const B64_RE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

export function parseSecureEnvelope(
  raw: unknown,
  options: { nowMs?: number; checkTime?: boolean } = {},
): SecureEnvelopeV1 {
  const obj = closedObject(raw, [
    'version', 'scope', 'sender_id', 'recipient', 'epoch', 'sequence',
    'key_id', 'payload_type', 'expires_at_ms', 'nonce_b64', 'aad',
    'ciphertext_b64',
  ]);
  if (obj['version'] !== 1) throw new SecureEnvelopeError('version_unsupported');
  const scope = closedObject(obj['scope'], ['kind', 'id']);
  if (scope['kind'] !== 'session' && scope['kind'] !== 'room') {
    throw new SecureEnvelopeError('scope_invalid');
  }
  const recipient = closedObject(obj['recipient'], ['kind', 'id']);
  if (recipient['kind'] !== 'peer' && recipient['kind'] !== 'group') {
    throw new SecureEnvelopeError('recipient_invalid');
  }
  const aad = closedObject(obj['aad'], ['traffic_class', 'content_encoding', 'contract_digest']);
  if (!['control', 'media', 'semantic', 'bulk'].includes(String(aad['traffic_class']))) {
    throw new SecureEnvelopeError('aad_invalid');
  }
  if (aad['content_encoding'] !== 'json' && aad['content_encoding'] !== 'binary') {
    throw new SecureEnvelopeError('aad_invalid');
  }
  if (typeof aad['contract_digest'] !== 'string' || !DIGEST_RE.test(aad['contract_digest'])) {
    throw new SecureEnvelopeError('aad_invalid');
  }
  const epoch = boundedInteger(obj['epoch'], 1, 2 ** 31 - 1, 'epoch_invalid');
  const sequence = boundedInteger(obj['sequence'], 1, MAX_SECURE_SEQUENCE, 'sequence_invalid');
  const expiresAt = boundedInteger(obj['expires_at_ms'], 1, MAX_SECURE_SEQUENCE, 'expiry_invalid');
  const now = options.nowMs ?? Date.now();
  if (options.checkTime !== false && expiresAt < now - 30_000) {
    throw new SecureEnvelopeError('expired');
  }
  if (options.checkTime !== false && expiresAt > now + 10 * 60_000) {
    throw new SecureEnvelopeError('expiry_too_far');
  }
  if (typeof obj['payload_type'] !== 'string' || !PAYLOAD_RE.test(obj['payload_type'])) {
    throw new SecureEnvelopeError('payload_type_invalid');
  }
  const nonce = decodeB64(obj['nonce_b64'], 'nonce_invalid');
  if (nonce.byteLength !== 12) throw new SecureEnvelopeError('nonce_invalid');
  const ciphertext = decodeB64(obj['ciphertext_b64'], 'ciphertext_invalid');
  if (ciphertext.byteLength < 16) throw new SecureEnvelopeError('ciphertext_invalid');
  if (ciphertext.byteLength > MAX_SECURE_CIPHERTEXT_BYTES) {
    throw new SecureEnvelopeError('ciphertext_oversize');
  }
  return {
    version: 1,
    scope: { kind: scope['kind'], id: identifier(scope['id'], 'scope_invalid') },
    sender_id: identifier(obj['sender_id'], 'sender_invalid'),
    recipient: { kind: recipient['kind'], id: identifier(recipient['id'], 'recipient_invalid') },
    epoch,
    sequence,
    key_id: identifier(obj['key_id'], 'key_id_invalid'),
    payload_type: obj['payload_type'],
    expires_at_ms: expiresAt,
    nonce_b64: encodeB64(nonce),
    aad: {
      traffic_class: aad['traffic_class'] as SecurityTrafficClass,
      content_encoding: aad['content_encoding'] as SecurePayloadEncoding,
      contract_digest: aad['contract_digest'],
    },
    ciphertext_b64: encodeB64(ciphertext),
  };
}

export function secureEnvelopeAad(envelope: SecureEnvelopeV1): Uint8Array {
  const { ciphertext_b64: _ciphertext, ...metadata } = envelope;
  return new TextEncoder().encode(canonicalSecurityJson({
    domain: 'ananta.webrtc.secure-envelope.v1', envelope: metadata,
  }));
}

export function canonicalSecurityJson(value: unknown): string {
  return JSON.stringify(sortCanonical(value));
}

export function encodeB64(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function decodeB64(value: unknown, reasonCode = 'base64_invalid'): Uint8Array {
  if (typeof value !== 'string' || !value || value.length > 400_000 || !B64_RE.test(value)) {
    throw new SecureEnvelopeError(reasonCode);
  }
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    throw new SecureEnvelopeError(reasonCode);
  }
}

function closedObject(value: unknown, expected: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SecureEnvelopeError('envelope_invalid_type');
  }
  const obj = value as Record<string, unknown>;
  const actual = Object.keys(obj);
  if (actual.some((key) => !expected.includes(key))) throw new SecureEnvelopeError('unknown_field');
  if (expected.some((key) => !(key in obj))) throw new SecureEnvelopeError('required_field_missing');
  return obj;
}

function identifier(value: unknown, reason: string): string {
  if (typeof value !== 'string' || !ID_RE.test(value)) throw new SecureEnvelopeError(reason);
  return value;
}

function boundedInteger(value: unknown, low: number, high: number, reason: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < low || (value as number) > high) {
    throw new SecureEnvelopeError(reason);
  }
  return value as number;
}

function sortCanonical(value: unknown): unknown {
  if (typeof value === 'number' && !Number.isFinite(value)) {
    throw new SecureEnvelopeError('non_finite_or_unserializable');
  }
  if (Array.isArray(value)) return value.map(sortCanonical);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const nested = (value as Record<string, unknown>)[key];
      if (nested === undefined) throw new SecureEnvelopeError('non_finite_or_unserializable');
      out[key] = sortCanonical(nested);
    }
    return out;
  }
  if (value === undefined || typeof value === 'function' || typeof value === 'symbol') {
    throw new SecureEnvelopeError('non_finite_or_unserializable');
  }
  return value;
}
