import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { SpeechEvidenceSyncApiService } from './speech-evidence-sync-api.service';
import { SpeechEvidenceMessage } from './speech-evidence-sync.validators';

describe('SpeechEvidenceSyncApiService', () => {
  const calls: Array<{ method: string; url: string; options: any }> = [];
  const core = {
    request: vi.fn((method: string, url: string, _base: string, options?: any) => {
      calls.push({ method, url, options });
      if (url.includes('/keys')) return of({ ok: true, data: { key: keyRecord() } });
      if (url.includes('/consents/current')) {
        return of({ ok: true, data: {
          local: consentAuthority('alice', '3'), remote: consentAuthority('bob', '4'),
        } });
      }
      if (method === 'GET' && url.includes('/speech-evidence-sync/offers?')) {
        return of({ ok: true, data: { offers: [offer()] } });
      }
      if (url.endsWith('/transfers/chunks')) return of({ ok: true, data: { transfer: transfer(), relay: {} } });
      if (url.endsWith('/transfers/acks') || url.includes('/transfers/')) {
        return of({ ok: true, data: { transfer: transfer() } });
      }
      return of({ ok: true, data: { offer: offer() } });
    }),
  };
  let api: SpeechEvidenceSyncApiService;

  beforeEach(() => {
    calls.length = 0; vi.clearAllMocks();
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceSyncApiService,
      { provide: HubApiCoreService, useValue: core },
    ] });
    api = TestBed.inject(SpeechEvidenceSyncApiService);
  });

  it('registers and discovers immutable current-epoch verification keys with closed bindings', async () => {
    const registered = await firstValueFrom(api.registerKey('http://hub.test/', {
      sessionId: 'session-a', pairId: 'session-a', audienceId: 'bob', epoch: 3,
      consentVersion: 4, keyId: 'speech-key', publicKeyB64: btoa('x'.repeat(32)),
      expiresAtMs: Date.now() + 60_000,
    }));
    const discovered = await firstValueFrom(api.discoverKey('http://hub.test', {
      sessionId: 'session-a', pairId: 'session-a', senderId: 'alice', epoch: 3, keyId: 'speech-key',
    }));

    expect(registered).toMatchObject({ sessionId: 'session-a', senderId: 'alice', consentVersion: 4 });
    expect(discovered.keyId).toBe('speech-key');
    expect(calls[0]).toMatchObject({
      method: 'POST', url: 'http://hub.test/v1/voice/speech-evidence-sync/keys',
      options: { body: expect.objectContaining({ audience_id: 'bob', epoch: 3, consent_version: 4 }) },
    });
    expect(calls[1].url).toBe(
      'http://hub.test/v1/voice/speech-evidence-sync/keys/speech-key'
      + '?session_id=session-a&pair_id=session-a&sender_id=alice&epoch=3',
    );
  });

  it('binds proposal, acceptance, transfer authorization, chunks, ACK, status and invalidation routes', async () => {
    const proposal = evidence('offer', 'proposal');
    const acceptance = evidence('offer', 'acceptance');
    const chunk = evidence('chunk');
    const ack = evidence('chunk_ack');
    const outer = {
      version: 'ananta.webrtc-datachannel.v1', traffic_class: 'evidence_bulk', message_id: 'outer-a',
      session_id: 'session-a', epoch: 3, sender_id: 'alice', audience_id: 'bob', sequence: 3,
      expires_at_ms: chunk.expires_at_ms, compression: 'none',
      security: { algorithm: 'AES-GCM-256', key_id: 'pair-key' }, payload_bytes: 16,
      payload_digest: '9'.repeat(64), ciphertext: btoa('x'.repeat(16)),
    } as const;

    await firstValueFrom(api.propose('http://hub.test', proposal));
    await firstValueFrom(api.accept('http://hub.test', acceptance));
    await firstValueFrom(api.authorizeTransfer('http://hub.test', 'offer-a'));
    await firstValueFrom(api.appendChunk('http://hub.test', chunk, outer));
    await firstValueFrom(api.acknowledgeChunk('http://hub.test', ack));
    await firstValueFrom(api.transferStatus('http://hub.test', 'offer-a', 'group-a'));
    await firstValueFrom(api.invalidate('http://hub.test', 'offer-a', 'consent_revoked'));

    expect(calls.map(value => `${value.method} ${value.url}`)).toEqual([
      'POST http://hub.test/v1/voice/speech-evidence-sync/offers/proposals',
      'POST http://hub.test/v1/voice/speech-evidence-sync/offers/acceptances',
      'POST http://hub.test/v1/voice/speech-evidence-sync/offers/offer-a/authorize-transfer',
      'POST http://hub.test/v1/voice/speech-evidence-sync/transfers/chunks',
      'POST http://hub.test/v1/voice/speech-evidence-sync/transfers/acks',
      'GET http://hub.test/v1/voice/speech-evidence-sync/offers/offer-a/transfers/group-a',
      'POST http://hub.test/v1/voice/speech-evidence-sync/offers/offer-a/invalidate',
    ]);
    expect(calls[3].options.body).toEqual({ message: chunk, relay_envelope: outer });
    expect(calls[6].options.body).toEqual({ reason_code: 'consent_revoked' });
  });

  it('lists only the explicitly bound current pair and epoch read model', async () => {
    const offers = await firstValueFrom(api.listOffers('http://hub.test/', {
      sessionId: 'session-a', pairId: 'session-a', epoch: 3,
    }));

    expect(offers).toHaveLength(1);
    expect(offers[0]).toMatchObject({
      offerId: 'offer-a', senderId: 'alice', recipientId: 'bob', groupIds: ['group-a'], epoch: 3,
    });
    expect(calls).toEqual([expect.objectContaining({
      method: 'GET',
      url: 'http://hub.test/v1/voice/speech-evidence-sync/offers'
        + '?session_id=session-a&pair_id=session-a&epoch=3',
    })]);
  });

  it('discovers content-free bilateral consent authority in the exact membership epoch', async () => {
    const pair = await firstValueFrom(api.currentConsentPair('http://hub.test', {
      sessionId: 'session-a', pairId: 'session-a', remotePeerId: 'bob', epoch: 3,
    }));

    expect(pair.local).toMatchObject({ peerId: 'alice', digest: '3'.repeat(64), version: 4 });
    expect(pair.remote).toMatchObject({ peerId: 'bob', digest: '4'.repeat(64), version: 4 });
    expect(calls[0].url).toBe(
      'http://hub.test/v1/voice/speech-evidence-sync/consents/current'
      + '?session_id=session-a&pair_id=session-a&remote_peer_id=bob&epoch=3',
    );
  });
});

function consentAuthority(peerId: string, digestValue: string) {
  return {
    peer_id: peerId, pair_id: 'session-a', version: 4, digest: digestValue.repeat(64),
    directions: ['sender_to_receiver'], purposes: ['curation'], data_classes: ['transcript', 'text_corrections'],
    fields: ['transcript'], trainer_classes: ['none'], maximum_retention_seconds: 3600,
    expires_at_ms: Date.now() + 60_000,
  };
}

function keyRecord() {
  return {
    session_id: 'session-a', pair_id: 'session-a', sender_id: 'alice', audience_id: 'bob', epoch: 3,
    key_id: 'speech-key', public_key_b64: btoa('x'.repeat(32)), fingerprint: 'f'.repeat(64),
    consent_version: 4, expires_at_ms: Date.now() + 60_000, version: 1,
  };
}

function offer() {
  return {
    offer_id: 'offer-a', proposal_verification_digest: '1'.repeat(64), acceptance_verification_digest: null,
    session_id: 'session-a', pair_id: 'session-a', epoch: 3, sender_id: 'alice', recipient_id: 'bob',
    inventory_root_digest: '2'.repeat(64), direction: 'sender_to_receiver', purpose: 'curation',
    data_classes: ['transcript'], fields: ['text'], retention_seconds: 60, trainer_class: 'none',
    group_ids: ['group-a'], group_previews: [preview()], group_preview_digest: '0'.repeat(64),
    total_bytes: 1, sender_consent_digest: '3'.repeat(64),
    recipient_consent_digest: '4'.repeat(64), scope_digest: '5'.repeat(64),
    expires_at_ms: Date.now() + 60_000, state: 'proposed', transfer_started: false,
    invalidation_reason: null, version: 1,
    protocol_version: 'ananta.speech-evidence-sync.v2',
  };
}

function transfer() {
  return {
    offer_id: 'offer-a', group_id: 'group-a', state: 'active', chunk_count: 1,
    acknowledged_chunks: 0, first_missing_index: 0, received_bytes: 1, in_flight_bytes: 1,
    expires_at_ms: Date.now() + 60_000, reason_code: null, version: 1,
  };
}

function evidence(type: 'offer' | 'chunk' | 'chunk_ack', stage = ''): SpeechEvidenceMessage {
  const expiresAt = Date.now() + 60_000;
  const payload: Record<string, unknown> = type === 'offer' ? {
    traffic_class: 'control', offer_id: 'offer-a', stage, inventory_root_digest: '2'.repeat(64),
    direction: 'sender_to_receiver', purpose: 'curation', data_classes: ['transcript'], fields: ['text'],
    retention_seconds: 60, trainer_class: 'none', group_ids: ['group-a'], group_previews: [preview()], total_bytes: 1,
    sender_consent_digest: '3'.repeat(64), recipient_consent_digest: '4'.repeat(64), scope_digest: '5'.repeat(64),
  } : type === 'chunk' ? {
    traffic_class: 'evidence_bulk', offer_id: 'offer-a', group_id: 'group-a', chunk_index: 0,
    chunk_count: 1, plaintext_bytes: 1, plaintext_digest: '6'.repeat(64), ciphertext_digest: '7'.repeat(64),
    nonce_b64: btoa('\0'.repeat(12)), ciphertext_b64: btoa('\0'.repeat(17)),
  } : {
    traffic_class: 'control', offer_id: 'offer-a', group_id: 'group-a', acknowledged_indices: [0],
    first_missing_index: 1, received_bytes: 1, complete: true,
  };
  return {
    protocol_version: type === 'offer' ? 'ananta.speech-evidence-sync.v2' : 'ananta.speech-evidence-sync.v1',
    message_type: type, message_id: `message-${type}`,
    session_id: 'session-a', pair_id: 'session-a', sender_id: 'alice', audience_id: 'bob', epoch: 3,
    sequence: type === 'offer' ? 1 : type === 'chunk' ? 2 : 3, consent_version: 4, key_id: 'speech-key',
    issued_at_ms: Date.now(), expires_at_ms: expiresAt, payload_digest: '8'.repeat(64), payload,
    signature_algorithm: 'Ed25519', signature_b64: btoa('\0'.repeat(64)),
  };
}

function preview() {
  const candidateDigest = 'e'.repeat(64);
  return {
    preview_version: 'ananta.speech-evidence-group-preview.v1', group_id: 'group-a',
    source_group_digest: 'a'.repeat(64), speaker_scope_digest: 'b'.repeat(64), quality_basis: 'policy',
    quality_digest: 'c'.repeat(64), resolution_digest: 'd'.repeat(64),
    original_candidates: [{
      ordinal: 1, candidate_digest: candidateDigest, authority_digest: 'f'.repeat(64), revision: 1,
    }],
    resolution_state: 'resolved', selected_candidate_digest: candidateDigest,
    unresolved_region_digests: [], comparison_digest: '9'.repeat(64),
    revision: 1, size_bytes: 1,
  };
}
