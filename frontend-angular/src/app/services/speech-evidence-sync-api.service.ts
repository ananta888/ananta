import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION,
  SpeechEvidenceGroupPreview,
  SpeechEvidenceMessage,
  speechEvidenceGroupPreviews,
  validateSpeechEvidenceMessage,
} from './speech-evidence-sync.validators';
import { SemanticDataChannelMessage } from './webrtc-datachannel.service';

export interface SpeechEvidencePeerKeyRecord {
  readonly sessionId: string;
  readonly pairId: string;
  readonly senderId: string;
  readonly audienceId: string;
  readonly epoch: number;
  readonly keyId: string;
  readonly publicKeyB64: string;
  readonly fingerprint: string;
  readonly consentVersion: number;
  readonly expiresAtMs: number;
  readonly version: number;
}

export interface SpeechEvidenceOfferRecord {
  readonly protocolVersion: string;
  readonly offerId: string;
  readonly sessionId: string;
  readonly pairId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly recipientId: string;
  readonly inventoryRootDigest: string;
  readonly direction: string;
  readonly purpose: string;
  readonly dataClasses: readonly string[];
  readonly fields: readonly string[];
  readonly retentionSeconds: number;
  readonly trainerClass: string;
  readonly groupIds: readonly string[];
  readonly groupPreviews: readonly SpeechEvidenceGroupPreview[];
  readonly groupPreviewDigest: string;
  readonly totalBytes: number;
  readonly senderConsentDigest: string;
  readonly recipientConsentDigest: string;
  readonly scopeDigest: string;
  readonly expiresAtMs: number;
  readonly state: string;
  readonly transferStarted: boolean;
  readonly invalidationReason: string | null;
  readonly version: number;
  readonly value: Readonly<Record<string, unknown>>;
}

export interface SpeechEvidenceConsentAuthority {
  readonly peerId: string;
  readonly pairId: string;
  readonly version: number;
  readonly digest: string;
  readonly directions: readonly string[];
  readonly purposes: readonly string[];
  readonly dataClasses: readonly string[];
  readonly fields: readonly string[];
  readonly trainerClasses: readonly string[];
  readonly maximumRetentionSeconds: number;
  readonly expiresAtMs: number;
}

export interface SpeechEvidenceConsentPairAuthority {
  readonly local: SpeechEvidenceConsentAuthority;
  readonly remote: SpeechEvidenceConsentAuthority;
}

export interface SpeechEvidenceHubTransferRecord {
  readonly offerId: string;
  readonly groupId: string;
  readonly state: string;
  readonly chunkCount: number;
  readonly acknowledgedChunks: number;
  readonly firstMissingIndex: number;
  readonly receivedBytes: number;
  readonly inFlightBytes: number;
  readonly expiresAtMs: number;
  readonly reasonCode: string | null;
  readonly version: number;
}

export interface SpeechEvidenceHubAdmissionReceipt {
  readonly version: string;
  readonly receiptId: string;
  readonly admissionDigest: string;
  readonly offerId: string;
  readonly inventoryRootDigest: string;
  readonly resolutionDigest: string;
  readonly acceptedGroupIds: readonly string[];
  readonly rejectedGroupIds: readonly string[];
  readonly quarantinedGroupIds: readonly string[];
  readonly consentDigest: string;
  readonly policyDigest: string;
  readonly resultDigest: string;
  readonly pairId: string;
  readonly direction: string;
  readonly issuedAtMs: number;
  readonly hubKeyId: string;
  readonly signatureB64: string;
  readonly unsignedValue: Readonly<Record<string, unknown>>;
}

export interface SpeechEvidenceHubCurationRecord {
  readonly curationId: string;
  readonly offerId: string;
  readonly admissionDigest: string;
  readonly state: string;
  readonly receipt: SpeechEvidenceHubAdmissionReceipt;
  readonly curationTaskId: string | null;
  readonly datasetId: string;
  readonly datasetParentDigest: string | null;
  readonly datasetManifestDigest: string | null;
  readonly consentVersion: number;
  readonly revocationEpoch: number;
}

export interface SpeechEvidenceHubReceiptKey {
  readonly keyId: string;
  readonly algorithm: 'Ed25519';
  readonly publicKeyB64: string;
}

export interface SpeechEvidenceHubCurationResponse {
  readonly curation: SpeechEvidenceHubCurationRecord;
  readonly hubReceiptKey: SpeechEvidenceHubReceiptKey;
}

/** Authenticated Hub control-plane client for the signed, pair-encrypted protocol. */
@Injectable({ providedIn: 'root' })
export class SpeechEvidenceSyncApiService {
  private readonly core = inject(HubApiCoreService);

  registerKey(
    hubUrl: string,
    request: Readonly<{
      sessionId: string;
      pairId: string;
      audienceId: string;
      epoch: number;
      consentVersion: number;
      keyId: string;
      publicKeyB64: string;
      expiresAtMs: number;
    }>,
  ): Observable<SpeechEvidencePeerKeyRecord> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>('POST', `${base}/v1/voice/speech-evidence-sync/keys`, base, {
      body: {
        session_id: identifier(request.sessionId),
        pair_id: identifier(request.pairId),
        audience_id: identifier(request.audienceId),
        epoch: positiveInteger(request.epoch),
        consent_version: positiveInteger(request.consentVersion),
        key_id: identifier(request.keyId),
        public_key_b64: publicKey(request.publicKeyB64),
        expires_at_ms: positiveInteger(request.expiresAtMs),
      },
    }).pipe(map(raw => parseKey(data(raw, ['key'])['key'])));
  }

  discoverKey(
    hubUrl: string,
    request: Readonly<{
      sessionId: string;
      pairId: string;
      senderId: string;
      epoch: number;
      keyId: string;
    }>,
  ): Observable<SpeechEvidencePeerKeyRecord> {
    const base = normalizeBase(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(request.sessionId),
      pair_id: identifier(request.pairId),
      sender_id: identifier(request.senderId),
      epoch: String(positiveInteger(request.epoch)),
    });
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/voice/speech-evidence-sync/keys/${encodeURIComponent(identifier(request.keyId))}?${query}`,
      base,
    ).pipe(map(raw => parseKey(data(raw, ['key'])['key'])));
  }

  propose(hubUrl: string, message: SpeechEvidenceMessage, relayEnvelope?: SemanticDataChannelMessage): Observable<SpeechEvidenceOfferRecord> {
    return this.offerMutation(hubUrl, 'proposals', message, relayEnvelope);
  }

  currentConsentPair(
    hubUrl: string,
    request: Readonly<{ sessionId: string; pairId: string; remotePeerId: string; epoch: number }>,
  ): Observable<SpeechEvidenceConsentPairAuthority> {
    const base = normalizeBase(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(request.sessionId),
      pair_id: identifier(request.pairId),
      remote_peer_id: identifier(request.remotePeerId),
      epoch: String(positiveInteger(request.epoch)),
    });
    return this.core.request<unknown>(
      'GET', `${base}/v1/voice/speech-evidence-sync/consents/current?${query}`, base,
    ).pipe(map(raw => {
      const pair = data(raw, ['local', 'remote']);
      return Object.freeze({ local: parseConsentAuthority(pair['local']), remote: parseConsentAuthority(pair['remote']) });
    }));
  }

  accept(hubUrl: string, message: SpeechEvidenceMessage, relayEnvelope?: SemanticDataChannelMessage): Observable<SpeechEvidenceOfferRecord> {
    return this.offerMutation(hubUrl, 'acceptances', message, relayEnvelope);
  }

  listOffers(
    hubUrl: string,
    request: Readonly<{ sessionId: string; pairId: string; epoch: number }>,
  ): Observable<readonly SpeechEvidenceOfferRecord[]> {
    const base = normalizeBase(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(request.sessionId),
      pair_id: identifier(request.pairId),
      epoch: String(positiveInteger(request.epoch)),
    });
    return this.core.request<unknown>(
      'GET', `${base}/v1/voice/speech-evidence-sync/offers?${query}`, base,
    ).pipe(map(raw => {
      const rows = data(raw, ['offers'])['offers'];
      if (!Array.isArray(rows) || rows.length > 50) fail('speech_evidence_offers_response_invalid');
      return Object.freeze(rows.map(parseOffer));
    }));
  }

  authorizeTransfer(hubUrl: string, offerId: string): Observable<SpeechEvidenceOfferRecord> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'POST',
      `${base}/v1/voice/speech-evidence-sync/offers/${encodeURIComponent(identifier(offerId))}/authorize-transfer`,
      base,
      { body: {} },
    ).pipe(map(raw => parseOffer(data(raw, ['offer'])['offer'])));
  }

  appendChunk(
    hubUrl: string,
    message: SpeechEvidenceMessage,
    relayEnvelope: SemanticDataChannelMessage,
  ): Observable<SpeechEvidenceHubTransferRecord> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'POST', `${base}/v1/voice/speech-evidence-sync/transfers/chunks`, base,
      { body: { message: validateSpeechEvidenceMessage(message), relay_envelope: relayEnvelope } },
    ).pipe(map(raw => parseTransfer(data(raw, ['transfer', 'relay'])['transfer'])));
  }

  acknowledgeChunk(
    hubUrl: string,
    message: SpeechEvidenceMessage,
    relayEnvelope?: SemanticDataChannelMessage,
  ): Observable<SpeechEvidenceHubTransferRecord> {
    const base = normalizeBase(hubUrl);
    const body: Record<string, unknown> = { message: validateSpeechEvidenceMessage(message) };
    if (relayEnvelope) body['relay_envelope'] = relayEnvelope;
    return this.core.request<unknown>(
      'POST', `${base}/v1/voice/speech-evidence-sync/transfers/acks`, base, { body },
    ).pipe(map(raw => parseTransfer(data(raw, ['transfer'])['transfer'])));
  }

  transferStatus(hubUrl: string, offerId: string, groupId: string): Observable<SpeechEvidenceHubTransferRecord> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/voice/speech-evidence-sync/offers/${encodeURIComponent(identifier(offerId))}`
        + `/transfers/${encodeURIComponent(identifier(groupId))}`,
      base,
    ).pipe(map(raw => parseTransfer(data(raw, ['transfer'])['transfer'])));
  }

  requestCuration(
    hubUrl: string,
    offerId: string,
    message: SpeechEvidenceMessage,
    groups: readonly Readonly<{ groupId: string; chunksB64: readonly string[] }>[],
  ): Observable<SpeechEvidenceHubCurationResponse> {
    const base = normalizeBase(hubUrl);
    if (!Array.isArray(groups) || !groups.length || groups.length > 4096) {
      fail('speech_evidence_curation_groups_invalid');
    }
    const bodyGroups = groups.map(group => ({
      group_id: identifier(group.groupId),
      chunks_b64: group.chunksB64.map(chunk => boundedBase64(chunk)),
    }));
    return this.core.request<unknown>(
      'POST',
      `${base}/v1/voice/speech-evidence-sync/offers/${encodeURIComponent(identifier(offerId))}/curation`,
      base,
      { body: { message: validateSpeechEvidenceMessage(message), groups: bodyGroups } },
    ).pipe(map(parseCurationResponse));
  }

  getCuration(hubUrl: string, offerId: string): Observable<SpeechEvidenceHubCurationResponse> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/voice/speech-evidence-sync/offers/${encodeURIComponent(identifier(offerId))}/curation`,
      base,
    ).pipe(map(parseCurationResponse));
  }

  invalidate(hubUrl: string, offerId: string, reasonCode: string): Observable<SpeechEvidenceOfferRecord> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'POST',
      `${base}/v1/voice/speech-evidence-sync/offers/${encodeURIComponent(identifier(offerId))}/invalidate`,
      base,
      { body: { reason_code: identifier(reasonCode) } },
    ).pipe(map(raw => parseOffer(data(raw, ['offer'])['offer'])));
  }

  private offerMutation(
    hubUrl: string,
    endpoint: 'proposals' | 'acceptances',
    message: SpeechEvidenceMessage,
    relayEnvelope?: SemanticDataChannelMessage,
  ): Observable<SpeechEvidenceOfferRecord> {
    const base = normalizeBase(hubUrl);
    const body: Record<string, unknown> = { message: validateSpeechEvidenceMessage(message) };
    if (relayEnvelope) body['relay_envelope'] = relayEnvelope;
    return this.core.request<unknown>(
      'POST', `${base}/v1/voice/speech-evidence-sync/offers/${endpoint}`, base, { body },
    ).pipe(map(raw => parseOffer(data(raw, ['offer'])['offer'])));
  }
}

function parseKey(raw: unknown): SpeechEvidencePeerKeyRecord {
  const row = closed(raw, [
    'session_id', 'pair_id', 'sender_id', 'audience_id', 'epoch', 'key_id', 'public_key_b64',
    'fingerprint', 'consent_version', 'expires_at_ms', 'version',
  ], 'speech_evidence_key_response_invalid');
  return Object.freeze({
    sessionId: identifier(row['session_id']), pairId: identifier(row['pair_id']),
    senderId: identifier(row['sender_id']), audienceId: identifier(row['audience_id']),
    epoch: positiveInteger(row['epoch']), keyId: identifier(row['key_id']),
    publicKeyB64: publicKey(row['public_key_b64']), fingerprint: digest(row['fingerprint']),
    consentVersion: positiveInteger(row['consent_version']), expiresAtMs: positiveInteger(row['expires_at_ms']),
    version: positiveInteger(row['version']),
  });
}

function parseConsentAuthority(raw: unknown): SpeechEvidenceConsentAuthority {
  const row = closed(raw, [
    'peer_id', 'pair_id', 'version', 'digest', 'directions', 'purposes', 'data_classes', 'fields',
    'trainer_classes', 'maximum_retention_seconds', 'expires_at_ms',
  ], 'speech_evidence_consent_authority_invalid');
  return Object.freeze({
    peerId: identifier(row['peer_id']),
    pairId: identifier(row['pair_id']),
    version: positiveInteger(row['version']),
    digest: digest(row['digest']),
    directions: Object.freeze(identifiers(row['directions'], 4)),
    purposes: Object.freeze(identifiers(row['purposes'], 16)),
    dataClasses: Object.freeze(identifiers(row['data_classes'], 16)),
    fields: Object.freeze(identifiers(row['fields'], 32)),
    trainerClasses: Object.freeze(identifiers(row['trainer_classes'], 8)),
    maximumRetentionSeconds: positiveInteger(row['maximum_retention_seconds']),
    expiresAtMs: positiveInteger(row['expires_at_ms']),
  });
}

function parseOffer(raw: unknown): SpeechEvidenceOfferRecord {
  const row = record(raw, 'speech_evidence_offer_response_invalid');
  const expected = [
    'offer_id', 'proposal_verification_digest', 'acceptance_verification_digest', 'session_id', 'pair_id',
    'epoch', 'sender_id', 'recipient_id', 'inventory_root_digest', 'direction', 'purpose', 'data_classes',
    'fields', 'retention_seconds', 'trainer_class', 'group_ids', 'group_previews', 'group_preview_digest',
    'total_bytes', 'sender_consent_digest',
    'recipient_consent_digest', 'scope_digest', 'expires_at_ms', 'state', 'transfer_started',
    'invalidation_reason', 'version', 'protocol_version',
  ];
  if (Object.keys(row).some(key => !expected.includes(key)) || expected.some(key => !(key in row))) {
    fail('speech_evidence_offer_response_invalid');
  }
  const offerId = identifier(row['offer_id']);
  digest(row['proposal_verification_digest']);
  if (
    row['protocol_version'] !== SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION
    ||
    !['proposed', 'accepted', 'invalidated'].includes(String(row['state']))
    || typeof row['transfer_started'] !== 'boolean'
    || (row['invalidation_reason'] !== null && typeof row['invalidation_reason'] !== 'string')
  ) {
    fail('speech_evidence_offer_response_invalid');
  }
  if (row['acceptance_verification_digest'] !== null) digest(row['acceptance_verification_digest']);
  const invalidationReason = row['invalidation_reason'] === null ? null : identifier(row['invalidation_reason']);
  const dataClasses = identifiers(row['data_classes'], 8);
  const fields = identifiers(row['fields'], 16);
  const groupIds = identifiers(row['group_ids'], 4096);
  const previewPayload = { group_previews: row['group_previews'] };
  const groupPreviews = speechEvidenceGroupPreviews(previewPayload);
  if (groupPreviews.length !== groupIds.length || groupPreviews.some(value => !groupIds.includes(value.groupId))) {
    fail('speech_evidence_offer_response_invalid');
  }
  return Object.freeze({
    protocolVersion: String(row['protocol_version']),
    offerId,
    sessionId: identifier(row['session_id']),
    pairId: identifier(row['pair_id']),
    epoch: positiveInteger(row['epoch']),
    senderId: identifier(row['sender_id']),
    recipientId: identifier(row['recipient_id']),
    inventoryRootDigest: digest(row['inventory_root_digest']),
    direction: identifier(row['direction']),
    purpose: identifier(row['purpose']),
    dataClasses: Object.freeze(dataClasses),
    fields: Object.freeze(fields),
    retentionSeconds: positiveInteger(row['retention_seconds']),
    trainerClass: identifier(row['trainer_class']),
    groupIds: Object.freeze(groupIds),
    groupPreviews,
    groupPreviewDigest: digest(row['group_preview_digest']),
    totalBytes: positiveInteger(row['total_bytes']),
    senderConsentDigest: digest(row['sender_consent_digest']),
    recipientConsentDigest: digest(row['recipient_consent_digest']),
    scopeDigest: digest(row['scope_digest']),
    expiresAtMs: positiveInteger(row['expires_at_ms']),
    state: String(row['state']),
    transferStarted: row['transfer_started'],
    invalidationReason,
    version: positiveInteger(row['version']),
    value: Object.freeze({ ...row }),
  });
}

function parseTransfer(raw: unknown): SpeechEvidenceHubTransferRecord {
  const row = closed(raw, [
    'offer_id', 'group_id', 'state', 'chunk_count', 'acknowledged_chunks', 'first_missing_index',
    'received_bytes', 'in_flight_bytes', 'expires_at_ms', 'reason_code', 'version',
  ], 'speech_evidence_transfer_response_invalid');
  if (!['active', 'completed', 'invalidated'].includes(String(row['state']))) {
    fail('speech_evidence_transfer_response_invalid');
  }
  const reasonCode = row['reason_code'] === null ? null : identifier(row['reason_code']);
  return Object.freeze({
    offerId: identifier(row['offer_id']), groupId: identifier(row['group_id']), state: String(row['state']),
    chunkCount: nonnegativeInteger(row['chunk_count']), acknowledgedChunks: nonnegativeInteger(row['acknowledged_chunks']),
    firstMissingIndex: nonnegativeInteger(row['first_missing_index']), receivedBytes: nonnegativeInteger(row['received_bytes']),
    inFlightBytes: nonnegativeInteger(row['in_flight_bytes']), expiresAtMs: positiveInteger(row['expires_at_ms']),
    reasonCode, version: positiveInteger(row['version']),
  });
}

function parseCurationResponse(raw: unknown): SpeechEvidenceHubCurationResponse {
  const response = data(raw, ['curation', 'hub_receipt_key']);
  return Object.freeze({
    curation: parseCuration(response['curation']),
    hubReceiptKey: parseReceiptKey(response['hub_receipt_key']),
  });
}

function parseCuration(raw: unknown): SpeechEvidenceHubCurationRecord {
  const row = closed(raw, [
    'curation_id', 'offer_id', 'admission_digest', 'state', 'receipt', 'curation_task_id',
    'dataset_id', 'dataset_parent_digest', 'dataset_manifest_digest', 'consent_version', 'revocation_epoch',
  ], 'speech_evidence_curation_response_invalid');
  const state = identifier(row['state']);
  if (!['admitted', 'quarantined', 'rejected', 'dataset_published'].includes(state)) {
    fail('speech_evidence_curation_response_invalid');
  }
  return Object.freeze({
    curationId: identifier(row['curation_id']),
    offerId: identifier(row['offer_id']),
    admissionDigest: digest(row['admission_digest']),
    state,
    receipt: parseReceipt(row['receipt']),
    curationTaskId: nullableIdentifier(row['curation_task_id']),
    datasetId: identifier(row['dataset_id']),
    datasetParentDigest: nullableDigest(row['dataset_parent_digest']),
    datasetManifestDigest: nullableDigest(row['dataset_manifest_digest']),
    consentVersion: positiveInteger(row['consent_version']),
    revocationEpoch: nonnegativeInteger(row['revocation_epoch']),
  });
}

function parseReceipt(raw: unknown): SpeechEvidenceHubAdmissionReceipt {
  const fields = [
    'version', 'receipt_id', 'admission_digest', 'offer_id', 'inventory_root_digest', 'resolution_digest',
    'accepted_group_ids', 'rejected_group_ids', 'quarantined_group_ids', 'consent_digest', 'policy_digest',
    'result_digest', 'pair_id', 'direction', 'issued_at_ms', 'hub_key_id', 'signature_b64',
  ];
  const row = closed(raw, fields, 'speech_evidence_receipt_response_invalid');
  const signature = boundedBase64(row['signature_b64']);
  const unsignedValue = Object.freeze(Object.fromEntries(
    Object.entries(row).filter(([key]) => key !== 'signature_b64'),
  ));
  return Object.freeze({
    version: identifier(row['version']),
    receiptId: digest(row['receipt_id']),
    admissionDigest: digest(row['admission_digest']),
    offerId: identifier(row['offer_id']),
    inventoryRootDigest: digest(row['inventory_root_digest']),
    resolutionDigest: digest(row['resolution_digest']),
    acceptedGroupIds: Object.freeze(identifiers(row['accepted_group_ids'], 4096)),
    rejectedGroupIds: Object.freeze(identifiers(row['rejected_group_ids'], 4096)),
    quarantinedGroupIds: Object.freeze(identifiers(row['quarantined_group_ids'], 4096)),
    consentDigest: digest(row['consent_digest']),
    policyDigest: digest(row['policy_digest']),
    resultDigest: digest(row['result_digest']),
    pairId: identifier(row['pair_id']),
    direction: identifier(row['direction']),
    issuedAtMs: positiveInteger(row['issued_at_ms']),
    hubKeyId: identifier(row['hub_key_id']),
    signatureB64: signature,
    unsignedValue,
  });
}

function parseReceiptKey(raw: unknown): SpeechEvidenceHubReceiptKey {
  const row = closed(raw, ['key_id', 'algorithm', 'public_key_b64'], 'speech_evidence_receipt_key_invalid');
  if (row['algorithm'] !== 'Ed25519') fail('speech_evidence_receipt_key_invalid');
  return Object.freeze({
    keyId: identifier(row['key_id']),
    algorithm: 'Ed25519',
    publicKeyB64: publicKey(row['public_key_b64']),
  });
}

function data(raw: unknown, fields: readonly string[]): Record<string, unknown> {
  const envelope = closed(raw, ['ok', 'data'], 'speech_evidence_response_invalid');
  if (envelope['ok'] !== true) fail('speech_evidence_response_invalid');
  return closed(envelope['data'], fields, 'speech_evidence_response_invalid');
}

function normalizeBase(value: string): string {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(base)) fail('speech_evidence_hub_url_invalid');
  return base;
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)) {
    fail('speech_evidence_identifier_invalid');
  }
  return value;
}

function publicKey(value: unknown): string {
  if (typeof value !== 'string' || value.length < 40 || value.length > 64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    fail('speech_evidence_public_key_invalid');
  }
  return value;
}

function boundedBase64(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 96 * 1024 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    fail('speech_evidence_base64_invalid');
  }
  return value;
}

function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail('speech_evidence_digest_invalid');
  return value;
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) fail('speech_evidence_integer_invalid');
  return Number(value);
}

function nonnegativeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) fail('speech_evidence_integer_invalid');
  return Number(value);
}

function nullableIdentifier(value: unknown): string | null {
  return value === null ? null : identifier(value);
}

function nullableDigest(value: unknown): string | null {
  return value === null ? null : digest(value);
}

function identifiers(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) fail('speech_evidence_identifiers_invalid');
  const result = value.map(identifier);
  if (new Set(result).size !== result.length) fail('speech_evidence_identifiers_invalid');
  return result;
}

function record(value: unknown, reason: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(reason);
  return value as Record<string, unknown>;
}

function closed(value: unknown, fields: readonly string[], reason: string): Record<string, unknown> {
  const row = record(value, reason);
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !(key in row))) fail(reason);
  return row;
}

function fail(reason: string): never { throw new Error(reason); }
