export const SPEECH_EVIDENCE_PROTOCOL_VERSION = 'ananta.speech-evidence-sync.v1';
export const SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION = 'ananta.speech-evidence-sync.v2';
export const SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION = 'ananta.speech-evidence-group-preview.v1';
export const SPEECH_EVIDENCE_MAX_CHUNK_BYTES = 64 * 1024;
export const SPEECH_EVIDENCE_MAX_IN_FLIGHT_BYTES = 1024 * 1024;

export type SpeechEvidenceMessageType =
  | 'inventory'
  | 'diff'
  | 'offer'
  | 'chunk'
  | 'chunk_ack'
  | 'resolution'
  | 'receipt'
  | 'revocation'
  | 'revocation_ack';

export interface SpeechEvidenceMessage {
  protocol_version: typeof SPEECH_EVIDENCE_PROTOCOL_VERSION | typeof SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION;
  message_type: SpeechEvidenceMessageType;
  message_id: string;
  session_id: string;
  pair_id: string;
  sender_id: string;
  audience_id: string;
  epoch: number;
  sequence: number;
  consent_version: number;
  key_id: string;
  issued_at_ms: number;
  expires_at_ms: number;
  payload_digest: string;
  payload: Readonly<Record<string, unknown>>;
  signature_algorithm: 'Ed25519';
  signature_b64: string;
}

export interface SpeechEvidenceGroupPreview {
  readonly previewVersion: typeof SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION;
  readonly groupId: string;
  readonly sourceGroupDigest: string;
  readonly speakerScopeDigest: string;
  readonly qualityBasis: 'decision' | 'policy';
  readonly qualityDigest: string;
  readonly resolutionDigest: string;
  readonly originalCandidates: readonly SpeechEvidenceCandidateProjection[];
  readonly resolutionState: 'resolved' | 'unresolved';
  readonly selectedCandidateDigest: string | null;
  readonly unresolvedRegionDigests: readonly string[];
  readonly comparisonDigest: string;
  readonly revision: number;
  readonly sizeBytes: number;
  readonly value: Readonly<Record<string, unknown>>;
}

export interface SpeechEvidenceCandidateProjection {
  readonly ordinal: number;
  readonly candidateDigest: string;
  readonly authorityDigest: string;
  readonly revision: number;
}

export interface SpeechEvidenceGroupPreviewExpectation {
  readonly speakerScopeDigest: string;
  readonly qualityBasis: 'decision' | 'policy';
  readonly qualityDigest: string;
  readonly currentSourceRevisions: ReadonlyMap<string, number>;
}

export interface SpeechEvidenceVerifyContext {
  sessionId: string;
  pairId: string;
  audienceId: string;
  epoch: number;
  consentVersion: number;
  nowMs: number;
}

export interface SpeechEvidencePublicKeyResolver {
  resolve(message: SpeechEvidenceMessage): Promise<CryptoKey | null>;
}

export class SpeechEvidenceValidationError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
  }
}

const TYPES = new Set<SpeechEvidenceMessageType>([
  'inventory', 'diff', 'offer', 'chunk', 'chunk_ack', 'resolution', 'receipt', 'revocation', 'revocation_ack',
]);
const TOP_LEVEL = new Set([
  'protocol_version', 'message_type', 'message_id', 'session_id', 'pair_id', 'sender_id', 'audience_id',
  'epoch', 'sequence', 'consent_version', 'key_id', 'issued_at_ms', 'expires_at_ms', 'payload_digest', 'payload',
  'signature_algorithm', 'signature_b64',
]);
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const MAX_SAFE = Number.MAX_SAFE_INTEGER;

export class SpeechEvidenceReplayWindow {
  private readonly contexts = new Map<string, { highest: number; seen: Set<number> }>();

  constructor(private readonly width = 256, private readonly maximumContexts = 2048) {
    if (width < 32 || width > 4096 || maximumContexts < 1) throw new Error('speech_replay_policy_invalid');
  }

  check(message: SpeechEvidenceMessage): void {
    const key = this.key(message);
    const entry = this.contexts.get(key);
    if (!entry) return;
    if (message.sequence <= entry.highest - this.width) fail('speech_evidence_sequence_stale');
    if (entry.seen.has(message.sequence)) fail('speech_evidence_replayed');
  }

  commit(message: SpeechEvidenceMessage): void {
    const key = this.key(message);
    const entry = this.contexts.get(key) ?? { highest: 0, seen: new Set<number>() };
    entry.highest = Math.max(entry.highest, message.sequence);
    entry.seen.add(message.sequence);
    for (const sequence of entry.seen) if (sequence <= entry.highest - this.width) entry.seen.delete(sequence);
    this.contexts.delete(key);
    this.contexts.set(key, entry);
    while (this.contexts.size > this.maximumContexts) this.contexts.delete(this.contexts.keys().next().value!);
  }

  advanceEpoch(sessionId: string, pairId: string, minimumEpoch: number): void {
    for (const key of this.contexts.keys()) {
      const [session, pair, , epoch] = key.split('\0');
      if (session === sessionId && pair === pairId && Number(epoch) < minimumEpoch) this.contexts.delete(key);
    }
  }

  private key(message: SpeechEvidenceMessage): string {
    const trafficClass = message.message_type === 'chunk' ? 'evidence_bulk' : 'control';
    return [message.session_id, message.pair_id, message.sender_id, message.epoch, trafficClass].join('\0');
  }
}

export function validateSpeechEvidenceMessage(raw: unknown, nowMs?: number): SpeechEvidenceMessage {
  const value = object(raw, 'speech_evidence_message_invalid');
  closed(value, TOP_LEVEL);
  if (![SPEECH_EVIDENCE_PROTOCOL_VERSION, SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION].includes(String(value['protocol_version']))) {
    fail('speech_evidence_version_unsupported');
  }
  if (!TYPES.has(value['message_type'] as SpeechEvidenceMessageType)) fail('speech_evidence_type_unsupported');
  if (value['signature_algorithm'] !== 'Ed25519') fail('speech_evidence_signature_algorithm_unsupported');
  const message = value as unknown as SpeechEvidenceMessage;
  for (const field of ['message_id', 'session_id', 'pair_id', 'sender_id', 'audience_id', 'key_id'] as const) {
    identifier(message[field], `speech_evidence_${field}_invalid`);
  }
  integer(message.epoch, 1, 2_147_483_647, 'speech_evidence_epoch_invalid');
  integer(message.sequence, 1, MAX_SAFE, 'speech_evidence_sequence_invalid');
  integer(message.consent_version, 1, 2_147_483_647, 'speech_evidence_consent_version_invalid');
  integer(message.issued_at_ms, 1, MAX_SAFE, 'speech_evidence_issued_at_invalid');
  integer(message.expires_at_ms, 1, MAX_SAFE, 'speech_evidence_expiry_invalid');
  digest(message.payload_digest, 'speech_evidence_payload_digest_invalid');
  decodeB64(message.signature_b64, 64, 'speech_evidence_signature_invalid');
  if (message.sender_id === message.audience_id) fail('speech_evidence_reflection_detected');
  if (nowMs !== undefined) {
    if (message.expires_at_ms <= nowMs || message.issued_at_ms > nowMs + 30_000) {
      fail('speech_evidence_expired');
    }
    if (message.expires_at_ms > message.issued_at_ms + 600_000) fail('speech_evidence_ttl_invalid');
  }
  validatePayload(message.message_type, object(message.payload, 'speech_evidence_payload_invalid'), message.protocol_version);
  return Object.freeze({ ...message, payload: Object.freeze({ ...message.payload }) });
}

export async function verifySpeechEvidenceMessage(
  raw: unknown,
  context: SpeechEvidenceVerifyContext,
  keys: SpeechEvidencePublicKeyResolver,
  replay: SpeechEvidenceReplayWindow,
): Promise<SpeechEvidenceMessage> {
  const message = validateSpeechEvidenceMessage(raw);
  if (message.session_id !== context.sessionId || message.pair_id !== context.pairId) fail('speech_evidence_wrong_pair');
  if (message.audience_id !== context.audienceId) fail('speech_evidence_wrong_audience');
  if (message.epoch !== context.epoch) fail('speech_evidence_epoch_stale');
  if (message.consent_version !== context.consentVersion) fail('speech_evidence_consent_stale');
  if (message.expires_at_ms <= context.nowMs || message.issued_at_ms > context.nowMs + 30_000) fail('speech_evidence_expired');
  if (message.expires_at_ms > message.issued_at_ms + 600_000) fail('speech_evidence_ttl_invalid');
  replay.check(message);
  const key = await keys.resolve(message);
  if (!key) fail('speech_evidence_key_unknown');
  const signatureOk = await crypto.subtle.verify(
    'Ed25519', key, arrayBuffer(decodeB64(message.signature_b64, 64, 'speech_evidence_signature_invalid')),
    arrayBuffer(new TextEncoder().encode(canonicalSigningJson(message))),
  );
  if (!signatureOk) fail('speech_evidence_signature_invalid');
  if (await sha256Canonical(message.payload) !== message.payload_digest) fail('speech_evidence_payload_digest_mismatch');
  replay.commit(message);
  return message;
}

export function canonicalSigningJson(message: SpeechEvidenceMessage): string {
  return canonicalJson({
    domain: 'ananta.speech-evidence-signature.v1', protocol_version: message.protocol_version,
    message_type: message.message_type, message_id: message.message_id, session_id: message.session_id,
    pair_id: message.pair_id, sender_id: message.sender_id, audience_id: message.audience_id,
    epoch: message.epoch, sequence: message.sequence, consent_version: message.consent_version,
    key_id: message.key_id, issued_at_ms: message.issued_at_ms, expires_at_ms: message.expires_at_ms,
    payload_digest: message.payload_digest, signature_algorithm: message.signature_algorithm,
  });
}

export function canonicalJson(value: unknown): string {
  if (typeof value === 'number' && !Number.isFinite(value)) fail('speech_evidence_non_finite');
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  const record = object(value, 'speech_evidence_message_invalid');
  return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
}

export async function sha256Canonical(value: unknown): Promise<string> {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function validatePayload(
  type: SpeechEvidenceMessageType,
  payload: Record<string, unknown>,
  protocolVersion: SpeechEvidenceMessage['protocol_version'],
): void {
  const expectedTraffic = type === 'chunk' ? 'evidence_bulk' : 'control';
  if (payload['traffic_class'] !== expectedTraffic) fail('speech_evidence_traffic_class_invalid');
  const validators: Record<SpeechEvidenceMessageType, () => void> = {
    inventory: () => {
      closed(payload, new Set(['traffic_class', 'inventory_id', 'root_digest', 'leaf_count', 'total_bytes', 'scope_digest', 'retention_until_ms', 'cursor_digest']));
      identifier(payload['inventory_id'], 'speech_evidence_inventory_id_invalid');
      for (const name of ['root_digest', 'scope_digest', 'cursor_digest']) digest(payload[name], `speech_evidence_${name}_invalid`);
      integer(payload['leaf_count'], 0, 100_000, 'speech_evidence_leaf_count_invalid');
      integer(payload['total_bytes'], 0, 1_073_741_824, 'speech_evidence_total_bytes_invalid');
    },
    diff: () => {
      closed(payload, new Set(['traffic_class', 'base_root_digest', 'target_root_digest', 'missing_group_ids', 'changed_group_ids', 'cursor_digest', 'complete', 'total_groups']));
      ids(payload['missing_group_ids'], 4096); ids(payload['changed_group_ids'], 4096);
    },
    offer: () => {
      if (protocolVersion !== SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION) fail('speech_evidence_offer_preview_required');
      closed(payload, new Set(['traffic_class', 'offer_id', 'stage', 'inventory_root_digest', 'direction', 'purpose', 'data_classes', 'fields', 'retention_seconds', 'trainer_class', 'group_ids', 'group_previews', 'total_bytes', 'sender_consent_digest', 'recipient_consent_digest', 'scope_digest']));
      if (!['proposal', 'acceptance'].includes(String(payload['stage']))) fail('speech_evidence_offer_stage_invalid');
      ids(payload['data_classes'], 8); ids(payload['fields'], 16);
      const groupIds = ids(payload['group_ids'], 4096);
      const previews = speechEvidenceGroupPreviews(payload);
      if (previews.length !== groupIds.length || previews.some(row => !groupIds.includes(row.groupId))) {
        fail('speech_evidence_offer_preview_groups_mismatch');
      }
      const totalBytes = integer(payload['total_bytes'], 1, 1_073_741_824, 'speech_evidence_total_bytes_invalid');
      if (previews.reduce((total, row) => total + row.sizeBytes, 0) !== totalBytes) {
        fail('speech_evidence_offer_preview_size_mismatch');
      }
    },
    chunk: () => {
      closed(payload, new Set(['traffic_class', 'offer_id', 'group_id', 'chunk_index', 'chunk_count', 'plaintext_bytes', 'plaintext_digest', 'ciphertext_digest', 'nonce_b64', 'ciphertext_b64']));
      const bytes = integer(payload['plaintext_bytes'], 1, SPEECH_EVIDENCE_MAX_CHUNK_BYTES, 'speech_evidence_chunk_oversized');
      const ciphertext = decodeB64(String(payload['ciphertext_b64']), undefined, 'speech_evidence_ciphertext_invalid');
      if (ciphertext.byteLength !== bytes + 16) fail('speech_evidence_chunk_oversized');
      decodeB64(String(payload['nonce_b64']), 12, 'speech_evidence_nonce_invalid');
    },
    chunk_ack: () => {
      closed(payload, new Set(['traffic_class', 'offer_id', 'group_id', 'acknowledged_indices', 'first_missing_index', 'received_bytes', 'complete']));
      const values = integers(payload['acknowledged_indices'], 4096, 0, 4095);
      if (values.some((value, index) => index > 0 && value <= values[index - 1])) fail('speech_evidence_ack_inconsistent');
    },
    resolution: () => {
      closed(payload, new Set(['traffic_class', 'resolution_id', 'policy_version', 'graph_digest', 'candidate_ids', 'accepted_candidate_ids', 'unresolved_region_ids', 'result_digest', 'candidates']));
      ids(payload['candidate_ids'], 32); ids(payload['accepted_candidate_ids'], 32); ids(payload['unresolved_region_ids'], 1024);
      if (!Array.isArray(payload['candidates']) || payload['candidates'].length > 32) fail('speech_evidence_candidates_invalid');
      for (const candidate of payload['candidates']) {
        const row = object(candidate, 'speech_evidence_candidate_invalid');
        if (typeof row['text'] !== 'string' || !row['text'] || row['text'].length > 32768) fail('speech_evidence_text_invalid');
        finite(row['confidence'], 0, 1, 'speech_evidence_confidence_invalid');
      }
    },
    receipt: () => {
      closed(payload, new Set(['traffic_class', 'receipt_id', 'offer_id', 'inventory_root_digest', 'resolution_digest', 'accepted_group_ids', 'rejected_group_ids', 'quarantined_group_ids', 'consent_digest', 'policy_digest', 'result_digest']));
      ids(payload['accepted_group_ids'], 4096); ids(payload['rejected_group_ids'], 4096); ids(payload['quarantined_group_ids'], 4096);
    },
    revocation: () => {
      closed(payload, new Set(['traffic_class', 'revocation_id', 'group_ids', 'scope_digest', 'reason_code', 'revocation_epoch', 'deadline_at_ms', 'requested_action']));
      ids(payload['group_ids'], 4096);
    },
    revocation_ack: () => {
      closed(payload, new Set(['traffic_class', 'revocation_id', 'scope_digest', 'revocation_epoch', 'impact_digest', 'group_results', 'decision']));
      if (!Array.isArray(payload['group_results']) || !payload['group_results'].length || payload['group_results'].length > 4096) fail('speech_evidence_revocation_ack_invalid');
      const seen = new Set<string>();
      for (const raw of payload['group_results']) {
        const row = object(raw, 'speech_evidence_revocation_ack_invalid');
        closed(row, new Set(['group_id', 'state', 'reason_code']));
        const groupId = identifier(row['group_id'], 'speech_evidence_group_id_invalid');
        if (seen.has(groupId) || !['deleted', 'use_stopped', 'not_found', 'unresolved'].includes(String(row['state']))) {
          fail('speech_evidence_revocation_ack_invalid');
        }
        seen.add(groupId);
        identifier(row['reason_code'], 'speech_evidence_reason_invalid');
      }
      if (!['complete', 'partial', 'unresolved'].includes(String(payload['decision']))) {
        fail('speech_evidence_revocation_ack_invalid');
      }
    },
  };
  validators[type]();
}

export function speechEvidenceGroupPreviews(
  payload: Readonly<Record<string, unknown>>,
): readonly SpeechEvidenceGroupPreview[] {
  const raw = payload['group_previews'];
  if (!Array.isArray(raw) || !raw.length || raw.length > 4096) fail('speech_evidence_offer_preview_invalid');
  const seenGroups = new Set<string>();
  const seenSources = new Set<string>();
  const previews = raw.map(value => {
    const row = object(value, 'speech_evidence_offer_preview_invalid');
    closed(row, new Set([
      'preview_version', 'group_id', 'source_group_digest', 'speaker_scope_digest', 'quality_basis',
      'quality_digest', 'resolution_digest', 'original_candidates', 'resolution_state',
      'selected_candidate_digest', 'unresolved_region_digests', 'comparison_digest', 'revision', 'size_bytes',
    ]));
    if (row['preview_version'] !== SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION) {
      fail('speech_evidence_offer_preview_version_invalid');
    }
    const groupId = identifier(row['group_id'], 'speech_evidence_group_id_invalid');
    const sourceGroupDigest = digest(row['source_group_digest'], 'speech_evidence_source_group_digest_invalid');
    const speakerScopeDigest = digest(row['speaker_scope_digest'], 'speech_evidence_speaker_scope_digest_invalid');
    const qualityBasis = String(row['quality_basis']);
    if (qualityBasis !== 'decision' && qualityBasis !== 'policy') fail('speech_evidence_quality_basis_invalid');
    const qualityDigest = digest(row['quality_digest'], 'speech_evidence_quality_digest_invalid');
    const resolutionDigest = digest(row['resolution_digest'], 'speech_evidence_resolution_digest_invalid');
    const originalCandidates = candidateProjections(row['original_candidates']);
    const resolutionState = String(row['resolution_state']);
    if (resolutionState !== 'resolved' && resolutionState !== 'unresolved') {
      fail('speech_evidence_comparison_resolution_invalid');
    }
    const selectedCandidateDigest = row['selected_candidate_digest'] === null
      ? null
      : digest(row['selected_candidate_digest'], 'speech_evidence_selected_candidate_digest_invalid');
    const unresolvedRegionDigests = digestArray(
      row['unresolved_region_digests'], 1024, 'speech_evidence_unresolved_regions_invalid',
    );
    const comparisonDigest = digest(row['comparison_digest'], 'speech_evidence_comparison_digest_invalid');
    if (
      (resolutionState === 'resolved'
        && (!selectedCandidateDigest
          || !originalCandidates.some(value => value.candidateDigest === selectedCandidateDigest)
          || unresolvedRegionDigests.length !== 0))
      || (resolutionState === 'unresolved'
        && (selectedCandidateDigest !== null || unresolvedRegionDigests.length === 0))
    ) fail('speech_evidence_comparison_resolution_invalid');
    const revision = integer(row['revision'], 1, 2_147_483_647, 'speech_evidence_preview_revision_invalid');
    if (originalCandidates.some(candidate => candidate.revision > revision)) {
      fail('speech_evidence_candidate_projection_invalid');
    }
    const sizeBytes = integer(row['size_bytes'], 1, 1_073_741_824, 'speech_evidence_preview_size_invalid');
    if (seenGroups.has(groupId) || seenSources.has(sourceGroupDigest)) fail('speech_evidence_offer_preview_duplicate');
    seenGroups.add(groupId); seenSources.add(sourceGroupDigest);
    return Object.freeze({
      previewVersion: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
      groupId,
      sourceGroupDigest,
      speakerScopeDigest,
      qualityBasis,
      qualityDigest,
      resolutionDigest,
      originalCandidates,
      resolutionState,
      selectedCandidateDigest,
      unresolvedRegionDigests,
      comparisonDigest,
      revision,
      sizeBytes,
      value: Object.freeze({ ...row }),
    } satisfies SpeechEvidenceGroupPreview);
  });
  return Object.freeze(previews);
}

export async function validateSpeechEvidenceGroupPreviewBindings(
  payload: Readonly<Record<string, unknown>>,
  expected: SpeechEvidenceGroupPreviewExpectation,
): Promise<readonly SpeechEvidenceGroupPreview[]> {
  const previews = speechEvidenceGroupPreviews(payload);
  for (const preview of previews) {
    const expectedGroupId = await speechEvidenceGroupId(preview.sourceGroupDigest, preview.revision);
    if (preview.groupId !== expectedGroupId) fail('speech_evidence_source_group_mismatch');
    const expectedResolution = await speechEvidenceResolutionDigest(preview.sourceGroupDigest, preview.revision);
    if (preview.resolutionDigest !== expectedResolution) fail('speech_evidence_resolution_digest_mismatch');
    const expectedComparison = await speechEvidenceComparisonDigest({
      sourceGroupDigest: preview.sourceGroupDigest,
      revision: preview.revision,
      originalCandidates: preview.originalCandidates,
      resolutionState: preview.resolutionState,
      selectedCandidateDigest: preview.selectedCandidateDigest,
      unresolvedRegionDigests: preview.unresolvedRegionDigests,
    });
    if (preview.comparisonDigest !== expectedComparison) fail('speech_evidence_comparison_digest_mismatch');
    if (
      preview.speakerScopeDigest !== expected.speakerScopeDigest
      || preview.qualityBasis !== expected.qualityBasis
      || preview.qualityDigest !== expected.qualityDigest
    ) fail('speech_evidence_offer_preview_scope_denied');
    const currentRevision = expected.currentSourceRevisions.get(preview.sourceGroupDigest);
    if (currentRevision === undefined) fail('speech_evidence_source_group_mismatch');
    if (currentRevision !== preview.revision) {
      fail('speech_evidence_offer_preview_stale');
    }
  }
  return previews;
}

export function speechEvidenceGroupPreviewSetDigest(
  previews: readonly SpeechEvidenceGroupPreview[],
): Promise<string> {
  return sha256Canonical({
    domain: 'ananta.speech-evidence-group-preview-set.v1',
    groups: [...previews]
      .sort((left, right) => left.groupId.localeCompare(right.groupId))
      .map(value => value.value),
  });
}

export function speechEvidenceComparisonDigest(value: Readonly<{
  sourceGroupDigest: string;
  revision: number;
  originalCandidates: readonly SpeechEvidenceCandidateProjection[];
  resolutionState: 'resolved' | 'unresolved';
  selectedCandidateDigest: string | null;
  unresolvedRegionDigests: readonly string[];
}>): Promise<string> {
  digest(value.sourceGroupDigest, 'speech_evidence_source_group_digest_invalid');
  integer(value.revision, 1, 2_147_483_647, 'speech_evidence_preview_revision_invalid');
  return sha256Canonical({
    domain: 'ananta.speech-evidence-comparison-preview.v1',
    source_group_digest: value.sourceGroupDigest,
    revision: value.revision,
    original_candidates: value.originalCandidates.map(candidate => ({
      ordinal: candidate.ordinal,
      candidate_digest: candidate.candidateDigest,
      authority_digest: candidate.authorityDigest,
      revision: candidate.revision,
    })),
    resolution_state: value.resolutionState,
    selected_candidate_digest: value.selectedCandidateDigest,
    unresolved_region_digests: [...value.unresolvedRegionDigests],
  });
}

export async function speechEvidenceGroupId(sourceGroupDigest: string, revision: number): Promise<string> {
  digest(sourceGroupDigest, 'speech_evidence_source_group_digest_invalid');
  integer(revision, 1, 2_147_483_647, 'speech_evidence_preview_revision_invalid');
  const value = await sha256Canonical({
    domain: 'ananta.speech-evidence-source-group.v1', revision, source_group_digest: sourceGroupDigest,
  });
  return `speech-group-${value.slice(0, 40)}`;
}

export function speechEvidenceResolutionDigest(sourceGroupDigest: string, revision: number): Promise<string> {
  digest(sourceGroupDigest, 'speech_evidence_source_group_digest_invalid');
  integer(revision, 1, 2_147_483_647, 'speech_evidence_preview_revision_invalid');
  return sha256Canonical({
    domain: 'ananta.speech-evidence-resolution-scope.v1', revision, source_group_digest: sourceGroupDigest,
  });
}

export function speechEvidenceSpeakerScopeDigest(pairId: string, epoch: number, speakerId: string): Promise<string> {
  identifier(pairId, 'speech_evidence_pair_invalid');
  identifier(speakerId, 'speech_evidence_speaker_invalid');
  integer(epoch, 1, 2_147_483_647, 'speech_evidence_epoch_invalid');
  return sha256Canonical({
    domain: 'ananta.speech-evidence-speaker-scope.v1', epoch, pair_id: pairId, speaker_id: speakerId,
  });
}

export function speechEvidenceQualityPolicyDigest(): Promise<string> {
  return sha256Canonical({
    accepted_source_states: ['corrected', 'correction_failed', 'final'],
    domain: 'ananta.speech-evidence-quality-policy.v1',
    policy: 'final-or-reviewed-transcript',
  });
}

function object(value: unknown, reason: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(reason);
  return value as Record<string, unknown>;
}
function closed(value: Record<string, unknown>, expected: Set<string>): void {
  const keys = Object.keys(value);
  if (keys.some(key => !expected.has(key))) fail('speech_evidence_unknown_field');
  if ([...expected].some(key => !(key in value))) fail('speech_evidence_required_field_missing');
}
function identifier(value: unknown, reason: string): string {
  if (typeof value !== 'string' || !ID.test(value) || value.includes('..') || value.includes('\\')) fail(reason);
  return value;
}
function digest(value: unknown, reason = 'speech_evidence_digest_invalid'): string {
  if (typeof value !== 'string' || !DIGEST.test(value)) fail(reason);
  return value;
}
function integer(value: unknown, low: number, high: number, reason: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < low || (value as number) > high) fail(reason);
  return value as number;
}
function finite(value: unknown, low: number, high: number, reason: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high) fail(reason);
  return value;
}
function candidateProjections(value: unknown): readonly SpeechEvidenceCandidateProjection[] {
  if (!Array.isArray(value) || !value.length || value.length > 32) {
    fail('speech_evidence_candidate_projection_invalid');
  }
  const seen = new Set<string>();
  return Object.freeze(value.map((raw, index) => {
    const row = object(raw, 'speech_evidence_candidate_projection_invalid');
    closed(row, new Set(['ordinal', 'candidate_digest', 'authority_digest', 'revision']));
    const ordinal = integer(row['ordinal'], 1, 32, 'speech_evidence_candidate_projection_invalid');
    if (ordinal !== index + 1) fail('speech_evidence_candidate_projection_invalid');
    const candidateDigest = digest(row['candidate_digest'], 'speech_evidence_candidate_projection_invalid');
    if (seen.has(candidateDigest)) fail('speech_evidence_candidate_projection_invalid');
    seen.add(candidateDigest);
    return Object.freeze({
      ordinal,
      candidateDigest,
      authorityDigest: digest(row['authority_digest'], 'speech_evidence_candidate_projection_invalid'),
      revision: integer(row['revision'], 1, 2_147_483_647, 'speech_evidence_candidate_projection_invalid'),
    });
  }));
}
function digestArray(value: unknown, maximum: number, reason: string): readonly string[] {
  if (!Array.isArray(value) || value.length > maximum) fail(reason);
  const rows = value.map(item => digest(item, reason));
  if (new Set(rows).size !== rows.length || rows.some((item, index) => index > 0 && item <= rows[index - 1])) {
    fail(reason);
  }
  return Object.freeze(rows);
}
function ids(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) fail('speech_evidence_groups_invalid');
  const rows = value.map(item => identifier(item, 'speech_evidence_groups_invalid'));
  if (new Set(rows).size !== rows.length) fail('speech_evidence_groups_invalid');
  return rows;
}
function integers(value: unknown, maximum: number, low: number, high: number): number[] {
  if (!Array.isArray(value) || value.length > maximum) fail('speech_evidence_integer_array_invalid');
  return value.map(item => integer(item, low, high, 'speech_evidence_integer_array_invalid'));
}
function decodeB64(value: string, exactBytes: number | undefined, reason: string): Uint8Array {
  try {
    const binary = atob(value);
    const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
    if (exactBytes !== undefined && bytes.byteLength !== exactBytes) fail(reason);
    return bytes;
  } catch {
    fail(reason);
  }
}
function fail(reason: string): never { throw new SpeechEvidenceValidationError(reason); }
function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
