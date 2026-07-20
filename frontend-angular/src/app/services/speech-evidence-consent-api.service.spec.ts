import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SPEECH_EVIDENCE_GRANTS,
  SpeechEvidenceConsentApiService,
  SpeechEvidenceConsentDocument,
  parseSpeechEvidenceConsentResponse,
} from './speech-evidence-consent-api.service';

const now = 1_700_000_000_000;
const consent = (): SpeechEvidenceConsentDocument => ({
  schema: 'ananta.speech-evidence-consent.v1', consent_id: 'consent-a', tenant_id: 'tenant-a',
  owner_subject: 'alice', speaker_id: 'alice', recipient_id: 'bob', direction: 'sender_to_receiver',
  pair_id: 'session-a', session_id: 'session-a', session_epoch: 3,
  purpose: 'speech_quality_improvement', data_classes: ['transcript', 'correction'], retention_seconds: 3600,
  trainer_locations: [],
  grants: Object.fromEntries(SPEECH_EVIDENCE_GRANTS.map(name => [name, name === 'capture' || name === 'transcript_share'])) as any,
  consent_version: 1, revocation_epoch: 0, issued_at_ms: now, expires_at_ms: now + 3_600_000,
  state: 'active', required_signers: ['alice', 'bob'],
  signatures: { alice: 'a'.repeat(64), bob: 'b'.repeat(64) },
});

const response = () => ({
  ok: true,
  data: { consent: consent(), consent_digest: 'c'.repeat(64), scope_digest: 'd'.repeat(64) },
});

describe('SpeechEvidenceConsentApiService', () => {
  it('parses the closed Hub projection and rejects missing grant fields', () => {
    expect(parseSpeechEvidenceConsentResponse(response()).consent.consent_id).toBe('consent-a');
    const invalid = response();
    delete (invalid.data.consent.grants as any).export;
    expect(() => parseSpeechEvidenceConsentResponse(invalid)).toThrow('speech_consent_grants_invalid');
  });

  it('uses idempotency and If-Match for authoritative mutations', () => {
    const request = vi.fn(() => of(response()));
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceConsentApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    const api = TestBed.inject(SpeechEvidenceConsentApiService);
    api.reduce('http://hub.test', consent(), 1, 'consent-reduce-1').subscribe();
    expect(request).toHaveBeenCalledWith(
      'POST',
      'http://hub.test/v1/voice/speech-evidence-consents/consent-a/reduce',
      'http://hub.test',
      expect.objectContaining({ headers: {
        'Idempotency-Key': 'consent-reduce-1', 'If-Match': '"1"',
      } }),
    );
  });
});
