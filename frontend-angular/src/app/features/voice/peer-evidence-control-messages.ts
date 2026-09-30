import { SpeechEvidenceQuarantineGroupSnapshot } from '../../services/speech-evidence-quarantine.store';
import { SpeechEvidenceConsentPairAuthority } from '../../services/speech-evidence-sync-api.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  sha256Canonical,
} from '../../services/speech-evidence-sync.validators';
import { SpeechTranscriptTurn } from '../../services/speech-transcript-revision.store';
import { requireTrainerClass } from './peer-evidence-consent-policy';
import { buildProposalGroupPreviews } from './peer-evidence-group-payload';
import { PeerEvidenceProposalIntent } from './peer-evidence-sync-panel.component';
import {
  ActiveOffer,
  LocalEvidenceArtifact,
  PEER_CURATION_REQUEST_POLICY_DIGEST,
  PeerEvidenceSyncContext,
  PendingRevocation,
} from './peer-evidence-sync.models';

/**
 * Pure builders for the unsigned control payloads of the peer evidence
 * protocol plus the inbound binding check. Signing and delivery stay with
 * the facade's crypto/transport collaborators.
 */

export interface PeerEvidenceProposalDraft {
  readonly payload: Record<string, unknown>;
  readonly expiresAtMs: number;
}

export function assertInboundBoundToContext(message: SpeechEvidenceMessage, context: PeerEvidenceSyncContext): void {
  if (
    message.session_id !== context.sessionId
    || message.pair_id !== context.pairId
    || message.epoch !== context.epoch
    || message.sender_id !== context.remotePeerId
    || message.audience_id !== context.localPeerId
    || message.consent_version !== context.consent.consent.consent_version
  ) throw new SpeechEvidenceValidationError('speech_evidence_context_mismatch');
}

export function currentSourceRevisions(turns: readonly SpeechTranscriptTurn[]): ReadonlyMap<string, number> {
  const sourceRevisions = new Map<string, number>();
  for (const turn of turns) {
    if (typeof turn.sourceDigest !== 'string' || !/^[a-f0-9]{64}$/.test(turn.sourceDigest)) continue;
    sourceRevisions.set(
      turn.sourceDigest,
      Math.max(sourceRevisions.get(turn.sourceDigest) ?? 0, turn.revision),
    );
  }
  return sourceRevisions;
}

export async function buildProposalDraft(
  artifacts: readonly LocalEvidenceArtifact[],
  intent: PeerEvidenceProposalIntent,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): Promise<PeerEvidenceProposalDraft> {
  if (!artifacts.length || new Set(artifacts.map(value => value.view.dataClass)).size !== 1) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_single_class_required');
  }
  if (artifacts.some(value => !consentPair.remote.dataClasses.includes(value.view.dataClass))) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
  }
  requireTrainerClass(context, intent.trainerClass, consentPair);
  const consent = context.consent.consent;
  const expiresAtMs = Math.min(
    consent.expires_at_ms,
    consentPair.remote.expiresAtMs,
    Date.now() + 5 * 60_000,
  );
  const inventoryRootDigest = await sha256Canonical(artifacts.map(value => ({
    group_id: value.view.groupId,
    content_digest: value.contentDigest,
    data_class: value.view.dataClass,
    bytes: value.view.byteLength,
  })));
  const groupPreviews = await buildProposalGroupPreviews(artifacts, context);
  const payload = {
    traffic_class: 'control',
    offer_id: `speech-offer-${crypto.randomUUID()}`,
    stage: 'proposal',
    inventory_root_digest: inventoryRootDigest,
    direction: consent.direction,
    purpose: consent.purpose,
    data_classes: [artifacts[0].view.dataClass],
    fields: ['transcript'],
    retention_seconds: Math.min(consent.retention_seconds, consentPair.remote.maximumRetentionSeconds),
    trainer_class: intent.trainerClass,
    group_ids: artifacts.map(value => value.view.groupId).sort(),
    group_previews: groupPreviews,
    total_bytes: artifacts.reduce((total, value) => total + value.view.byteLength, 0),
    sender_consent_digest: context.consent.consentDigest,
    recipient_consent_digest: consentPair.remote.digest,
    scope_digest: context.consent.scopeDigest,
  };
  return { payload, expiresAtMs };
}

export async function buildHubCurationRequestPayload(
  offer: ActiveOffer,
  context: PeerEvidenceSyncContext,
): Promise<Record<string, unknown>> {
  const groupIds = [...offer.groupIds].sort();
  const resolutionDigest = await sha256Canonical({
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    quarantined_group_ids: groupIds,
  });
  const resultDigest = await sha256Canonical({
    accepted: [],
    quarantined: groupIds,
    rejected: [],
  });
  return {
    traffic_class: 'control',
    receipt_id: `curation-request-${crypto.randomUUID()}`,
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    resolution_digest: resolutionDigest,
    accepted_group_ids: [],
    rejected_group_ids: [],
    quarantined_group_ids: groupIds,
    consent_digest: context.consent.consentDigest,
    policy_digest: PEER_CURATION_REQUEST_POLICY_DIGEST,
    result_digest: resultDigest,
  };
}

export function chunkAckPayload(
  offerId: string,
  groupId: string,
  chunkIndex: unknown,
  snapshot: SpeechEvidenceQuarantineGroupSnapshot,
): Record<string, unknown> {
  return {
    traffic_class: 'control',
    offer_id: offerId,
    group_id: groupId,
    acknowledged_indices: [Number(chunkIndex)],
    first_missing_index: snapshot.firstMissingIndex,
    received_bytes: snapshot.receivedBytes,
    complete: snapshot.complete,
  };
}

export async function revocationAckPayload(
  message: SpeechEvidenceMessage,
  scopeDigest: string,
  groups: readonly string[],
): Promise<Record<string, unknown>> {
  const groupResults = groups.map(groupId => ({
    group_id: groupId,
    state: 'deleted',
    reason_code: 'local_cleanup_complete',
  }));
  const impactDigest = await sha256Canonical(groupResults);
  return {
    traffic_class: 'control',
    revocation_id: message.payload['revocation_id'],
    scope_digest: scopeDigest,
    revocation_epoch: message.payload['revocation_epoch'],
    impact_digest: impactDigest,
    group_results: groupResults,
    decision: 'complete',
  };
}

export function revocationRequestPayload(pending: PendingRevocation): Record<string, unknown> {
  return {
    traffic_class: 'control',
    revocation_id: pending.revocationId,
    group_ids: [...pending.groupIds],
    scope_digest: pending.scopeDigest,
    reason_code: 'speech_evidence_user_revoked',
    revocation_epoch: pending.revocationEpoch,
    deadline_at_ms: pending.deadlineAtMs,
    requested_action: 'delete',
  };
}
