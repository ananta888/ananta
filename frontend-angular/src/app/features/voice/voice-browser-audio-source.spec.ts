import {
  acquireBrowserAudioStream,
  browserCaptureSourceSupported,
} from './voice-browser-audio-source';

class FakeTrack {
  readonly stop = vi.fn();
  readyState: MediaStreamTrackState = 'live';
  private readonly listeners = new Map<string, Set<() => void>>();

  addEventListener(name: string, handler: () => void): void {
    const handlers = this.listeners.get(name) || new Set<() => void>();
    handlers.add(handler);
    this.listeners.set(name, handlers);
  }

  removeEventListener(name: string, handler: () => void): void {
    this.listeners.get(name)?.delete(handler);
  }

  end(): void {
    this.readyState = 'ended';
    [...(this.listeners.get('ended') || [])].forEach((handler) => handler());
  }
}

class FakeStream {
  constructor(
    private readonly audioTracks: FakeTrack[],
    private readonly videoTracks: FakeTrack[] = [],
  ) {}

  getAudioTracks(): MediaStreamTrack[] {
    return this.audioTracks as unknown as MediaStreamTrack[];
  }

  getTracks(): MediaStreamTrack[] {
    return [...this.audioTracks, ...this.videoTracks] as unknown as MediaStreamTrack[];
  }
}

describe('browser Voice audio source', () => {
  it('reports microphone and system support independently', () => {
    expect(browserCaptureSourceSupported('microphone', {
      getUserMedia: vi.fn(),
    })).toBe(true);
    expect(browserCaptureSourceSupported('system_audio', {
      getUserMedia: vi.fn(),
    })).toBe(false);
    expect(browserCaptureSourceSupported('system_audio', {
      getDisplayMedia: vi.fn(),
    })).toBe(true);
  });

  it('requests display consent but exposes only audio to the recorder pipeline', async () => {
    const audio = new FakeTrack();
    const video = new FakeTrack();
    const display = new FakeStream([audio], [video]);
    const audioOnly = new FakeStream([audio]);
    const getDisplayMedia = vi.fn(async () => display as unknown as MediaStream);
    const streamFactory = vi.fn(() => audioOnly as unknown as MediaStream);

    const lease = await acquireBrowserAudioStream(
      'system_audio',
      { getDisplayMedia } as unknown as MediaDevices,
      streamFactory,
    );

    expect(getDisplayMedia).toHaveBeenCalledWith(expect.objectContaining({
      video: true,
      audio: true,
      systemAudio: 'include',
    }));
    expect(streamFactory).toHaveBeenCalledWith([audio]);
    expect(lease.audioStream.getTracks()).toEqual([audio]);
    lease.release();
    lease.release();
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('rejects display sharing without audio and releases every selected track', async () => {
    const video = new FakeTrack();
    const display = new FakeStream([], [video]);

    await expect(acquireBrowserAudioStream(
      'system_audio',
      { getDisplayMedia: vi.fn(async () => display as unknown as MediaStream) } as unknown as MediaDevices,
    )).rejects.toThrow('voice.capture.system_audio_not_shared');

    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('reports browser share revocation once and removes the handler on release', async () => {
    const audio = new FakeTrack();
    const display = new FakeStream([audio]);
    const lease = await acquireBrowserAudioStream(
      'system_audio',
      { getDisplayMedia: vi.fn(async () => display as unknown as MediaStream) } as unknown as MediaDevices,
      () => new FakeStream([audio]) as unknown as MediaStream,
    );
    const ended = vi.fn();
    lease.onEnded(ended);

    audio.end();
    audio.end();
    expect(lease.ended).toBe(true);
    expect(ended).toHaveBeenCalledTimes(1);
    lease.release();
    audio.end();
    expect(ended).toHaveBeenCalledTimes(1);
  });

  it('ends the lease when the owner display video track is revoked', async () => {
    const audio = new FakeTrack();
    const video = new FakeTrack();
    const display = new FakeStream([audio], [video]);
    const lease = await acquireBrowserAudioStream(
      'system_audio',
      { getDisplayMedia: vi.fn(async () => display as unknown as MediaStream) } as unknown as MediaDevices,
      () => new FakeStream([audio]) as unknown as MediaStream,
    );
    const ended = vi.fn();
    lease.onEnded(ended);

    video.end();

    expect(lease.ended).toBe(true);
    expect(ended).toHaveBeenCalledTimes(1);
    lease.release();
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('immediately reports a share that ended before a consumer registered', async () => {
    const audio = new FakeTrack();
    const display = new FakeStream([audio]);
    const lease = await acquireBrowserAudioStream(
      'system_audio',
      { getDisplayMedia: vi.fn(async () => display as unknown as MediaStream) } as unknown as MediaDevices,
      () => new FakeStream([audio]) as unknown as MediaStream,
    );
    audio.end();
    const ended = vi.fn();

    lease.onEnded(ended);

    expect(ended).toHaveBeenCalledTimes(1);
    lease.release();
  });
});
