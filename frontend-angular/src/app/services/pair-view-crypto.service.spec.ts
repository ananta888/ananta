import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { E2eEncryptionService } from './e2e-encryption.service';
import { PairViewCryptoService } from './pair-view-crypto.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcReplayWindowService } from './webrtc-replay-window.service';
import { SecureEnvelopeV1 } from './webrtc-secure-envelope';

const binding = {
  scopeKind: 'session' as const,
  scopeId: 'session-a',
  localPeerId: 'peer-local',
  remotePeerId: 'peer-remote',
  peerPublicKeySpkiB64: 'unused-in-test',
  epoch: 3,
  keyId: 'key-a',
  contractDigest: 'a'.repeat(64),
};

function envelope(
  payloadType: string,
  trafficClass: SecureEnvelopeV1['aad']['traffic_class'],
  contentEncoding: SecureEnvelopeV1['aad']['content_encoding'] = 'json',
): SecureEnvelopeV1 {
  return {
    version: 1,
    scope: { kind: 'session', id: 'session-a' },
    sender_id: 'peer-remote',
    recipient: { kind: 'peer', id: 'peer-local' },
    epoch: 3,
    sequence: 1,
    key_id: 'key-a',
    payload_type: payloadType,
    expires_at_ms: Date.now() + 60_000,
    nonce_b64: 'AAAAAAAAAAAAAAAA',
    aad: {
      traffic_class: trafficClass,
      content_encoding: contentEncoding,
      contract_digest: 'a'.repeat(64),
    },
    ciphertext_b64: 'AAAAAAAAAAAAAAAAAAAAAA==',
  };
}

function setup() {
  TestBed.resetTestingModule();
  const encryption = {
    open: vi.fn(async (_binding: unknown, parsed: SecureEnvelopeV1) => ({
      envelope: parsed,
      plaintext: new TextEncoder().encode('{"ok":true}'),
    })),
    seal: vi.fn(),
  };
  const peerKeys = { requireBinding: vi.fn(() => binding), clear: vi.fn() };
  const replay = { accept: vi.fn(async () => 'ok'), clearScope: vi.fn() };
  TestBed.configureTestingModule({ providers: [
    { provide: E2eEncryptionService, useValue: encryption },
    { provide: WebrtcPeerKeyService, useValue: peerKeys },
    { provide: WebrtcReplayWindowService, useValue: replay },
  ] });
  const service = TestBed.runInInjectionContext(() => new PairViewCryptoService());
  return { service, encryption, replay };
}

describe('PairViewCryptoService traffic domains', () => {
  it('rejects an outbound cross-domain payload before encryption', async () => {
    const { service, encryption } = setup();
    await expect(service.seal('{}', {
      scopeId: 'session-a', epoch: 3, sequence: 1,
      payloadType: 'pair.view_delta', trafficClass: 'control',
    })).rejects.toMatchObject({ reasonCode: 'traffic_class_mismatch' });
    expect(encryption.seal).not.toHaveBeenCalled();
  });

  it('rejects a payload-type/traffic-class mismatch before AEAD and replay state', async () => {
    const { service, encryption, replay } = setup();
    await expect(service.open(JSON.stringify(envelope('pair.cursor', 'semantic')), {
      scopeId: 'session-a', epoch: 3,
    })).rejects.toMatchObject({ reasonCode: 'traffic_class_mismatch' });
    expect(encryption.open).not.toHaveBeenCalled();
    expect(replay.accept).not.toHaveBeenCalled();
  });

  it('rejects non-JSON Pair payloads before AEAD and replay state', async () => {
    const { service, encryption, replay } = setup();
    await expect(service.open(JSON.stringify(envelope('pair.view_delta', 'semantic', 'binary')), {
      scopeId: 'session-a', epoch: 3,
    })).rejects.toMatchObject({ reasonCode: 'content_encoding_mismatch' });
    expect(encryption.open).not.toHaveBeenCalled();
    expect(replay.accept).not.toHaveBeenCalled();
  });

  it('opens an authorized JSON payload in its closed replay domain', async () => {
    const { service, encryption, replay } = setup();
    await expect(service.open(JSON.stringify(envelope('pair.cursor', 'control')), {
      scopeId: 'session-a', epoch: 3,
    })).resolves.toMatchObject({ payloadType: 'pair.cursor', senderId: 'peer-remote' });
    expect(encryption.open).toHaveBeenCalledOnce();
    expect(replay.accept).toHaveBeenCalledOnce();
  });

  it('preserves the existing strict Pair chat payload contract', async () => {
    const { service, encryption, replay } = setup();
    await expect(service.open(JSON.stringify(envelope('pair.chat_message', 'semantic')), {
      scopeId: 'session-a', epoch: 3,
    })).resolves.toMatchObject({ payloadType: 'pair.chat_message', senderId: 'peer-remote' });
    expect(encryption.open).toHaveBeenCalledOnce();
    expect(replay.accept).toHaveBeenCalledOnce();
  });
});
