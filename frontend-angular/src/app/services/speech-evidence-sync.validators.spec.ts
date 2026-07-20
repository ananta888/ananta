import fixture from './fixtures/speech-evidence-protocol.v1.json';
import previewFixture from './fixtures/speech-evidence-offer-preview.v2.json';

import {
  SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
  SPEECH_EVIDENCE_PROTOCOL_VERSION,
  SpeechEvidenceMessage,
  SpeechEvidenceReplayWindow,
  SpeechEvidenceValidationError,
  canonicalJson,
  canonicalSigningJson,
  sha256Canonical,
  speechEvidenceComparisonDigest,
  speechEvidenceGroupId,
  speechEvidenceGroupPreviews,
  speechEvidenceGroupPreviewSetDigest,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceResolutionDigest,
  speechEvidenceSpeakerScopeDigest,
  validateSpeechEvidenceMessage,
  validateSpeechEvidenceGroupPreviewBindings,
  verifySpeechEvidenceMessage,
} from './speech-evidence-sync.validators';

const NOW = 2_000_000;

async function signedInventory(sequence = 1): Promise<{ message: SpeechEvidenceMessage; publicKey: CryptoKey }> {
  const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
  const payload = {
    traffic_class: 'control', inventory_id: 'inventory-test', root_digest: 'a'.repeat(64), leaf_count: 1,
    total_bytes: 16, scope_digest: 'b'.repeat(64), retention_until_ms: 3_000_000, cursor_digest: 'c'.repeat(64),
  };
  const raw = {
    protocol_version: SPEECH_EVIDENCE_PROTOCOL_VERSION, message_type: 'inventory' as const,
    message_id: `message-${sequence}`, session_id: 'session-test', pair_id: 'pair-test', sender_id: 'peer-a',
    audience_id: 'peer-b', epoch: 7, sequence, consent_version: 3, key_id: 'peer-key-test',
    issued_at_ms: NOW - 1_000, expires_at_ms: NOW + 60_000, payload_digest: await sha256Canonical(payload), payload,
    signature_algorithm: 'Ed25519' as const, signature_b64: btoa(String.fromCharCode(...new Uint8Array(64))),
  } satisfies SpeechEvidenceMessage;
  const signature = await crypto.subtle.sign('Ed25519', keys.privateKey, new TextEncoder().encode(canonicalSigningJson(raw)));
  raw.signature_b64 = btoa(String.fromCharCode(...new Uint8Array(signature)));
  return { message: raw, publicKey: keys.publicKey };
}

function context() {
  return { sessionId: 'session-test', pairId: 'pair-test', audienceId: 'peer-b', epoch: 7, consentVersion: 3, nowMs: NOW };
}

describe('speech evidence sync validators', () => {
  it('shares UTF-8 canonical bytes with the Python verifier', async () => {
    expect(canonicalJson(fixture.canonical_utf8.value)).toBe(fixture.canonical_utf8.json);
    await expect(sha256Canonical(fixture.canonical_utf8.value)).resolves.toBe(fixture.canonical_utf8.sha256);
  });

  it('uses the shared Python/TypeScript fixture taxonomy', () => {
    expect(fixture.cases.map(row => row.name)).toEqual([
      'valid', 'stale', 'tampered', 'replayed', 'wrong-peer', 'oversized', 'revoked',
    ]);
    expect(previewFixture.negative_cases.map(row => row.name)).toEqual([
      'missing', 'forged', 'stale', 'wrong-source-group', 'resolution-digest-mismatch', 'scope-expansion',
    ]);
  });

  it('matches the shared Python/TypeScript preview derivation fixture', async () => {
    const preview = previewFixture.valid;
    await expect(speechEvidenceGroupId(preview.source_group_digest, preview.revision))
      .resolves.toBe(preview.group_id);
    await expect(speechEvidenceResolutionDigest(preview.source_group_digest, preview.revision))
      .resolves.toBe(preview.resolution_digest);
    await expect(speechEvidenceSpeakerScopeDigest('pair-test', 7, 'peer-a'))
      .resolves.toBe(preview.speaker_scope_digest);
    await expect(speechEvidenceQualityPolicyDigest()).resolves.toBe(preview.quality_digest);
    const parsed = speechEvidenceGroupPreviews({ group_previews: [preview] });
    await expect(speechEvidenceGroupPreviewSetDigest(parsed)).resolves.toBe(previewFixture.preview_set_digest);
  });

  it('verifies canonical Ed25519 signatures and rejects replay', async () => {
    const { message, publicKey } = await signedInventory();
    const replay = new SpeechEvidenceReplayWindow();
    const resolver = { resolve: async () => publicKey };
    await expect(verifySpeechEvidenceMessage(message, context(), resolver, replay)).resolves.toBeTruthy();
    await expect(verifySpeechEvidenceMessage(message, context(), resolver, replay)).rejects.toMatchObject({
      reasonCode: 'speech_evidence_replayed',
    });
  });

  it('shares sequence space by traffic class instead of message type', async () => {
    const replay = new SpeechEvidenceReplayWindow();
    const inventory = (await signedInventory(9)).message;
    replay.commit(inventory);
    expect(() => replay.check({ ...inventory, message_type: 'offer' })).toThrowError(
      expect.objectContaining({ reasonCode: 'speech_evidence_replayed' }),
    );
    expect(() => replay.check({ ...inventory, message_type: 'chunk' })).not.toThrow();
  });

  it('returns stable stale, tampered, wrong-peer and revoked reason codes', async () => {
    const { message, publicKey } = await signedInventory();
    const cases: Array<[SpeechEvidenceMessage, CryptoKey | null, string]> = [
      [{ ...message, expires_at_ms: NOW - 1 }, publicKey, 'speech_evidence_expired'],
      [{ ...message, audience_id: 'peer-c' }, publicKey, 'speech_evidence_wrong_audience'],
      [message, null, 'speech_evidence_key_unknown'],
      [{ ...message, payload: { ...message.payload, leaf_count: 2 } }, publicKey, 'speech_evidence_payload_digest_mismatch'],
    ];
    for (const [raw, key, reasonCode] of cases) {
      await expect(verifySpeechEvidenceMessage(
        raw, context(), { resolve: async () => key }, new SpeechEvidenceReplayWindow(),
      )).rejects.toMatchObject({ reasonCode });
    }
  });

  it('rejects unknown fields, non-finite values and oversized chunks synchronously', () => {
    expect(() => validateSpeechEvidenceMessage({ protocol_version: SPEECH_EVIDENCE_PROTOCOL_VERSION, unknown: true }))
      .toThrowError(SpeechEvidenceValidationError);
    expect(() => validateSpeechEvidenceMessage({ ...({} as SpeechEvidenceMessage), epoch: Number.NaN }))
      .toThrowError(SpeechEvidenceValidationError);
    expect(fixture.cases.find(row => row.name === 'oversized')?.reason_code).toBe('speech_evidence_chunk_oversized');
  });

  it('requires closed, unique and reason-coded revocation acknowledgement results', async () => {
    const { message } = await signedInventory();
    const payload = {
      traffic_class: 'control', revocation_id: 'revocation-a', scope_digest: 'a'.repeat(64),
      revocation_epoch: 2, impact_digest: 'b'.repeat(64), decision: 'complete',
      group_results: [{ group_id: 'group-a', state: 'deleted', reason_code: 'local_cleanup_complete' }],
    };
    const acknowledgement = { ...message, message_type: 'revocation_ack' as const, payload };

    expect(validateSpeechEvidenceMessage(acknowledgement).message_type).toBe('revocation_ack');
    expect(() => validateSpeechEvidenceMessage({
      ...acknowledgement,
      payload: { ...payload, group_results: [{ group_id: 'group-a', state: 'deleted' }] },
    })).toThrowError(expect.objectContaining({ reasonCode: 'speech_evidence_required_field_missing' }));
    expect(() => validateSpeechEvidenceMessage({
      ...acknowledgement,
      payload: { ...payload, group_results: [...payload.group_results, ...payload.group_results] },
    })).toThrowError(expect.objectContaining({ reasonCode: 'speech_evidence_revocation_ack_invalid' }));
  });

  it('verifies content-free group preview scope and fails closed for stale or mismatched bindings', async () => {
    const source = 'a'.repeat(64);
    const revision = 3;
    const groupId = await speechEvidenceGroupId(source, revision);
    const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest('pair-test', 7, 'peer-a');
    const qualityDigest = await speechEvidenceQualityPolicyDigest();
    const resolutionDigest = await speechEvidenceResolutionDigest(source, revision);
    const candidate = {
      ordinal: 1,
      candidateDigest: 'b'.repeat(64),
      authorityDigest: 'c'.repeat(64),
      revision,
    };
    const comparisonDigest = await speechEvidenceComparisonDigest({
      sourceGroupDigest: source,
      revision,
      originalCandidates: [candidate],
      resolutionState: 'resolved',
      selectedCandidateDigest: candidate.candidateDigest,
      unresolvedRegionDigests: [],
    });
    const preview = {
      preview_version: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
      group_id: groupId,
      source_group_digest: source,
      speaker_scope_digest: speakerScopeDigest,
      quality_basis: 'policy',
      quality_digest: qualityDigest,
      resolution_digest: resolutionDigest,
      original_candidates: [{
        ordinal: candidate.ordinal,
        candidate_digest: candidate.candidateDigest,
        authority_digest: candidate.authorityDigest,
        revision: candidate.revision,
      }],
      resolution_state: 'resolved',
      selected_candidate_digest: candidate.candidateDigest,
      unresolved_region_digests: [],
      comparison_digest: comparisonDigest,
      revision,
      size_bytes: 17,
    };
    const payload = { group_previews: [preview] };
    const expected = {
      speakerScopeDigest,
      qualityBasis: 'policy' as const,
      qualityDigest,
      currentSourceRevisions: new Map([[source, revision]]),
    };

    await expect(validateSpeechEvidenceGroupPreviewBindings(payload, expected)).resolves.toHaveLength(1);
    await expect(validateSpeechEvidenceGroupPreviewBindings({
      group_previews: [{ ...preview, group_id: 'group-wrong' }],
    }, expected)).rejects.toMatchObject({ reasonCode: 'speech_evidence_source_group_mismatch' });
    await expect(validateSpeechEvidenceGroupPreviewBindings({
      group_previews: [{ ...preview, resolution_digest: 'f'.repeat(64) }],
    }, expected)).rejects.toMatchObject({ reasonCode: 'speech_evidence_resolution_digest_mismatch' });
    await expect(validateSpeechEvidenceGroupPreviewBindings({
      group_previews: [{ ...preview, comparison_digest: 'f'.repeat(64) }],
    }, expected)).rejects.toMatchObject({ reasonCode: 'speech_evidence_comparison_digest_mismatch' });
    expect(JSON.stringify(preview)).not.toMatch(/"(?:text|audio|features)"/i);
    await expect(validateSpeechEvidenceGroupPreviewBindings(payload, {
      ...expected, speakerScopeDigest: 'f'.repeat(64),
    })).rejects.toMatchObject({ reasonCode: 'speech_evidence_offer_preview_scope_denied' });
    await expect(validateSpeechEvidenceGroupPreviewBindings(payload, {
      ...expected, currentSourceRevisions: new Map([[source, revision + 1]]),
    })).rejects.toMatchObject({ reasonCode: 'speech_evidence_offer_preview_stale' });
    await expect(validateSpeechEvidenceGroupPreviewBindings(payload, {
      ...expected, currentSourceRevisions: new Map(),
    })).rejects.toMatchObject({ reasonCode: 'speech_evidence_source_group_mismatch' });
    await expect(validateSpeechEvidenceGroupPreviewBindings(payload, {
      ...expected, currentSourceRevisions: new Map([[source, revision - 1]]),
    })).rejects.toMatchObject({ reasonCode: 'speech_evidence_offer_preview_stale' });
  });
});
