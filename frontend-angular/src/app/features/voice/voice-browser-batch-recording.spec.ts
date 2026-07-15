import {
  BrowserVoiceAudioCaptureAdapter,
  BrowserVoiceBatchRecordingAdapter,
} from './voice-audio-capture';

class FakeTrack {
  readonly stop = vi.fn();
  private readonly listeners = new Map<string, Set<() => void>>();

  constructor(readonly kind: 'audio' | 'video') {}

  addEventListener(name: string, handler: () => void): void {
    const handlers = this.listeners.get(name) || new Set<() => void>();
    handlers.add(handler);
    this.listeners.set(name, handlers);
  }

  removeEventListener(name: string, handler: () => void): void {
    this.listeners.get(name)?.delete(handler);
  }

  end(): void {
    [...(this.listeners.get('ended') || [])].forEach((handler) => handler());
  }
}

class FakeStream {
  constructor(private readonly tracks: FakeTrack[]) {}

  getAudioTracks(): MediaStreamTrack[] {
    return this.tracks.filter((track) => track.kind === 'audio') as unknown as MediaStreamTrack[];
  }

  getTracks(): MediaStreamTrack[] {
    return [...this.tracks] as unknown as MediaStreamTrack[];
  }
}

interface RecorderBehavior {
  constructorError?: Error;
  startError?: Error;
}

describe('BrowserVoiceBatchRecordingAdapter system audio', () => {
  let originalMediaDevices: PropertyDescriptor | undefined;
  let behavior: RecorderBehavior;
  let recorders: FakeMediaRecorder[];
  let derivedStreams: FakeStream[];

  class FakeMediaRecorder {
    static readonly isTypeSupported = vi.fn(() => true);

    state: RecordingState = 'inactive';
    readonly mimeType: string;
    ondataavailable: ((event: BlobEvent) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    onstop: (() => void) | null = null;
    readonly startCall = vi.fn();
    readonly stopCall = vi.fn();

    constructor(
      readonly stream: MediaStream,
      options?: MediaRecorderOptions,
    ) {
      if (behavior.constructorError) throw behavior.constructorError;
      this.mimeType = options?.mimeType || 'audio/webm';
      recorders.push(this);
    }

    start(timeslice?: number): void {
      this.startCall(timeslice);
      if (behavior.startError) throw behavior.startError;
      this.state = 'recording';
    }

    stop(): void {
      this.stopCall();
      this.state = 'inactive';
      this.ondataavailable?.({
        data: new Blob(['system audio'], { type: this.mimeType }),
      } as BlobEvent);
      this.onstop?.();
    }
  }

  beforeEach(() => {
    behavior = {};
    recorders = [];
    derivedStreams = [];
    originalMediaDevices = Object.getOwnPropertyDescriptor(globalThis.navigator, 'mediaDevices');
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder as unknown as typeof MediaRecorder);
    vi.stubGlobal('MediaStream', class {
      constructor(tracks: MediaStreamTrack[] = []) {
        const stream = new FakeStream(tracks as unknown as FakeTrack[]);
        derivedStreams.push(stream);
        return stream;
      }
    } as unknown as typeof MediaStream);
  });

  afterEach(() => {
    if (originalMediaDevices) {
      Object.defineProperty(globalThis.navigator, 'mediaDevices', originalMediaDevices);
    } else {
      delete (globalThis.navigator as Navigator & { mediaDevices?: MediaDevices }).mediaDevices;
    }
    vi.unstubAllGlobals();
  });

  it('passes only the derived audio stream to MediaRecorder', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    const displayStream = new FakeStream([audio, video]);
    const getDisplayMedia = vi.fn(async () => displayStream as unknown as MediaStream);
    installMediaDevices(getDisplayMedia);
    const adapter = new BrowserVoiceBatchRecordingAdapter();

    await adapter.start('system_audio');

    expect(getDisplayMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: true,
      video: true,
      systemAudio: 'include',
    }));
    expect(derivedStreams).toHaveLength(1);
    expect(recorders).toHaveLength(1);
    expect(recorders[0].stream).toBe(derivedStreams[0]);
    expect(recorders[0].stream.getTracks()).toEqual([audio]);
    expect(recorders[0].stream.getTracks()).not.toContain(video);

    await adapter.cancel();
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('stops and finalizes when the shared system-audio track ends', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    installMediaDevices(vi.fn(async () => new FakeStream([audio, video]) as unknown as MediaStream));
    const adapter = new BrowserVoiceBatchRecordingAdapter();
    const ended = vi.fn();
    await adapter.start('system_audio', { ended });

    audio.end();
    const result = await adapter.stop();

    expect(recorders[0].stopCall).toHaveBeenCalledTimes(1);
    expect(result.type).toBe('audio/webm;codecs=opus');
    expect(result.size).toBeGreaterThan(0);
    expect(adapter.active).toBe(false);
    expect(ended).toHaveBeenCalledWith('source_ended');
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('releases a batch source that resolves after cancellation', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    let resolveDisplay!: (stream: MediaStream) => void;
    installMediaDevices(vi.fn(() => new Promise<MediaStream>((resolve) => {
      resolveDisplay = resolve;
    })));
    const adapter = new BrowserVoiceBatchRecordingAdapter();
    const starting = adapter.start('system_audio');
    await vi.waitFor(() => expect(resolveDisplay).toBeTypeOf('function'));

    await adapter.cancel();
    resolveDisplay(new FakeStream([audio, video]) as unknown as MediaStream);

    await expect(starting).rejects.toThrow('voice.capture.cancelled');
    expect(recorders).toHaveLength(0);
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('releases a live source that resolves after cancellation', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    let resolveDisplay!: (stream: MediaStream) => void;
    installMediaDevices(vi.fn(() => new Promise<MediaStream>((resolve) => {
      resolveDisplay = resolve;
    })));
    vi.stubGlobal('AudioContext', class {});
    const adapter = new BrowserVoiceAudioCaptureAdapter();
    const preparing = adapter.prepare('system_audio');
    await vi.waitFor(() => expect(resolveDisplay).toBeTypeOf('function'));

    await adapter.stop();
    resolveDisplay(new FakeStream([audio, video]) as unknown as MediaStream);

    await expect(preparing).rejects.toThrow('voice.capture.cancelled');
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
  });

  it('releases every selected track when MediaRecorder construction fails', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    installMediaDevices(vi.fn(async () => new FakeStream([audio, video]) as unknown as MediaStream));
    behavior.constructorError = new Error('recorder construction failed');
    const adapter = new BrowserVoiceBatchRecordingAdapter();

    await expect(adapter.start('system_audio')).rejects.toThrow('recorder construction failed');

    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
    expect(adapter.active).toBe(false);
  });

  it('releases every selected track when MediaRecorder.start fails', async () => {
    const audio = new FakeTrack('audio');
    const video = new FakeTrack('video');
    installMediaDevices(vi.fn(async () => new FakeStream([audio, video]) as unknown as MediaStream));
    behavior.startError = new Error('recorder start failed');
    const adapter = new BrowserVoiceBatchRecordingAdapter();

    await expect(adapter.start('system_audio')).rejects.toThrow('recorder start failed');

    expect(recorders[0].startCall).toHaveBeenCalledWith(500);
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(video.stop).toHaveBeenCalledTimes(1);
    expect(adapter.active).toBe(false);
  });

  function installMediaDevices(getDisplayMedia: ReturnType<typeof vi.fn>): void {
    Object.defineProperty(globalThis.navigator, 'mediaDevices', {
      configurable: true,
      value: { getDisplayMedia },
    });
  }
});
