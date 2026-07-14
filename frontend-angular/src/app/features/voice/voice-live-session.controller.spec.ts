import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { VOICE_AUDIO_CAPTURE, VoiceAudioCapturePort } from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import { VoiceLiveSessionController } from './voice-live-session.controller';

describe('VoiceLiveSessionController', () => {
  let chunkHandler: ((chunk: ArrayBuffer) => void) | null;
  let captureErrorHandler: ((error: unknown) => void) | null;
  let capture: VoiceAudioCapturePort;
  const api = {
    createStream: vi.fn(),
    pushStreamChunk: vi.fn(),
    finalizeStream: vi.fn(),
    cancelStream: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    chunkHandler = null;
    captureErrorHandler = null;
    let active = false;
    capture = {
      supported: true,
      get active() { return active; },
      start: vi.fn(async (handler, onError) => {
        chunkHandler = handler;
        captureErrorHandler = onError || null;
        active = true;
      }),
      stop: vi.fn(async () => { active = false; }),
    };
    api.createStream.mockReturnValue(of({
      stream: { session_id: 'voice-stream-a', state: 'created', next_chunk_sequence: 0 },
    }));
    api.pushStreamChunk.mockImplementation((_hub: string, _id: string, sequence: number) => of({
      stream: { session_id: 'voice-stream-a', state: 'active', next_chunk_sequence: sequence + 1 },
      event: { event_type: 'partial', payload: { text: `partial-${sequence}` } },
    }));
    api.finalizeStream.mockReturnValue(of({
      stream: { session_id: 'voice-stream-a', state: 'final', next_chunk_sequence: 2 },
      result: { text: 'final' },
      result_ref: 'voice-result-a',
    }));
    api.cancelStream.mockReturnValue(of({
      stream: { session_id: 'voice-stream-a', state: 'closed', next_chunk_sequence: 0 },
      deleted: true,
    }));

    TestBed.configureTestingModule({
      providers: [
        VoiceLiveSessionController,
        { provide: VoiceApiService, useValue: api },
        { provide: VOICE_AUDIO_CAPTURE, useValue: capture },
      ],
    });
  });

  it('uploads native or browser PCM chunks sequentially before finalization', async () => {
    const events: string[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', {
      profile_id: 'profile-a',
      configuration_session_id: 'session-a',
    }, 'create-key', {
      event: (event) => events.push(String(event?.payload?.text || '')),
    });

    chunkHandler!(new ArrayBuffer(4));
    chunkHandler!(new ArrayBuffer(6));
    const result = await controller.finalize();

    expect(api.createStream).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      profile_id: 'profile-a',
      configuration_session_id: 'session-a',
      media_type: 'audio/pcm;rate=16000;channels=1',
    }), 'create-key');
    expect(api.pushStreamChunk.mock.calls.map((call) => call[2])).toEqual([0, 1]);
    expect(api.pushStreamChunk.mock.calls.map((call) => (call[3] as ArrayBuffer).byteLength)).toEqual([4, 6]);
    expect(events).toEqual(['partial-0', 'partial-1']);
    expect(api.finalizeStream.mock.invocationCallOrder[0]).toBeGreaterThan(
      api.pushStreamChunk.mock.invocationCallOrder.at(-1) || 0,
    );
    expect(result.result.text).toBe('final');
  });

  it('cancels the Hub capability when microphone startup fails', async () => {
    vi.mocked(capture.start).mockRejectedValueOnce(new Error('permission denied'));
    const controller = TestBed.inject(VoiceLiveSessionController);

    await expect(controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key'))
      .rejects.toThrow('permission denied');

    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'voice-stream-a');
  });

  it('owns capture-error cleanup and permits a fresh stream after Hub cancellation', async () => {
    const errors: unknown[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      error: (error) => errors.push(error),
    });

    captureErrorHandler!(new Error('native capture failed'));
    await vi.waitFor(() => expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'voice-stream-a'));

    expect(errors).toHaveLength(1);
    expect(controller.sessionId).toBe('');
    await expect(controller.start('http://hub.test', { profile_id: 'profile-a' }, 'retry-key'))
      .resolves.toEqual(expect.objectContaining({ session_id: 'voice-stream-a' }));
  });
});
