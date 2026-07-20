import { SemanticFrameEncoderService, SemanticEncodeRequest, StandardVisualEncoderPort } from './semantic-frame-encoder.service';
import { SemanticResidualChunkerService } from './semantic-residual-chunker.service';

const D = 'a'.repeat(64);
const request = (patch: Partial<SemanticEncodeRequest> = {}): SemanticEncodeRequest => ({
  frameId: 'frame-1', sessionId: 'session', contractId: 'contract', contractDigest: D,
  leaseId: 'lease', leaseDigest: D, epoch: 1, sequence: 1, kind: 'reference',
  baseReferenceId: null, sceneDigest: D, codec: 'image/webp', source: {},
  estimatedInputBytes: 100, deadlineMs: 2000, expiresAtMs: 3000, ...patch,
});
const port: StandardVisualEncoderPort = { encode: async () => new Uint8Array([1, 2, 3, 4]) };

describe('SemanticFrameEncoderService', () => {
  it('encodes only through documented standard codec port and binds all authority context', async () => {
    const encoder = new SemanticFrameEncoderService(2, 1000, 1000, () => 1000);
    const result = await encoder.encode(request(), port);
    expect(result.status).toBe('encoded');
    if (result.status !== 'encoded') return;
    expect(result.frame).toMatchObject({
      contract_id: 'contract', lease_id: 'lease', epoch: 1, sequence: 1,
      scene_digest: D, algorithm: { name: 'standard-web-codec', codec: 'image/webp' }, total_bytes: 4,
    });
    const chunks = await new SemanticResidualChunkerService().chunk(result.frame, result.bytes, 2);
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toMatchObject({ frame_digest: result.frame.encoded_digest, total_bytes: 4, total_chunks: 2 });
  });

  it('never emits a delta without a known reference', async () => {
    const encoder = new SemanticFrameEncoderService(2, 1000, 1000, () => 1000);
    await expect(encoder.encode(request({ kind: 'delta', baseReferenceId: 'missing' }), port))
      .resolves.toEqual({ status: 'recovery', reasonCode: 'base_reference_missing' });
    const reference = await encoder.encode(request(), port);
    expect(reference.status).toBe('encoded');
    const delta = await encoder.encode(request({ frameId: 'frame-2', kind: 'delta', baseReferenceId: 'frame-1' }), port);
    expect(delta.status).toBe('encoded');
  });

  it('bounds queues and cancels in-flight work on cancel or scene cut', async () => {
    let release!: () => void;
    const waiting: StandardVisualEncoderPort = {
      encode: (_source, _codec, signal) => new Promise((resolve, reject) => {
        release = () => resolve(new Uint8Array([1]));
        signal.addEventListener('abort', () => reject(new Error('abort')), { once: true });
      }),
    };
    const encoder = new SemanticFrameEncoderService(1, 1000, 1000, () => 1000);
    const first = encoder.encode(request(), waiting);
    await expect(encoder.encode(request({ frameId: 'frame-2' }), port))
      .resolves.toEqual({ status: 'recovery', reasonCode: 'encoder_backpressure' });
    encoder.invalidateForSceneCut();
    release();
    await expect(first).resolves.toMatchObject({ status: 'recovery' });
    expect(encoder.snapshot()).toEqual({ queued: 0, inFlightBytes: 0, references: 0 });
  });

  it('fails a late standard encoder result at its explicit deadline', async () => {
    const encoder = new SemanticFrameEncoderService(2, 1000, 1000, () => 2001);
    await expect(encoder.encode(request({ expiresAtMs: 3000 }), port))
      .resolves.toEqual({ status: 'recovery', reasonCode: 'encoder_deadline_exceeded' });
  });
});
