import { SpeechEvidenceQuarantineGroupSnapshot } from '../../services/speech-evidence-quarantine.store';
import { SpeechEvidenceHubCurationResponse } from '../../services/speech-evidence-sync-api.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  sha256Canonical,
} from '../../services/speech-evidence-sync.validators';
import { SpeechTranscriptTurn } from '../../services/speech-transcript-revision.store';
import type { SpeechDatasetLineageNodeView } from '../ml-intern/speech-dataset-lineage.component';
import {
  PeerEvidenceLineageView,
  PeerEvidenceQuarantineView,
} from './peer-evidence-sync-panel.component';
import {
  PeerTranscriptCandidateView,
  PeerTranscriptRegionView,
} from './peer-transcript-conflict-panel.component';
import { ActiveOffer, PeerEvidenceGroupPayload, PendingRevocation } from './peer-evidence-sync.models';
import { boundedText, object, sha256Text, stringArray } from './peer-evidence-sync-primitives';

/**
 * Pure projections from verified evidence (receipts, resolutions, completed
 * groups, quarantine summaries) into immutable panel view rows.
 */

export interface PeerEvidenceConflictProjection {
  readonly candidates: readonly PeerTranscriptCandidateView[];
  readonly regions: readonly PeerTranscriptRegionView[];
}

export function lineageWithReceiptStates(
  lineage: readonly PeerEvidenceLineageView[],
  acceptedGroupIds: readonly string[],
  rejectedGroupIds: readonly string[],
  quarantinedGroupIds: readonly string[],
): readonly PeerEvidenceLineageView[] {
  const accepted = new Set(acceptedGroupIds);
  const rejected = new Set(rejectedGroupIds);
  const quarantined = new Set(quarantinedGroupIds);
  return Object.freeze(lineage.map(row => Object.freeze({
    ...row,
    state: accepted.has(row.groupId) ? 'accepted' as const
      : rejected.has(row.groupId) ? 'rejected' as const
        : quarantined.has(row.groupId) ? 'quarantined' as const : row.state,
  })));
}

export function lineageWithRevokedGroups(
  lineage: readonly PeerEvidenceLineageView[],
  groupIds: readonly string[],
): readonly PeerEvidenceLineageView[] {
  return Object.freeze(lineage.map(row =>
    groupIds.includes(row.groupId) ? Object.freeze({ ...row, state: 'revoked' as const }) : row));
}

export function lineageWithQuarantinedGroup(
  lineage: readonly PeerEvidenceLineageView[],
  groupId: string,
  contributorDigest: string,
  fieldDigest: string,
  consentDigest: string,
): readonly PeerEvidenceLineageView[] {
  return Object.freeze([
    ...lineage.filter(value => value.groupId !== groupId),
    Object.freeze({
      groupId,
      contributorDigest,
      consentDigest,
      fieldProvenanceDigests: Object.freeze([fieldDigest]),
      state: 'quarantined' as const,
    }),
  ]);
}

/** Validates the signed resolution candidates; the peer text is display-only. */
export async function resolutionCandidates(
  payload: Readonly<Record<string, unknown>>,
): Promise<readonly PeerTranscriptCandidateView[]> {
  const candidates = payload['candidates'];
  if (!Array.isArray(candidates)) throw new SpeechEvidenceValidationError('speech_evidence_candidates_invalid');
  const candidateIds = stringArray(payload['candidate_ids']);
  if (candidateIds.length !== candidates.length) {
    throw new SpeechEvidenceValidationError('speech_evidence_candidate_ids_invalid');
  }
  return Object.freeze(await Promise.all(candidates.map(async (raw, index) => {
    const value = object(raw, 'speech_evidence_candidate_invalid');
    const text = boundedText(value['text']);
    return Object.freeze({
      candidateId: candidateIds[index],
      contributorLabel: 'Peer',
      sourceLabel: 'signierte Resolution-Evidence',
      revision: 1,
      text,
      verified: true,
    });
  })));
}

export function unresolvedResolutionRegions(
  unresolvedRegionIds: readonly string[],
  candidates: readonly PeerTranscriptCandidateView[],
): readonly PeerTranscriptRegionView[] {
  return Object.freeze(unresolvedRegionIds.map(regionId => Object.freeze({
    regionId,
    kind: 'unresolved',
    candidateIds: Object.freeze(candidates.map(value => value.candidateId)),
    selectedCandidateId: null,
    unresolved: true,
    reasonCode: 'hub_curation_required',
  })));
}

export function completedGroupConflict(
  groupId: string,
  payload: PeerEvidenceGroupPayload,
  localTurn: SpeechTranscriptTurn | undefined,
): PeerEvidenceConflictProjection {
  const remoteCandidates: PeerTranscriptCandidateView[] = payload.candidates.map((candidate, index) => ({
    candidateId: `${groupId}-remote-${index}`,
    contributorLabel: 'Peer',
    sourceLabel: candidate.authority,
    revision: candidate.revision,
    text: candidate.text,
    verified: true,
  }));
  const localCandidates: PeerTranscriptCandidateView[] = (localTurn?.originalCandidates ?? []).map((candidate, index) => ({
    candidateId: `${groupId}-local-${index}`,
    contributorLabel: 'Lokal',
    sourceLabel: candidate.authority,
    revision: candidate.revision,
    text: candidate.text,
    verified: true,
  }));
  const candidates = [...localCandidates, ...remoteCandidates];
  const texts = new Set(candidates.map(value => value.text));
  return {
    candidates: Object.freeze(candidates.map(value => Object.freeze(value))),
    regions: Object.freeze([Object.freeze({
      regionId: `region-${groupId}`,
      kind: texts.size <= 1 ? 'exact' : 'lexical',
      candidateIds: Object.freeze(candidates.map(value => value.candidateId)),
      selectedCandidateId: texts.size <= 1 ? candidates[0]?.candidateId ?? null : null,
      unresolved: texts.size > 1,
      reasonCode: texts.size <= 1 ? 'exact_match' : 'hub_resolution_required',
    })]),
  };
}

export function quarantineRowsFromSummaries(
  summaries: readonly SpeechEvidenceQuarantineGroupSnapshot[],
  localPreAdmissionReasons: ReadonlyMap<string, string>,
): readonly PeerEvidenceQuarantineView[] {
  return Object.freeze(summaries.map(row => {
    const localReason = localPreAdmissionReasons.get(row.groupId) ?? null;
    return Object.freeze({
      offerId: row.offerId,
      groupId: row.groupId,
      receivedChunks: row.receivedChunks,
      chunkCount: row.chunkCount,
      firstMissingIndex: row.firstMissingIndex,
      receivedBytes: row.receivedBytes,
      state: row.conflictCount || localReason ? 'conflict' as const
        : row.complete ? 'quarantined' as const : 'receiving' as const,
      reasonCode: row.conflictCount ? 'speech_evidence_chunk_index_conflict' : localReason,
    });
  }));
}

/** Returns the pending revocation the acknowledgement is bound to, or fails closed. */
export function verifiedRevocationAckTarget(
  message: SpeechEvidenceMessage,
  pending: PendingRevocation | null,
): PendingRevocation {
  if (
    !pending
    || message.payload['revocation_id'] !== pending.revocationId
    || message.payload['scope_digest'] !== pending.scopeDigest
    || message.payload['revocation_epoch'] !== pending.revocationEpoch
  ) throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_binding_mismatch');
  return pending;
}

/** Whether a bound revocation acknowledgement confirms cleanup of every revoked group. */
export function revocationAckResolves(message: SpeechEvidenceMessage, pending: PendingRevocation): boolean {
  const results = message.payload['group_results'];
  if (!Array.isArray(results)) throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_invalid');
  const resolved = new Set(results
    .map(value => object(value, 'speech_evidence_revocation_ack_invalid'))
    .filter(value => ['deleted', 'use_stopped', 'not_found'].includes(String(value['state'])))
    .map(value => String(value['group_id'])));
  if ([...resolved].some(groupId => !pending.groupIds.includes(groupId))) {
    throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_groups_invalid');
  }
  return resolved.size === pending.groupIds.length && message.payload['decision'] === 'complete';
}

export async function buildDatasetLineageNodes(
  response: SpeechEvidenceHubCurationResponse,
  offer: ActiveOffer,
  evidenceLineage: readonly PeerEvidenceLineageView[],
): Promise<readonly SpeechDatasetLineageNodeView[]> {
  const curation = response.curation;
  const manifestDigest = curation.datasetManifestDigest;
  const taskId = curation.curationTaskId;
  if (curation.state !== 'dataset_published' || !manifestDigest || !taskId) return Object.freeze([]);
  const accepted = evidenceLineage.filter(value => value.state === 'accepted');
  const contributors = [...new Set(accepted.map(value => value.contributorDigest))];
  if (!contributors.length) contributors.push(await sha256Text(`peer\0${offer.senderId}`));
  const fieldProvenance = [...new Set(accepted.flatMap(value => value.fieldProvenanceDigests))];
  if (!fieldProvenance.length) fieldProvenance.push(await sha256Canonical([...offer.fields].sort()));
  return Object.freeze([Object.freeze({
    datasetId: curation.datasetId,
    version: `sha256:${manifestDigest}`,
    parentVersion: curation.datasetParentDigest ? `sha256:${curation.datasetParentDigest}` : null,
    manifestDigest,
    receiptId: curation.receipt.receiptId,
    contributorDigests: Object.freeze(contributors.sort()),
    direction: curation.receipt.direction,
    consentDigest: curation.receipt.consentDigest,
    fieldProvenanceDigests: Object.freeze(fieldProvenance.sort()),
    createdByTaskId: taskId,
  })]);
}
