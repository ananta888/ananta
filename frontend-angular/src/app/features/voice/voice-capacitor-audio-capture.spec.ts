import { vi } from 'vitest';

const native = vi.hoisted(() => {
  type Listener = (event: Record<string, unknown>) => void;
  const listeners = new Map<string, Listener>();
  const removedListeners: string[] = [];

  const playback = {
    getStatus: vi.fn(async () => ({ supported: true, prepared: false, capturing: false })),
    prepare: vi.fn(async () => ({ prepared: true })),
    start: vi.fn(async () => ({ started: true })),
    stop: vi.fn(async () => ({ stopped: true })),
    addListener: vi.fn(async (eventName: string, listener: Listener) => {
      listeners.set(eventName, listener);
      return {
        remove: vi.fn(async () => {
          removedListeners.push(eventName);
          if (listeners.get(eventName) === listener) listeners.delete(eventName);
        }),
      };
    }),
  };
  const microphone = {
    getStatus: vi.fn(async () => ({ capturing: false })),
    requestMicrophonePermission: vi.fn(async () => ({ state: 'granted' })),
    start: vi.fn(async () => ({ started: true })),
    stop: vi.fn(async () => ({ stopped: true })),
    addListener: playback.addListener,
  };

  return { listeners, microphone, playback, removedListeners };
});

vi.mock('@capacitor/core', () => ({
  Capacitor: {
    getPlatform: () => 'android',
    isNativePlatform: () => true,
  },
  registerPlugin: (name: string) => name === 'PlaybackAudioCapture'
    ? native.playback
    : native.microphone,
}));

import {
  CapacitorVoiceAudioCaptureAdapter,
  CapacitorVoiceBatchRecordingAdapter,
} from './voice-audio-capture';

function queueFinalPcmChunk(): void {
  globalThis.setTimeout(() => native.listeners.get('voicePcmChunk')?.({
    sequence: 0,
    dataBase64: 'AQI=',
    byteLength: 2,
    capturedBytes: 2,
    capturedMilliseconds: 0,
    sampleRate: 16_000,
    channels: 1,
    encoding: 'pcm_s16le',
  }), 0);
}

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error('blob_read_failed'));
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.readAsArrayBuffer(blob);
  });
}

describe('Capacitor playback-audio stop drain', () => {
  beforeEach(() => {
    native.listeners.clear();
    native.removedListeners.length = 0;
    native.playback.getStatus.mockReset();
    native.playback.getStatus.mockResolvedValue({ supported: true, prepared: false, capturing: false });
    native.playback.prepare.mockClear();
    native.playback.start.mockClear();
    native.playback.stop.mockReset();
    native.playback.stop.mockImplementation(async () => {
      queueFinalPcmChunk();
      return { stopped: true };
    });
    native.playback.addListener.mockClear();
  });

  it('accepts the final native PCM remainder while an explicit stop is draining', async () => {
    const capture = new CapacitorVoiceAudioCaptureAdapter();
    const chunks: number[][] = [];

    await capture.prepare('system_audio');
    await capture.start((chunk) => chunks.push([...new Uint8Array(chunk)]));
    await capture.stop();

    expect(chunks).toEqual([[1, 2]]);
    expect(native.removedListeners).toEqual(expect.arrayContaining([
      'voicePcmChunk',
      'voiceCaptureError',
      'voiceCaptureStopped',
    ]));
  });

  it('keeps a sub-500-ms batch recording instead of returning an empty WAV', async () => {
    const recording = new CapacitorVoiceBatchRecordingAdapter();

    await recording.start('system_audio');
    const wav = new Uint8Array(await readBlob(await recording.stop()));

    expect([...wav.slice(44)]).toEqual([1, 2]);
  });

  it('coalesces overlapping adapter stops without closing the drain generation early', async () => {
    const capture = new CapacitorVoiceAudioCaptureAdapter();
    const chunks: number[][] = [];
    let resolveNativeStop: (() => void) | undefined;
    native.playback.stop.mockImplementation(() => new Promise((resolve) => {
      resolveNativeStop = () => {
        queueFinalPcmChunk();
        resolve({ stopped: true });
      };
    }));

    await capture.prepare('system_audio');
    await capture.start((chunk) => chunks.push([...new Uint8Array(chunk)]));
    const firstStop = capture.stop();
    const secondStop = capture.stop();

    expect(native.playback.stop).toHaveBeenCalledTimes(1);
    resolveNativeStop?.();
    await Promise.all([firstStop, secondStop]);

    expect(chunks).toEqual([[1, 2]]);
  });

  it('keeps playback capture disabled when the native API reports Android below API 29', async () => {
    native.playback.getStatus.mockResolvedValueOnce({
      supported: false,
      prepared: false,
      capturing: false,
    });
    const capture = new CapacitorVoiceAudioCaptureAdapter();

    await capture.refreshCapabilities();

    expect(capture.supportsSource('microphone')).toBe(true);
    expect(capture.supportsSource('system_audio')).toBe(false);
  });

  it('does not open MediaProjection after stop cancels a pending native capability check', async () => {
    let resolveStatus!: (status: { supported: boolean; prepared: boolean; capturing: boolean }) => void;
    native.playback.getStatus.mockImplementationOnce(() => new Promise((resolve) => {
      resolveStatus = resolve;
    }));
    const capture = new CapacitorVoiceAudioCaptureAdapter();
    const preparing = capture.prepare('system_audio');
    await vi.waitFor(() => expect(native.playback.getStatus).toHaveBeenCalled());

    await capture.stop();
    resolveStatus({ supported: true, prepared: false, capturing: false });

    await expect(preparing).rejects.toThrow('voice.capture.cancelled');
    expect(native.playback.prepare).not.toHaveBeenCalled();
  });
});
