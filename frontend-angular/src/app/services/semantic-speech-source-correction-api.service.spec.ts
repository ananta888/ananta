import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HubApiCoreService } from './hub-api-core.service';
import { SemanticSpeechSourceCorrectionApiService } from './semantic-speech-source-correction-api.service';

describe('SemanticSpeechSourceCorrectionApiService', () => {
  const request = vi.fn();
  let service: SemanticSpeechSourceCorrectionApiService;

  beforeEach(() => {
    request.mockReset();
    request.mockReturnValue(of({ authority: 'corrected' }));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechSourceCorrectionApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    service = TestBed.inject(SemanticSpeechSourceCorrectionApiService);
  });

  it('binds source bytes, consent, epoch and stable idempotency to the bounded Hub endpoint', async () => {
    const deadlineAtMs = Date.now() + 20_000;
    await firstValueFrom(service.correct({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 2, turnId: 'turn-a',
      finalRevision: 3, consentVersion: 4, consentId: 'consent-a', consentDigest: 'b'.repeat(64),
      consentRevocationEpoch: 1, contractDigest: 'a'.repeat(64), sourceDigest: 'c'.repeat(64),
      sourceExpiresAtMs: deadlineAtMs + 1_000, deadlineAtMs, finalText: 'Finaler Text',
      sourceAudio: new Uint8Array([1, 2, 3]), language: 'de',
    }));

    expect(request).toHaveBeenCalledOnce();
    const [method, url, hubUrl, options] = request.mock.calls[0];
    expect([method, url, hubUrl]).toEqual([
      'POST', 'http://hub.test/v1/voice/source-corrections', 'http://hub.test',
    ]);
    const form = options.body as FormData;
    expect(form.get('session_id')).toBe('session-a');
    expect(form.get('epoch')).toBe('2');
    expect(form.get('consent_id')).toBe('consent-a');
    expect(form.get('consent_digest')).toBe('b'.repeat(64));
    expect(form.get('consent_revocation_epoch')).toBe('1');
    expect(form.get('final_text')).toBe('Finaler Text');
    const sourceFile = form.get('file') as File;
    expect(sourceFile).toBeInstanceOf(Blob);
    expect(sourceFile.type).toBe('audio/wav');
    expect(sourceFile.name).toBe('turn-a.wav');
    expect(options.headers['Idempotency-Key']).toBe(
      `semantic-source-correction:session-a:2:turn-a:3:${'c'.repeat(64)}`,
    );
    expect(options.timeoutMs).toBeGreaterThan(0);
    expect(options.timeoutMs).toBeLessThanOrEqual(30_000);
  });
});
