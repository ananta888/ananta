import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { map, type Observable } from 'rxjs';

import type { SfuBroadcastJsonObject } from './sfu-broadcast-contracts';

export type SfuLayerProjectionKind = 'room' | 'publisher' | 'receiver';

export interface SfuLayerProjectionReadBinding {
  readonly kind: SfuLayerProjectionKind;
  readonly roomRef: string;
  readonly subjectRef: string;
  readonly cursor: number;
  readonly etag?: string;
  readonly waitMs?: number;
}

export type SfuLayerProjectionApiResult =
  | Readonly<{ status: 'heartbeat' | 'not_modified'; cursor: number; etag: string | null }>
  | Readonly<{
      status: 'projection'; cursor: number; etag: string | null;
      document: SfuBroadcastJsonObject; rawDocument: string;
      digest: string; signature: string; signatureKeyId: string;
      signatureAlgorithm: 'Ed25519' | 'HMAC-SHA-256';
      signatureAlgorithmVersion: number; signatureKeyVersion: number;
    }>;

@Injectable({ providedIn: 'root' })
export class SfuLayerProjectionApiService {
  private readonly http = inject(HttpClient);

  read(binding: SfuLayerProjectionReadBinding): Observable<SfuLayerProjectionApiResult> {
    validateBinding(binding);
    const path = projectionPath(binding);
    let params = new HttpParams().set('cursor', binding.cursor).set('wait_ms', Math.min(1500, Math.max(0, binding.waitMs ?? 0)));
    let headers = new HttpHeaders({ Accept: 'application/json' });
    if (binding.etag) headers = headers.set('If-None-Match', binding.etag);
    return this.http.get(path, { params, headers, observe: 'response', responseType: 'text' }).pipe(
      map(response => {
        if (response.status === 304) {
          return Object.freeze({ status: 'not_modified' as const, cursor: binding.cursor, etag: response.headers.get('ETag') });
        }
        return decodeEnvelope(response.body ?? '', response.headers.get('ETag'));
      }),
    );
  }

  submitReceipt(roomRef: string, projectionRef: string, receipt: SfuBroadcastJsonObject): Observable<void> {
    validateRef(roomRef);
    validateRef(projectionRef);
    const encoded = JSON.stringify(receipt);
    if (new TextEncoder().encode(encoded).byteLength > 8192) throw new Error('sfu_projection_receipt_bytes_exceeded');
    return this.http.post(
      `/v1/semantic-media/sfu/rooms/${encodeURIComponent(roomRef)}/projection-receipts/${encodeURIComponent(projectionRef)}`,
      encoded,
      { headers: new HttpHeaders({ 'Content-Type': 'application/json' }), responseType: 'text' },
    ).pipe(map(() => undefined));
  }
}

function projectionPath(binding: SfuLayerProjectionReadBinding): string {
  const root = `/v1/semantic-media/sfu/rooms/${encodeURIComponent(binding.roomRef)}/projections`;
  if (binding.kind === 'room') return `${root}/room`;
  const segment = binding.kind === 'publisher' ? 'publishers' : 'receivers';
  return `${root}/${segment}/${encodeURIComponent(binding.subjectRef)}`;
}

function decodeEnvelope(raw: string, etag: string | null): SfuLayerProjectionApiResult {
  if (new TextEncoder().encode(raw).byteLength > 12_288) throw new Error('sfu_projection_envelope_bytes_exceeded');
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error('sfu_projection_envelope_json_invalid'); }
  if (!isRecord(value) || value['ok'] !== true || !Number.isSafeInteger(value['cursor'])) {
    throw new Error('sfu_projection_envelope_invalid');
  }
  const cursor = Number(value['cursor']);
  if (value['status'] === 'heartbeat') return Object.freeze({ status: 'heartbeat', cursor, etag });
  if (value['status'] !== 'projection' || !isRecord(value['document'])
      || typeof value['projection_digest'] !== 'string' || !/^[a-f0-9]{64}$/.test(value['projection_digest'])
      || typeof value['signature'] !== 'string' || value['signature'].length > 256
      || typeof value['signature_key_id'] !== 'string' || value['signature_key_id'].length > 128) {
    throw new Error('sfu_projection_envelope_invalid');
  }
  const metadata = signatureMetadata(value);
  const document = value['document'] as SfuBroadcastJsonObject;
  return Object.freeze({
    status: 'projection', cursor, etag, document, rawDocument: JSON.stringify(document),
    digest: value['projection_digest'], signature: value['signature'], signatureKeyId: value['signature_key_id'],
    signatureAlgorithm: metadata.algorithm,
    signatureAlgorithmVersion: metadata.algorithmVersion,
    signatureKeyVersion: metadata.keyVersion,
  });
}

function signatureMetadata(value: Record<string, unknown>): Readonly<{
  algorithm: 'Ed25519' | 'HMAC-SHA-256'; algorithmVersion: number; keyVersion: number;
}> {
  const fields = [
    value['signature_algorithm'],
    value['signature_algorithm_version'],
    value['signature_key_version'],
  ];
  if (fields.every(field => field === undefined)) {
    return Object.freeze({ algorithm: 'HMAC-SHA-256', algorithmVersion: 1, keyVersion: 1 });
  }
  if (fields.some(field => field === undefined)
      || (fields[0] !== 'Ed25519' && fields[0] !== 'HMAC-SHA-256')
      || !Number.isSafeInteger(fields[1]) || Number(fields[1]) < 1
      || !Number.isSafeInteger(fields[2]) || Number(fields[2]) < 1) {
    throw new Error('sfu_projection_signature_metadata_invalid');
  }
  return Object.freeze({
    algorithm: fields[0], algorithmVersion: Number(fields[1]), keyVersion: Number(fields[2]),
  });
}

function validateBinding(binding: SfuLayerProjectionReadBinding): void {
  validateRef(binding.roomRef);
  validateRef(binding.subjectRef);
  if (!Number.isSafeInteger(binding.cursor) || binding.cursor < 0) throw new Error('sfu_projection_cursor_invalid');
}

function validateRef(value: string): void {
  if (!value || new TextEncoder().encode(value).byteLength > 128 || /[\u0000-\u0020\u007f]/.test(value)) {
    throw new Error('sfu_projection_scope_invalid');
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}
