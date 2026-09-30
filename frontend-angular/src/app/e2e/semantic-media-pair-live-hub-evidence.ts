import { firstValueFrom } from 'rxjs';

import {
  buildPeerEvidenceAcceptancePayload,
  peerEvidenceAcceptEnabled,
  verifyPeerEvidenceOfferPreview,
} from '../features/voice/peer-evidence-acceptance';
import {
  SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
  SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION,
  SPEECH_EVIDENCE_PROTOCOL_VERSION,
  SpeechEvidenceMessage,
  SpeechEvidenceMessageType,
  canonicalSigningJson,
  sha256Canonical,
  speechEvidenceComparisonDigest,
  speechEvidenceGroupId,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceResolutionDigest,
  speechEvidenceSpeakerScopeDigest,
  validateSpeechEvidenceMessage,
} from '../services/speech-evidence-sync.validators';
import { SemanticDataChannelMessage } from '../services/webrtc-datachannel.service';
import {
  SemanticMediaPairProductPorts,
  SemanticPeerHubAcceptanceResult,
  SemanticPeerHubCurationResult,
  SemanticPeerHubFixture,
  SemanticPeerHubOffer,
  SemanticPeerHubTransferExerciseResult,
} from './semantic-media-pair-live-driver.models';
import {
  asArrayBuffer,
  assertOfferRecord,
  boundedExpiry,
  curationSummary,
  decodeB64,
  encodeB64,
  localAudience,
  localSubject,
  observedHttpStatus,
  relayEnvelope,
  sha256Bytes,
  sha256Text,
  validateHubFixture,
} from './semantic-media-pair-live-driver.support';

/**
 * Hub-relayed peer evidence flow of the Pair live gate: key registration,
 * signed proposal/acceptance through the product acceptance policy, chunk
 * relay (including reorder/replay), ACK, revocation and curation. The
 * ephemeral Ed25519 signing identity lives only in this module.
 */

const PEER_CURATION_REQUEST_POLICY_DIGEST = 'bd02f0ea6843e13b4be73b3742f1d196a054522ffa4377ce0ddc339d39c46c19';
let hubSigningState: { keys: CryptoKeyPair; keyId: string; sequence: number } | null = null;

export async function prepareHubPeer(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
): Promise<void> {
  const ports = requirePorts();
  validateHubFixture(fixture);
  const keys = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']) as CryptoKeyPair;
  const publicBytes = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
  const fingerprint = await sha256Bytes(publicBytes);
  const keyId = `speech-sign-${fingerprint.slice(0, 32)}`;
  // A page reload rotates the ephemeral signing key, but the protocol replay
  // window is scoped to the peer/session/epoch/traffic class rather than the
  // key id. Keep the monotone sequence cursor in sessionStorage so a real
  // reconnect cannot replay sequence 1 under a freshly generated key.
  hubSigningState = { keys, keyId, sequence: readSigningSequence(fixture) };
  await firstValueFrom(ports.syncApi.registerKey(fixture.hubUrl, {
    sessionId: fixture.sessionId,
    pairId: fixture.sessionId,
    audienceId: localAudience(fixture),
    epoch: fixture.epoch,
    consentVersion: fixture.consentVersion,
    keyId,
    publicKeyB64: encodeB64(publicBytes),
    expiresAtMs: boundedExpiry(fixture),
  }));
  publicBytes.fill(0);
}

export async function proposeHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  relayCanary: string,
): Promise<SemanticPeerHubOffer> {
  const ports = requirePorts();
  const local = localSubject(fixture);
  if (local !== fixture.senderId || !/^ANANTA_RELAY_CANARY_[A-Za-z0-9]{24,96}$/.test(relayCanary)) {
    throw new Error('semantic_peer_hub_proposal_context_invalid');
  }
  const sourceGroupDigest = await sha256Text(`source:${fixture.sessionId}`);
  const originalCandidates = [
    { revision: 1, authority: 'final', text: relayCanary },
    { revision: 2, authority: 'corrected', text: `${relayCanary}-corrected` },
  ];
  const groupBytes = new TextEncoder().encode(JSON.stringify({
    schema: 'ananta.peer-transcript-evidence.v1',
    turn_id: 'turn-live-relay',
    revision: 2,
    state: 'corrected',
    source_digest: sourceGroupDigest,
    candidates: originalCandidates,
  }));
  const groupDigest = await sha256Bytes(groupBytes);
  const groupId = await speechEvidenceGroupId(sourceGroupDigest, 2);
  const inventoryRootDigest = await sha256Canonical({
    group_id: groupId,
    content_digest: groupDigest,
    byte_length: groupBytes.byteLength,
  });
  const offerId = `speech-offer-${crypto.randomUUID()}`;
  const expiresAtMs = boundedExpiry(fixture);
  const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest(
    fixture.sessionId,
    fixture.epoch,
    fixture.senderId,
  );
  const qualityDigest = await speechEvidenceQualityPolicyDigest();
  const candidateProjections = await Promise.all(originalCandidates.map(async (candidate, index) => ({
    ordinal: index + 1,
    candidateDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-original-candidate.v1', source_group_digest: sourceGroupDigest,
      ordinal: index + 1, revision: candidate.revision, authority: candidate.authority,
      candidate_value: candidate.text,
    }),
    authorityDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-candidate-authority.v1', authority: candidate.authority,
    }),
    revision: candidate.revision,
  })));
  const selectedCandidateDigest = candidateProjections[1].candidateDigest;
  const comparisonDigest = await speechEvidenceComparisonDigest({
    sourceGroupDigest, revision: 2, originalCandidates: candidateProjections,
    resolutionState: 'resolved', selectedCandidateDigest, unresolvedRegionDigests: [],
  });
  const payload = Object.freeze({
    traffic_class: 'control',
    offer_id: offerId,
    stage: 'proposal',
    inventory_root_digest: inventoryRootDigest,
    direction: 'sender_to_receiver',
    purpose: 'speech_dataset_curation',
    data_classes: ['text_corrections'],
    fields: ['transcript'],
    retention_seconds: 3_600,
    trainer_class: 'speech_adaptation',
    group_ids: [groupId],
    group_previews: [{
      preview_version: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
      group_id: groupId,
      source_group_digest: sourceGroupDigest,
      speaker_scope_digest: speakerScopeDigest,
      quality_basis: 'policy',
      quality_digest: qualityDigest,
      resolution_digest: await speechEvidenceResolutionDigest(sourceGroupDigest, 2),
      original_candidates: candidateProjections.map(candidate => ({
        ordinal: candidate.ordinal, candidate_digest: candidate.candidateDigest,
        authority_digest: candidate.authorityDigest, revision: candidate.revision,
      })),
      resolution_state: 'resolved',
      selected_candidate_digest: selectedCandidateDigest,
      unresolved_region_digests: [],
      comparison_digest: comparisonDigest,
      revision: 2,
      size_bytes: groupBytes.byteLength,
    }],
    total_bytes: groupBytes.byteLength,
    sender_consent_digest: fixture.senderConsentDigest,
    recipient_consent_digest: fixture.recipientConsentDigest,
    scope_digest: fixture.scopeDigest,
  });
  const message = await signHubMessage(fixture, 'offer', payload, expiresAtMs);
  const record = await firstValueFrom(ports.syncApi.propose(fixture.hubUrl, message));
  assertOfferRecord(record, fixture, offerId, groupId, 'proposed');
  return Object.freeze({
    offerId,
    inventoryRootDigest,
    groupId,
    groupB64: encodeB64(groupBytes),
    totalBytes: groupBytes.byteLength,
    expiresAtMs,
    value: payload,
    signedMessage: message,
    record,
  });
}

export async function acceptHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubAcceptanceResult> {
  const ports = requirePorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_acceptance_context_invalid');
  }
  const verified = await verifyPeerEvidenceOfferPreview({
    pairId: fixture.sessionId,
    epoch: fixture.epoch,
    speakerId: fixture.senderId,
    groupIds: offer.record.groupIds,
    totalBytes: offer.record.totalBytes,
    payload: { group_previews: offer.record.groupPreviews.map(value => value.value) },
    expectedPreviewDigest: offer.record.groupPreviewDigest,
    currentSourceRevisions: new Map(
      offer.record.groupPreviews.map(value => [value.sourceGroupDigest, value.revision]),
    ),
  });
  const selectedClasses = [...offer.record.dataClasses];
  const previewView = Object.freeze({
    offerId: offer.record.offerId,
    direction: offer.record.direction,
    purpose: offer.record.purpose,
    dataClasses: offer.record.dataClasses,
    fields: offer.record.fields,
    retentionSeconds: offer.record.retentionSeconds,
    trainerClass: offer.record.trainerClass,
    groupCount: offer.record.groupIds.length,
    groupPreviews: verified.previews,
    previewVerified: true,
    totalBytes: offer.record.totalBytes,
    senderConsentVersion: fixture.consentVersion,
    recipientConsentVersion: fixture.consentVersion,
    state: 'proposed',
    action: 'accept',
    expiresAtMs: offer.record.expiresAtMs,
  });
  const productAcceptPolicyUsed = peerEvidenceAcceptEnabled(previewView, false, selectedClasses);
  const signedPreviewVisible = previewView.previewVerified && previewView.groupPreviews.length > 0;
  const comparisonPreviewVisible = previewView.groupPreviews.every(value => (
    value.originalCandidates.length >= 2
    && value.resolutionState === 'resolved'
    && value.selectedCandidateDigest !== null
    && value.unresolvedRegionDigests.length === 0
  ));
  const productUiProjectionVisible = ports.renderOfferPreview(previewView);
  if (
    !signedPreviewVisible
    || !comparisonPreviewVisible
    || !productAcceptPolicyUsed
    || !productUiProjectionVisible
  ) {
    throw new Error('semantic_peer_hub_product_accept_policy_denied');
  }
  const payload = buildPeerEvidenceAcceptancePayload({
    offer: {
      ...offer.record,
      groupPreviews: verified.previews,
    },
    acceptedClasses: selectedClasses,
    retentionSeconds: offer.record.retentionSeconds,
    trainerClass: offer.record.trainerClass === 'speech_adaptation' ? 'speech_adaptation' : 'none',
    recipientConsentDigest: fixture.recipientConsentDigest,
  });
  const message = await signHubMessage(fixture, 'offer', payload, offer.expiresAtMs);
  const accepted = await firstValueFrom(ports.syncApi.accept(fixture.hubUrl, message));
  assertOfferRecord(accepted, fixture, offer.offerId, offer.groupId, 'accepted');
  const authorized = await firstValueFrom(ports.syncApi.authorizeTransfer(fixture.hubUrl, offer.offerId));
  if (!authorized.transferStarted || authorized.state !== 'accepted') {
    throw new Error('semantic_peer_hub_transfer_not_authorized');
  }
  return Object.freeze({
    signedPreviewVisible,
    comparisonPreviewVisible,
    productAcceptPolicyUsed,
    productUiProjectionVisible,
  });
}

export async function transferHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<void> {
  const ports = requirePorts();
  if (localSubject(fixture) !== fixture.senderId) {
    throw new Error('semantic_peer_hub_transfer_context_invalid');
  }
  const clear = decodeB64(offer.groupB64);
  const contentKey = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: asArrayBuffer(nonce) }, contentKey, asArrayBuffer(clear),
  ));
  const ciphertextDigest = await sha256Bytes(ciphertext);
  const message = await signHubMessage(fixture, 'chunk', {
    traffic_class: 'evidence_bulk',
    offer_id: offer.offerId,
    group_id: offer.groupId,
    chunk_index: 0,
    chunk_count: 1,
    plaintext_bytes: clear.byteLength,
    plaintext_digest: await sha256Bytes(clear),
    ciphertext_digest: ciphertextDigest,
    nonce_b64: encodeB64(nonce),
    ciphertext_b64: encodeB64(ciphertext),
  }, offer.expiresAtMs);
  const relay = await relayEnvelope(fixture, message, ciphertext, ciphertextDigest);
  const transfer = await firstValueFrom(ports.syncApi.appendChunk(fixture.hubUrl, message, relay));
  if (transfer.groupId !== offer.groupId || transfer.chunkCount !== 1 || transfer.state !== 'active') {
    throw new Error('semantic_peer_hub_chunk_not_registered');
  }
  clear.fill(0);
  nonce.fill(0);
  ciphertext.fill(0);
}

/**
 * Exercise the real Hub transfer route with out-of-order delivery and an
 * exact replay. The result is derived exclusively from returned transfer
 * records and the observed HTTP conflict; no synthetic outcome map is used.
 */
export async function exerciseHubEvidenceTransfer(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubTransferExerciseResult> {
  const ports = requirePorts();
  if (localSubject(fixture) !== fixture.senderId || fixture.transport !== 'hub_relay') {
    throw new Error('semantic_peer_hub_transfer_context_invalid');
  }
  const clear = decodeB64(offer.groupB64);
  const splitAt = Math.max(1, Math.floor(clear.byteLength / 2));
  const parts = [clear.slice(0, splitAt), clear.slice(splitAt)];
  let backendRouteCount = 0;
  const contentKey = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 }, false, ['encrypt'],
  );
  const messages: Array<{ message: SpeechEvidenceMessage; relay: SemanticDataChannelMessage }> = [];
  try {
    for (const [index, part] of parts.entries()) {
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: asArrayBuffer(nonce) }, contentKey, asArrayBuffer(part),
      ));
      const ciphertextDigest = await sha256Bytes(ciphertext);
      const message = await signHubMessage(fixture, 'chunk', {
        traffic_class: 'evidence_bulk',
        offer_id: offer.offerId,
        group_id: offer.groupId,
        chunk_index: index,
        chunk_count: parts.length,
        plaintext_bytes: part.byteLength,
        plaintext_digest: await sha256Bytes(part),
        ciphertext_digest: ciphertextDigest,
        nonce_b64: encodeB64(nonce),
        ciphertext_b64: encodeB64(ciphertext),
      }, offer.expiresAtMs);
      messages.push({
        message,
        relay: await relayEnvelope(fixture, message, ciphertext, ciphertextDigest),
      });
      nonce.fill(0);
      ciphertext.fill(0);
    }

    const outOfOrder = await firstValueFrom(ports.syncApi.appendChunk(
      fixture.hubUrl, messages[1].message, messages[1].relay,
    ));
    backendRouteCount += 1;
    let duplicateRejected = false;
    try {
      await firstValueFrom(ports.syncApi.appendChunk(
        fixture.hubUrl, messages[1].message, messages[1].relay,
      ));
      backendRouteCount += 1;
    } catch (error) {
      backendRouteCount += 1;
      duplicateRejected = observedHttpStatus(error) === 409;
    }
    const completedUpload = await firstValueFrom(ports.syncApi.appendChunk(
      fixture.hubUrl, messages[0].message, messages[0].relay,
    ));
    backendRouteCount += 1;
    const status = await firstValueFrom(ports.syncApi.transferStatus(
      fixture.hubUrl, offer.offerId, offer.groupId,
    ));
    backendRouteCount += 1;
    const reorderRecovered = outOfOrder.firstMissingIndex === 0
      && completedUpload.chunkCount === 2
      && status.chunkCount === 2
      && status.inFlightBytes === offer.totalBytes;
    if (!duplicateRejected || !reorderRecovered) {
      throw new Error('semantic_peer_hub_transfer_exercise_failed');
    }
    return Object.freeze({
      backendRouteCount,
      forcedRelayUsed: fixture.transport === 'hub_relay',
      duplicateRejected,
      reorderRecovered,
    });
  } finally {
    clear.fill(0);
    for (const part of parts) part.fill(0);
  }
}

export async function recoverHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<boolean> {
  const ports = requirePorts();
  const offers = await firstValueFrom(ports.syncApi.listOffers(fixture.hubUrl, {
    sessionId: fixture.sessionId,
    pairId: fixture.sessionId,
    epoch: fixture.epoch,
  }));
  const recovered = offers.find(value => value.offerId === offer.offerId);
  if (!recovered || recovered.groupPreviewDigest !== offer.record.groupPreviewDigest) return false;
  const transfer = await firstValueFrom(ports.syncApi.transferStatus(
    fixture.hubUrl, offer.offerId, offer.groupId,
  ));
  return transfer.chunkCount === 2 && transfer.state === 'active';
}

export async function revokeHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<boolean> {
  const ports = requirePorts();
  const reasonCode = 'speech_evidence_e2e_user_revoked';
  const invalidated = await firstValueFrom(ports.syncApi.invalidate(
    fixture.hubUrl, offer.offerId, reasonCode,
  ));
  return invalidated.state === 'invalidated'
    && invalidated.invalidationReason === reasonCode;
}

export async function acknowledgeHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<void> {
  const ports = requirePorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_ack_context_invalid');
  }
  const status = await firstValueFrom(ports.syncApi.transferStatus(
    fixture.hubUrl, offer.offerId, offer.groupId,
  ));
  const indices = Array.from({ length: status.chunkCount }, (_, index) => index);
  const message = await signHubMessage(fixture, 'chunk_ack', {
    traffic_class: 'control',
    offer_id: offer.offerId,
    group_id: offer.groupId,
    acknowledged_indices: indices,
    first_missing_index: status.chunkCount,
    received_bytes: offer.totalBytes,
    complete: true,
  }, offer.expiresAtMs);
  const transfer = await firstValueFrom(ports.syncApi.acknowledgeChunk(fixture.hubUrl, message));
  if (transfer.state !== 'completed' || transfer.firstMissingIndex !== status.chunkCount) {
    throw new Error('semantic_peer_hub_ack_not_completed');
  }
}

export async function curateHubEvidence(
  requirePorts: () => SemanticMediaPairProductPorts,
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubCurationResult> {
  const ports = requirePorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_curation_context_invalid');
  }
  const quarantined = [offer.groupId];
  const resolutionDigest = await sha256Canonical({
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    quarantined_group_ids: quarantined,
  });
  const resultDigest = await sha256Canonical({ accepted: [], quarantined, rejected: [] });
  const message = await signHubMessage(fixture, 'receipt', {
    traffic_class: 'control',
    receipt_id: `curation-request-${crypto.randomUUID()}`,
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    resolution_digest: resolutionDigest,
    accepted_group_ids: [],
    rejected_group_ids: [],
    quarantined_group_ids: quarantined,
    consent_digest: fixture.recipientConsentDigest,
    policy_digest: PEER_CURATION_REQUEST_POLICY_DIGEST,
    result_digest: resultDigest,
  }, offer.expiresAtMs);
  // exerciseHubEvidenceTransfer deliberately sends two signed plaintext
  // chunks out of order. Curation must disclose those exact ACK-bound chunk
  // boundaries; sending the rejoined group as one chunk correctly fails the
  // Hub's digest/sequence binding with 409.
  const clear = decodeB64(offer.groupB64);
  const splitAt = Math.max(1, Math.floor(clear.byteLength / 2));
  const chunks = [clear.slice(0, splitAt), clear.slice(splitAt)];
  try {
    const response = await ports.curation.request({
      hubUrl: fixture.hubUrl,
      binding: {
        offerId: offer.offerId,
        inventoryRootDigest: offer.inventoryRootDigest,
        pairId: fixture.sessionId,
        direction: 'sender_to_receiver',
        consentDigest: fixture.recipientConsentDigest,
        groupIds: [offer.groupId],
      },
      message,
      groups: [{ groupId: offer.groupId, chunksB64: chunks.map(encodeB64) }],
    });
    return curationSummary(response);
  } finally {
    clear.fill(0);
    for (const chunk of chunks) chunk.fill(0);
  }
}

async function signHubMessage(
  fixture: SemanticPeerHubFixture,
  type: SpeechEvidenceMessageType,
  payload: Readonly<Record<string, unknown>>,
  expiresAtMs: number,
): Promise<SpeechEvidenceMessage> {
  const state = requireHubSigningState();
  const issuedAtMs = Date.now();
  const unsigned: SpeechEvidenceMessage = {
    protocol_version: type === 'offer'
      ? SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION
      : SPEECH_EVIDENCE_PROTOCOL_VERSION,
    message_type: type,
    message_id: `speech-e2e-${type}-${crypto.randomUUID()}`,
    session_id: fixture.sessionId,
    pair_id: fixture.sessionId,
    sender_id: localSubject(fixture),
    audience_id: localAudience(fixture),
    epoch: fixture.epoch,
    sequence: nextSigningSequence(fixture, state),
    consent_version: fixture.consentVersion,
    key_id: state.keyId,
    issued_at_ms: issuedAtMs,
    expires_at_ms: Math.min(expiresAtMs, issuedAtMs + 5 * 60_000),
    payload_digest: await sha256Canonical(payload),
    payload,
    signature_algorithm: 'Ed25519',
    signature_b64: encodeB64(new Uint8Array(64)),
  };
  const signature = new Uint8Array(await crypto.subtle.sign(
    'Ed25519', state.keys.privateKey, new TextEncoder().encode(canonicalSigningJson(unsigned)),
  ));
  const signed = validateSpeechEvidenceMessage({ ...unsigned, signature_b64: encodeB64(signature) });
  signature.fill(0);
  return signed;
}

function signingSequenceStorageKey(fixture: SemanticPeerHubFixture): string {
  return [
    'ananta.semantic-media-e2e.signing-sequence',
    fixture.sessionId,
    String(fixture.epoch),
    localSubject(fixture),
  ].join(':');
}

function readSigningSequence(fixture: SemanticPeerHubFixture): number {
  const value = Number(sessionStorage.getItem(signingSequenceStorageKey(fixture)) || '0');
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function nextSigningSequence(
  fixture: SemanticPeerHubFixture,
  state: { sequence: number },
): number {
  state.sequence += 1;
  sessionStorage.setItem(signingSequenceStorageKey(fixture), String(state.sequence));
  return state.sequence;
}

function requireHubSigningState(): { keys: CryptoKeyPair; keyId: string; sequence: number } {
  if (!hubSigningState) throw new Error('semantic_peer_hub_signing_state_missing');
  return hubSigningState;
}
