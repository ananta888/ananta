import { SpeechEvidenceConsentReadModel } from '../../services/speech-evidence-consent-api.service';
import { SpeechEvidenceConsentPairAuthority } from '../../services/speech-evidence-sync-api.service';
import {
  SpeechEvidenceValidationError,
  canonicalJson,
} from '../../services/speech-evidence-sync.validators';
import { peerEvidenceBulkAcceptForbidden } from './peer-evidence-acceptance';
import { ActiveOffer, PeerEvidenceSyncContext } from './peer-evidence-sync.models';

/**
 * Pure consent and scope policy of the peer evidence sync flow. Every check
 * fails closed with a machine-readable reason code; none of them mutate state.
 */

export function validateContext(value: PeerEvidenceSyncContext): PeerEvidenceSyncContext {
  const consent = value.consent.consent;
  const participants = new Set([consent.speaker_id, consent.recipient_id]);
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || value.pairId !== value.sessionId
    || consent.session_id !== value.sessionId
    || consent.pair_id !== value.pairId
    || consent.session_epoch !== value.epoch
    || consent.owner_subject !== consent.speaker_id
    || participants.size !== 2
    || !participants.has(value.localPeerId)
    || !participants.has(value.remotePeerId)
    || consent.required_signers.length !== 2
    || consent.required_signers.some(signer => !participants.has(signer))
    || consent.direction !== 'sender_to_receiver'
    || consent.state !== 'active'
    || consent.expires_at_ms <= Date.now()
    || (!consent.grants.transcript_share && !consent.grants.feature_share)
  ) throw new SpeechEvidenceValidationError('peer_evidence_sync_context_invalid');
  return Object.freeze({ ...value, hubUrl: value.hubUrl.replace(/\/+$/, '') });
}

export function contextKey(value: PeerEvidenceSyncContext | null): string {
  return value ? [
    value.hubUrl, value.sessionId, value.pairId, value.epoch, value.localPeerId, value.remotePeerId,
    value.consent.consent.consent_version, value.consent.consentDigest,
  ].join('\0') : '';
}

export function validatePairConsentAuthority(
  value: SpeechEvidenceConsentPairAuthority,
  context: PeerEvidenceSyncContext,
): SpeechEvidenceConsentPairAuthority {
  const local = value.local;
  const remote = value.remote;
  const consent = context.consent.consent;
  if (
    local.peerId !== context.localPeerId
    || remote.peerId !== context.remotePeerId
    || local.pairId !== context.pairId
    || remote.pairId !== context.pairId
    || local.version !== consent.consent_version
    || remote.version !== local.version
    || local.digest !== context.consent.consentDigest
    || local.expiresAtMs > consent.expires_at_ms
    || local.expiresAtMs <= Date.now()
    || remote.expiresAtMs <= Date.now()
    || !local.directions.includes(consent.direction)
    || !remote.directions.includes(consent.direction)
    || !local.purposes.includes(consent.purpose)
    || !remote.purposes.includes(consent.purpose)
  ) throw new SpeechEvidenceValidationError('speech_evidence_consent_authority_stale');
  return value;
}

export function consentDigestForPeer(
  peerId: string,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): string {
  if (peerId === context.localPeerId) return consentPair.local.digest;
  if (peerId === context.remotePeerId) return consentPair.remote.digest;
  return '';
}

export function allowedDataClasses(consent: SpeechEvidenceConsentReadModel): Set<'transcript' | 'text_corrections'> {
  const values = new Set<'transcript' | 'text_corrections'>();
  if (consent.consent.grants.transcript_share && consent.consent.data_classes.includes('transcript')) {
    values.add('transcript');
    values.add('text_corrections');
  }
  if (consent.consent.grants.transcript_share && consent.consent.data_classes.includes('correction')) {
    values.add('text_corrections');
  }
  return values;
}

export function requireTrainerClass(
  context: PeerEvidenceSyncContext,
  value: string,
  consentPair: SpeechEvidenceConsentPairAuthority,
): void {
  if (value !== 'none' && value !== 'speech_adaptation') {
    throw new SpeechEvidenceValidationError('speech_evidence_trainer_class_invalid');
  }
  if (value === 'speech_adaptation'
    && (!context.consent.consent.grants.dataset_import
      || !context.consent.consent.grants.training
      || !consentPair.remote.trainerClasses.includes('speech_adaptation'))) {
    throw new SpeechEvidenceValidationError('speech_evidence_training_consent_required');
  }
}

export function allowedTrainerClass(
  context: PeerEvidenceSyncContext,
  requested: string,
  consentPair: SpeechEvidenceConsentPairAuthority,
): 'none' | 'speech_adaptation' {
  if (requested === 'speech_adaptation'
    && context.consent.consent.grants.dataset_import
    && context.consent.consent.grants.training
    && consentPair.remote.trainerClasses.includes('speech_adaptation')) return 'speech_adaptation';
  return 'none';
}

/** A received proposal must stay inside both the local consent and the remote consent authority. */
export function assertIncomingProposalInScope(
  incoming: ActiveOffer,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): void {
  const allowedClasses = allowedDataClasses(context.consent);
  if (
    incoming.recipientId !== context.localPeerId
    || incoming.senderConsentDigest !== consentPair.remote.digest
    || incoming.recipientConsentDigest !== context.consent.consentDigest
    || incoming.direction !== context.consent.consent.direction
    || incoming.purpose !== context.consent.consent.purpose
    || incoming.dataClasses.some(value => peerEvidenceBulkAcceptForbidden(value)
      || !allowedClasses.has(value as 'transcript' | 'text_corrections')
      || !consentPair.remote.dataClasses.includes(value))
    || incoming.fields.some(value => value !== 'transcript' || !consentPair.remote.fields.includes(value))
    || incoming.retentionSeconds > Math.min(
      context.consent.consent.retention_seconds,
      consentPair.remote.maximumRetentionSeconds,
    )
    || (incoming.trainerClass === 'speech_adaptation'
      && (!context.consent.consent.grants.dataset_import || !context.consent.consent.grants.training))
  ) throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
}

/** A received acceptance may only narrow the offer this peer proposed. */
export function assertAcceptanceNarrowsOffer(
  stage: unknown,
  incoming: ActiveOffer,
  current: ActiveOffer,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): void {
  if (
    stage !== 'acceptance'
    || current.senderId !== context.localPeerId
    || incoming.offerId !== current.offerId
    || incoming.senderConsentDigest !== current.senderConsentDigest
    || incoming.recipientConsentDigest !== consentPair.remote.digest
    || incoming.scopeDigest !== current.scopeDigest
    || incoming.inventoryRootDigest !== current.inventoryRootDigest
    || incoming.direction !== current.direction
    || incoming.purpose !== current.purpose
    || incoming.groupIds.some(value => !current.groupIds.includes(value))
    || incoming.groupPreviews.some(value => {
      const proposed = current.groupPreviews.find(row => row.groupId === value.groupId);
      return !proposed || canonicalJson(value.value) !== canonicalJson(proposed.value);
    })
    || incoming.dataClasses.some(value => !current.dataClasses.includes(value))
    || incoming.fields.some(value => !current.fields.includes(value))
    || incoming.retentionSeconds > current.retentionSeconds
    || (current.trainerClass === 'none' && incoming.trainerClass !== 'none')
    || incoming.totalBytes > current.totalBytes
  ) throw new SpeechEvidenceValidationError('speech_evidence_offer_acceptance_invalid');
}
