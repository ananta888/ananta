import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';

export const SPEECH_EVIDENCE_CONSENT_SCHEMA = 'ananta.speech-evidence-consent.v1' as const;
export const SPEECH_EVIDENCE_GRANTS = [
  'capture',
  'transcript_share',
  'feature_share',
  'raw_audio_share',
  'dataset_import',
  'training',
  'inference',
  'export',
] as const;

export type SpeechEvidenceGrant = typeof SPEECH_EVIDENCE_GRANTS[number];
export type SpeechEvidenceDirection = 'local' | 'sender_to_receiver' | 'receiver_to_sender';
export type SpeechEvidenceConsentState = 'active' | 'revoked' | 'expired';
export type SpeechEvidenceDataClass =
  | 'audio'
  | 'transcript'
  | 'acoustic_features'
  | 'speaker_embedding'
  | 'correction'
  | 'quality_metrics';

export interface SpeechEvidenceConsentDocument {
  readonly schema: typeof SPEECH_EVIDENCE_CONSENT_SCHEMA;
  readonly consent_id: string;
  readonly tenant_id: string;
  readonly owner_subject: string;
  readonly speaker_id: string;
  readonly recipient_id: string;
  readonly direction: SpeechEvidenceDirection;
  readonly pair_id: string;
  readonly session_id: string;
  readonly session_epoch: number;
  readonly purpose: string;
  readonly data_classes: readonly SpeechEvidenceDataClass[];
  readonly retention_seconds: number;
  readonly trainer_locations: readonly string[];
  readonly grants: Readonly<Record<SpeechEvidenceGrant, boolean>>;
  readonly consent_version: number;
  readonly revocation_epoch: number;
  readonly issued_at_ms: number;
  readonly expires_at_ms: number;
  readonly state: SpeechEvidenceConsentState;
  readonly required_signers: readonly string[];
  readonly signatures: Readonly<Record<string, string>>;
}

export interface SpeechEvidenceConsentReadModel {
  readonly consent: SpeechEvidenceConsentDocument;
  readonly consentDigest: string;
  readonly scopeDigest: string;
}

@Injectable({ providedIn: 'root' })
export class SpeechEvidenceConsentApiService {
  private readonly core = inject(HubApiCoreService);

  get(hubUrl: string, consentId: string): Observable<SpeechEvidenceConsentReadModel> {
    const base = normalizeHubUrl(hubUrl);
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/voice/speech-evidence-consents/${encodeURIComponent(identifier(consentId))}`,
      base,
    ).pipe(map(parseSpeechEvidenceConsentResponse));
  }

  grant(
    hubUrl: string,
    consent: SpeechEvidenceConsentDocument,
    idempotencyKey: string,
  ): Observable<SpeechEvidenceConsentReadModel> {
    return this.mutation(hubUrl, '', 'grant', consent, 0, idempotencyKey);
  }

  reduce(
    hubUrl: string,
    consent: SpeechEvidenceConsentDocument,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechEvidenceConsentReadModel> {
    return this.mutation(hubUrl, consent.consent_id, 'reduce', { consent }, expectedVersion, idempotencyKey);
  }

  renew(
    hubUrl: string,
    consent: SpeechEvidenceConsentDocument,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechEvidenceConsentReadModel> {
    return this.mutation(hubUrl, consent.consent_id, 'renew', { consent }, expectedVersion, idempotencyKey);
  }

  revoke(
    hubUrl: string,
    consentId: string,
    expectedVersion: number,
    contributorId: string | null,
    idempotencyKey: string,
  ): Observable<SpeechEvidenceConsentReadModel> {
    return this.mutation(
      hubUrl,
      consentId,
      'revoke',
      contributorId ? { contributor_id: identifier(contributorId) } : {},
      expectedVersion,
      idempotencyKey,
    );
  }

  private mutation(
    hubUrl: string,
    consentId: string,
    action: 'grant' | 'reduce' | 'renew' | 'revoke',
    body: unknown,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechEvidenceConsentReadModel> {
    const base = normalizeHubUrl(hubUrl);
    if (action === 'grant') parseSpeechEvidenceConsent(body);
    const url = action === 'grant'
      ? `${base}/v1/voice/speech-evidence-consents`
      : `${base}/v1/voice/speech-evidence-consents/${encodeURIComponent(identifier(consentId))}/${action}`;
    const headers: Record<string, string> = { 'Idempotency-Key': mutationKey(idempotencyKey) };
    if (action !== 'grant') headers['If-Match'] = `"${positiveInteger(expectedVersion)}"`;
    return this.core.request<unknown>('POST', url, base, { body, headers })
      .pipe(map(parseSpeechEvidenceConsentResponse));
  }
}

export function parseSpeechEvidenceConsentResponse(value: unknown): SpeechEvidenceConsentReadModel {
  const envelope = closedRecord(value, ['ok', 'data'], 'speech_consent_envelope_invalid');
  if (envelope['ok'] !== true) fail('speech_consent_envelope_invalid');
  const data = closedRecord(
    envelope['data'],
    ['consent', 'consent_digest', 'scope_digest'],
    'speech_consent_response_invalid',
  );
  return Object.freeze({
    consent: parseSpeechEvidenceConsent(data['consent']),
    consentDigest: digest(data['consent_digest']),
    scopeDigest: digest(data['scope_digest']),
  });
}

export function parseSpeechEvidenceConsent(value: unknown): SpeechEvidenceConsentDocument {
  const row = closedRecord(value, [
    'schema', 'consent_id', 'tenant_id', 'owner_subject', 'speaker_id', 'recipient_id',
    'direction', 'pair_id', 'session_id', 'session_epoch', 'purpose', 'data_classes',
    'retention_seconds', 'trainer_locations', 'grants', 'consent_version', 'revocation_epoch',
    'issued_at_ms', 'expires_at_ms', 'state', 'required_signers', 'signatures',
  ], 'speech_consent_document_invalid');
  if (row['schema'] !== SPEECH_EVIDENCE_CONSENT_SCHEMA) fail('speech_consent_schema_invalid');
  const direction = enumValue(row['direction'], ['local', 'sender_to_receiver', 'receiver_to_sender'] as const);
  const state = enumValue(row['state'], ['active', 'revoked', 'expired'] as const);
  const issuedAt = nonnegativeInteger(row['issued_at_ms']);
  const expiresAt = positiveInteger(row['expires_at_ms']);
  if (expiresAt <= issuedAt || expiresAt - issuedAt > 366 * 24 * 60 * 60 * 1_000) {
    fail('speech_consent_expiry_invalid');
  }
  const dataClasses = stringSet(
    row['data_classes'],
    ['audio', 'transcript', 'acoustic_features', 'speaker_embedding', 'correction', 'quality_metrics'] as const,
    16,
  );
  const trainerLocations = identifiers(row['trainer_locations'], 32);
  const grantsRow = closedRecord(row['grants'], SPEECH_EVIDENCE_GRANTS, 'speech_consent_grants_invalid');
  const grants = Object.fromEntries(SPEECH_EVIDENCE_GRANTS.map(name => {
    if (typeof grantsRow[name] !== 'boolean') fail('speech_consent_grants_invalid');
    return [name, grantsRow[name]];
  })) as Record<SpeechEvidenceGrant, boolean>;
  if (grants.training && !trainerLocations.length) fail('speech_consent_trainer_location_required');
  if (grants.raw_audio_share && !dataClasses.includes('audio')) fail('speech_consent_raw_audio_class_missing');
  const requiredSigners = identifiers(row['required_signers'], 4);
  const signatureRow = record(row['signatures'], 'speech_consent_signatures_invalid');
  const signatures: Record<string, string> = {};
  for (const [signer, signature] of Object.entries(signatureRow)) signatures[identifier(signer)] = digest(signature);
  if (Object.keys(signatures).sort().join('\0') !== [...requiredSigners].sort().join('\0')) {
    fail('speech_consent_signatures_incomplete');
  }
  const speaker = identifier(row['speaker_id']);
  const recipient = identifier(row['recipient_id']);
  if (direction !== 'local' && (speaker === recipient || requiredSigners.length !== 2
      || !requiredSigners.includes(speaker) || !requiredSigners.includes(recipient))) {
    fail('speech_consent_bilateral_signers_required');
  }
  return Object.freeze({
    schema: SPEECH_EVIDENCE_CONSENT_SCHEMA,
    consent_id: identifier(row['consent_id']),
    tenant_id: identifier(row['tenant_id']),
    owner_subject: identifier(row['owner_subject']),
    speaker_id: speaker,
    recipient_id: recipient,
    direction,
    pair_id: identifier(row['pair_id']),
    session_id: identifier(row['session_id']),
    session_epoch: positiveInteger(row['session_epoch']),
    purpose: identifier(row['purpose']),
    data_classes: Object.freeze(dataClasses),
    retention_seconds: boundedInteger(row['retention_seconds'], 60, 31_536_000),
    trainer_locations: Object.freeze(trainerLocations),
    grants: Object.freeze(grants),
    consent_version: positiveInteger(row['consent_version']),
    revocation_epoch: nonnegativeInteger(row['revocation_epoch']),
    issued_at_ms: issuedAt,
    expires_at_ms: expiresAt,
    state,
    required_signers: Object.freeze(requiredSigners),
    signatures: Object.freeze(signatures),
  });
}

function normalizeHubUrl(value: string): string {
  const normalized = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(normalized)) fail('speech_consent_hub_url_invalid');
  return normalized;
}

function closedRecord(value: unknown, fields: readonly string[], reason: string): Record<string, unknown> {
  const result = record(value, reason);
  if (Object.keys(result).some(key => !fields.includes(key)) || fields.some(key => !(key in result))) fail(reason);
  return result;
}

function record(value: unknown, reason: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(reason);
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  const rendered = String(value ?? '');
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$/.test(rendered)) fail('speech_consent_identifier_invalid');
  return rendered;
}

function digest(value: unknown): string {
  const rendered = String(value ?? '');
  if (!/^[0-9a-f]{64}$/.test(rendered)) fail('speech_consent_digest_invalid');
  return rendered;
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    fail('speech_consent_integer_invalid');
  }
  return Number(value);
}

function positiveInteger(value: unknown): number { return boundedInteger(value, 1, Number.MAX_SAFE_INTEGER); }
function nonnegativeInteger(value: unknown): number { return boundedInteger(value, 0, Number.MAX_SAFE_INTEGER); }

function identifiers(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) fail('speech_consent_identifier_array_invalid');
  const rows = value.map(identifier);
  if (new Set(rows).size !== rows.length) fail('speech_consent_identifier_array_invalid');
  return rows;
}

function stringSet<const T extends readonly string[]>(value: unknown, allowed: T, maximum: number): T[number][] {
  if (!Array.isArray(value) || !value.length || value.length > maximum) fail('speech_consent_enum_array_invalid');
  const rows = value.map(item => enumValue(item, allowed));
  if (new Set(rows).size !== rows.length) fail('speech_consent_enum_array_invalid');
  return rows;
}

function enumValue<const T extends readonly string[]>(value: unknown, values: T): T[number] {
  if (!values.includes(value as T[number])) fail('speech_consent_enum_invalid');
  return value as T[number];
}

function mutationKey(value: string): string {
  const rendered = String(value || '').trim();
  if (rendered.length < 8 || rendered.length > 256 || /\s/.test(rendered)) {
    fail('speech_consent_idempotency_key_invalid');
  }
  return rendered;
}

function fail(reason: string): never { throw new Error(reason); }
