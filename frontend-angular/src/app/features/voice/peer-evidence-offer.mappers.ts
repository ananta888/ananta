import {
  SpeechEvidenceConsentPairAuthority,
  SpeechEvidenceHubTransferRecord,
  SpeechEvidenceOfferRecord,
} from '../../services/speech-evidence-sync-api.service';
import { SpeechEvidenceHubCurationBinding } from '../../services/speech-evidence-hub-curation.facade';
import { SpeechEvidenceTransferSnapshot } from '../../services/speech-evidence-sync.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  speechEvidenceGroupPreviews,
} from '../../services/speech-evidence-sync.validators';
import { PeerEvidenceOfferView, PeerEvidenceSyncView } from './peer-evidence-sync-panel.component';
import { ActiveOffer, PeerEvidenceSyncContext } from './peer-evidence-sync.models';
import { digest, identifier, positiveInteger, stringArray } from './peer-evidence-sync-primitives';

/** Pure mappers between signed messages, Hub records and the offer/sync view models. */

export function emptySync(state: string): PeerEvidenceSyncView {
  return Object.freeze({
    state,
    pending: false,
    acknowledgedChunks: 0,
    chunkCount: 0,
    firstMissingIndex: 0,
    inFlightBytes: 0,
    retries: 0,
    quarantineCount: 0,
    receiptId: null,
    receiptVerification: 'none',
    curationTaskId: null,
    datasetId: null,
    datasetManifestDigest: null,
    datasetLineageNodes: Object.freeze([]),
    revocationState: null,
    reasonCode: null,
    localGroups: Object.freeze([]),
    quarantine: Object.freeze([]),
    lineage: Object.freeze([]),
    candidates: Object.freeze([]),
    regions: Object.freeze([]),
    resolutionHash: '',
    resolutionPolicyVersion: '',
  });
}

export function offerFromMessage(message: SpeechEvidenceMessage, localConsentVersion: number): ActiveOffer {
  const payload = message.payload;
  const stage = String(payload['stage']);
  if (stage !== 'proposal' && stage !== 'acceptance') {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_stage_invalid');
  }
  const groupPreviews = speechEvidenceGroupPreviews(payload);
  return Object.freeze({
    offerId: identifier(payload['offer_id']),
    sessionId: message.session_id,
    pairId: message.pair_id,
    epoch: message.epoch,
    senderId: stage === 'proposal' ? message.sender_id : message.audience_id,
    recipientId: stage === 'proposal' ? message.audience_id : message.sender_id,
    inventoryRootDigest: digest(payload['inventory_root_digest']),
    direction: identifier(payload['direction']),
    purpose: identifier(payload['purpose']),
    dataClasses: Object.freeze(stringArray(payload['data_classes'])),
    fields: Object.freeze(stringArray(payload['fields'])),
    retentionSeconds: positiveInteger(payload['retention_seconds']),
    trainerClass: identifier(payload['trainer_class']),
    groupIds: Object.freeze(stringArray(payload['group_ids'])),
    groupPreviews,
    groupPreviewDigest: message.payload_digest,
    previewVerified: false,
    totalBytes: positiveInteger(payload['total_bytes']),
    senderConsentDigest: digest(payload['sender_consent_digest']),
    recipientConsentDigest: digest(payload['recipient_consent_digest']),
    scopeDigest: digest(payload['scope_digest']),
    expiresAtMs: message.expires_at_ms,
    state: stage === 'proposal' ? 'proposed' : 'accepted',
    transferStarted: false,
    senderConsentVersion: stage === 'proposal' ? message.consent_version : localConsentVersion,
    recipientConsentVersion: stage === 'acceptance' ? message.consent_version : localConsentVersion,
  });
}

export function offerFromRecord(
  record: SpeechEvidenceOfferRecord,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): ActiveOffer {
  const versionFor = (peerId: string): number => peerId === context.localPeerId
    ? consentPair.local.version
    : peerId === context.remotePeerId
      ? consentPair.remote.version
      : 0;
  const senderConsentVersion = versionFor(record.senderId);
  const recipientConsentVersion = versionFor(record.recipientId);
  if (!senderConsentVersion || !recipientConsentVersion) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_pair_invalid');
  }
  return Object.freeze({
    offerId: record.offerId,
    sessionId: record.sessionId,
    pairId: record.pairId,
    epoch: record.epoch,
    senderId: record.senderId,
    recipientId: record.recipientId,
    inventoryRootDigest: record.inventoryRootDigest,
    direction: record.direction,
    purpose: record.purpose,
    dataClasses: record.dataClasses,
    fields: record.fields,
    retentionSeconds: record.retentionSeconds,
    trainerClass: record.trainerClass,
    groupIds: record.groupIds,
    groupPreviews: record.groupPreviews,
    groupPreviewDigest: record.groupPreviewDigest,
    previewVerified: false,
    totalBytes: record.totalBytes,
    senderConsentDigest: record.senderConsentDigest,
    recipientConsentDigest: record.recipientConsentDigest,
    scopeDigest: record.scopeDigest,
    expiresAtMs: record.expiresAtMs,
    state: record.state,
    transferStarted: record.transferStarted,
    senderConsentVersion,
    recipientConsentVersion,
  });
}

export function hubCurationBinding(
  context: PeerEvidenceSyncContext,
  offer: ActiveOffer,
): SpeechEvidenceHubCurationBinding {
  return Object.freeze({
    offerId: offer.offerId,
    inventoryRootDigest: offer.inventoryRootDigest,
    pairId: offer.pairId,
    direction: offer.direction,
    consentDigest: context.consent.consentDigest,
    groupIds: Object.freeze([...offer.groupIds]),
  });
}

export function offerView(offer: ActiveOffer, localPeerId: string): PeerEvidenceOfferView {
  const action: PeerEvidenceOfferView['action'] = ['invalidated', 'expired', 'rejected'].includes(offer.state)
    ? 'terminal'
    : offer.state === 'proposed' && offer.recipientId === localPeerId
      ? 'accept'
      : offer.state === 'proposed'
        ? 'awaiting_peer'
        : 'transfer';
  return Object.freeze({
    offerId: offer.offerId,
    direction: offer.direction,
    purpose: offer.purpose,
    dataClasses: offer.dataClasses,
    fields: offer.fields,
    retentionSeconds: offer.retentionSeconds,
    trainerClass: offer.trainerClass,
    groupCount: offer.groupIds.length,
    groupPreviews: offer.groupPreviews,
    previewVerified: offer.previewVerified,
    totalBytes: offer.totalBytes,
    senderConsentVersion: offer.senderConsentVersion,
    recipientConsentVersion: offer.recipientConsentVersion,
    state: offer.state,
    action,
    expiresAtMs: offer.expiresAtMs,
  });
}

export function outboundSnapshotFromHubStatus(
  status: SpeechEvidenceHubTransferRecord,
  previousRetries: number,
): SpeechEvidenceTransferSnapshot {
  return {
    offerId: status.offerId,
    groupId: status.groupId,
    state: status.state === 'completed' ? 'completed' : status.state === 'active' ? 'active' : 'failed',
    chunkCount: status.chunkCount,
    acknowledgedChunks: status.acknowledgedChunks,
    firstMissingIndex: status.firstMissingIndex,
    inFlightBytes: status.inFlightBytes,
    retries: previousRetries,
    reasonCode: status.reasonCode,
  };
}

export function aggregateOutboundSnapshots(
  snapshots: readonly SpeechEvidenceTransferSnapshot[],
): Partial<PeerEvidenceSyncView> {
  const acknowledged = snapshots.reduce((total, value) => total + value.acknowledgedChunks, 0);
  const count = snapshots.reduce((total, value) => total + value.chunkCount, 0);
  return {
    state: snapshots.length && snapshots.every(value => value.state === 'completed') ? 'completed'
      : snapshots.some(value => value.state === 'failed') ? 'failed' : 'transferring',
    acknowledgedChunks: acknowledged,
    chunkCount: count,
    firstMissingIndex: snapshots.length ? Math.min(...snapshots.map(value => value.firstMissingIndex)) : 0,
    inFlightBytes: snapshots.reduce((total, value) => total + value.inFlightBytes, 0),
    retries: snapshots.reduce((total, value) => total + value.retries, 0),
    reasonCode: snapshots.find(value => value.reasonCode)?.reasonCode ?? null,
  };
}
