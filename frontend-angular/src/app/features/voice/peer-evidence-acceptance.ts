import {
  SpeechEvidenceGroupPreview,
  SpeechEvidenceValidationError,
  speechEvidenceGroupPreviewSetDigest,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceSpeakerScopeDigest,
  validateSpeechEvidenceGroupPreviewBindings,
} from '../../services/speech-evidence-sync.validators';

export interface PeerEvidenceAcceptanceOffer {
  readonly offerId: string;
  readonly inventoryRootDigest: string;
  readonly direction: string;
  readonly purpose: string;
  readonly dataClasses: readonly string[];
  readonly fields: readonly string[];
  readonly retentionSeconds: number;
  readonly trainerClass: string;
  readonly groupIds: readonly string[];
  readonly groupPreviews: readonly SpeechEvidenceGroupPreview[];
  readonly totalBytes: number;
  readonly senderConsentDigest: string;
  readonly scopeDigest: string;
}

export interface PeerEvidencePreviewVerificationInput {
  readonly pairId: string;
  readonly epoch: number;
  readonly speakerId: string;
  readonly groupIds: readonly string[];
  readonly totalBytes: number;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly expectedPreviewDigest?: string;
  readonly currentSourceRevisions: ReadonlyMap<string, number>;
}

export interface PeerEvidencePreviewVerificationResult {
  readonly previews: readonly SpeechEvidenceGroupPreview[];
  readonly previewDigest: string;
}

export interface PeerEvidenceAcceptDisplay {
  readonly action: 'accept' | 'awaiting_peer' | 'transfer' | 'terminal';
  readonly previewVerified: boolean;
  readonly dataClasses: readonly string[];
}

const FORBIDDEN_BULK = new Set(['raw_audio', 'audio', 'adapter_export', 'model_artifact', 'export']);

/**
 * SRP policy boundary shared by the UI facade and source-bound live gate.
 * It has no transport, storage or Angular-DI side effects.
 */
export async function verifyPeerEvidenceOfferPreview(
  input: PeerEvidencePreviewVerificationInput,
): Promise<PeerEvidencePreviewVerificationResult> {
  const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest(
    input.pairId,
    input.epoch,
    input.speakerId,
  );
  const qualityDigest = await speechEvidenceQualityPolicyDigest();
  const verified = await validateSpeechEvidenceGroupPreviewBindings(input.payload, {
    speakerScopeDigest,
    qualityBasis: 'policy',
    qualityDigest,
    currentSourceRevisions: input.currentSourceRevisions,
  });
  if (
    verified.length !== input.groupIds.length
    || verified.some(value => !input.groupIds.includes(value.groupId))
    || verified.reduce((total, value) => total + value.sizeBytes, 0) !== input.totalBytes
  ) throw new SpeechEvidenceValidationError('speech_evidence_offer_preview_invalid');
  const previewDigest = await speechEvidenceGroupPreviewSetDigest(verified);
  if (input.expectedPreviewDigest !== undefined && previewDigest !== input.expectedPreviewDigest) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_preview_digest_mismatch');
  }
  return Object.freeze({ previews: verified, previewDigest });
}

export function buildPeerEvidenceAcceptancePayload(input: Readonly<{
  offer: PeerEvidenceAcceptanceOffer;
  acceptedClasses: readonly string[];
  retentionSeconds: number;
  trainerClass: 'none' | 'speech_adaptation';
  recipientConsentDigest: string;
}>): Readonly<Record<string, unknown>> {
  const acceptedClasses = [...new Set(input.acceptedClasses)];
  if (
    !acceptedClasses.length
    || acceptedClasses.some(value => peerEvidenceBulkAcceptForbidden(value)
      || !input.offer.dataClasses.includes(value))
  ) throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
  // Offers have no group-to-class map. Partial class acceptance would retain
  // ambiguous groups and is therefore denied rather than silently widened.
  if (acceptedClasses.length !== input.offer.dataClasses.length) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_group_class_mapping_unavailable');
  }
  if (input.retentionSeconds < 1 || input.retentionSeconds > input.offer.retentionSeconds) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
  }
  if (input.trainerClass !== 'none' && input.trainerClass !== input.offer.trainerClass) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
  }
  return Object.freeze({
    traffic_class: 'control',
    offer_id: input.offer.offerId,
    stage: 'acceptance',
    inventory_root_digest: input.offer.inventoryRootDigest,
    direction: input.offer.direction,
    purpose: input.offer.purpose,
    data_classes: acceptedClasses.sort(),
    fields: [...input.offer.fields],
    retention_seconds: input.retentionSeconds,
    trainer_class: input.trainerClass,
    group_ids: [...input.offer.groupIds],
    group_previews: input.offer.groupPreviews.map(value => value.value),
    total_bytes: input.offer.totalBytes,
    sender_consent_digest: input.offer.senderConsentDigest,
    recipient_consent_digest: input.recipientConsentDigest,
    scope_digest: input.offer.scopeDigest,
  });
}

export function peerEvidenceAcceptEnabled(
  offer: PeerEvidenceAcceptDisplay | null,
  pending: boolean,
  selectedDataClasses: readonly string[],
): boolean {
  return offer?.action === 'accept'
    && offer.previewVerified
    && !pending
    && selectedDataClasses.length > 0
    && selectedDataClasses.every(value => (
      offer.dataClasses.includes(value) && !peerEvidenceBulkAcceptForbidden(value)
    ));
}

export function peerEvidenceBulkAcceptForbidden(value: string): boolean {
  return FORBIDDEN_BULK.has(value);
}
