import { describe, expect, it } from 'vitest';

import { WebrtcReplayWindowService } from './webrtc-replay-window.service';
import { SecureEnvelopeV1 } from './webrtc-secure-envelope';

function envelope(sequence: number, epoch = 3): SecureEnvelopeV1 {
  return {
    version: 1, scope: { kind: 'session', id: 's1' }, sender_id: 'alice',
    recipient: { kind: 'peer', id: 'bob' }, epoch, sequence, key_id: 'key',
    payload_type: 'pair.view_delta', expires_at_ms: Date.now() + 1000,
    nonce_b64: 'AAAAAAAAAAAAAAAA',
    aad: { traffic_class: 'semantic', content_encoding: 'json', contract_digest: '0'.repeat(64) },
    ciphertext_b64: 'AAAAAAAAAAAAAAAAAAAAAA==',
  };
}

describe('WebrtcReplayWindowService', () => {
  it('separates sender/traffic windows and rejects duplicate, old, ahead and epoch mismatch', () => {
    const service = new WebrtcReplayWindowService();
    const context = { scopeId: 's1', epoch: 3, authenticatedSenderId: 'alice', localPeerId: 'bob' };
    expect(service.accept(envelope(1), context)).toBe('ok');
    expect(service.accept(envelope(1), context)).toBe('sequence_duplicate');
    expect(service.accept(envelope(5000), context)).toBe('sequence_too_far_ahead');
    expect(service.accept(envelope(2, 2), context)).toBe('epoch_stale');
    expect(service.accept(envelope(2, 4), context)).toBe('epoch_future');
    expect(service.accept(envelope(2), { ...context, authenticatedSenderId: 'mallory' })).toBe('sender_mismatch');
  });

  it('clears all state for a finished scope idempotently', () => {
    const service = new WebrtcReplayWindowService();
    const context = { scopeId: 's1', epoch: 3, authenticatedSenderId: 'alice', localPeerId: 'bob' };
    expect(service.accept(envelope(1), context)).toBe('ok');
    service.clearScope('s1');
    service.clearScope('s1');
    expect(service.accept(envelope(1), context)).toBe('ok');
  });
});
