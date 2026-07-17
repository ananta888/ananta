import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';

import { VOICE_AUDIO_CAPTURE, VoiceAudioCapturePort } from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import {
  VOICE_LONG_RUN_LIVE_PREVIEW,
  VoiceLongRunLivePreviewObserver,
  VoiceLongRunLivePreviewPort,
} from './voice-long-run-live-preview';
import {
  VOICE_LONG_RUN_RECOVERY,
  VoiceLongRunRecoveryMetadata,
  VoiceLongRunRecoveryPort,
} from './voice-long-run-recovery';
import { VOICE_LONG_RUN_SEGMENTER_FACTORY, VoiceLongRunPcmSegmenter } from './voice-long-run-segmenter';
import {
  VOICE_LONG_RUN_SPOOL,
  VOICE_PROFILE_DELETION_EVENT,
  VOICE_PROFILE_DELETION_STORAGE_PREFIX,
  VoiceLongRunSpoolMetadata,
  VoiceLongRunSpoolPort,
  VoiceLongRunSpoolSegment,
} from './voice-long-run-spool';
import { VoiceLongRunController, appendVoiceLongRunTranscript } from './voice-long-run.controller';
import { VoiceLongRunResponse } from './voice.models';

describe('VoiceLongRunController', () => {
  let chunkHandler: ((chunk: ArrayBuffer) => void) | null;
  let stoppedHandler: ((reason?: string) => void) | null;
  let capture: VoiceAudioCapturePort;
  let spool: MemorySpool;
  let recovery: MemoryRecovery;
  let preview: VoiceLongRunLivePreviewPort;
  const api = {
    acquireLongRunLease: vi.fn(),
    createLongRun: vi.fn(),
    getLongRun: vi.fn(),
    heartbeatLongRun: vi.fn(),
    uploadLongRunSegment: vi.fn(),
    stopLongRun: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    chunkHandler = null;
    stoppedHandler = null;
    spool = new MemorySpool(5);
    recovery = new MemoryRecovery();
    preview = {
      active: false,
      disabled: false,
      segmentSequence: 0,
      start: vi.fn(async () => undefined),
      acceptPcm: vi.fn(),
      endSegment: vi.fn(async () => undefined),
      stop: vi.fn(async () => undefined),
      dispose: vi.fn(async () => undefined),
    };
    let active = false;
    let prepared = false;
    capture = {
      supported: true,
      get active() { return active; },
      get prepared() { return prepared; },
      supportsSource: vi.fn(() => true),
      prepare: vi.fn(async () => { prepared = true; }),
      start: vi.fn(async (onChunk, _onError, onStopped) => {
        chunkHandler = onChunk;
        stoppedHandler = onStopped || null;
        active = true;
      }),
      stop: vi.fn(async () => { active = false; prepared = false; }),
    };
    api.acquireLongRunLease.mockReturnValue(of({
      lease_token: 'lease-a',
      expires_at: Date.now() + 60_000,
      profile_id: 'default',
    }));
    api.createLongRun.mockReturnValue(of(snapshot('run-a')));
    api.getLongRun.mockReturnValue(of(snapshot('run-a')));
    api.heartbeatLongRun.mockReturnValue(of(snapshot('run-a')));
    api.uploadLongRunSegment.mockImplementation(
      (_hub: string, _run: string, sequence: number) => of(uploadSnapshot('run-a', sequence)),
    );
    api.stopLongRun.mockReturnValue(of(snapshot('run-a', 'completed')));

    TestBed.configureTestingModule({
      providers: [
        VoiceLongRunController,
        { provide: VoiceApiService, useValue: api },
        { provide: VOICE_AUDIO_CAPTURE, useValue: capture },
        { provide: VOICE_LONG_RUN_SPOOL, useValue: spool },
        { provide: VOICE_LONG_RUN_RECOVERY, useValue: recovery },
        { provide: VOICE_LONG_RUN_LIVE_PREVIEW, useValue: preview },
        {
          provide: VOICE_LONG_RUN_SEGMENTER_FACTORY,
          useValue: (options: ConstructorParameters<typeof VoiceLongRunPcmSegmenter>[0]) => (
            new VoiceLongRunPcmSegmenter({ ...options, bytesPerSecond: 2 })
          ),
        },
      ],
    });
  });

  afterEach(async () => {
    const controller = TestBed.inject(VoiceLongRunController);
    if (controller.runId) await controller.dispose();
  });

  it('simulates a continuous eight-hour capture as 240 bounded rolling segments', async () => {
    const stopped: string[] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.prepareCapture('system_audio');
    await controller.start('http://hub.test', createRequest(120, 28_800), 'create-key', {
      stopped: (_response, reason) => stopped.push(reason),
    });

    for (let sequence = 0; sequence < 240; sequence += 1) {
      chunkHandler!(new ArrayBuffer(240));
      await settleAsyncWork();
    }

    await vi.waitFor(() => expect(api.stopLongRun).toHaveBeenCalledTimes(1));
    const uploadedSequences = api.uploadLongRunSegment.mock.calls.map((call) => call[2]);
    expect(uploadedSequences).toEqual(Array.from({ length: 240 }, (_value, index) => index));
    expect(capture.start).toHaveBeenCalledTimes(1);
    expect(capture.start).toHaveBeenCalledWith(
      expect.any(Function), expect.any(Function), expect.any(Function),
      { maxDurationSeconds: 28_800 },
    );
    expect(capture.stop).toHaveBeenCalledTimes(1);
    expect(spool.maxObservedSegments).toBeLessThanOrEqual(5);
    expect(spool.records.size).toBe(0);
    expect(stopped).toEqual(['safety_limit']);
  });

  it('keeps capturing segment N+1 while segment N is still processing remotely', async () => {
    const firstUpload = new Subject<any>();
    api.uploadLongRunSegment
      .mockReturnValueOnce(firstUpload)
      .mockImplementation((_hub: string, _run: string, sequence: number) => of(uploadSnapshot('run-a', sequence)));
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

    chunkHandler!(new ArrayBuffer(120));
    await vi.waitFor(() => expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1));
    chunkHandler!(new ArrayBuffer(120));
    await settleAsyncWork();

    expect(capture.active).toBe(true);
    expect(capture.start).toHaveBeenCalledTimes(1);
    expect(spool.records.has('run-a:1')).toBe(true);
    expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);

    firstUpload.next(uploadSnapshot('run-a', 0));
    firstUpload.complete();
    await vi.waitFor(() => expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(2));
    expect(api.uploadLongRunSegment.mock.calls[1][2]).toBe(1);

    await controller.stop();
    expect(capture.stop).toHaveBeenCalledTimes(1);
  });

  it('keeps segment display mode free of preview-stream work', async () => {
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

    chunkHandler!(new ArrayBuffer(120));
    await settleAsyncWork();

    expect(preview.start).not.toHaveBeenCalled();
    expect(preview.acceptPcm).not.toHaveBeenCalled();
    expect(preview.endSegment).not.toHaveBeenCalled();
    expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);
    await controller.stop();
  });

  it('mirrors one capture into a disposable live preview while preserving segment ASR', async () => {
    const updates: string[] = [];
    const startedSequences: number[] = [];
    let previewObserver: VoiceLongRunLivePreviewObserver | undefined;
    vi.mocked(preview.start).mockImplementation(async (_context, observer) => {
      previewObserver = observer;
    });
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      livePreviewStarted: (sequence) => startedSequences.push(sequence),
      livePreview: (update) => updates.push(update.text),
    }, 'live');

    expect(preview.start).toHaveBeenCalledWith(expect.objectContaining({
      hubUrl: 'http://hub.test',
      liveRunId: 'run-a',
      profileId: 'default',
      segmentDurationSeconds: 60,
      initialSegmentSequence: 0,
    }), expect.any(Object));
    previewObserver?.segmentStarted?.(0);
    previewObserver?.preview?.({
      liveRunId: 'run-a',
      segmentSequence: 0,
      streamSessionId: 'preview-a',
      text: 'laufender Rohtext',
      event: { event_type: 'partial', payload: { text: 'laufender Rohtext' } },
    });
    previewObserver?.segmentStarted?.(1);
    chunkHandler!(new ArrayBuffer(120));
    await settleAsyncWork();

    expect(updates).toEqual(['laufender Rohtext']);
    expect(startedSequences).toEqual([0, 1]);
    expect(preview.acceptPcm).toHaveBeenCalledTimes(1);
    expect(preview.endSegment).toHaveBeenCalledTimes(1);
    expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);
    expect(recovery.load()).toEqual(expect.objectContaining({ displayMode: 'live' }));

    await controller.stop();
    expect(preview.stop).toHaveBeenCalled();
  });

  it('degrades a failed live preview without stopping capture or creating a gap', async () => {
    const unavailable: unknown[] = [];
    vi.mocked(preview.start).mockImplementation(async (_context, observer) => {
      observer?.error?.(new Error('preview offline'));
    });
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      livePreviewUnavailable: (error) => unavailable.push(error),
    }, 'live');

    expect(unavailable).toHaveLength(1);
    expect(capture.active).toBe(true);
    chunkHandler!(new ArrayBuffer(120));
    await settleAsyncWork();
    expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);
    expect(api.stopLongRun).not.toHaveBeenCalled();

    await controller.stop();
  });

  it('polls revision deltas without overlap and replaces provisional text in place', async () => {
    vi.useFakeTimers();
    try {
      const correction = new Subject<VoiceLongRunResponse>();
      api.uploadLongRunSegment.mockReturnValueOnce(of(revisionUpload('run-a', 0, {
        revision: 1,
        timeline_revision: 10,
        text_state: 'provisional',
        correction_status: 'pending',
        text: 'roher Texd',
      })));
      api.getLongRun.mockReturnValue(correction);
      const visible: string[] = [];
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
        timelineUpdated: (timeline) => visible.push(timeline.composedTranscript),
      });

      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();
      expect(visible.at(-1)).toBe('roher Texd');

      await vi.advanceTimersByTimeAsync(1_500);
      await settleAsyncWork();
      expect(api.getLongRun).toHaveBeenCalledWith('http://hub.test', 'run-a', {
        afterRevision: 10,
        limit: 100,
      });
      await vi.advanceTimersByTimeAsync(5_000);
      expect(api.getLongRun).toHaveBeenCalledTimes(1);

      correction.next(revisionDelta('run-a', 11, {
        revision: 2,
        timeline_revision: 11,
        text_state: 'final',
        correction_status: 'completed',
        text: 'Korrigierter Text.',
      }));
      correction.complete();
      await settleAsyncWork();

      expect(visible.at(-1)).toBe('Korrigierter Text.');
      await vi.advanceTimersByTimeAsync(10_000);
      expect(api.getLongRun).toHaveBeenCalledTimes(1);
      await controller.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not let a textless heartbeat skip an in-flight correction delta', async () => {
    vi.useFakeTimers();
    try {
      const correction = new Subject<VoiceLongRunResponse>();
      api.uploadLongRunSegment.mockReturnValueOnce(of(revisionUpload('run-a', 0, {
        revision: 1,
        timeline_revision: 10,
        text_state: 'provisional',
        correction_status: 'pending',
        text: 'Sichtbarer Rohtext',
      })));
      api.getLongRun.mockReturnValue(correction);
      api.heartbeatLongRun.mockReturnValue(of({
        ...snapshot('run-a'),
        run: { id: 'run-a', status: 'active', timeline_revision: 11 },
        segments: [{
          sequence: 0,
          status: 'completed',
          revision: 2,
          timeline_revision: 11,
          text_state: 'final',
          correction_status: 'completed',
          text: null,
        }],
      }));
      const visible: string[] = [];
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
        timelineUpdated: (timeline) => visible.push(timeline.composedTranscript),
      });
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();

      await vi.advanceTimersByTimeAsync(15_000);
      await settleAsyncWork();
      expect(visible.at(-1)).toBe('Sichtbarer Rohtext');
      expect(api.getLongRun).toHaveBeenCalledWith('http://hub.test', 'run-a', {
        afterRevision: 10,
        limit: 100,
      });

      correction.next(revisionDelta('run-a', 11, {
        revision: 2,
        timeline_revision: 11,
        text_state: 'final',
        correction_status: 'completed',
        text: 'Korrigierter Text',
      }));
      correction.complete();
      await settleAsyncWork();
      expect(visible.at(-1)).toBe('Korrigierter Text');
      await controller.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('drains every bounded revision page even when the first page resolves the last pending segment', async () => {
    vi.useFakeTimers();
    try {
      api.uploadLongRunSegment.mockReturnValueOnce(of(revisionUpload('run-a', 0, {
        revision: 1,
        timeline_revision: 10,
        text_state: 'provisional',
        correction_status: 'pending',
        text: 'Rohtext',
      })));
      const firstPage = revisionDelta('run-a', 11, {
        revision: 2,
        timeline_revision: 11,
        text_state: 'final',
        correction_status: 'completed',
        text: 'Finaler Text',
      });
      firstPage.page!.has_more = true;
      const lastPage: VoiceLongRunResponse = {
        ...snapshot('run-a'),
        run: { id: 'run-a', status: 'active', timeline_revision: 11 },
        page: {
          after_sequence: -1,
          next_after_sequence: -1,
          after_revision: 11,
          next_after_revision: 11,
          limit: 100,
          has_more: false,
        },
      };
      api.getLongRun.mockReturnValueOnce(of(firstPage)).mockReturnValueOnce(of(lastPage));
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key');
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();

      await vi.advanceTimersByTimeAsync(1_500);
      await settleAsyncWork();
      await vi.advanceTimersByTimeAsync(1);
      await settleAsyncWork();

      expect(api.getLongRun.mock.calls.map((call) => call[2].afterRevision)).toEqual([10, 11]);
      await controller.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops polling after a non-retriable revision error', async () => {
    vi.useFakeTimers();
    try {
      api.uploadLongRunSegment.mockReturnValueOnce(of(revisionUpload('run-a', 0, {
        revision: 1,
        timeline_revision: 1,
        text_state: 'provisional',
        correction_status: 'pending',
        text: 'Rohtext',
      })));
      const forbidden = { status: 403, error: { code: 'auth.forbidden', retriable: false } };
      api.getLongRun.mockReturnValue(throwError(() => forbidden));
      const errors: unknown[] = [];
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
        error: (error) => errors.push(error),
      });
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();

      await vi.advanceTimersByTimeAsync(1_500);
      await settleAsyncWork();
      await vi.advanceTimersByTimeAsync(30_000);

      expect(api.getLongRun).toHaveBeenCalledTimes(1);
      expect(errors).toContain(forbidden);
      await controller.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps recovery while stop waits for corrections and retries the terminal freeze', async () => {
    vi.useFakeTimers();
    try {
      const inFlight = {
        status: 409,
        error: { code: 'voice_live_run.segments_in_flight', retriable: true },
      };
      api.stopLongRun
        .mockReturnValueOnce(throwError(() => inFlight))
        .mockReturnValueOnce(of(snapshot('run-a', 'completed')));
      api.getLongRun.mockReturnValue(of(snapshot('run-a')));
      const error = vi.fn();
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key', { error });

      const stopping = controller.stop();
      await settleAsyncWork();
      expect(api.stopLongRun).toHaveBeenCalledTimes(1);
      expect(controller.recoveryMetadata()?.runId).toBe('run-a');

      await vi.advanceTimersByTimeAsync(2_500);
      await settleAsyncWork();
      await stopping;

      expect(api.stopLongRun).toHaveBeenCalledTimes(2);
      expect(api.getLongRun).toHaveBeenCalledWith('http://hub.test', 'run-a', {
        afterRevision: 0,
        limit: 100,
      });
      expect(controller.recoveryMetadata()).toBeNull();
      expect(error).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('fails closed before requesting capture consent when encrypted storage is unavailable', async () => {
    vi.spyOn(spool, 'initialize').mockRejectedValueOnce(
      new Error('voice.long_run.secure_spool_unavailable'),
    );
    const controller = TestBed.inject(VoiceLongRunController);

    await expect(controller.prepareCapture('system_audio'))
      .rejects.toThrow('voice.long_run.secure_spool_unavailable');

    expect(capture.prepare).not.toHaveBeenCalled();
    expect(api.createLongRun).not.toHaveBeenCalled();
  });

  it('reports bounded-buffer evictions as heartbeat gaps', async () => {
    spool = new MemorySpool(2);
    TestBed.overrideProvider(VOICE_LONG_RUN_SPOOL, { useValue: spool });
    const firstUpload = new Subject<any>();
    api.uploadLongRunSegment
      .mockReturnValueOnce(firstUpload)
      .mockImplementation((_hub: string, _run: string, sequence: number) => of(uploadSnapshot('run-a', sequence)));
    const gaps: number[] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      gap: (sequence) => gaps.push(sequence),
    });

    for (let sequence = 0; sequence < 4; sequence += 1) {
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();
    }

    expect(spool.maxObservedSegments).toBe(2);
    expect(gaps).toContain(1);
    expect(gaps).not.toContain(0);
    stoppedHandler?.('source_ended');
    firstUpload.next(uploadSnapshot('run-a', 0));
    firstUpload.complete();
  });

  it('replaces a transient Hub gap with the healed authoritative snapshot', async () => {
    const upload = new Subject<any>();
    api.uploadLongRunSegment.mockReturnValueOnce(upload);
    api.heartbeatLongRun.mockReturnValueOnce(of({
      ...snapshot('run-a'),
      run: { ...snapshot('run-a').run, version: 2 },
      gaps: [0],
    }));
    const gapSnapshots: number[][] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      gapsUpdated: (sequences) => gapSnapshots.push([...sequences]),
    });
    chunkHandler!(new ArrayBuffer(120));
    await vi.waitFor(() => expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1));

    await (controller as any).sendHeartbeat();
    expect(gapSnapshots.at(-1)).toEqual([0]);

    upload.next({
      ...uploadSnapshot('run-a', 0),
      run: { ...snapshot('run-a').run, version: 3 },
      gaps: [],
      resume: { next_sequence: 1, acknowledged_through_sequence: 0 },
    });
    upload.complete();
    await settleAsyncWork();

    expect(gapSnapshots.at(-1)).toEqual([]);
    await controller.stop();
  });

  it('ignores a delayed lower-version gap projection after upload confirmation', async () => {
    const upload = new Subject<any>();
    const delayedHeartbeat = new Subject<VoiceLongRunResponse>();
    api.uploadLongRunSegment.mockReturnValueOnce(upload);
    api.heartbeatLongRun.mockReturnValueOnce(delayedHeartbeat);
    const gapSnapshots: number[][] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      gapsUpdated: (sequences) => gapSnapshots.push([...sequences]),
    });
    chunkHandler!(new ArrayBuffer(120));
    await vi.waitFor(() => expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1));
    const heartbeat = (controller as any).sendHeartbeat() as Promise<void>;

    upload.next({
      ...uploadSnapshot('run-a', 0),
      run: { ...snapshot('run-a').run, version: 3 },
      gaps: [],
    });
    upload.complete();
    await settleAsyncWork();
    delayedHeartbeat.next({
      ...snapshot('run-a'),
      run: { ...snapshot('run-a').run, version: 2 },
      gaps: [0],
    });
    delayedHeartbeat.complete();
    await heartbeat;

    expect(gapSnapshots.at(-1)).toEqual([]);
    await controller.stop();
  });

  it('removes a local gap when the Hub proves that segment completed', async () => {
    const gapSnapshots: number[][] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      gapsUpdated: (sequences) => gapSnapshots.push([...sequences]),
    });

    (controller as any).reportGap(0);
    expect(gapSnapshots.at(-1)).toEqual([0]);
    (controller as any).publishResponse({
      ...snapshot('run-a'),
      run: { ...snapshot('run-a').run, version: 2, timeline_revision: 1 },
      segments: [{
        sequence: 0,
        status: 'completed',
        revision: 1,
        timeline_revision: 1,
        text_state: 'final_uncorrected',
        correction_status: 'failed',
        text: 'Nachgereichtes Segment',
      }],
      gaps: [],
    });

    expect(gapSnapshots.at(-1)).toEqual([]);
    await controller.stop();
  });

  it('publishes current recovery cursors while capture remains active', async () => {
    const recoveryUpdates: VoiceLongRunRecoveryMetadata[] = [];
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
      recoveryUpdated: (metadata) => recoveryUpdates.push(metadata),
    });

    chunkHandler!(new ArrayBuffer(2));

    expect(recoveryUpdates.at(-1)).toEqual(expect.objectContaining({
      runId: 'run-a',
      nextSequence: 0,
      timelineMilliseconds: 1_000,
    }));
    await controller.stop();
  });

  it('retries a lost create response with the same key and resumes after the Hub cursor', async () => {
    const replay = {
      ...snapshot('run-replayed'),
      idempotent_replay: true,
      segments: [
        { sequence: 0, status: 'completed', started_at_ms: 0, ended_at_ms: 60_000 },
        { sequence: 1, status: 'completed', started_at_ms: 60_000, ended_at_ms: 120_000 },
      ],
      resume: {
        next_sequence: 2,
        acknowledged_through_sequence: 1,
        last_seen_sequence: 1,
      },
    };
    api.createLongRun
      .mockReturnValueOnce(throwError(() => ({ status: 0, message: 'response lost' })))
      .mockReturnValueOnce(of(replay));
    api.acquireLongRunLease
      .mockReturnValueOnce(of({
        lease_token: 'lease-first', expires_at: Date.now() + 60_000, profile_id: 'default',
      }))
      .mockReturnValueOnce(of({
        lease_token: 'lease-retry', expires_at: Date.now() + 60_000, profile_id: 'default',
      }));
    const controller = TestBed.inject(VoiceLongRunController);

    await expect(controller.start('http://hub.test', createRequest(60, 600), 'first-key'))
      .rejects.toBeTruthy();
    const pendingCreate = controller.recoveryMetadata() as any;
    expect(pendingCreate?.lease_token).toBeUndefined();
    expect(pendingCreate?.request?.lease_token).toBeUndefined();
    await controller.prepareCapture('system_audio');
    await controller.start('http://hub.test', createRequest(60, 600), 'different-key');
    chunkHandler!(new ArrayBuffer(120));

    await vi.waitFor(() => expect(api.uploadLongRunSegment).toHaveBeenCalled());
    expect(api.acquireLongRunLease).toHaveBeenCalledTimes(2);
    expect(api.createLongRun.mock.calls.map((call) => call[2])).toEqual(['first-key', 'first-key']);
    expect(api.createLongRun.mock.calls.map((call) => call[1].lease_token))
      .toEqual(['lease-first', 'lease-retry']);
    expect(api.uploadLongRunSegment.mock.calls[0][2]).toBe(2);
    expect(api.uploadLongRunSegment.mock.calls[0][3]).toEqual(expect.objectContaining({
      startedAtMs: 120_000,
      endedAtMs: 180_000,
    }));
    await controller.stop();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('retries identical encrypted-spool bytes with one stable idempotency key', async () => {
    vi.useFakeTimers();
    try {
      api.uploadLongRunSegment
        .mockReturnValueOnce(throwError(() => ({ status: 503, error: { retriable: true } })))
        .mockImplementation((_hub: string, _run: string, sequence: number) => of(uploadSnapshot('run-a', sequence)));
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key');
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();
      expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(1_000);
      await settleAsyncWork();
      expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(2);

      const [first, second] = api.uploadLongRunSegment.mock.calls;
      expect(first[4]).toBe(second[4]);
      vi.useRealTimers();
      const firstBytes = new Uint8Array(await readBlob(first[3].file as Blob));
      const secondBytes = new Uint8Array(await readBlob(second[3].file as Blob));
      expect(firstBytes).toEqual(secondBytes);
      await controller.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('rejects a terminal idempotent create replay without starting capture', async () => {
    api.createLongRun.mockReturnValueOnce(of({
      ...snapshot('terminal-run', 'completed'),
      idempotent_replay: true,
    }));
    const controller = TestBed.inject(VoiceLongRunController);

    await expect(controller.start('http://hub.test', createRequest(60, 600), 'create-key'))
      .rejects.toThrow('voice.long_run.create_replay_terminal');

    expect(capture.start).not.toHaveBeenCalled();
    expect(api.stopLongRun).not.toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('stops capture when stalled encrypted persistence reaches the plaintext cap', async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(spool, 'put').mockImplementation(() => new Promise(() => undefined));
      const gaps: number[] = [];
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key', {
        gap: (sequence) => gaps.push(sequence),
      });

      chunkHandler!(new ArrayBuffer(120));
      chunkHandler!(new ArrayBuffer(120));
      chunkHandler!(new ArrayBuffer(120));
      await settleAsyncWork();

      expect(capture.stop).toHaveBeenCalledTimes(1);
      expect(gaps).toContain(2);

      await vi.advanceTimersByTimeAsync(10_000);
      await settleAsyncWork();
      await vi.advanceTimersByTimeAsync(10_000);
      await settleAsyncWork();
      expect(spool.put).toHaveBeenCalledTimes(2);
      await vi.waitFor(() => expect(api.stopLongRun).toHaveBeenCalledTimes(1));
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops and discards local capture when another tab deletes the profile', async () => {
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

    globalThis.dispatchEvent(new StorageEvent('storage', {
      key: `${VOICE_PROFILE_DELETION_STORAGE_PREFIX}default`,
      newValue: String(Date.now()),
    }));

    await vi.waitFor(() => expect(controller.runId).toBe(''));
    expect(capture.stop).toHaveBeenCalledTimes(1);
    expect(api.stopLongRun).not.toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('also stops immediately for a same-document privacy deletion signal', async () => {
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

    globalThis.dispatchEvent(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId: 'default' },
    }));

    await vi.waitFor(() => expect(controller.runId).toBe(''));
    expect(capture.stop).toHaveBeenCalledTimes(1);
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('preempts a pending capture preparation before lease, create or capture can continue', async () => {
    let resolvePrepare!: () => void;
    vi.mocked(capture.prepare).mockImplementationOnce(() => new Promise<void>((resolve) => {
      resolvePrepare = resolve;
    }));
    const controller = TestBed.inject(VoiceLongRunController);
    const preparing = controller.prepareCapture('system_audio', 'default');
    await vi.waitFor(() => expect(capture.prepare).toHaveBeenCalledTimes(1));

    globalThis.dispatchEvent(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId: 'default' },
    }));
    resolvePrepare();

    await expect(preparing).rejects.toThrow('voice.capture.cancelled');
    expect(api.acquireLongRunLease).not.toHaveBeenCalled();
    expect(api.createLongRun).not.toHaveBeenCalled();
    expect(capture.start).not.toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('preempts a pending profile lease before create or capture can continue', async () => {
    const leaseResponse = new Subject<any>();
    api.acquireLongRunLease.mockReturnValueOnce(leaseResponse);
    const controller = TestBed.inject(VoiceLongRunController);
    const starting = controller.start('http://hub.test', createRequest(60, 600), 'create-key');
    await vi.waitFor(() => expect(api.acquireLongRunLease).toHaveBeenCalledTimes(1));

    globalThis.dispatchEvent(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId: 'default' },
    }));
    leaseResponse.next({
      lease_token: 'late-lease', expires_at: Date.now() + 60_000, profile_id: 'default',
    });
    leaseResponse.complete();

    await expect(starting).rejects.toThrow('voice.capture.cancelled');
    expect(api.createLongRun).not.toHaveBeenCalled();
    expect(capture.start).not.toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('reserves one explicit gap when a crash loses an in-progress partial segment', async () => {
    const generation = await spool.allowProfile('default');
    await spool.put(bufferedSegment('run-a', 0, 0, 60_000, generation));
    recovery.save(recoveryDescriptor({
      profileGeneration: generation,
      nextSequence: 1,
      timelineMilliseconds: 60_500,
      completedTimelineMilliseconds: 60_000,
      durableNextSequence: 1,
      durableTimelineMilliseconds: 60_000,
    }));
    api.getLongRun.mockReturnValue(of(snapshotWithLease('run-a')));
    TestBed.overrideProvider(VOICE_LONG_RUN_SEGMENTER_FACTORY, {
      useValue: (options: ConstructorParameters<typeof VoiceLongRunPcmSegmenter>[0]) => (
        new VoiceLongRunPcmSegmenter({ ...options, bytesPerSecond: 4 })
      ),
    });
    const gaps: number[] = [];
    const controller = TestBed.inject(VoiceLongRunController);

    await controller.initializeSecureStorage();
    await controller.inspectRecovery();
    await controller.prepareCapture('system_audio');
    await controller.resumeCapture({ gap: (sequence) => gaps.push(sequence) });
    chunkHandler!(new ArrayBuffer(240));

    await vi.waitFor(() => expect(api.uploadLongRunSegment.mock.calls.some((call) => call[2] === 2)).toBe(true));
    const resumedUpload = api.uploadLongRunSegment.mock.calls.find((call) => call[2] === 2)!;
    expect(resumedUpload[3]).toEqual(expect.objectContaining({
      startedAtMs: 60_500,
      endedAtMs: 120_500,
    }));
    await controller.stop();
    expect(gaps).toEqual([1]);
    const heartbeatGaps = api.heartbeatLongRun.mock.calls.flatMap((call) => call[2].gaps || []);
    expect(heartbeatGaps.filter((sequence) => sequence === 1)).toHaveLength(1);
  });

  it('does not synthesize a gap for a completed segment already in the encrypted spool', async () => {
    const generation = await spool.allowProfile('default');
    await spool.put(bufferedSegment('run-a', 0, 0, 60_000, generation));
    await spool.put(bufferedSegment('run-a', 1, 60_000, 120_000, generation));
    recovery.save(recoveryDescriptor({
      profileGeneration: generation,
      nextSequence: 1,
      timelineMilliseconds: 70_000,
      completedTimelineMilliseconds: 60_000,
      durableNextSequence: 1,
      durableTimelineMilliseconds: 60_000,
    }));
    api.getLongRun.mockReturnValue(of(snapshotWithLease('run-a')));
    const gaps: number[] = [];
    const controller = TestBed.inject(VoiceLongRunController);

    await controller.initializeSecureStorage();
    await controller.inspectRecovery();
    await controller.prepareCapture('system_audio');
    await controller.resumeCapture({ gap: (sequence) => gaps.push(sequence) });
    chunkHandler!(new ArrayBuffer(120));

    await vi.waitFor(() => expect(api.uploadLongRunSegment.mock.calls.some((call) => call[2] === 2)).toBe(true));
    const resumedUpload = api.uploadLongRunSegment.mock.calls.find((call) => call[2] === 2)!;
    expect(resumedUpload[3]).toEqual(expect.objectContaining({
      startedAtMs: 120_000,
      endedAtMs: 180_000,
    }));
    expect(gaps).toEqual([]);
    await controller.stop();
  });

  it('drains encrypted audio after the capture deadline without requesting capture permission', async () => {
    const generation = await spool.allowProfile('default');
    await spool.put(bufferedSegment('run-a', 0, 0, 60_000, generation));
    recovery.save(recoveryDescriptor({
      profileGeneration: generation,
      nextSequence: 1,
      timelineMilliseconds: 60_000,
      completedTimelineMilliseconds: 60_000,
      durableNextSequence: 1,
      durableTimelineMilliseconds: 60_000,
    }));
    const draining = snapshot('run-a');
    draining.run.capture_deadline_at = Date.now() - 1_000;
    draining.run.expires_at = Date.now() + 60_000;
    api.getLongRun.mockReturnValue(of(draining));
    const controller = TestBed.inject(VoiceLongRunController);

    await controller.initializeSecureStorage();
    await controller.inspectRecovery();
    expect(controller.recoveryDrainOnly()).toBe(true);
    const response = await controller.drainRecovery();

    expect(response.run.status).toBe('completed');
    expect(api.uploadLongRunSegment).toHaveBeenCalledWith(
      'http://hub.test', 'run-a', 0, expect.any(Object),
      'voice-ui:long-run-segment:run-a:0',
    );
    expect(capture.prepare).not.toHaveBeenCalled();
    expect(capture.start).not.toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
    expect(await spool.list('run-a')).toEqual([]);
  });

  it('preserves live display mode when recovery drain fails after refreshing its cursor', async () => {
    const generation = await spool.allowProfile('default');
    recovery.save(recoveryDescriptor({
      profileGeneration: generation,
      displayMode: 'live',
    }));
    const draining = snapshot('run-a');
    draining.run.capture_deadline_at = Date.now() - 1_000;
    draining.run.expires_at = Date.now() + 60_000;
    api.getLongRun.mockReturnValue(of(draining));
    api.stopLongRun.mockReturnValue(throwError(() => ({ status: 0, message: 'offline' })));
    const controller = TestBed.inject(VoiceLongRunController);

    await expect(controller.drainRecovery()).rejects.toEqual(expect.objectContaining({
      status: 0,
      message: 'offline',
    }));

    expect(controller.recoveryMetadata()).toEqual(expect.objectContaining({
      runId: 'run-a',
      displayMode: 'live',
    }));
  });

  it('retains recovery ciphertext while the Hub is finalizing', async () => {
    const generation = await spool.allowProfile('default');
    await spool.put(bufferedSegment('run-a', 0, 0, 60_000, generation));
    recovery.save(recoveryDescriptor({ profileGeneration: generation }));
    api.getLongRun.mockReturnValue(of(snapshot('run-a', 'finalizing')));
    const controller = TestBed.inject(VoiceLongRunController);

    const inspected = await controller.inspectRecovery();

    expect(inspected?.run.status).toBe('finalizing');
    expect(controller.recoveryFinalizing()).toBe(true);
    expect(controller.recoveryMetadata()?.runId).toBe('run-a');
    expect(await spool.list('run-a')).toHaveLength(1);
    await expect(controller.drainRecovery()).rejects.toThrow('voice.long_run.recovery_finalizing');
    expect(controller.recoveryMetadata()?.runId).toBe('run-a');
    expect(await spool.list('run-a')).toHaveLength(1);
  });

  it('always discards local recovery even when the Hub is offline', async () => {
    const generation = await spool.allowProfile('default');
    await spool.put(bufferedSegment('run-a', 0, 0, 60_000, generation));
    recovery.save(recoveryDescriptor({ profileGeneration: generation }));
    api.getLongRun.mockReturnValue(throwError(() => ({ status: 0, message: 'offline' })));
    const controller = TestBed.inject(VoiceLongRunController);

    await expect(controller.discardRecovery()).resolves.toBe(false);

    expect(controller.recoveryMetadata()).toBeNull();
    expect(await spool.list('run-a')).toEqual([]);
  });

  it('accepts a backend lease-fence rejection after preempting an already-sent create', async () => {
    const createResponse = new Subject<any>();
    api.createLongRun.mockReturnValueOnce(createResponse);
    const controller = TestBed.inject(VoiceLongRunController);
    const starting = controller.start('http://hub.test', createRequest(60, 600), 'create-key');
    await vi.waitFor(() => expect(api.createLongRun).toHaveBeenCalledTimes(1));

    globalThis.dispatchEvent(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId: 'default' },
    }));
    const leaseFence = {
      status: 409,
      error: { code: 'voice_live_run.start_lease_revoked', retriable: false },
    };
    createResponse.error(leaseFence);

    await expect(starting).rejects.toBe(leaseFence);
    expect(api.createLongRun).toHaveBeenCalledTimes(1);
    expect(capture.start).not.toHaveBeenCalled();
    expect(capture.stop).toHaveBeenCalled();
    expect(controller.recoveryMetadata()).toBeNull();
  });

  it('lets profile deletion preempt an ordinary stop and local upload drain', async () => {
    const controller = TestBed.inject(VoiceLongRunController);
    await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

    const stopping = controller.stop();
    globalThis.dispatchEvent(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId: 'default' },
    }));

    await expect(stopping).rejects.toThrow('voice.capture.cancelled');
    await vi.waitFor(() => expect(controller.runId).toBe(''));
    expect(controller.recoveryMetadata()).toBeNull();
    expect(api.stopLongRun).not.toHaveBeenCalled();
  });

  it('stops at the Hub capture deadline even when no more PCM arrives', async () => {
    vi.useFakeTimers();
    try {
      const leased = snapshot('run-a');
      leased.run.capture_deadline_at = Date.now() + 1_000;
      api.createLongRun.mockReturnValueOnce(of(leased));
      const controller = TestBed.inject(VoiceLongRunController);
      await controller.start('http://hub.test', createRequest(60, 600), 'create-key');

      await vi.advanceTimersByTimeAsync(1_000);
      await settleAsyncWork();

      expect(capture.stop).toHaveBeenCalledTimes(1);
      expect(api.stopLongRun).toHaveBeenCalledWith(
        'http://hub.test', 'run-a', expect.objectContaining({ reason: 'capture_deadline' }),
        'voice-ui:long-run-stop:run-a',
      );
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('appendVoiceLongRunTranscript', () => {
  it('deduplicates the configured segment overlap from incremental upload results', () => {
    expect(appendVoiceLongRunTranscript(
      'Heute planen wir den nächsten Release.',
      'den nächsten Release und prüfen danach die Tests.',
    )).toBe('Heute planen wir den nächsten Release. und prüfen danach die Tests.');
  });
});

function createRequest(segmentSeconds: number, maxSeconds: number) {
  return {
    source: 'system_audio' as const,
    profile_id: 'default',
    language: 'de',
    segment_duration_seconds: segmentSeconds,
    max_duration_seconds: maxSeconds,
    overlap_milliseconds: 0,
  };
}

function snapshot(runId: string, status = 'active'): VoiceLongRunResponse {
  return {
    run: { id: runId, status, last_local_sequence: -1 },
    segments: [],
    composed_transcript: '',
    gaps: [],
    resume: { next_sequence: 0, acknowledged_through_sequence: -1 },
  };
}

function snapshotWithLease(runId: string): VoiceLongRunResponse {
  const response = snapshot(runId);
  response.run.capture_deadline_at = Date.now() + 60_000;
  response.run.expires_at = Date.now() + 120_000;
  return response;
}

function uploadSnapshot(runId: string, sequence: number) {
  return {
    ...snapshot(runId),
    segment: { sequence, status: 'completed', text: null },
    result: { text: `segment-${sequence}` },
    composed_transcript: null,
  };
}

function revisionUpload(
  runId: string,
  sequence: number,
  segmentOverrides: Record<string, unknown>,
) {
  const response = snapshot(runId);
  return {
    ...response,
    run: { ...response.run, timeline_revision: segmentOverrides['timeline_revision'] },
    segment: { sequence, status: 'completed', ...segmentOverrides },
    segments: [],
    composed_transcript: null,
    resume: { next_sequence: sequence + 1, acknowledged_through_sequence: sequence },
  };
}

function revisionDelta(
  runId: string,
  timelineRevision: number,
  segmentOverrides: Record<string, unknown>,
): VoiceLongRunResponse {
  return {
    run: { id: runId, status: 'active', timeline_revision: timelineRevision },
    segments: [{ sequence: 0, status: 'completed', ...segmentOverrides }],
    gaps: [],
    page: {
      after_sequence: -1,
      next_after_sequence: -1,
      after_revision: timelineRevision - 1,
      next_after_revision: timelineRevision,
      limit: 100,
      has_more: false,
    },
  };
}

function recoveryDescriptor(
  overrides: Partial<VoiceLongRunRecoveryMetadata> = {},
): VoiceLongRunRecoveryMetadata {
  return {
    schemaVersion: 1,
    runId: 'run-a',
    hubUrl: 'http://hub.test',
    createIdempotencyKey: 'create-key',
    profileGeneration: 1,
    request: createRequest(60, 600),
    nextSequence: 1,
    timelineMilliseconds: 60_000,
    completedTimelineMilliseconds: 60_000,
    durableNextSequence: 1,
    durableTimelineMilliseconds: 60_000,
    updatedAt: Date.now(),
    ...overrides,
  };
}

function bufferedSegment(
  runId: string,
  sequence: number,
  startedAtMs: number,
  endedAtMs: number,
  profileGeneration: number,
): VoiceLongRunSpoolSegment {
  return {
    runId,
    profileId: 'default',
    profileGeneration,
    sequence,
    startedAtMs,
    endedAtMs,
    durationMs: endedAtMs - startedAtMs,
    overlapMilliseconds: 0,
    idempotencyKey: `voice-ui:long-run-segment:${runId}:${sequence}`,
    audio: new ArrayBuffer(120),
  };
}

async function settleAsyncWork(): Promise<void> {
  for (let turn = 0; turn < 12; turn += 1) await Promise.resolve();
}

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

class MemorySpool implements VoiceLongRunSpoolPort {
  readonly records = new Map<string, VoiceLongRunSpoolSegment>();
  maxObservedSegments = 0;

  constructor(private readonly maxSegments: number) {}

  async initialize(): Promise<void> {}

  async put(segment: VoiceLongRunSpoolSegment) {
    const evicted: VoiceLongRunSpoolMetadata[] = [];
    while (this.records.size >= this.maxSegments) {
      const oldest = this.records.values().next().value as VoiceLongRunSpoolSegment;
      this.records.delete(`${oldest.runId}:${oldest.sequence}`);
      evicted.push(this.metadata(oldest));
    }
    this.records.set(`${segment.runId}:${segment.sequence}`, segment);
    this.maxObservedSegments = Math.max(this.maxObservedSegments, this.records.size);
    return { stored: this.metadata(segment), evicted };
  }

  async read(runId: string, sequence: number) {
    return this.records.get(`${runId}:${sequence}`) || null;
  }

  async list(runId: string) {
    return [...this.records.values()]
      .filter((item) => item.runId === runId)
      .sort((left, right) => left.sequence - right.sequence)
      .map((item) => this.metadata(item));
  }

  async delete(runId: string, sequence: number) {
    this.records.delete(`${runId}:${sequence}`);
  }

  async clearRun(runId: string) {
    for (const [key, value] of this.records) if (value.runId === runId) this.records.delete(key);
  }

  signalProfileDeletion(_profileId: string) {}

  async clearProfile(profileId: string) {
    for (const [key, value] of this.records) if (value.profileId === profileId) this.records.delete(key);
  }

  async allowProfile(_profileId: string) { return Date.now() || 1; }

  async stats(runId?: string) {
    const records = [...this.records.values()].filter((item) => !runId || item.runId === runId);
    return {
      segments: records.length,
      bytes: records.reduce((total, item) => total + item.audio.byteLength, 0),
      maxSegments: this.maxSegments,
      maxBytes: Number.MAX_SAFE_INTEGER,
    };
  }

  private metadata(segment: VoiceLongRunSpoolSegment): VoiceLongRunSpoolMetadata {
    return {
      runId: segment.runId,
      profileId: segment.profileId,
      profileGeneration: segment.profileGeneration,
      sequence: segment.sequence,
      startedAtMs: segment.startedAtMs,
      endedAtMs: segment.endedAtMs,
      durationMs: segment.durationMs,
      overlapMilliseconds: segment.overlapMilliseconds,
      idempotencyKey: segment.idempotencyKey,
      byteLength: segment.audio.byteLength,
      createdAt: segment.sequence,
      expiresAt: Number.MAX_SAFE_INTEGER,
    };
  }
}

class MemoryRecovery implements VoiceLongRunRecoveryPort {
  private metadata: VoiceLongRunRecoveryMetadata | null = null;

  load(): VoiceLongRunRecoveryMetadata | null {
    return this.metadata ? structuredClone(this.metadata) : null;
  }

  save(metadata: VoiceLongRunRecoveryMetadata): void {
    this.metadata = structuredClone(metadata);
  }

  clear(runId?: string): void {
    if (!runId || this.metadata?.runId === runId) this.metadata = null;
  }
}
