export interface AuthenticatedLayerCiphertext {
  readonly validation: 'media-layer-ciphertext-authenticated-v1';
  readonly ciphertextId: string;
  readonly ciphertextDigest: string;
  readonly publicationId: string;
  readonly senderPeerId: string;
  readonly receiverScope: string;
  readonly keyEpoch: number;
  readonly spatialId: number;
  readonly temporalId: number;
  readonly keyFrame: boolean;
}

export interface AcceptedLayerSelectionLease {
  readonly validation: 'hub-layer-selection-accepted-v1';
  readonly subscriberPeerId: string;
  readonly publicationId: string;
  readonly maximumSpatialId: number;
  readonly maximumTemporalId: number;
  readonly keyEpoch: number;
  readonly expiresAtMs: number;
  readonly transport: 'direct_mesh' | 'livekit_e2ee';
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;

/** Selects only ciphertext whose visible metadata already passed authentication. */
export class AuthenticatedLayerSelection {
  constructor(private readonly clock: () => number = () => Date.now()) {}

  select(
    lease: AcceptedLayerSelectionLease,
    candidates: readonly AuthenticatedLayerCiphertext[],
  ): AuthenticatedLayerCiphertext | null {
    this.validateLease(lease);
    if (candidates.length > 16) throw new Error('media_layer_candidate_budget_exceeded');
    const points = new Set<string>();
    const eligible = candidates.filter(candidate => {
      this.validateCandidate(candidate);
      if (candidate.publicationId !== lease.publicationId
          || candidate.receiverScope !== lease.subscriberPeerId
          || candidate.keyEpoch !== lease.keyEpoch) throw new Error('media_layer_scope_mismatch');
      const point = `${candidate.spatialId}:${candidate.temporalId}`;
      if (points.has(point)) throw new Error('media_layer_point_ambiguous');
      points.add(point);
      return candidate.spatialId <= lease.maximumSpatialId
        && candidate.temporalId <= lease.maximumTemporalId;
    });
    eligible.sort((left, right) => right.spatialId - left.spatialId
      || right.temporalId - left.temporalId || left.ciphertextId.localeCompare(right.ciphertextId));
    return eligible[0] ?? null;
  }

  private validateLease(lease: AcceptedLayerSelectionLease): void {
    if (lease.validation !== 'hub-layer-selection-accepted-v1'
        || !ID_RE.test(lease.subscriberPeerId) || !ID_RE.test(lease.publicationId)
        || !layer(lease.maximumSpatialId) || !layer(lease.maximumTemporalId)
        || !Number.isSafeInteger(lease.keyEpoch) || lease.keyEpoch < 1
        || lease.expiresAtMs <= this.clock()
        || !['direct_mesh', 'livekit_e2ee'].includes(lease.transport)) {
      throw new Error('media_layer_selection_lease_invalid');
    }
  }

  private validateCandidate(candidate: AuthenticatedLayerCiphertext): void {
    if (candidate.validation !== 'media-layer-ciphertext-authenticated-v1'
        || ![candidate.ciphertextId, candidate.publicationId, candidate.senderPeerId, candidate.receiverScope]
          .every(value => ID_RE.test(value))
        || !DIGEST_RE.test(candidate.ciphertextDigest)
        || !Number.isSafeInteger(candidate.keyEpoch) || candidate.keyEpoch < 1
        || !layer(candidate.spatialId) || !layer(candidate.temporalId)) {
      throw new Error('media_layer_ciphertext_unauthenticated');
    }
  }
}

function layer(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0 && value <= 3;
}
