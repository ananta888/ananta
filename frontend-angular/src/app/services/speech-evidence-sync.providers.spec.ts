import { TestBed } from '@angular/core/testing';

import { E2eEncryptionService } from './e2e-encryption.service';
import { SpeechEvidenceSyncCryptoContext } from './speech-evidence-sync.providers';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';

const binding = {
  scopeKind: 'session' as const, scopeId: 'session-a', localPeerId: 'alice', remotePeerId: 'bob',
  peerPublicKeySpkiB64: 'unused', epoch: 3, keyId: 'pair-key', contractDigest: 'a'.repeat(64),
  packageId: 'package-a', tenantId: 'tenant-a', deviceId: 'device-a', membershipId: 'member-a',
  membershipVersion: 1, peerFingerprint: 'b'.repeat(64), confirmed: true, fingerprintChanged: false,
  transcriptDigest: 'c'.repeat(64),
};

describe('SpeechEvidenceSyncCryptoContext', () => {
  const peerKeys = { requireBinding: vi.fn(() => binding) };
  const contentKey = {} as CryptoKey;
  const encryption = { derivePurposeAesKey: vi.fn(async () => contentKey) };
  let context: SpeechEvidenceSyncCryptoContext;

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceSyncCryptoContext,
      { provide: WebrtcPeerKeyService, useValue: peerKeys },
      { provide: E2eEncryptionService, useValue: encryption },
    ] });
    context = TestBed.inject(SpeechEvidenceSyncCryptoContext);
  });

  it('fails closed before explicit consent and derives non-exported pair content keys after binding', async () => {
    await expect(context.resolve('offer-a', 3, 'content-key')).rejects
      .toThrow('speech_evidence_signing_context_unavailable');
    context.configure('session-a', 2);
    expect(await context.resolve('offer-a', 3, 'content-key')).toBe(contentKey);
    expect(encryption.derivePurposeAesKey).toHaveBeenCalledWith(
      binding,
      'speech-evidence-chunk',
      expect.stringMatching(/^evidence-[0-9a-f]{64}$/),
    );
  });

  it('emits canonical Ed25519 protocol messages only for the confirmed pair context', async () => {
    context.configure('session-a', 2);
    const message = await context.sign('revocation', {
      traffic_class: 'control', revocation_id: 'revoke-a', group_ids: ['group-a'],
      scope_digest: 'a'.repeat(64), reason_code: 'user_request', revocation_epoch: 4,
      deadline_at_ms: Date.now() + 30_000, requested_action: 'delete',
    }, Date.now() + 60_000);
    expect(message).toMatchObject({
      protocol_version: 'ananta.speech-evidence-sync.v1', message_type: 'revocation',
      session_id: 'session-a', pair_id: 'session-a', sender_id: 'alice', audience_id: 'bob',
      signature_algorithm: 'Ed25519', consent_version: 2,
    });
    expect(atob(message.signature_b64)).toHaveLength(64);
  });
});
