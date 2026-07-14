import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
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
