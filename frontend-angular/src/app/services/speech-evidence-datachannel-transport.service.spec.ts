import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of, Subject } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { SpeechEvidenceDatachannelTransportService } from './speech-evidence-datachannel-transport.service';
import { SpeechEvidenceSyncApiService } from './speech-evidence-sync-api.service';
import {
  canonicalSigningJson,
  sha256Canonical,
  SpeechEvidenceMessage,
} from './speech-evidence-sync.validators';
import { SemanticDataChannelMessage } from './webrtc-datachannel.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcTransportService } from './webrtc-transport.service';

const binding = {
  scopeKind: 'session' as const, scopeId: 'session-a', localPeerId: 'alice', remotePeerId: 'bob',
  peerPublicKeySpkiB64: 'unused', epoch: 3, keyId: 'pair-key', contractDigest: 'a'.repeat(64),
  confirmed: true,
};

describe('SpeechEvidenceDatachannelTransportService', () => {
  let service: SpeechEvidenceDatachannelTransportService;
  let transport: any;
  let encryption: any;
  let api: any;

  beforeEach(() => {
    transport = {
      mode$: new BehaviorSubject('webrtc'), semanticMessage$: new Subject<SemanticDataChannelMessage>(),
      setSemanticEpoch: vi.fn(), enableSemanticTraffic: vi.fn(), disableSemanticTraffic: vi.fn(),
      sendSemantic: vi.fn(async () => ({})),
    };
    encryption = {
      seal: vi.fn(async (_binding, _payload, options) => envelope('alice', 'bob', options.sequence, options.expiresAtMs)),
      open: vi.fn(),
    };
    api = {
      appendChunk: vi.fn(() => of({})), acknowledgeChunk: vi.fn(() => of({})),
      propose: vi.fn(() => of({})), accept: vi.fn(() => of({})), discoverKey: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceDatachannelTransportService,
      { provide: WebrtcTransportService, useValue: transport },
      { provide: WebrtcPeerKeyService, useValue: { requireBinding: () => binding } },
      { provide: E2eEncryptionService, useValue: encryption },
      { provide: SpeechEvidenceSyncApiService, useValue: api },
    ] });
    service = TestBed.inject(SpeechEvidenceDatachannelTransportService);
    service.bind('http://hub.test', 4);
  });

  afterEach(() => service.ngOnDestroy());

  it('wraps signed evidence chunks in the confirmed pair AEAD transport', async () => {
    const expiresAt = Date.now() + 60_000;
    const message = evidenceMessage('alice', 'bob', 1, expiresAt, 'chunk');
    expect(await service.send('evidence_bulk', JSON.stringify(message), expiresAt)).toBe(true);
    expect(transport.setSemanticEpoch).toHaveBeenCalledWith(3);
    expect(transport.enableSemanticTraffic).toHaveBeenCalledWith('evidence_bulk');
    expect(encryption.seal).toHaveBeenCalledWith(
      binding, expect.anything(), expect.objectContaining({
        sequence: 1, payloadType: 'speech_evidence', trafficClass: 'bulk', expiresAtMs: expiresAt,
      }),
    );
    expect(transport.sendSemantic).toHaveBeenCalledWith(
      expect.objectContaining({
        traffic_class: 'evidence_bulk', session_id: 'session-a', sender_id: 'alice', audience_id: 'bob',
      }),
      expect.any(Object),
    );
    expect(api.appendChunk).toHaveBeenCalledWith(
      'http://hub.test', expect.objectContaining({ message_type: 'chunk' }), expect.any(Object),
    );
  });

  it('uses the authenticated Hub append as the single send in relay mode', async () => {
    transport.mode$.next('hub_relay');
    const expiresAt = Date.now() + 60_000;
    const message = evidenceMessage('alice', 'bob', 11, expiresAt, 'chunk');

    expect(await service.send('evidence_bulk', JSON.stringify(message), expiresAt)).toBe(true);
    expect(api.appendChunk).toHaveBeenCalledOnce();
    expect(transport.sendSemantic).not.toHaveBeenCalled();
  });

  it('relays signed revocation controls through the generic Hub transport', async () => {
    transport.mode$.next('hub_relay');
    const expiresAt = Date.now() + 60_000;
    const message = evidenceMessage('alice', 'bob', 12, expiresAt, 'revocation');

    expect(await service.send('control', JSON.stringify(message), expiresAt)).toBe(true);
    expect(api.appendChunk).not.toHaveBeenCalled();
    expect(api.acknowledgeChunk).not.toHaveBeenCalled();
    expect(transport.sendSemantic).toHaveBeenCalledWith(
      expect.objectContaining({ traffic_class: 'control', sequence: 12 }),
      expect.any(Object),
    );
  });

  it('rejects decrypted inbound protocol messages when the Hub key is not resolvable', async () => {
    const expiresAt = Date.now() + 60_000;
    const inner = evidenceMessage('bob', 'alice', 2, expiresAt, 'revocation');
    encryption.open.mockResolvedValue({
      envelope: envelope('bob', 'alice', 2, expiresAt),
      plaintext: new TextEncoder().encode(JSON.stringify(inner)),
    });
    const ciphertext = new TextEncoder().encode('{}');
    const outer: SemanticDataChannelMessage = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'control', message_id: 'outer-2',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice', sequence: 2,
      expires_at_ms: expiresAt, compression: 'none', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: btoa(String.fromCharCode(...ciphertext)),
    };
    api.discoverKey.mockReturnValue(of(null));
    const received: any[] = [];
    service.verificationRejected$.subscribe(value => received.push(value));
    transport.semanticMessage$.next(outer);
    await vi.waitFor(() => expect(received).toHaveLength(1));
    expect(received[0]).toMatchObject({ messageId: 'message-2', reasonCode: 'speech_evidence_key_unknown' });
  });

  it('emits a trusted inbound message only after Hub key discovery and Ed25519 verification', async () => {
    const expiresAt = Date.now() + 60_000;
    const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
    const inner = evidenceMessage('bob', 'alice', 3, expiresAt, 'revocation');
    inner.payload_digest = await sha256Canonical(inner.payload);
    inner.signature_b64 = bytesToB64(new Uint8Array(await crypto.subtle.sign(
      'Ed25519', keys.privateKey, new TextEncoder().encode(canonicalSigningJson(inner)),
    )));
    const rawKey = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
    api.discoverKey.mockReturnValue(of({
      sessionId: 'session-a', pairId: 'session-a', senderId: 'bob', audienceId: 'alice', epoch: 3,
      keyId: 'speech-key', publicKeyB64: bytesToB64(rawKey), fingerprint: 'f'.repeat(64),
      consentVersion: 4, expiresAtMs: expiresAt, version: 1,
    }));
    encryption.open.mockResolvedValue({
      envelope: envelope('bob', 'alice', 3, expiresAt),
      plaintext: new TextEncoder().encode(JSON.stringify(inner)),
    });
    const ciphertext = new TextEncoder().encode('{}');
    const outer: SemanticDataChannelMessage = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'control', message_id: 'outer-3',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice', sequence: 3,
      expires_at_ms: expiresAt, compression: 'none', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: btoa(String.fromCharCode(...ciphertext)),
    };
    const received: SpeechEvidenceMessage[] = [];
    service.verifiedInbound$.subscribe(value => received.push(value));
    transport.semanticMessage$.next(outer);
    await vi.waitFor(() => expect(received).toHaveLength(1));
    expect(received[0]).toMatchObject({ message_type: 'revocation', sender_id: 'bob', sequence: 3 });
  });

  it('reports wrong-audience IDOR attempts without decrypting or exposing payload content', async () => {
    const expiresAt = Date.now() + 60_000;
    const ciphertext = new TextEncoder().encode('{}');
    const outer: SemanticDataChannelMessage = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'control', message_id: 'outer-idor',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'mallory', sequence: 4,
      expires_at_ms: expiresAt, compression: 'none', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: btoa(String.fromCharCode(...ciphertext)),
    };
    const rejected: any[] = [];
    service.verificationRejected$.subscribe(value => rejected.push(value));

    transport.semanticMessage$.next(outer);
    await vi.waitFor(() => expect(rejected).toHaveLength(1));
    expect(rejected[0]).toEqual({ messageId: 'outer-idor', reasonCode: 'speech_evidence_outer_binding_mismatch' });
    expect(encryption.open).not.toHaveBeenCalled();
  });

  it('rejects a bad Ed25519 signature after current Hub key discovery', async () => {
    const expiresAt = Date.now() + 60_000;
    const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
    const inner = evidenceMessage('bob', 'alice', 5, expiresAt, 'revocation');
    inner.payload_digest = await sha256Canonical(inner.payload);
    const rawKey = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
    api.discoverKey.mockReturnValue(of({
      sessionId: 'session-a', pairId: 'session-a', senderId: 'bob', audienceId: 'alice', epoch: 3,
      keyId: 'speech-key', publicKeyB64: bytesToB64(rawKey), fingerprint: 'f'.repeat(64),
      consentVersion: 4, expiresAtMs: expiresAt, version: 1,
    }));
    encryption.open.mockResolvedValue({
      envelope: envelope('bob', 'alice', 5, expiresAt),
      plaintext: new TextEncoder().encode(JSON.stringify(inner)),
    });
    const ciphertext = new TextEncoder().encode('{}');
    const outer: SemanticDataChannelMessage = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'control', message_id: 'outer-bad-signature',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice', sequence: 5,
      expires_at_ms: expiresAt, compression: 'none', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: btoa(String.fromCharCode(...ciphertext)),
    };
    const rejected: any[] = [];
    service.verificationRejected$.subscribe(value => rejected.push(value));

    transport.semanticMessage$.next(outer);
    await vi.waitFor(() => expect(rejected).toHaveLength(1));
    expect(rejected[0]).toEqual({ messageId: 'message-5', reasonCode: 'speech_evidence_signature_invalid' });
  });
});

function evidenceMessage(
  sender: string,
  audience: string,
  sequence: number,
  expiresAt: number,
  type: 'chunk' | 'revocation',
): SpeechEvidenceMessage {
  const payload = type === 'chunk' ? {
    traffic_class: 'evidence_bulk', offer_id: 'offer-a', group_id: 'group-a', chunk_index: 0,
    chunk_count: 1, plaintext_bytes: 1, plaintext_digest: 'b'.repeat(64), ciphertext_digest: 'c'.repeat(64),
    nonce_b64: btoa(String.fromCharCode(...new Uint8Array(12))),
    ciphertext_b64: btoa(String.fromCharCode(...new Uint8Array(17))),
  } : {
    traffic_class: 'control', revocation_id: 'revocation-a', group_ids: ['group-a'],
    scope_digest: 'd'.repeat(64), reason_code: 'user_request', revocation_epoch: 2,
    deadline_at_ms: expiresAt, requested_action: 'delete',
  };
  return {
    protocol_version: 'ananta.speech-evidence-sync.v1', message_type: type,
    message_id: `message-${sequence}`, session_id: 'session-a', pair_id: 'session-a', sender_id: sender,
    audience_id: audience, epoch: 3, sequence, consent_version: 4, key_id: 'speech-key',
    issued_at_ms: Date.now(), expires_at_ms: expiresAt, payload_digest: 'e'.repeat(64), payload,
    signature_algorithm: 'Ed25519', signature_b64: btoa(String.fromCharCode(...new Uint8Array(64))),
  };
}

function bytesToB64(value: Uint8Array): string {
  let binary = ''; for (const byte of value) binary += String.fromCharCode(byte); return btoa(binary);
}

function envelope(sender: string, recipient: string, sequence: number, expiresAt: number) {
  return {
    version: 1, scope: { kind: 'session', id: 'session-a' }, sender_id: sender,
    recipient: { kind: 'peer', id: recipient }, epoch: 3, sequence, key_id: 'pair-key',
    payload_type: 'speech_evidence', expires_at_ms: expiresAt,
    nonce_b64: btoa(String.fromCharCode(...new Uint8Array(12))),
    aad: { traffic_class: 'control', content_encoding: 'json', contract_digest: 'a'.repeat(64) },
    ciphertext_b64: btoa(String.fromCharCode(...new Uint8Array(16))),
  };
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
