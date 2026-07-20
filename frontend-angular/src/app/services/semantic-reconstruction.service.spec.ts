import { SemanticReconstructionService, SemanticRendererPort } from './semantic-reconstruction.service';
import { SemanticFrameEnvelope } from './semantic-frame-encoder.service';
import { parseSemanticScene } from './semantic-scene-model';

const D = 'a'.repeat(64);
const scene = parseSemanticScene({
  schema: 'ananta.semantic-scene.v1', scene_id: 'scene', session_id: 'session', contract_id: 'contract',
  contract_digest: D, epoch: 1, sequence: 1, source_frame_digest: D,
  coordinate_space: { unit: 'normalized', origin: 'top_left', width: 1, height: 1 },
  timebase: { unit: 'milliseconds', captured_at_ms: 1000, duration_ms: 80 },
  provenance: { source: 'heuristic', algorithm: 'grid', version: '1', authoritative: false }, nodes: [],
  security: { classification: 'derived_semantic_metadata', raw_media_included: false },
});
const frame: SemanticFrameEnvelope = {
  schema: 'ananta.semantic-frame.v1', frame_id: 'frame', session_id: 'session', contract_id: 'contract',
  contract_digest: D, lease_id: 'lease', lease_digest: D, epoch: 1, sequence: 1,
  frame_kind: 'reference', base_reference_id: null, scene_digest: D,
  algorithm: { name: 'standard-web-codec', version: '1.0.0', codec: 'image/webp' },
  encoded_digest: D, total_bytes: 2, created_at_ms: 1000, expires_at_ms: 30_000,
};
const renderer: SemanticRendererPort = {
  render: async input => {
    expect(Object.isFrozen(input)).toBe(true);
    expect(input.encodedBlob).toBeInstanceOf(Blob);
    return { renderMs: 2, workingBytes: 2, driftScore: 0, staleRegions: 0, qualityScore: 1 };
  },
};

describe('SemanticReconstructionService', () => {
  it.each([1999, 20001, 2500.5])('rejects invalid delay %s instead of normalizing it', delay => {
    const service = new SemanticReconstructionService();
    expect(service.enqueue({
      receiverId: 'receiver', negotiatedDelayMs: delay, receivedAtMs: 1000, deadlineMs: 20_000,
      scene, frame, encodedBytes: new Uint8Array([1, 2]),
    })).toMatchObject({ status: 'fallback', reasonCode: 'invalid_receiver_delay' });
  });

  it('renders exactly when the receiver-specific delay becomes due and records bounded sequence metrics', async () => {
    const service = new SemanticReconstructionService();
    expect(service.enqueue({
      receiverId: 'receiver', negotiatedDelayMs: 2000, receivedAtMs: 1000, deadlineMs: 20_000,
      scene, frame, encodedBytes: new Uint8Array([1, 2]),
    }).status).toBe('queued');
    expect(await service.drainDue(2999, renderer)).toEqual([]);
    const outcomes = await service.drainDue(3000, renderer);
    expect(outcomes[0]).toMatchObject({ status: 'rendered', metric: { sequence: 1, queuedDelayMs: 2000 } });
    expect(service.snapshot()).toEqual({ queued: 0, reservedBytes: 0, metrics: 1, timers: 0 });
  });

  it('returns machine-readable fallback without scheduling timers or sharing an audio/control queue', async () => {
    const service = new SemanticReconstructionService();
    const delta = { ...frame, frame_kind: 'delta' as const, base_reference_id: 'missing' };
    service.enqueue({ receiverId: 'receiver', negotiatedDelayMs: 2000, receivedAtMs: 1000,
      deadlineMs: 20_000, scene, frame: delta, encodedBytes: new Uint8Array([1, 2]) });
    expect(await service.drainDue(3000, renderer)).toEqual([
      { status: 'fallback', reasonCode: 'missing_reference', sequence: 1 },
    ]);
    expect(service.snapshot().timers).toBe(0);
  });
});
