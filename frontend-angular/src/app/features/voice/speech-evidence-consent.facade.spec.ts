import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import {
  SPEECH_EVIDENCE_GRANTS,
  SpeechEvidenceConsentApiService,
} from '../../services/speech-evidence-consent-api.service';
import { SpeechEvidenceConsentFacade, SpeechEvidenceConsentDraft } from './speech-evidence-consent.facade';

describe('SpeechEvidenceConsentFacade', () => {
  let facade: SpeechEvidenceConsentFacade;
  let api: any;

  beforeEach(() => {
    api = {
      grant: vi.fn((_hub: string, consent: any) => of({
        consent, consentDigest: 'c'.repeat(64), scopeDigest: 'd'.repeat(64),
      })),
      get: vi.fn(), reduce: vi.fn(), renew: vi.fn(), revoke: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceConsentFacade,
      { provide: SpeechEvidenceConsentApiService, useValue: api },
    ] });
    facade = TestBed.inject(SpeechEvidenceConsentFacade);
    facade.bind({
      hubUrl: 'http://hub.test', tenantId: 'tenant-a', sessionId: 'session-a', epoch: 3,
      localPeerId: 'alice', remotePeerId: 'bob',
    });
  });

  afterEach(() => facade.ngOnDestroy());

  it('never fabricates bilateral signature digests and binds explicit ones to the Hub request', async () => {
    const unsigned = draft({});
    await facade.handle({ kind: 'grant', draft: unsigned });
    expect(api.grant).not.toHaveBeenCalled();
    expect(facade.state$.value.errorCode).toBe('speech_consent_bilateral_signature_digest_required');

    const signed = draft({ alice: 'a'.repeat(64), bob: 'b'.repeat(64) });
    await facade.handle({ kind: 'grant', draft: signed });
    expect(api.grant).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({
        tenant_id: 'tenant-a', session_id: 'session-a', session_epoch: 3,
        required_signers: ['alice', 'bob'],
        signatures: { alice: 'a'.repeat(64), bob: 'b'.repeat(64) },
      }),
      expect.stringMatching(/^speech-consent-grant-/),
    );
    expect(facade.state$.value.consent?.consent.consent_version).toBe(1);
  });

  it('loads a shared bilateral consent for its bound recipient without transferring mutation authority', async () => {
    const now = Date.now();
    api.get.mockReturnValue(of({
      consentDigest: 'c'.repeat(64), scopeDigest: 'd'.repeat(64),
      consent: {
        schema: 'ananta.speech-evidence-consent.v1', consent_id: 'consent-shared', tenant_id: 'tenant-a',
        owner_subject: 'bob', speaker_id: 'bob', recipient_id: 'alice', direction: 'sender_to_receiver',
        pair_id: 'session-a', session_id: 'session-a', session_epoch: 3, purpose: 'speech_quality_improvement',
        data_classes: ['transcript'], retention_seconds: 3600, trainer_locations: [],
        grants: Object.fromEntries(SPEECH_EVIDENCE_GRANTS.map(name => [
          name, name === 'capture' || name === 'transcript_share',
        ])),
        consent_version: 1, revocation_epoch: 0, issued_at_ms: now - 1_000,
        expires_at_ms: now + 60_000, state: 'active', required_signers: ['alice', 'bob'],
        signatures: { alice: 'a'.repeat(64), bob: 'b'.repeat(64) },
      },
    }));

    await facade.handle({ kind: 'load', consentId: 'consent-shared' });

    expect(facade.state$.value.consent?.consent).toMatchObject({ speaker_id: 'bob', recipient_id: 'alice' });
    expect(facade.state$.value.errorCode).toBeNull();
  });
});

function draft(signerDigests: Record<string, string>): SpeechEvidenceConsentDraft {
  return {
    consentId: 'consent-a', purpose: 'speech_quality_improvement', dataClasses: ['transcript', 'correction'],
    retentionSeconds: 3600, trainerLocations: [], expiresInHours: 24,
    grants: Object.fromEntries(SPEECH_EVIDENCE_GRANTS.map(name => [
      name, name === 'capture' || name === 'transcript_share',
    ])) as any,
    signerDigests,
  };
}
