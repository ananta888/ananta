import { SpeechEvidenceQuarantineGroupSnapshot } from '../../services/speech-evidence-quarantine.store';
import {
  SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
  SpeechEvidenceGroupPreview,
  SpeechEvidenceValidationError,
  canonicalJson,
  sha256Canonical,
  speechEvidenceComparisonDigest,
  speechEvidenceGroupId,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceResolutionDigest,
  speechEvidenceSpeakerScopeDigest,
} from '../../services/speech-evidence-sync.validators';
import { SpeechTranscriptTurn } from '../../services/speech-transcript-revision.store';
import {
  LocalEvidenceArtifact,
  PeerEvidenceComparisonProjection,
  PeerEvidenceGroupCandidate,
  PeerEvidenceGroupPayload,
  PeerEvidenceSyncContext,
} from './peer-evidence-sync.models';
import {
  boundedText,
  digest,
  identifier,
  object,
  positiveInteger,
  sha256Bytes,
} from './peer-evidence-sync-primitives';

/**
 * Pure codec for the canonical transcript evidence group: building it from a
 * local turn, parsing it on the recipient and deriving its content-free
 * comparison projection and preview.
 */

const EVIDENCE_GROUP_SCHEMA = 'ananta.peer-transcript-evidence.v1';

export function isShareableTranscriptTurn(turn: SpeechTranscriptTurn): boolean {
  return ['final', 'corrected', 'correction_failed'].includes(turn.state)
    && typeof turn.sourceDigest === 'string' && /^[a-f0-9]{64}$/.test(turn.sourceDigest);
}

export async function buildLocalEvidenceArtifact(
  turn: SpeechTranscriptTurn,
  allowed: ReadonlySet<'transcript' | 'text_corrections'>,
): Promise<LocalEvidenceArtifact | null> {
  const dataClass = turn.state === 'corrected' ? 'text_corrections' as const : 'transcript' as const;
  if (!allowed.has(dataClass)) return null;
  const payload = {
    schema: EVIDENCE_GROUP_SCHEMA,
    turn_id: turn.turnId,
    revision: turn.revision,
    state: turn.state,
    source_digest: turn.sourceDigest,
    candidates: turn.originalCandidates.map(candidate => ({
      revision: candidate.revision,
      authority: candidate.authority,
      text: candidate.text,
    })),
  };
  const bytes = new TextEncoder().encode(canonicalJson(payload));
  if (!bytes.byteLength || bytes.byteLength > 1024 * 1024) return null;
  const contentDigest = await sha256Bytes(bytes);
  const groupId = await speechEvidenceGroupId(turn.sourceDigest as string, turn.revision);
  const comparison = await contentFreeComparisonProjection(turn);
  return Object.freeze({
    view: Object.freeze({
      groupId,
      turnId: turn.turnId,
      revision: turn.revision,
      dataClass,
      fields: Object.freeze(['transcript']),
      byteLength: bytes.byteLength,
      sourceState: turn.state,
    }),
    bytes,
    contentDigest,
    sourceGroupDigest: turn.sourceDigest as string,
    ...comparison,
  } satisfies LocalEvidenceArtifact);
}

/** Wire previews (snake_case) the sender signs into a proposal, sorted by group id. */
export async function buildProposalGroupPreviews(
  artifacts: readonly LocalEvidenceArtifact[],
  context: PeerEvidenceSyncContext,
): Promise<Record<string, unknown>[]> {
  const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest(
    context.pairId,
    context.epoch,
    context.consent.consent.speaker_id,
  );
  const qualityDigest = await speechEvidenceQualityPolicyDigest();
  const groupPreviews = await Promise.all(artifacts.map(async artifact => ({
    preview_version: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
    group_id: artifact.view.groupId,
    source_group_digest: artifact.sourceGroupDigest,
    speaker_scope_digest: speakerScopeDigest,
    quality_basis: 'policy',
    quality_digest: qualityDigest,
    resolution_digest: await speechEvidenceResolutionDigest(
      artifact.sourceGroupDigest,
      artifact.view.revision,
    ),
    original_candidates: artifact.originalCandidates.map(candidate => ({
      ordinal: candidate.ordinal,
      candidate_digest: candidate.candidateDigest,
      authority_digest: candidate.authorityDigest,
      revision: candidate.revision,
    })),
    resolution_state: artifact.resolutionState,
    selected_candidate_digest: artifact.selectedCandidateDigest,
    unresolved_region_digests: [...artifact.unresolvedRegionDigests],
    comparison_digest: artifact.comparisonDigest,
    revision: artifact.view.revision,
    size_bytes: artifact.view.byteLength,
  })));
  return groupPreviews.sort((left, right) => left.group_id.localeCompare(right.group_id));
}

export function parseEvidenceGroup(bytes: Uint8Array): Readonly<PeerEvidenceGroupPayload> {
  let raw: unknown;
  try { raw = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid'); }
  const row = object(raw, 'speech_evidence_group_payload_invalid');
  const fields = ['schema', 'turn_id', 'revision', 'state', 'source_digest', 'candidates'];
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !(key in row))) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid');
  }
  if (row['schema'] !== EVIDENCE_GROUP_SCHEMA || !Array.isArray(row['candidates'])
    || !row['candidates'].length || row['candidates'].length > 32) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid');
  }
  const revision = positiveInteger(row['revision']);
  const state = String(row['state']);
  if (!['final', 'corrected', 'correction_failed'].includes(state)) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_state_invalid');
  }
  const sourceDigest = digest(row['source_digest']);
  const candidates = row['candidates'].map(value => {
    const candidate = object(value, 'speech_evidence_candidate_invalid');
    const expected = ['revision', 'authority', 'text'];
    if (Object.keys(candidate).some(key => !expected.includes(key)) || expected.some(key => !(key in candidate))) {
      throw new SpeechEvidenceValidationError('speech_evidence_candidate_invalid');
    }
    const candidateRevision = positiveInteger(candidate['revision']);
    if (candidateRevision > revision) {
      throw new SpeechEvidenceValidationError('speech_evidence_candidate_revision_invalid');
    }
    return Object.freeze({
      revision: candidateRevision,
      authority: identifier(candidate['authority']),
      text: boundedText(candidate['text']),
    });
  });
  return Object.freeze({
    turnId: identifier(row['turn_id']),
    revision,
    state: state as 'final' | 'corrected' | 'correction_failed',
    sourceDigest,
    candidates: Object.freeze(candidates),
  });
}

export async function recipientPreAdmissionReason(
  payload: PeerEvidenceGroupPayload,
  snapshot: SpeechEvidenceQuarantineGroupSnapshot,
  expectedGroupId: string,
  preview: SpeechEvidenceGroupPreview | undefined,
): Promise<string | null> {
  if (!preview) return 'speech_evidence_offer_preview_required';
  if (
    preview.sourceGroupDigest !== payload.sourceDigest
    || preview.groupId !== expectedGroupId
  ) return 'speech_evidence_source_group_mismatch';
  if (preview.revision !== payload.revision) return 'speech_evidence_offer_preview_stale';
  if (preview.sizeBytes !== snapshot.receivedBytes) return 'speech_evidence_offer_preview_size_mismatch';
  const actualComparison = await contentFreeComparisonProjection(payload);
  if (
    preview.comparisonDigest !== actualComparison.comparisonDigest
    || preview.resolutionState !== actualComparison.resolutionState
    || preview.selectedCandidateDigest !== actualComparison.selectedCandidateDigest
    || canonicalJson(preview.originalCandidates) !== canonicalJson(actualComparison.originalCandidates)
    || canonicalJson(preview.unresolvedRegionDigests) !== canonicalJson(actualComparison.unresolvedRegionDigests)
  ) return 'speech_evidence_comparison_projection_mismatch';
  if (snapshot.groupId !== expectedGroupId || snapshot.conflictCount !== 0 || !snapshot.complete) {
    return 'speech_evidence_local_digest_binding_failed';
  }
  const candidateBindings = new Set<string>();
  for (const candidate of payload.candidates) {
    const binding = `${candidate.authority}\0${candidate.revision}\0${candidate.text}`;
    if (candidateBindings.has(binding)) return 'speech_evidence_local_candidate_replay';
    candidateBindings.add(binding);
    const text = candidate.text.toLowerCase();
    if (/\bignore\s+(?:all\s+|any\s+)?(?:previous|prior)\s+(?:system\s+)?(?:instructions?|prompts?)\b/.test(text)
      || /\btargeted[\s_-]+trigger\b/.test(text)) {
      return 'speech_evidence_local_prompt_injection_risk';
    }
    if (/\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b/i.test(candidate.text)
      || /\b(?:api[_ -]?key|access[_ -]?token|password|private[_ -]?key)\s*[:=]\s*\S+/i.test(candidate.text)) {
      return 'speech_evidence_local_privacy_risk';
    }
  }
  return null;
}

export async function contentFreeComparisonProjection(value: Readonly<{
  revision: number;
  state: string;
  sourceDigest: string | null;
  originalCandidates?: readonly PeerEvidenceGroupCandidate[];
  candidates?: readonly PeerEvidenceGroupCandidate[];
}>): Promise<Readonly<PeerEvidenceComparisonProjection>> {
  const sourceGroupDigest = value.sourceDigest;
  const rawCandidates = value.originalCandidates ?? value.candidates ?? [];
  if (!sourceGroupDigest || !/^[a-f0-9]{64}$/.test(sourceGroupDigest) || !rawCandidates.length || rawCandidates.length > 32) {
    throw new SpeechEvidenceValidationError('speech_evidence_candidate_projection_invalid');
  }
  const originalCandidates = Object.freeze(await Promise.all(rawCandidates.map(async (candidate, index) => Object.freeze({
    ordinal: index + 1,
    candidateDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-original-candidate.v1',
      source_group_digest: sourceGroupDigest,
      ordinal: index + 1,
      revision: candidate.revision,
      authority: candidate.authority,
      candidate_value: candidate.text,
    }),
    authorityDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-candidate-authority.v1',
      authority: candidate.authority,
    }),
    revision: candidate.revision,
  }))));
  if (new Set(originalCandidates.map(candidate => candidate.candidateDigest)).size !== originalCandidates.length) {
    throw new SpeechEvidenceValidationError('speech_evidence_candidate_projection_invalid');
  }
  const resolutionState = value.state === 'correction_failed' ? 'unresolved' as const : 'resolved' as const;
  const selectedCandidateDigest = resolutionState === 'resolved'
    ? ([...originalCandidates].reverse().find(candidate => candidate.revision === value.revision)
      ?? originalCandidates[originalCandidates.length - 1]).candidateDigest
    : null;
  const unresolvedRegionDigests = resolutionState === 'unresolved'
    ? Object.freeze([await sha256Canonical({
      domain: 'ananta.speech-evidence-unresolved-region.v1',
      source_group_digest: sourceGroupDigest,
      candidate_digests: originalCandidates.map(candidate => candidate.candidateDigest).sort(),
    })])
    : Object.freeze([] as string[]);
  const comparisonDigest = await speechEvidenceComparisonDigest({
    sourceGroupDigest,
    revision: value.revision,
    originalCandidates,
    resolutionState,
    selectedCandidateDigest,
    unresolvedRegionDigests,
  });
  return Object.freeze({
    originalCandidates,
    resolutionState,
    selectedCandidateDigest,
    unresolvedRegionDigests,
    comparisonDigest,
  });
}
