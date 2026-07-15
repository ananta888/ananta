import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import {
  HTTP_INTERCEPTORS,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { AuthInterceptor } from '../../services/auth.interceptor';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { UserAuthService } from '../../services/user-auth.service';
import { VoiceApiService } from './voice-api.service';

describe('VoiceApiService', () => {
  const core = {
    get: vi.fn(),
    post: vi.fn(),
    request: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        VoiceApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('uses only the supplied Hub URL for configuration and capabilities', async () => {
    const hubUrl = 'http://hub.test:5000';
    core.get
      .mockReturnValueOnce(of({ schema_version: 'ananta.voice-configuration.v1', properties: {} }))
      .mockReturnValueOnce(of({ configuration: { schema_version: 'ananta.voice-configuration.v1', effective: {}, sources: [], version: 1 } }))
      .mockReturnValueOnce(of({ available: true, provider: 'voice-runtime', capabilities: [], models: [] }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.getConfigurationSchema(hubUrl));
    await firstValueFrom(api.getConfiguration(hubUrl, { profileId: 'p one', sessionId: 's/1' }));
    await firstValueFrom(api.getCapabilities(hubUrl));

    const urls = core.get.mock.calls.map((call) => String(call[0]));
    expect(urls).toEqual([
      `${hubUrl}/v1/voice/configuration/schema`,
      `${hubUrl}/v1/voice/configuration?profile_id=p+one&session_id=s%2F1`,
      `${hubUrl}/v1/voice/capabilities`,
    ]);
    expect(urls.every((url) => url.startsWith(hubUrl))).toBe(true);
    expect(urls.join(' ')).not.toMatch(/voice-runtime|restricted-inference/);
  });

  it('sends sparse configuration deltas with an idempotency key to the Hub contract', async () => {
    core.request.mockReturnValue(of({
      configuration: {
        schema_version: 'ananta.voice-configuration.v1',
        effective: { recognition_strategy: 'parallel_compare' },
        sources: [],
        version: 4,
      },
    }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.saveConfiguration('http://hub.test', {
      scope: 'profile',
      scope_id: 'profile-a',
      delta: { recognition_strategy: 'parallel_compare' },
      expected_version: 3,
    }, 'configuration-key'));

    expect(core.request).toHaveBeenCalledWith(
      'PUT',
      'http://hub.test/v1/voice/configuration',
      'http://hub.test',
      expect.objectContaining({ headers: { 'Idempotency-Key': 'configuration-key' } }),
    );
  });

  it('keeps review, consent and reset mutations on versioned Hub endpoints', async () => {
    core.request
      .mockReturnValueOnce(of({ review: { id: 'review-a' } }))
      .mockReturnValueOnce(of({ consent: { profile_id: 'profile-a' } }))
      .mockReturnValueOnce(of({ reset: { profile_id: 'profile-a', deleted_count: 2, version: 3 } }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.createReview('http://hub.test', {
      profile_id: 'profile-a', result_ref: 'audit-a', candidate_ids: ['candidate-a'],
    }, 'review-key'));
    await firstValueFrom(api.setConsent('http://hub.test', 'profile-a', {
      granted: true, categories: ['vocabulary'], retention_days: 90,
    }, 'consent-key'));
    await firstValueFrom(api.resetPersonalization('http://hub.test', 'profile-a', 'reset-key'));

    expect(core.request.mock.calls.map((call) => `${call[0]} ${call[1]}`)).toEqual([
      'POST http://hub.test/v1/voice/reviews',
      'PUT http://hub.test/v1/voice/consents/profile-a',
      'DELETE http://hub.test/v1/voice/personalization/profile-a',
    ]);
    expect(core.request.mock.calls.map((call) => call[3].headers['Idempotency-Key'])).toEqual([
      'review-key', 'consent-key', 'reset-key',
    ]);
  });

  it('sends profile/session scope and replay protection with audio only to the Hub', async () => {
    core.request.mockReturnValue(of({ text: 'ok', candidates: [] }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.transcribe('http://hub.test', {
      file: new Blob(['audio'], { type: 'audio/wav' }),
      fileName: 'sample.wav',
      language: 'de',
      profileId: 'profile-a',
      sessionId: 'session-a',
      idempotencyKey: 'transcribe-key',
    }));

    expect(core.request).toHaveBeenCalledWith(
      'POST', 'http://hub.test/v1/voice/transcribe', 'http://hub.test',
      expect.objectContaining({
        headers: { 'Idempotency-Key': 'transcribe-key' },
        timeoutMs: 120_000,
      }),
    );
    const form = core.request.mock.calls[0][3].body as FormData;
    expect(form.get('profile_id')).toBe('profile-a');
    expect(form.get('session_id')).toBe('session-a');
    expect(form.get('language')).toBe('de');
  });

  it('keeps the typed stream lifecycle on ordered Hub endpoints', async () => {
    core.request
      .mockReturnValueOnce(of({ stream: { session_id: 'stream/a', state: 'created', next_chunk_sequence: 0 } }))
      .mockReturnValueOnce(of({ stream: { session_id: 'stream/a', state: 'active', next_chunk_sequence: 1 } }))
      .mockReturnValueOnce(of({
        stream: { session_id: 'stream/a', state: 'final', next_chunk_sequence: 1 },
        result: { text: 'Hallo' },
        result_ref: 'voice-result-a',
      }))
      .mockReturnValueOnce(of({ stream: { session_id: 'stream/a', state: 'closed' }, deleted: true }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.createStream('http://hub.test', {
      profile_id: 'profile-a',
      configuration_session_id: 'session-a',
      media_type: 'audio/pcm;rate=16000;channels=1',
    }, 'stream-create-key'));
    await firstValueFrom(api.pushStreamChunk(
      'http://hub.test', 'stream/a', 0, new ArrayBuffer(8),
    ));
    await firstValueFrom(api.finalizeStream('http://hub.test', 'stream/a'));
    await firstValueFrom(api.cancelStream('http://hub.test', 'stream/a'));

    expect(core.request.mock.calls.map((call) => `${call[0]} ${call[1]}`)).toEqual([
      'POST http://hub.test/v1/voice/streams',
      'PUT http://hub.test/v1/voice/streams/stream%2Fa/chunks/0',
      'POST http://hub.test/v1/voice/streams/stream%2Fa/finalize',
      'DELETE http://hub.test/v1/voice/streams/stream%2Fa',
    ]);
    expect(core.request.mock.calls[0][3].headers).toEqual({ 'Idempotency-Key': 'stream-create-key' });
    expect(core.request.mock.calls[1][3].headers).toEqual({
      'Content-Type': 'audio/pcm;rate=16000;channels=1',
    });
  });

  it('uses the additive Hub-owned live-run contract with idempotent WAV segments', async () => {
    core.request.mockReturnValue(of({
      run: { id: 'run/a', status: 'active' }, segments: [], gaps: [], resume: { next_sequence: 0 },
    }));
    core.get.mockReturnValue(of({
      run: { id: 'run/a', status: 'active' }, segments: [], gaps: [], resume: { next_sequence: 0 },
    }));
    const api = TestBed.inject(VoiceApiService);

    await firstValueFrom(api.acquireLongRunLease('http://hub.test', 'profile-a'));
    await firstValueFrom(api.createLongRun('http://hub.test', {
      source: 'system_audio', profile_id: 'profile-a', segment_duration_seconds: 120,
      max_duration_seconds: 28_800, overlap_milliseconds: 1_000, lease_token: 'lease-a',
    }, 'create-key'));
    await firstValueFrom(api.uploadLongRunSegment('http://hub.test', 'run/a', 3, {
      file: new Blob(['wav'], { type: 'audio/wav' }), fileName: 'segment.wav',
      startedAtMs: 357_000, endedAtMs: 477_000, durationMs: 120_000,
      overlapMilliseconds: 1_000,
    }, 'segment-key'));
    await firstValueFrom(api.heartbeatLongRun('http://hub.test', 'run/a', {
      last_local_sequence: 3, gaps: [1],
    }));
    await firstValueFrom(api.getLongRun('http://hub.test', 'run/a', { includeText: false }));
    await firstValueFrom(api.getLongRun('http://hub.test', 'run/a', {
      afterRevision: 17,
      limit: 100,
    }));
    await firstValueFrom(api.stopLongRun(
      'http://hub.test', 'run/a', { last_sequence: 3, reason: 'user_stop' }, 'stop-key',
    ));

    expect(core.request.mock.calls.map((call) => `${call[0]} ${call[1]}`)).toEqual([
      'POST http://hub.test/v1/voice/live-runs/lease',
      'POST http://hub.test/v1/voice/live-runs',
      'PUT http://hub.test/v1/voice/live-runs/run%2Fa/segments/3',
      'POST http://hub.test/v1/voice/live-runs/run%2Fa/heartbeat',
      'POST http://hub.test/v1/voice/live-runs/run%2Fa/stop',
    ]);
    expect(core.request.mock.calls[0][3]).toEqual({ body: { profile_id: 'profile-a' } });
    expect(core.request.mock.calls[1][3]).toEqual(expect.objectContaining({
      body: expect.objectContaining({ lease_token: 'lease-a' }),
      headers: { 'Idempotency-Key': 'create-key' },
    }));
    const form = core.request.mock.calls[2][3].body as FormData;
    expect(form.get('started_at_ms')).toBe('357000');
    expect(form.get('ended_at_ms')).toBe('477000');
    expect(form.get('duration_ms')).toBe('120000');
    expect(form.get('overlap_milliseconds')).toBe('1000');
    expect(core.request.mock.calls[2][3].headers).toEqual({ 'Idempotency-Key': 'segment-key' });
    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/v1/voice/live-runs/run%2Fa?include_text=false',
      'http://hub.test', undefined, false,
    );
    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/v1/voice/live-runs/run%2Fa?after_revision=17&limit=100',
      'http://hub.test', undefined, false,
    );
  });

  it('keeps import, feedback reset, full privacy delete and export-task creation distinct', async () => {
    core.request
      .mockReturnValueOnce(of({ import: { profile_id: 'profile-a', imported_count: 1, version: 2 } }))
      .mockReturnValueOnce(of({ reset: { profile_id: 'profile-a', deleted_count: 1, version: 3 } }))
      .mockReturnValueOnce(of({
        deletion: {
          profile_id: 'profile-a', deleted_count: 4, deleted_by_store: {}, snapshots_revoked: true,
          revoked_stream_count: 1, runtime_cleanup_failed_count: 0, runtime_cleanup_pending: false,
        },
      }))
      .mockReturnValueOnce(of({ task_id: 'voice-training-export-a', idempotent_replay: false }));
    const api = TestBed.inject(VoiceApiService);
    const payload = {
      schema_version: 'voice-personalization.v1' as const,
      profile_id: 'profile-a',
      items: [{ kind: 'vocabulary' as const, target_text: 'Ananta' }],
    };

    await firstValueFrom(api.importPersonalization('http://hub.test', 'profile-a', payload, 'import-key'));
    await firstValueFrom(api.resetPersonalization('http://hub.test', 'profile-a', 'reset-key'));
    const deletion = await firstValueFrom(
      api.deleteVoiceProfile('http://hub.test', 'profile-a', 'delete-key'),
    );
    await firstValueFrom(api.createFineTuningExportTask('http://hub.test', 'profile-a', {
      confirmed: true, purpose: 'private spelling model', license: 'private',
    }, 'export-task-key'));

    expect(core.request.mock.calls.map((call) => `${call[0]} ${call[1]}`)).toEqual([
      'POST http://hub.test/v1/voice/personalization/profile-a/import',
      'DELETE http://hub.test/v1/voice/personalization/profile-a',
      'DELETE http://hub.test/v1/voice/privacy/profile-a',
      'POST http://hub.test/v1/voice/personalization/profile-a/fine-tuning-export-tasks',
    ]);
    expect(core.request.mock.calls[2][3]).toEqual(expect.objectContaining({
      body: { confirmed: true }, headers: { 'Idempotency-Key': 'delete-key' },
    }));
    expect(core.request.mock.calls[3][3]).toEqual(expect.objectContaining({
      body: { confirmed: true, purpose: 'private spelling model', license: 'private' },
      headers: { 'Idempotency-Key': 'export-task-key' },
    }));
    expect(deletion).toEqual(expect.objectContaining({
      revoked_stream_count: 1,
      runtime_cleanup_failed_count: 0,
      runtime_cleanup_pending: false,
    }));
  });
});

describe('VoiceApiService Hub auth integration', () => {
  let httpMock: HttpTestingController;
  const userAuth = {
    token: 'old-user-token',
    token$: of('old-user-token'),
    refreshToken: vi.fn(() => of({ access_token: 'new-user-token' })),
    logout: vi.fn(),
    logoutHub: vi.fn(),
  };

  beforeEach(() => {
    TestBed.resetTestingModule();
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        VoiceApiService,
        HubApiCoreService,
        { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
        {
          provide: AgentDirectoryService,
          useValue: {
            list: () => [{ name: 'hub', role: 'hub', url: 'http://hub.test', token: 'hub-secret' }],
          },
        },
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('refreshes one failed segment upload and retries the same payload with the new Hub token', async () => {
    const core = TestBed.inject(HubApiCoreService);
    expect(core.getHeaders('http://hub.test').headers.get('Authorization')).toBeNull();
    expect(core.getHeaders('http://hub.test', 'service-token').headers.get('Authorization'))
      .toBe('Bearer service-token');
    const api = TestBed.inject(VoiceApiService);
    const result = firstValueFrom(api.uploadLongRunSegment('http://hub.test', 'run-a', 4, {
      file: new Blob(['wav'], { type: 'audio/wav' }),
      fileName: 'segment.wav',
      startedAtMs: 240_000,
      endedAtMs: 300_000,
      durationMs: 60_000,
      overlapMilliseconds: 1_000,
    }, 'segment-key'));

    const first = httpMock.expectOne('http://hub.test/v1/voice/live-runs/run-a/segments/4');
    expect(first.request.headers.get('Authorization')).toBe('Bearer old-user-token');
    expect(first.request.headers.get('Idempotency-Key')).toBe('segment-key');
    const originalBody = first.request.body;
    first.flush({ error: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    const retried = httpMock.expectOne('http://hub.test/v1/voice/live-runs/run-a/segments/4');
    expect(retried.request.headers.get('Authorization')).toBe('Bearer new-user-token');
    expect(retried.request.headers.get('Idempotency-Key')).toBe('segment-key');
    expect(retried.request.body).toBe(originalBody);
    retried.flush({
      status: 'success',
      data: {
        run: { id: 'run-a', status: 'active' },
        segment: { sequence: 4, status: 'completed' },
        segments: [],
        gaps: [],
        resume: { next_sequence: 5 },
      },
    });

    await expect(result).resolves.toEqual(expect.objectContaining({
      segment: expect.objectContaining({ sequence: 4 }),
    }));
    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logoutHub).not.toHaveBeenCalled();
  });
});
