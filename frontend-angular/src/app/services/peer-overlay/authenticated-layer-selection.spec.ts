import {
  AcceptedLayerSelectionLease,
  AuthenticatedLayerCiphertext,
  AuthenticatedLayerSelection,
} from './authenticated-layer-selection';

describe('AuthenticatedLayerSelection', () => {
  it('selects the highest independently authenticated ciphertext inside one receiver corridor', () => {
    const selector = new AuthenticatedLayerSelection(() => 1_000);
    const selected = selector.select(lease(), [ciphertext('low', 0, 0), ciphertext('high', 1, 1)]);
    expect(selected?.ciphertextId).toBe('high');
    expect(selected?.ciphertextDigest).toBe('b'.repeat(64));
  });

  it('uses metadata only after authentication and rejects cross-receiver leakage', () => {
    const selector = new AuthenticatedLayerSelection(() => 1_000);
    expect(() => selector.select(lease(), [
      ciphertext('forged', 0, 0, { validation: 'forged' as never }),
    ])).toThrow('media_layer_ciphertext_unauthenticated');
    expect(() => selector.select(lease(), [
      ciphertext('other', 0, 0, { receiverScope: 'subscriber-b' }),
    ])).toThrow('media_layer_scope_mismatch');
  });

  it('cannot authorize the peer data DAG as a media transport', () => {
    expect(() => new AuthenticatedLayerSelection(() => 1_000).select(
      lease({ transport: 'peer_data_dag' as never }), [ciphertext('low', 0, 0)],
    )).toThrow('media_layer_selection_lease_invalid');
  });
});

function lease(changes: Partial<AcceptedLayerSelectionLease> = {}): AcceptedLayerSelectionLease {
  return Object.freeze({
    validation: 'hub-layer-selection-accepted-v1', subscriberPeerId: 'subscriber-a',
    publicationId: 'publication-1', maximumSpatialId: 1, maximumTemporalId: 1,
    keyEpoch: 4, expiresAtMs: 2_000, transport: 'livekit_e2ee', ...changes,
  });
}

function ciphertext(
  ciphertextId: string,
  spatialId: number,
  temporalId: number,
  changes: Partial<AuthenticatedLayerCiphertext> = {},
): AuthenticatedLayerCiphertext {
  return Object.freeze({
    validation: 'media-layer-ciphertext-authenticated-v1', ciphertextId,
    ciphertextDigest: (ciphertextId === 'high' ? 'b' : 'a').repeat(64),
    publicationId: 'publication-1', senderPeerId: 'sender-a', receiverScope: 'subscriber-a',
    keyEpoch: 4, spatialId, temporalId, keyFrame: spatialId === 0, ...changes,
  });
}
