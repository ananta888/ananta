import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { IndexedDbE2eReplayStore, PAIR_REPLAY_WINDOW_STORE } from './e2e-replay.store';
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
  it('separates sender/traffic windows and rejects duplicate, old, ahead and epoch mismatch', async () => {
    const service = serviceWith(new IndexedDbE2eReplayStore());
    const context = { scopeId: 's1', epoch: 3, authenticatedSenderId: 'alice', localPeerId: 'bob' };
    expect(await service.accept(envelope(1), context)).toBe('ok');
    expect(await service.accept(envelope(1), context)).toBe('sequence_duplicate');
    expect(await service.accept(envelope(5000), context)).toBe('sequence_too_far_ahead');
    expect(await service.accept(envelope(2, 2), context)).toBe('epoch_stale');
    expect(await service.accept(envelope(2, 4), context)).toBe('epoch_future');
    expect(await service.accept(
      envelope(2), { ...context, authenticatedSenderId: 'mallory' },
    )).toBe('sender_mismatch');
  });

  it('does not clear an active epoch when a client merely unbinds', async () => {
    const store = new IndexedDbE2eReplayStore();
    const service = serviceWith(store);
    const context = { scopeId: 's1', epoch: 3, authenticatedSenderId: 'alice', localPeerId: 'bob' };
    expect(await service.accept(envelope(1), context)).toBe('ok');
    service.clearScope('s1');
    service.clearScope('s1');
    expect(await serviceWith(new IndexedDbE2eReplayStore()).accept(envelope(1), context))
      .toBe('sequence_duplicate');
  });

  it('fails closed when persistent replay state is unavailable', async () => {
    const failed = serviceWith({
      claimSequence: async () => { throw new Error('indexeddb_unavailable'); },
    });
    const context = { scopeId: 's1', epoch: 3, authenticatedSenderId: 'alice', localPeerId: 'bob' };
    expect(await failed.accept(envelope(1), context)).toBe('replay_store_failed');
  });
});

function serviceWith(store: { claimSequence: IndexedDbE2eReplayStore['claimSequence'] }): WebrtcReplayWindowService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    WebrtcReplayWindowService,
    { provide: PAIR_REPLAY_WINDOW_STORE, useValue: store },
  ] });
  return TestBed.inject(WebrtcReplayWindowService);
}
