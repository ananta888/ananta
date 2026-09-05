import {
  MediaKeyRotationCoordinator,
  MediaKeyRotationPort,
  MediaRotationScheduler,
  RotatingMediaKeyLease,
} from './media-key-rotation-coordinator';

describe('MediaKeyRotationCoordinator', () => {
  it('activates video, retires the old sender and requests a keyframe', async () => {
    const port = new FakePort();
    const scheduler = new FakeScheduler();
    const previous = lease(1);
    const next = lease(2);
    const coordinator = new MediaKeyRotationCoordinator(port, scheduler, 5_000);
    await coordinator.rotate(previous, next, 'refresh', 'video');
    expect(port.activated).toEqual([2]);
    expect(port.senderRetired).toEqual([1]);
    expect(port.keyFrames).toEqual(['publication-1']);
    expect(previous.release).not.toHaveBeenCalled();
    scheduler.run();
    expect(port.receiverRetired).toEqual([1]);
    expect(previous.release).toHaveBeenCalledOnce();
  });

  it('never extends receive grace across revoke', async () => {
    const port = new FakePort();
    const previous = lease(3);
    await new MediaKeyRotationCoordinator(port, new FakeScheduler()).rotate(
      previous, lease(4), 'revoke', 'audio',
    );
    expect(port.receiverRetired).toEqual([3]);
    expect(previous.release).toHaveBeenCalledOnce();
  });

  it('releases a rejected new lease and leaves the previous receiver available', async () => {
    const port = new FakePort();
    port.failActivation = true;
    const previous = lease(5);
    const next = lease(6);
    await expect(new MediaKeyRotationCoordinator(port).rotate(previous, next, 'refresh', 'data'))
      .rejects.toThrow('activation_failed');
    expect(next.release).toHaveBeenCalledOnce();
    expect(previous.release).not.toHaveBeenCalled();
  });
});

class FakePort implements MediaKeyRotationPort {
  readonly activated: number[] = [];
  readonly senderRetired: number[] = [];
  readonly receiverRetired: number[] = [];
  readonly keyFrames: string[] = [];
  failActivation = false;
  async activate(value: RotatingMediaKeyLease): Promise<void> {
    if (this.failActivation) throw new Error('activation_failed');
    this.activated.push(value.keyEpoch);
  }
  retireSender(_publicationId: string, epoch: number): void { this.senderRetired.push(epoch); }
  retireReceiver(_publicationId: string, epoch: number): void { this.receiverRetired.push(epoch); }
  requestKeyFrame(publicationId: string): void { this.keyFrames.push(publicationId); }
}

class FakeScheduler implements MediaRotationScheduler {
  private operation: (() => void) | null = null;
  now(): number { return Date.now(); }
  schedule(operation: () => void): { cancel(): void } {
    this.operation = operation;
    return { cancel: () => { this.operation = null; } };
  }
  run(): void { this.operation?.(); }
}

function lease(keyEpoch: number): RotatingMediaKeyLease & { release: ReturnType<typeof vi.fn> } {
  return {
    publicationId: 'publication-1', keyEpoch, expiresAtMs: Date.now() + 60_000,
    release: vi.fn(),
  };
}
