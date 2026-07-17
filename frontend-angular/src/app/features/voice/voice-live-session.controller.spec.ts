import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';

import { VOICE_AUDIO_CAPTURE, VoiceAudioCapturePort } from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import { VoiceLiveSessionController } from './voice-live-session.controller';

describe('VoiceLiveSessionController', () => {
  let chunkHandler: ((chunk: ArrayBuffer) => void) | null;
  let captureErrorHandler: ((error: unknown) => void) | null;
  let captureStoppedHandler: ((reason?: string) => void) | null;
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
    captureStoppedHandler = null;
    let active = false;
    let prepared = false;
    capture = {
      supported: true,
      get active() { return active; },
      get prepared() { return prepared; },
      supportsSource: vi.fn(() => true),
      prepare: vi.fn(async () => { prepared = true; }),
      start: vi.fn(async (handler, onError, onStopped) => {
        if (!prepared) throw new Error('not prepared');
        chunkHandler = handler;
        captureErrorHandler = onError || null;
        captureStoppedHandler = onStopped || null;
        active = true;
      }),
      stop: vi.fn(async () => { active = false; prepared = false; }),
    };
    api.createStream.mockReturnValue(of({
      stream: {
        session_id: 'voice-stream-a',
        state: 'created',
        next_chunk_sequence: 0,
        max_audio_seconds: 120,
        max_audio_bytes: 3_840_000,
        accepted_audio_bytes: 0,
      },
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
    expect(capture.prepare).toHaveBeenCalledWith('microphone');
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

  it('prepares a selected system source before creating the Hub stream', async () => {
    const controller = TestBed.inject(VoiceLiveSessionController);

    await controller.prepareCapture('system_audio');
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key');

    expect(capture.prepare).toHaveBeenCalledWith('system_audio');
    expect(vi.mocked(capture.prepare).mock.invocationCallOrder[0]).toBeLessThan(
      api.createStream.mock.invocationCallOrder[0],
    );
  });

  it('releases a prepared source when Hub stream creation fails', async () => {
    api.createStream.mockImplementationOnce(() => {
      throw new Error('hub unavailable');
    });
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.prepareCapture('system_audio');

    await expect(controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key'))
      .rejects.toThrow();

    expect(capture.stop).toHaveBeenCalled();
    expect(capture.prepared).toBe(false);
  });

  it('cancels a Hub stream that is created after the UI already cancelled startup', async () => {
    const createResult = new Subject<any>();
    api.createStream.mockReturnValueOnce(createResult);
    const controller = TestBed.inject(VoiceLiveSessionController);
    const starting = controller.start(
      'http://hub.test',
      { profile_id: 'profile-a' },
      'create-key',
      {},
      'system_audio',
    );
    await vi.waitFor(() => expect(api.createStream).toHaveBeenCalled());

    await controller.cancel();
    createResult.next({
      stream: { session_id: 'late-voice-stream', state: 'created', next_chunk_sequence: 0 },
    });
    createResult.complete();

    await expect(starting).rejects.toThrow('voice.capture.cancelled');
    expect(capture.prepare).toHaveBeenCalledWith('system_audio');
    expect(capture.prepare).not.toHaveBeenCalledWith('microphone');
    expect(capture.start).not.toHaveBeenCalled();
    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'late-voice-stream');
  });

  it('does not report startup success when the capture source ends inside start()', async () => {
    vi.mocked(capture.start).mockImplementationOnce(async (_onChunk, _onError, onStopped) => {
      onStopped?.('source_ended');
    });
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.prepareCapture('system_audio');

    await expect(controller.start(
      'http://hub.test',
      { profile_id: 'profile-a' },
      'create-key',
      {},
      'system_audio',
    )).rejects.toThrow('voice.capture.cancelled');

    await vi.waitFor(() => expect(api.cancelStream)
      .toHaveBeenCalledWith('http://hub.test', 'voice-stream-a'));
    expect(controller.sessionId).toBe('');
  });

  it('finalizes uploaded audio when the native safety limit ends an active capture', async () => {
    const finalized: Array<{ reason?: string; text: string }> = [];
    const finalizing: Array<string | undefined> = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      finalizing: (reason) => finalizing.push(reason),
      finalized: (response, reason) => finalized.push({
        reason,
        text: response.result.text,
      }),
    });
    chunkHandler!(new ArrayBuffer(4));

    captureStoppedHandler?.('safety_limit');
    const overlappingCancel = controller.cancel();

    expect(finalizing).toEqual(['safety_limit']);
    await overlappingCancel;
    await vi.waitFor(() => expect(finalized).toEqual([{ reason: 'safety_limit', text: 'final' }]));
    expect(api.finalizeStream).toHaveBeenCalledWith('http://hub.test', 'voice-stream-a');
    expect(api.cancelStream).not.toHaveBeenCalled();
    expect(capture.stop).toHaveBeenCalledTimes(1);
    expect(controller.sessionId).toBe('');
  });

  it('stops after 240 half-second chunks without uploading sequence 240', async () => {
    const finalized: Array<{ reason?: string; text: string }> = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', {
      profile_id: 'profile-a',
      max_audio_seconds: 120,
    }, 'create-key', {
      finalized: (response, reason) => finalized.push({ reason, text: response.result.text }),
    });

    for (let sequence = 0; sequence <= 240; sequence += 1) {
      chunkHandler!(new ArrayBuffer(16_000));
    }

    await vi.waitFor(() => expect(finalized).toEqual([{ reason: 'safety_limit', text: 'final' }]));
    expect(api.pushStreamChunk).toHaveBeenCalledTimes(240);
    expect(api.pushStreamChunk.mock.calls.map((call) => call[2]))
      .toEqual(Array.from({ length: 240 }, (_value, sequence) => sequence));
    expect(api.pushStreamChunk.mock.calls.every((call) => (
      (call[3] as ArrayBuffer).byteLength === 16_000
    ))).toBe(true);
    expect(api.finalizeStream).toHaveBeenCalledWith('http://hub.test', 'voice-stream-a');
    expect(api.cancelStream).not.toHaveBeenCalled();
    expect(capture.start).toHaveBeenCalledWith(
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
      { maxDurationSeconds: 120 },
    );
  });

  it('finalizes before a chunk that would exceed the seconds-derived fallback budget', async () => {
    api.createStream.mockReturnValueOnce(of({
      stream: {
        session_id: 'voice-stream-a',
        state: 'created',
        next_chunk_sequence: 0,
        max_audio_seconds: 10 / 32_000,
        accepted_audio_bytes: 0,
      },
    }));
    const finalized: string[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      finalized: (_response, reason) => finalized.push(String(reason)),
    });

    chunkHandler!(new ArrayBuffer(6));
    chunkHandler!(new ArrayBuffer(6));

    await vi.waitFor(() => expect(finalized).toEqual(['safety_limit']));
    expect(api.pushStreamChunk).toHaveBeenCalledTimes(1);
    expect((api.pushStreamChunk.mock.calls[0][3] as ArrayBuffer).byteLength).toBe(6);
    expect(api.cancelStream).not.toHaveBeenCalled();
  });

  it('waits for the budget-filling upload and drops a reentrant capture-stop tail', async () => {
    const pendingUpload = new Subject<any>();
    api.createStream.mockReturnValueOnce(of({
      stream: {
        session_id: 'voice-stream-a',
        state: 'created',
        next_chunk_sequence: 0,
        max_audio_seconds: 4 / 32_000,
        max_audio_bytes: 4,
        accepted_audio_bytes: 0,
      },
    }));
    api.pushStreamChunk.mockReturnValueOnce(pendingUpload);
    vi.mocked(capture.stop).mockImplementationOnce(async () => {
      // Browser/native stop may synchronously flush one final PCM remainder.
      chunkHandler?.(new ArrayBuffer(2));
    });
    const finalized: string[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      finalized: (_response, reason) => finalized.push(String(reason)),
    });

    chunkHandler!(new ArrayBuffer(4));
    await vi.waitFor(() => expect(capture.stop).toHaveBeenCalledTimes(1));

    expect(api.pushStreamChunk).toHaveBeenCalledTimes(1);
    expect(api.finalizeStream).not.toHaveBeenCalled();
    pendingUpload.next({
      stream: { session_id: 'voice-stream-a', state: 'active', next_chunk_sequence: 1 },
      event: { event_type: 'partial', payload: { text: 'partial-0' } },
    });
    pendingUpload.complete();

    await vi.waitFor(() => expect(finalized).toEqual(['safety_limit']));
    expect(api.finalizeStream).toHaveBeenCalledTimes(1);
    expect(api.cancelStream).not.toHaveBeenCalled();
  });

  it('includes bytes accepted by an idempotently replayed stream in the local budget', async () => {
    api.createStream.mockReturnValueOnce(of({
      stream: {
        session_id: 'voice-stream-a',
        state: 'active',
        next_chunk_sequence: 1,
        max_audio_seconds: 10 / 32_000,
        max_audio_bytes: 10,
        accepted_audio_bytes: 6,
      },
      idempotent_replay: true,
    }));
    const finalized: string[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      finalized: (_response, reason) => finalized.push(String(reason)),
    });

    chunkHandler!(new ArrayBuffer(4));

    await vi.waitFor(() => expect(finalized).toEqual(['safety_limit']));
    expect(api.pushStreamChunk).toHaveBeenCalledWith(
      'http://hub.test', 'voice-stream-a', 1, expect.any(ArrayBuffer),
    );
    expect(api.cancelStream).not.toHaveBeenCalled();
  });

  it('keeps an unexpected server-side 413 inside the local budget as an error', async () => {
    const serverError = { status: 413, error: { code: 'voice_stream.audio_budget_exceeded' } };
    api.createStream.mockReturnValueOnce(of({
      stream: {
        session_id: 'voice-stream-a',
        state: 'created',
        next_chunk_sequence: 0,
        max_audio_seconds: 1,
        max_audio_bytes: 32_000,
        accepted_audio_bytes: 0,
      },
    }));
    api.pushStreamChunk.mockReturnValueOnce(throwError(() => serverError));
    const errors: unknown[] = [];
    const controller = TestBed.inject(VoiceLiveSessionController);
    await controller.start('http://hub.test', { profile_id: 'profile-a' }, 'create-key', {
      error: (error) => errors.push(error),
    });

    chunkHandler!(new ArrayBuffer(4));

    await vi.waitFor(() => expect(errors).toEqual([serverError]));
    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'voice-stream-a');
    expect(api.finalizeStream).not.toHaveBeenCalled();
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
