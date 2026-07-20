import { SemanticCaptureService } from './semantic-capture.service';
import {
  CaptureResource,
  CaptureStreamHandle,
  MaskedCaptureFrame,
  RawCaptureFrame,
  SemanticCaptureAuthorization,
  SemanticCaptureBackend,
} from './semantic-capture.types';

class Resource implements CaptureResource {
  closes = 0;
  constructor(readonly byteLength: number) {}
  close(): void { this.closes += 1; }
}

class Intermediate extends Resource {
  constructor(readonly kind: 'canvas' | 'bitmap' | 'gpu_buffer') { super(10); }
}

class Backend implements SemanticCaptureBackend {
  readonly raw = Object.assign(new Resource(400), { width: 10, height: 10 }) as RawCaptureFrame & Resource;
  readonly masked = Object.assign(new Resource(400), { width: 10, height: 10, masked: true as const }) as MaskedCaptureFrame & Resource;
  streamCloses = 0;
  maskCalls = 0;
  permission: 'granted' | 'denied' = 'granted';
  measurement = { cpuMs: 1, gpuMs: 0, workingBytes: 400 };
  readonly intermediates = [new Intermediate('canvas'), new Intermediate('bitmap'), new Intermediate('gpu_buffer')];

  async open(): Promise<CaptureStreamHandle> {
    return {
      permission: this.permission,
      nextFrame: async () => this.raw,
      close: () => { this.streamCloses += 1; },
    };
  }
  async maskBeforeProcessing(): Promise<any> {
    this.maskCalls += 1;
    return { frame: this.masked, measurement: this.measurement, intermediates: this.intermediates };
  }
}

const AUTH: SemanticCaptureAuthorization = {
  consentId: 'consent', sessionId: 'session', epoch: 1,
  browserPermission: 'granted', capabilities: ['semantic_visual_capture'], expiresAtMs: 10_000,
};

describe('SemanticCaptureService', () => {
  it('requires browser permission and the active capture capability', async () => {
    const service = new SemanticCaptureService();
    await expect(service.open({ ...AUTH, browserPermission: 'denied' }, new Backend(), undefined, 1000))
      .rejects.toMatchObject({ reasonCode: 'capture_permission_missing' });
    await expect(service.open({ ...AUTH, capabilities: [] }, new Backend(), undefined, 1000))
      .rejects.toMatchObject({ reasonCode: 'capture_capability_missing' });
  });

  it('masks before exposing a frame and deterministically closes raw and masked resources', async () => {
    const backend = new Backend();
    const session = await new SemanticCaptureService().open(AUTH, backend, undefined, 1000);
    const result = await session.dispatch([], async frame => {
      expect(backend.maskCalls).toBe(1);
      expect(backend.raw.closes).toBe(1);
      expect(frame.masked).toBe(true);
      return 'safe';
    }, 1100);
    expect(result).toBe('safe');
    expect(backend.masked.closes).toBe(1);
    expect(backend.intermediates.map(resource => resource.closes)).toEqual([1, 1, 1]);
    session.destroy();
    expect(backend.streamCloses).toBe(1);
  });

  it('closes every resource on consumer error, budget failure, revoke, track switch and destroy', async () => {
    const backend = new Backend();
    const session = await new SemanticCaptureService().open(AUTH, backend, undefined, 1000);
    await expect(session.dispatch([], async () => { throw new Error('consumer'); }, 1100)).rejects.toThrow('consumer');
    expect(backend.raw.closes).toBe(1);
    expect(backend.masked.closes).toBe(1);
    expect(backend.intermediates.map(resource => resource.closes)).toEqual([1, 1, 1]);
    const replacement = new Backend();
    await session.replaceTrack(replacement, 1200);
    expect(backend.streamCloses).toBe(1);
    session.revoke();
    expect(replacement.streamCloses).toBe(1);
    await expect(session.dispatch([], async () => undefined, 1300)).rejects.toMatchObject({ reasonCode: 'capture_revoked' });
    session.destroy();
  });

  it('stops on resource overrun before downstream feature/model access', async () => {
    const backend = new Backend();
    backend.measurement = { cpuMs: 1000, gpuMs: 0, workingBytes: 400 };
    const session = await new SemanticCaptureService().open(AUTH, backend, undefined, 1000);
    let consumed = false;
    await expect(session.dispatch([], async () => { consumed = true; }, 1100))
      .rejects.toMatchObject({ reasonCode: 'capture_budget_exceeded' });
    expect(consumed).toBe(false);
    expect(backend.raw.closes).toBe(1);
    expect(backend.masked.closes).toBe(1);
  });
});
