import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import {
  VOICE_LONG_RUN_LIVE_PREVIEW,
  VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES,
  VOICE_LONG_RUN_LIVE_PREVIEW_LIMITS,
  VoiceLongRunLivePreviewLimits,
  VoiceLongRunLivePreviewPort,
} from './voice-long-run-live-preview';
import {
  VoiceStreamChunkResponse,
  VoiceStreamCreateRequest,
  VoiceStreamCreateResponse,
} from './voice.models';

describe('VoiceLongRunLivePreviewCoordinator', () => {
  const limits: VoiceLongRunLivePreviewLimits = {
    maxQueuedChunks: 8,
    maxQueuedBytes: 512 * 1024,
  };
  const api = {
    createStream: vi.fn(),
    pushStreamChunk: vi.fn(),
    cancelStream: vi.fn(),
    finalizeStream: vi.fn(),
  };

  beforeEach(() => {
    for (const mock of Object.values(api)) mock.mockReset();
    limits.maxQueuedChunks = 8;
    limits.maxQueuedBytes = 512 * 1024;
    api.createStream.mockImplementation(
      (_hubUrl: string, request: VoiceStreamCreateRequest) => of(createdStream(
        `preview-${request.live_run_segment_sequence}`,
        Number(request.live_run_segment_sequence || 0),
      )),
    );
    api.pushStreamChunk.mockImplementation(
      (_hubUrl: string, sessionId: string, sequence: number) => of(chunkResponse(
        sessionId,
        sequence,
        `partial-${sequence}`,
      )),
    );
    api.cancelStream.mockImplementation((_hubUrl: string, sessionId: string) => of({
      stream: { session_id: sessionId, state: 'closed', next_chunk_sequence: 0 },
      deleted: true,
    }));

    TestBed.configureTestingModule({
      providers: [
        { provide: VoiceApiService, useValue: api },
        { provide: VOICE_LONG_RUN_LIVE_PREVIEW_LIMITS, useValue: limits },
      ],
    });
  });

  it('uses linked, epoch-scoped stream capabilities and rotates only after drain plus DELETE', async () => {
    const firstUpload = new Subject<VoiceStreamChunkResponse>();
    api.pushStreamChunk
      .mockReturnValueOnce(firstUpload)
      .mockImplementationOnce((_hubUrl: string, sessionId: string, sequence: number) => of(
        chunkResponse(sessionId, sequence, 'second partial'),
      ));
    const updates: string[] = [];
    const started: number[] = [];
    const preview = port();
    await preview.start(context({ initialSegmentSequence: 4 }), {
      segmentStarted: (sequence) => started.push(sequence),
      preview: (update) => updates.push(update.text),
    });

    for (let index = 0; index < 6; index += 1) preview.acceptPcm(pcm(16_000, index));
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(1));
    const rotating = preview.endSegment();

    expect(api.pushStreamChunk.mock.calls[0][2]).toBe(0);
    expect((api.pushStreamChunk.mock.calls[0][3] as ArrayBuffer).byteLength)
      .toBe(VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES);
    expect(api.cancelStream).not.toHaveBeenCalled();
    firstUpload.next(chunkResponse('preview-4', 0, 'first partial'));
    firstUpload.complete();
    await rotating;

    expect(api.pushStreamChunk.mock.calls.map((call) => call[2])).toEqual([0, 1]);
    expect(api.pushStreamChunk.mock.calls.map((call) => (call[3] as ArrayBuffer).byteLength))
      .toEqual([64_000, 32_000]);
    expect(updates).toEqual([]);
    expect(started).toEqual([4, 5]);
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'preview-4');
    expect(api.cancelStream.mock.invocationCallOrder[0]).toBeGreaterThan(
      api.pushStreamChunk.mock.invocationCallOrder.at(-1) || 0,
    );
    expect(api.createStream).toHaveBeenCalledTimes(2);
    expect(api.createStream.mock.calls[1][1]).toEqual(expect.objectContaining({
      live_run_id: 'run-a',
      live_run_segment_sequence: 5,
      max_audio_seconds: 60,
    }));
    expect(api.createStream.mock.invocationCallOrder[1]).toBeGreaterThan(
      api.cancelStream.mock.invocationCallOrder[0],
    );
    const firstKey = String(api.createStream.mock.calls[0][2]);
    const secondKey = String(api.createStream.mock.calls[1][2]);
    expect(firstKey.split(':').at(-2)).toBe(secondKey.split(':').at(-2));
    expect(firstKey).toMatch(/:4$/);
    expect(secondKey).toMatch(/:5$/);
    expect(api.finalizeStream).not.toHaveBeenCalled();

    await preview.stop();
    expect(api.cancelStream).toHaveBeenCalledTimes(2);
  });

  it('uses a fresh non-persisted preview epoch for a later start of the same segment', async () => {
    const preview = port();
    await preview.start(context());
    const firstKey = String(api.createStream.mock.calls[0][2]);
    await preview.stop();

    await preview.start(context());
    const secondKey = String(api.createStream.mock.calls[1][2]);

    expect(firstKey).not.toBe(secondKey);
    expect(firstKey).toMatch(/^voice-ui:long-run-preview:run-a:.+:0$/);
    expect(secondKey).toMatch(/^voice-ui:long-run-preview:run-a:.+:0$/);
    await preview.stop();
  });

  it('flushes a short PCM remainder on stop, ignores its stale partial, and only deletes', async () => {
    const upload = new Subject<VoiceStreamChunkResponse>();
    api.pushStreamChunk.mockReturnValueOnce(upload);
    const updates: string[] = [];
    const preview = port();
    await preview.start(context(), { preview: (update) => updates.push(update.text) });
    preview.acceptPcm(pcm(16_000));
    expect(api.pushStreamChunk).not.toHaveBeenCalled();

    const stopping = preview.stop();
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(1));
    expect((api.pushStreamChunk.mock.calls[0][3] as ArrayBuffer).byteLength).toBe(16_000);
    expect(api.cancelStream).not.toHaveBeenCalled();
    upload.next(chunkResponse('preview-0', 0, 'too late'));
    upload.complete();
    await stopping;

    expect(updates).toEqual([]);
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    expect(api.cancelStream.mock.invocationCallOrder[0]).toBeGreaterThan(
      api.pushStreamChunk.mock.invocationCallOrder[0],
    );
    expect(api.finalizeStream).not.toHaveBeenCalled();
  });

  it('never exceeds the authoritative max_audio_bytes capability', async () => {
    api.createStream.mockImplementation(
      (_hubUrl: string, request: VoiceStreamCreateRequest) => of(createdStream(
        `preview-${request.live_run_segment_sequence}`,
        Number(request.live_run_segment_sequence || 0),
        70_000,
      )),
    );
    const preview = port();
    await preview.start(context());

    preview.acceptPcm(pcm(64_000));
    preview.acceptPcm(pcm(16_000));
    preview.acceptPcm(pcm(16_000));
    await preview.endSegment();

    const uploadedBytes = api.pushStreamChunk.mock.calls
      .map((call) => (call[3] as ArrayBuffer).byteLength);
    expect(uploadedBytes).toEqual([64_000, 6_000]);
    expect(uploadedBytes.reduce((total, value) => total + value, 0)).toBe(70_000);
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    await preview.stop();
  });

  it('carries a capture-chunk suffix across an exact segment capability boundary', async () => {
    const preview = port();
    await preview.start(context({ segmentDurationSeconds: 2 }));

    preview.acceptPcm(pcm(70_000));
    await preview.endSegment();
    await preview.stop();

    expect(api.pushStreamChunk.mock.calls.map((call) => call[1])).toEqual([
      'preview-0',
      'preview-1',
    ]);
    expect(api.pushStreamChunk.mock.calls.map((call) => (call[3] as ArrayBuffer).byteLength))
      .toEqual([64_000, 6_000]);
    expect(api.cancelStream.mock.calls.map((call) => call[1])).toEqual([
      'preview-0',
      'preview-1',
    ]);
  });

  it('bounds queued upload batches and disables preview fail-open for the run', async () => {
    limits.maxQueuedChunks = 1;
    limits.maxQueuedBytes = VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES;
    const upload = new Subject<VoiceStreamChunkResponse>();
    api.pushStreamChunk.mockReturnValueOnce(upload);
    const errors: unknown[] = [];
    const preview = port();
    await preview.start(context(), { error: (error) => errors.push(error) });

    preview.acceptPcm(pcm(64_000));
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(1));
    preview.acceptPcm(pcm(64_000));

    expect(preview.active).toBe(false);
    expect(preview.disabled).toBe(true);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toEqual(expect.objectContaining({
      message: 'voice.long_run.live_preview_queue_exhausted',
    }));
    upload.next(chunkResponse('preview-0', 0, 'stale after failure'));
    upload.complete();
    await vi.waitFor(() => expect(api.cancelStream).toHaveBeenCalledTimes(1));
    expect(api.pushStreamChunk).toHaveBeenCalledTimes(1);
    expect(api.finalizeStream).not.toHaveBeenCalled();
    await preview.stop();
  });

  it('ignores empty and regressing partial revisions', async () => {
    api.pushStreamChunk
      .mockReturnValueOnce(of(chunkResponse('preview-0', 0, '', 1)))
      .mockReturnValueOnce(of(chunkResponse('preview-0', 1, 'fresh', 3)))
      .mockReturnValueOnce(of(chunkResponse('preview-0', 2, 'stale', 2)))
      .mockReturnValueOnce(of(chunkResponse('preview-0', 3, 'fresh expanded', 3)));
    const updates: string[] = [];
    const preview = port();
    await preview.start(context(), { preview: (update) => updates.push(update.text) });

    preview.acceptPcm(pcm(64_000));
    preview.acceptPcm(pcm(64_000));
    preview.acceptPcm(pcm(64_000));
    preview.acceptPcm(pcm(64_000));
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(4));
    await vi.waitFor(() => expect(updates).toEqual(['fresh', 'fresh expanded']));
    await preview.endSegment();

    expect(updates).toEqual(['fresh', 'fresh expanded']);
    await preview.stop();
  });

  it('fences partial responses that arrive after endSegment', async () => {
    const lateUpload = new Subject<VoiceStreamChunkResponse>();
    api.pushStreamChunk
      .mockReturnValueOnce(of(chunkResponse('preview-0', 0, 'while accepting')))
      .mockReturnValueOnce(lateUpload);
    const updates: string[] = [];
    const preview = port();
    await preview.start(context(), { preview: (update) => updates.push(update.text) });
    preview.acceptPcm(pcm(64_000));
    await vi.waitFor(() => expect(updates).toEqual(['while accepting']));
    preview.acceptPcm(pcm(16_000));

    const rotating = preview.endSegment();
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(2));
    lateUpload.next(chunkResponse('preview-0', 1, 'late old-window partial'));
    lateUpload.complete();
    await rotating;

    expect(updates).toEqual(['while accepting']);
    await preview.stop();
  });

  it('rotates after Hub cleanup wins the closing PUT and DELETE race', async () => {
    const closingUpload = new Subject<VoiceStreamChunkResponse>();
    const missingCapability = { status: 404, error: { code: 'voice_stream.not_found' } };
    api.pushStreamChunk.mockReturnValueOnce(closingUpload);
    api.cancelStream.mockReturnValueOnce(throwError(() => missingCapability));
    const errors: unknown[] = [];
    const preview = port();
    await preview.start(context(), { error: (error) => errors.push(error) });
    preview.acceptPcm(pcm(64_000));
    await vi.waitFor(() => expect(api.pushStreamChunk).toHaveBeenCalledTimes(1));

    const rotating = preview.endSegment();
    closingUpload.error(missingCapability);
    await rotating;

    expect(errors).toEqual([]);
    expect(preview.active).toBe(true);
    expect(preview.segmentSequence).toBe(1);
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    expect(api.createStream).toHaveBeenCalledTimes(2);
    await preview.stop();
    expect(api.cancelStream).toHaveBeenCalledTimes(2);
  });

  it('reports create failures without rejecting or affecting the long run', async () => {
    const failure = new Error('preview unavailable');
    api.createStream.mockReturnValueOnce(throwError(() => failure));
    const errors: unknown[] = [];
    const preview = port();

    await expect(preview.start(context(), { error: (error) => errors.push(error) }))
      .resolves.toBeUndefined();

    expect(preview.active).toBe(false);
    expect(preview.disabled).toBe(true);
    expect(errors).toEqual([failure]);
    preview.acceptPcm(pcm(64_000));
    expect(api.pushStreamChunk).not.toHaveBeenCalled();
    await preview.stop();
  });

  it('disables and deletes an invalid authoritative byte capability exactly once', async () => {
    api.createStream.mockReturnValueOnce(of(createdStream('invalid-preview', 0, 0)));
    const errors: unknown[] = [];
    const preview = port();

    await preview.start(context(), { error: (error) => errors.push(error) });

    expect(preview.disabled).toBe(true);
    expect(errors[0]).toEqual(expect.objectContaining({
      message: 'voice.long_run.live_preview_invalid_audio_budget',
    }));
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'invalid-preview');
    await preview.stop();
    expect(api.cancelStream).toHaveBeenCalledTimes(1);
  });

  it('deletes exactly once when dispose races a pending create', async () => {
    const pendingCreate = new Subject<VoiceStreamCreateResponse>();
    api.createStream.mockReturnValueOnce(pendingCreate);
    const preview = port();
    const starting = preview.start(context());
    await vi.waitFor(() => expect(api.createStream).toHaveBeenCalledTimes(1));

    const disposing = preview.dispose();
    pendingCreate.next(createdStream('late-preview', 0));
    pendingCreate.complete();
    await Promise.all([starting, disposing]);

    expect(api.cancelStream).toHaveBeenCalledTimes(1);
    expect(api.cancelStream).toHaveBeenCalledWith('http://hub.test', 'late-preview');
    expect(api.pushStreamChunk).not.toHaveBeenCalled();
    expect(api.finalizeStream).not.toHaveBeenCalled();
  });

  function port(): VoiceLongRunLivePreviewPort {
    return TestBed.inject(VOICE_LONG_RUN_LIVE_PREVIEW);
  }
});

function context(overrides: Partial<Parameters<VoiceLongRunLivePreviewPort['start']>[0]> = {}) {
  return {
    hubUrl: 'http://hub.test',
    liveRunId: 'run-a',
    profileId: 'profile-a',
    configurationSessionId: 'configuration-a',
    language: 'de',
    segmentDurationSeconds: 60,
    ...overrides,
  };
}

function createdStream(
  sessionId: string,
  segmentSequence: number,
  maxAudioBytes = 60 * 16_000 * 2,
): VoiceStreamCreateResponse {
  return {
    stream: {
      session_id: sessionId,
      state: 'created',
      next_chunk_sequence: 0,
      max_audio_seconds: 60,
      max_audio_bytes: maxAudioBytes,
      accepted_audio_bytes: 0,
      live_run_segment_sequence: segmentSequence,
    },
  };
}

function chunkResponse(
  sessionId: string,
  sequence: number,
  text: string,
  textRevision = sequence + 1,
): VoiceStreamChunkResponse {
  return {
    stream: {
      session_id: sessionId,
      state: 'active',
      next_chunk_sequence: sequence + 1,
    },
    event: {
      event_type: 'partial',
      payload: { text, text_revision: textRevision },
    },
  };
}

function pcm(byteLength: number, fill = 1): ArrayBuffer {
  const bytes = new Uint8Array(byteLength);
  bytes.fill(fill);
  return bytes.buffer;
}
