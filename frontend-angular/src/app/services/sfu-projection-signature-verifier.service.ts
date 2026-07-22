import { Inject, Injectable, InjectionToken, type Provider } from '@angular/core';

import type { SfuBroadcastJsonValue } from './sfu-broadcast-contracts';
import {
  SFU_PROJECTION_SIGNATURE_VERIFIER,
  type SfuProjectionSignatureVerifierPort,
} from './sfu-layer-projection.validator';

export type SfuProjectionTrustedKey = Readonly<{
  keyId: string;
  algorithm: 'HMAC-SHA-256' | 'Ed25519';
  algorithmVersion: number;
  keyVersion: number;
  status: 'active' | 'overlap' | 'revoked';
  keyMaterialBase64Url: string;
  format: 'raw' | 'spki';
  legacy?: true;
}>;

export type SfuProjectionTrustedKeysetBootstrap = Readonly<{
  schema: 'ananta.sfu-projection-trusted-keyset.v1';
  keysetVersion: number;
  keys: readonly SfuProjectionTrustedKey[];
}>;

const MAX_TRUSTED_KEYS = 8;
const EMPTY_KEYSET: SfuProjectionTrustedKeysetBootstrap = Object.freeze({
  schema: 'ananta.sfu-projection-trusted-keyset.v1',
  keysetVersion: 0,
  keys: Object.freeze([]),
});

export const SFU_PROJECTION_INITIAL_TRUSTED_KEYSET = new InjectionToken<SfuProjectionTrustedKeysetBootstrap>(
  'SFU_PROJECTION_INITIAL_TRUSTED_KEYSET',
  { providedIn: 'root', factory: readRuntimeKeyset },
);

@Injectable({ providedIn: 'root' })
export class SfuProjectionTrustedKeysetService {
  private keys = new Map<string, SfuProjectionTrustedKey>();
  private version = -1;

  constructor(
    @Inject(SFU_PROJECTION_INITIAL_TRUSTED_KEYSET) initial: SfuProjectionTrustedKeysetBootstrap,
  ) {
    try {
      this.replace(initial);
    } catch {
      this.clear();
    }
  }

  replace(keyset: SfuProjectionTrustedKeysetBootstrap): void {
    const normalized = normalizeKeyset(keyset);
    if (normalized.keysetVersion <= this.version) throw new Error('sfu_projection_keyset_version_stale');
    const next = new Map<string, SfuProjectionTrustedKey>();
    for (const key of normalized.keys) {
      if (next.has(key.keyId)) throw new Error('sfu_projection_keyset_duplicate');
      next.set(key.keyId, key);
    }
    this.keys = next;
    this.version = normalized.keysetVersion;
  }

  resolve(keyId: string): SfuProjectionTrustedKey | null {
    const key = this.keys.get(keyId) ?? null;
    return key?.status === 'revoked' ? null : key;
  }

  clear(): void {
    this.keys = new Map();
    this.version = 0;
  }
}

@Injectable({ providedIn: 'root' })
export class WebCryptoSfuProjectionSignatureVerifier implements SfuProjectionSignatureVerifierPort {
  constructor(private readonly keyset: SfuProjectionTrustedKeysetService) {}

  async verify(input: Parameters<SfuProjectionSignatureVerifierPort['verify']>[0]): Promise<boolean> {
    const trusted = this.keyset.resolve(input.keyId);
    if (!trusted || !globalThis.crypto?.subtle || !/^[a-f0-9]{64}$/.test(input.digest)
        || trusted.algorithm !== input.algorithm
        || trusted.algorithmVersion !== input.algorithmVersion
        || trusted.keyVersion !== input.keyVersion) return false;
    const signature = decodeBase64Url(input.signature);
    if (!signature) return false;
    let canonicalBytes: Uint8Array<ArrayBuffer>;
    let signedBytes: Uint8Array<ArrayBuffer>;
    try {
      canonicalBytes = encodeOwned(canonicalJson(input.document));
      signedBytes = encodeOwned(`ananta:sfu-projection:v1:${input.digest}`);
    } catch {
      signature.fill(0);
      return false;
    }
    try {
      const actualDigest = new Uint8Array(await crypto.subtle.digest('SHA-256', canonicalBytes.buffer));
      if (!constantTimeEqualHex(actualDigest, input.digest)) return false;
      const key = await importTrustedKey(trusted);
      if (!key) return false;
      return trusted.algorithm === 'HMAC-SHA-256'
        ? crypto.subtle.verify('HMAC', key, signature.buffer, signedBytes.buffer)
        : crypto.subtle.verify({ name: 'Ed25519' }, key, signature.buffer, signedBytes.buffer);
    } catch {
      return false;
    } finally {
      signature.fill(0);
      canonicalBytes.fill(0);
      signedBytes.fill(0);
    }
  }
}

export function provideSfuProjectionSignatureVerifier(): Provider {
  return {
    provide: SFU_PROJECTION_SIGNATURE_VERIFIER,
    useExisting: WebCryptoSfuProjectionSignatureVerifier,
  };
}

export function parseSfuProjectionTrustedKeysetBootstrap(value: unknown): SfuProjectionTrustedKeysetBootstrap {
  return normalizeKeyset(value);
}

function readRuntimeKeyset(): SfuProjectionTrustedKeysetBootstrap {
  const runtime = globalThis as typeof globalThis & {
    __ANANTA_SFU_PROJECTION_TRUSTED_KEYS__?: unknown;
  };
  const value = runtime.__ANANTA_SFU_PROJECTION_TRUSTED_KEYS__;
  if (value === undefined) return EMPTY_KEYSET;
  try {
    return normalizeKeyset(value);
  } catch {
    return EMPTY_KEYSET;
  }
}

function normalizeKeyset(value: unknown): SfuProjectionTrustedKeysetBootstrap {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('sfu_projection_keyset_invalid');
  }
  const record = value as Record<string, unknown>;
  const keysetVersion = record['keysetVersion'];
  const keys = record['keys'];
  if (record['schema'] !== 'ananta.sfu-projection-trusted-keyset.v1'
      || !Number.isSafeInteger(keysetVersion) || Number(keysetVersion) < 0
      || !Array.isArray(keys) || keys.length > MAX_TRUSTED_KEYS
      || (Number(keysetVersion) === 0 && keys.length !== 0)
      || (Number(keysetVersion) > 0 && keys.length === 0)) {
    throw new Error('sfu_projection_keyset_invalid');
  }
  const normalized = keys.map(item => normalizeKey(item));
  if (new Set(normalized.map(key => key.keyId)).size !== normalized.length) {
    throw new Error('sfu_projection_keyset_duplicate');
  }
  return Object.freeze({
    schema: 'ananta.sfu-projection-trusted-keyset.v1',
    keysetVersion: Number(keysetVersion),
    keys: Object.freeze(normalized),
  });
}

function normalizeKey(value: unknown): SfuProjectionTrustedKey {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('sfu_projection_key_invalid');
  }
  const record = value as Record<string, unknown>;
  const keyId = typeof record['keyId'] === 'string' ? record['keyId'] : '';
  const algorithm = record['algorithm'];
  const algorithmVersion = record['algorithmVersion'];
  const keyVersion = record['keyVersion'];
  const status = record['status'];
  const keyMaterialBase64Url = typeof record['keyMaterialBase64Url'] === 'string'
    ? record['keyMaterialBase64Url'] : '';
  const format = record['format'];
  const legacy = record['legacy'];
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,111}:v[1-9][0-9]{0,8}$/.test(keyId)
      || (algorithm !== 'HMAC-SHA-256' && algorithm !== 'Ed25519')
      || algorithmVersion !== 1
      || !Number.isSafeInteger(keyVersion) || Number(keyVersion) < 1
      || !keyId.endsWith(`:v${Number(keyVersion)}`)
      || (status !== 'active' && status !== 'overlap' && status !== 'revoked')
      || !/^[A-Za-z0-9_-]{32,512}$/.test(keyMaterialBase64Url)
      || (format !== 'raw' && format !== 'spki')
      || (algorithm === 'HMAC-SHA-256' && (format !== 'raw' || legacy !== true))
      || (algorithm === 'Ed25519' && legacy !== undefined)) {
    throw new Error('sfu_projection_key_invalid');
  }
  return Object.freeze({
    keyId, algorithm, algorithmVersion: 1, keyVersion: Number(keyVersion),
    status, keyMaterialBase64Url, format,
    ...(algorithm === 'HMAC-SHA-256' ? { legacy: true as const } : {}),
  });
}

async function importTrustedKey(key: SfuProjectionTrustedKey): Promise<CryptoKey | null> {
  const material = decodeBase64Url(key.keyMaterialBase64Url);
  if (!material) return null;
  try {
    if (key.algorithm === 'HMAC-SHA-256') {
      if (key.legacy !== true || material.byteLength < 32) return null;
      return await crypto.subtle.importKey(
        'raw', material.buffer, { name: 'HMAC', hash: 'SHA-256' }, false, ['verify'],
      );
    }
    if (key.format === 'raw' && material.byteLength !== 32) return null;
    return key.format === 'spki'
      ? await crypto.subtle.importKey('spki', material.buffer, { name: 'Ed25519' }, false, ['verify'])
      : await crypto.subtle.importKey('raw', material.buffer, { name: 'Ed25519' }, false, ['verify']);
  } catch {
    return null;
  } finally {
    material.fill(0);
  }
}

function decodeBase64Url(value: string): Uint8Array<ArrayBuffer> | null {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
    const decoded = atob(padded);
    const result = new Uint8Array(new ArrayBuffer(decoded.length));
    for (let index = 0; index < decoded.length; index += 1) result[index] = decoded.charCodeAt(index);
    return result;
  } catch {
    return null;
  }
}

function encodeOwned(value: string): Uint8Array<ArrayBuffer> {
  const transient = new TextEncoder().encode(value);
  const owned = new Uint8Array(new ArrayBuffer(transient.byteLength));
  owned.set(transient);
  transient.fill(0);
  return owned;
}

function canonicalJson(value: SfuBroadcastJsonValue): string {
  const normalized = canonicalValue(value);
  const serialized = JSON.stringify(normalized);
  if (serialized === undefined) throw new Error('sfu_projection_document_invalid');
  return serialized.replace(/[\u0080-\uffff]/g, character =>
    `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
  );
}

function canonicalValue(value: SfuBroadcastJsonValue): SfuBroadcastJsonValue {
  if (Array.isArray(value)) return value.map(item => canonicalValue(item));
  if (value && typeof value === 'object') {
    const result: Record<string, SfuBroadcastJsonValue> = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalValue(value[key]);
    return result;
  }
  return value;
}

function constantTimeEqualHex(actual: Uint8Array, expectedHex: string): boolean {
  let difference = actual.byteLength === expectedHex.length / 2 ? 0 : 1;
  for (let index = 0; index < actual.byteLength; index += 1) {
    const expected = Number.parseInt(expectedHex.slice(index * 2, index * 2 + 2), 16);
    difference |= actual[index] ^ expected;
  }
  actual.fill(0);
  return difference === 0;
}
