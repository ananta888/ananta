import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SemanticSfuAdmissionApiService,
  parseSemanticSfuState,
  parseSemanticSfuToken,
} from './semantic-sfu-admission-api.service';

const roomId = `sfu-${'a'.repeat(32)}`;
const publication = {
  schema: 'ananta.webrtc.media-publication.v1', publication_id: 'mic-a', tenant_id: 'tenant-a',
  room_id: roomId, participant_id: 'alice', membership_epoch: 3, revision: 2,
  source: 'microphone', kind: 'audio', privacy: 'ordinary', status: 'authorized',
  audience_participant_id: null, authorized_subscriber_ids: ['bob'],
  constraints: { max_bitrate_bps: 128_000, max_width: 0, max_height: 0, max_fps: 0 },
};

describe('SemanticSfuAdmissionApiService', () => {
  it('strictly parses caller-bounded state and token projections', () => {
    expect(parseSemanticSfuState({
      ok: true, room_id: roomId, membership_epoch: 3, revision: 0,
      joined: false, publications: [], subscriptions: [],
    })).toMatchObject({ roomId, membershipEpoch: 3, revision: 0, joined: false });
    expect(parseSemanticSfuToken({
      ok: true, server_url: 'wss://sfu.test', access_token: 'jwt', expires_at: 1_900_000_000,
      room_id: roomId, livekit_identity: 'lk_alice', membership_epoch: 3, revision: 2, publication,
      authorized_subscriber_livekit_identities: { bob: 'lk_bob' },
    })).toMatchObject({
      livekitIdentity: 'lk_alice',
      authorizedSubscriberLivekitIdentities: { bob: 'lk_bob' },
      publication: { authorized_subscriber_ids: ['bob'] },
    });
    expect(() => parseSemanticSfuState({
      ok: true, room_id: roomId, membership_epoch: 3, revision: 0,
      joined: false, publications: [], subscriptions: [], participants: ['mallory'],
    })).toThrow('sfu_state_response_invalid');
  });

  it('sends explicit idempotency and CAS fields to the Hub publication endpoint', () => {
    const request = vi.fn(() => of({
      ok: true, server_url: 'wss://sfu.test', access_token: 'jwt', expires_at: 1_900_000_000,
      room_id: roomId, membership_epoch: 3, revision: 2, publication,
    }));
    TestBed.configureTestingModule({ providers: [
      SemanticSfuAdmissionApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    const api = TestBed.inject(SemanticSfuAdmissionApiService);
    api.authorizePublication('http://hub.test', {
      sessionId: 'session-a', membershipEpoch: 3, expectedRevision: 1, idempotencyKey: 'publish-a',
    }, {
      publicationId: 'mic-a', source: 'microphone', kind: 'audio', privacy: 'ordinary',
      audienceParticipantId: null, authorizedSubscriberIds: ['bob'], constraints: publication.constraints,
    }).subscribe();
    expect(request).toHaveBeenCalledWith(
      'POST', 'http://hub.test/v1/semantic-media/sfu/admissions/publications', 'http://hub.test',
      { body: expect.objectContaining({
        session_id: 'session-a', membership_epoch: 3, expected_revision: 1,
        idempotency_key: 'publish-a', authorized_subscriber_ids: ['bob'],
      }) },
    );
  });
});
