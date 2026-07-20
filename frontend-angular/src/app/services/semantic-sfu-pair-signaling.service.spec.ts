import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { SemanticSfuPairSignalingService } from './semantic-sfu-pair-signaling.service';
import { SemanticDataChannelMessage } from './webrtc-datachannel.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcTransportService } from './webrtc-transport.service';

const roomId = `sfu-${'a'.repeat(32)}`;
const binding = {
  scopeKind: 'session' as const, scopeId: 'session-a', localPeerId: 'alice', remotePeerId: 'bob',
  peerPublicKeySpkiB64: 'unused', epoch: 3, keyId: 'pair-key', contractDigest: 'a'.repeat(64), confirmed: true,
};

describe('SemanticSfuPairSignalingService', () => {
  let service: SemanticSfuPairSignalingService;
  let transport: any;
  let encryption: any;

  beforeEach(() => {
    transport = {
      mode$: new BehaviorSubject('webrtc'), semanticMessage$: new Subject<SemanticDataChannelMessage>(),
      setSemanticEpoch: vi.fn(), enableSemanticTraffic: vi.fn(), sendSemantic: vi.fn(async () => ({})),
    };
    encryption = {
      seal: vi.fn(async (_pair, _bytes, options) => envelope('alice', 'bob', options.sequence, options.expiresAtMs)),
      open: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SemanticSfuPairSignalingService,
      { provide: WebrtcTransportService, useValue: transport },
      { provide: WebrtcPeerKeyService, useValue: { requireBinding: () => binding } },
      { provide: E2eEncryptionService, useValue: encryption },
    ] });
    service = TestBed.inject(SemanticSfuPairSignalingService);
    service.bind();
  });

  afterEach(() => service.ngOnDestroy());

  it('sends only a pair-encrypted publication hint and no token or authority claim', async () => {
    const expiresAt = Date.now() + 30_000;
    await service.sendPublicationHint('mic-alice', roomId, expiresAt);
    const plaintext = encryption.seal.mock.calls[0][1] as Uint8Array;
    const hint = JSON.parse(new TextDecoder().decode(plaintext));
    expect(hint).toMatchObject({
      kind: 'publication_hint', publication_id: 'mic-alice', sender_id: 'alice', audience_id: 'bob',
    });
    expect(JSON.stringify(hint)).not.toMatch(/token|authorized|permission/i);
    expect(transport.sendSemantic).toHaveBeenCalledWith(
      expect.objectContaining({ traffic_class: 'control', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' } }),
      expect.any(Object),
    );
  });

  it('accepts an inbound hint only after pair AEAD context validation', async () => {
    const expiresAt = Date.now() + 30_000;
    const hint = {
      schema: 'ananta.semantic-sfu-pair-signal.v1', kind: 'publication_hint', signal_id: 'signal-bob',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice',
      publication_id: 'mic-bob', room_id: roomId, expires_at_ms: expiresAt,
    };
    encryption.open.mockResolvedValue({
      envelope: envelope('bob', 'alice', 2, expiresAt),
      plaintext: new TextEncoder().encode(JSON.stringify(hint)),
    });
    const ciphertext = new TextEncoder().encode('{}');
    const outer: SemanticDataChannelMessage = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'control', message_id: 'signal-bob',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice', sequence: 2,
      expires_at_ms: expiresAt, compression: 'none', security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: btoa(String.fromCharCode(...ciphertext)),
    };
    const received: any[] = [];
    service.publicationHint$.subscribe(value => received.push(value));
    transport.semanticMessage$.next(outer);
    await vi.waitFor(() => expect(received).toHaveLength(1));
    expect(received[0].publication_id).toBe('mic-bob');
  });
});

function envelope(sender: string, recipient: string, sequence: number, expiresAt: number) {
  return {
    version: 1, scope: { kind: 'session', id: 'session-a' }, sender_id: sender,
    recipient: { kind: 'peer', id: recipient }, epoch: 3, sequence, key_id: 'pair-key',
    payload_type: 'semantic_sfu_control', expires_at_ms: expiresAt,
    nonce_b64: btoa(String.fromCharCode(...new Uint8Array(12))),
    aad: { traffic_class: 'control', content_encoding: 'json', contract_digest: 'a'.repeat(64) },
    ciphertext_b64: btoa(String.fromCharCode(...new Uint8Array(16))),
  };
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
