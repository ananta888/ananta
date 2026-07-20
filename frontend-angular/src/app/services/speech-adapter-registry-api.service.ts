import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import {
  SpeechAdapterMetadata,
  SpeechDirection,
} from '../features/voice/reconstruction/personalized-speech-reconstructor.service';
import { HubApiCoreService } from './hub-api-core.service';

export interface SpeechAdapterRegistryPage {
  readonly items: readonly SpeechAdapterMetadata[];
  readonly count: number;
}

@Injectable({ providedIn: 'root' })
export class SpeechAdapterRegistryApiService {
  private readonly core = inject(HubApiCoreService);

  list(hubUrl: string, pairId: string, direction: SpeechDirection): Observable<SpeechAdapterRegistryPage> {
    const base = normalizeHubUrl(hubUrl);
    identifier(pairId, 'speech_adapter_pair_invalid');
    const query = new URLSearchParams({ pair_id: pairId, direction });
    return this.core.request<unknown>(
      'GET', `${base}/api/ml-intern-speech-adapters?${query.toString()}`, base,
    ).pipe(map(value => parseSpeechAdapterRegistryPage(value)));
  }

  get(
    hubUrl: string,
    adapterId: string,
    pairId: string,
    direction: SpeechDirection,
  ): Observable<SpeechAdapterMetadata> {
    const base = normalizeHubUrl(hubUrl);
    identifier(adapterId, 'speech_adapter_id_invalid');
    identifier(pairId, 'speech_adapter_pair_invalid');
    const query = new URLSearchParams({ pair_id: pairId, direction });
    return this.core.request<unknown>(
      'GET', `${base}/api/ml-intern-speech-adapters/${encodeURIComponent(adapterId)}?${query.toString()}`, base,
    ).pipe(map(value => parseSpeechAdapterEnvelope(value)));
  }
}

const PUBLIC_FIELDS = [
  'adapter_id', 'version', 'pair_id', 'direction', 'speaker_digest', 'scope_digest',
  'base_model_id', 'base_model_digest', 'backend', 'backend_digest', 'dataset_digest',
  'split_digest', 'evaluation_report_digest', 'evaluation_policy_version', 'consent_digest',
  'consent_expires_at_ms', 'artifact_ref', 'artifact_sha256', 'artifact_size_bytes',
  'expires_at_ms', 'status', 'registry_version', 'approval_reason_code', 'approved_at_ms',
  'revoked_at_ms', 'deprecated_at_ms', 'expired_at_ms', 'rollback_of_adapter_id',
  'created_at_ms', 'updated_at_ms', 'lineage',
] as const;

export function parseSpeechAdapterRegistryPage(value: unknown, nowMs?: number): SpeechAdapterRegistryPage {
  const envelope = closedRecord(value, ['ok', 'data'], 'speech_adapter_envelope_invalid');
  if (envelope['ok'] !== true) fail('speech_adapter_envelope_invalid');
  const data = closedRecord(envelope['data'], ['items', 'count'], 'speech_adapter_page_invalid');
  if (!Array.isArray(data['items']) || data['items'].length > 256) fail('speech_adapter_page_invalid');
  const items = data['items'].map(item => parseSpeechAdapterMetadata(item, nowMs));
  const count = integer(data['count'], 0, 256, 'speech_adapter_count_invalid');
  if (count !== items.length) fail('speech_adapter_count_invalid');
  return Object.freeze({ items: Object.freeze(items), count });
}

export function parseSpeechAdapterEnvelope(value: unknown, nowMs?: number): SpeechAdapterMetadata {
  const envelope = closedRecord(value, ['ok', 'data'], 'speech_adapter_envelope_invalid');
  if (envelope['ok'] !== true) fail('speech_adapter_envelope_invalid');
  return parseSpeechAdapterMetadata(envelope['data'], nowMs);
}

function parseSpeechAdapterMetadata(value: unknown, nowMs?: number): SpeechAdapterMetadata {
  const row = closedRecord(value, PUBLIC_FIELDS, 'speech_adapter_metadata_invalid');
  const direction = row['direction'];
  if (direction !== 'sender_to_receiver' && direction !== 'receiver_to_sender') {
    fail('speech_adapter_direction_invalid');
  }
  const status = row['status'];
  if (!['evaluated', 'approved', 'revoked', 'deprecated', 'expired'].includes(String(status))) {
    fail('speech_adapter_status_invalid');
  }
  const artifactRef = String(row['artifact_ref'] ?? '');
  if (
    !/^artifact:\/\/speech-adapters\/[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,447}$/.test(artifactRef)
    || artifactRef.split('/').includes('..')
  ) fail('speech_adapter_artifact_ref_invalid');
  const expiresAtMs = integer(row['expires_at_ms'], 1, Number.MAX_SAFE_INTEGER, 'speech_adapter_expiry_invalid');
  const consentExpiresAtMs = integer(
    row['consent_expires_at_ms'], 1, Number.MAX_SAFE_INTEGER, 'speech_adapter_consent_expiry_invalid',
  );
  integer(row['artifact_size_bytes'], 1, 8 * 1024 * 1024 * 1024, 'speech_adapter_artifact_size_invalid');
  if (nowMs !== undefined && (expiresAtMs <= nowMs || consentExpiresAtMs <= nowMs)) {
    fail('speech_adapter_expired');
  }
  return Object.freeze({
    adapter_id: identifier(row['adapter_id'], 'speech_adapter_id_invalid'),
    pair_id: identifier(row['pair_id'], 'speech_adapter_pair_invalid'),
    direction,
    speaker_digest: digest(row['speaker_digest'], 'speech_adapter_speaker_invalid'),
    scope_digest: digest(row['scope_digest'], 'speech_adapter_scope_invalid'),
    base_model_id: identifier(row['base_model_id'], 'speech_adapter_model_invalid'),
    base_model_digest: digest(row['base_model_digest'], 'speech_adapter_model_digest_invalid'),
    consent_digest: digest(row['consent_digest'], 'speech_adapter_consent_invalid'),
    artifact_ref: artifactRef,
    artifact_sha256: digest(row['artifact_sha256'], 'speech_adapter_artifact_digest_invalid'),
    expires_at_ms: expiresAtMs,
    consent_expires_at_ms: consentExpiresAtMs,
    registry_version: integer(row['registry_version'], 1, 1_000_000, 'speech_adapter_version_invalid'),
    status: status as SpeechAdapterMetadata['status'],
  });
}

function normalizeHubUrl(value: string): string {
  const normalized = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(normalized)) fail('speech_adapter_hub_url_invalid');
  return normalized;
}

function closedRecord(value: unknown, fields: readonly string[], reason: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(reason);
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !(key in row))) fail(reason);
  return row;
}

function identifier(value: unknown, reason: string): string {
  const rendered = String(value ?? '');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(rendered)) fail(reason);
  return rendered;
}

function digest(value: unknown, reason: string): string {
  const rendered = String(value ?? '');
  if (!/^[0-9a-f]{64}$/.test(rendered)) fail(reason);
  return rendered;
}

function integer(value: unknown, minimum: number, maximum: number, reason: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) fail(reason);
  return Number(value);
}

function fail(reason: string): never { throw new Error(reason); }
