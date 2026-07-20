import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of, Subject } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { LivekitSfuTransportService } from './livekit-sfu-transport.service';
import { SemanticSfuAdmissionApiService } from './semantic-sfu-admission-api.service';
import {
  SEMANTIC_MEDIA_PATH_CLOCK,
  SemanticSfuPathCoordinatorService,
} from './semantic-sfu-path-coordinator.service';
import { SFU_COOLDOWN_MS } from './semantic-media-transport-state-machine';
import { SemanticSfuPairSignalingService } from './semantic-sfu-pair-signaling.service';
import { SemanticSfuGroupKeyService } from './semantic-sfu-group-key.service';
import { WebrtcMediaSessionService } from './webrtc-media-session.service';
import { WebrtcOrdinaryHealthMonitorService } from './webrtc-ordinary-health-monitor.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';

const roomId = `sfu-${'a'.repeat(32)}`;
const context = {
  hubUrl: 'http://hub.test', tenantId: 'tenant-a', sessionId: 'session-a', membershipEpoch: 3, localPeerId: 'alice',
  remotePeerIds: ['bob'], featureEnabled: true,
};
const pair = {
  scopeId: 'session-a', epoch: 3, localPeerId: 'alice', remotePeerId: 'bob', tenantId: 'tenant-a',
  keyId: 'pair-key', confirmed: true,
};

describe('SemanticSfuPathCoordinatorService', () => {
  let coordinator: SemanticSfuPathCoordinatorService;
  let api: any;
  let sfu: any;
  let media: any;
  let key: Uint8Array;
  let track: any;
  let signaling: any;
  let groupKeys: any;
  let ordinaryHealth: any;
  let nowMs: number;

  beforeEach(() => {
    vi.stubGlobal('Worker', class {});
    nowMs = 20_000;
    track = { kind: 'audio', readyState: 'live', stop: vi.fn() };
    key = new Uint8Array(32).fill(7);
    const publication = {
      schema: 'ananta.webrtc.media-publication.v1', publication_id: 'mic-a', tenant_id: 'tenant-a',
      room_id: roomId, participant_id: 'alice', membership_epoch: 3, revision: 2,
      source: 'microphone', kind: 'audio', privacy: 'ordinary', status: 'authorized',
      audience_participant_id: null, authorized_subscriber_ids: ['bob'],
      constraints: { max_bitrate_bps: 128_000, max_width: 0, max_height: 0, max_fps: 0 },
    };
    api = {
      state: vi.fn(() => of({
        roomId, membershipEpoch: 3, revision: 0, joined: false, publications: [], subscriptions: [],
      })),
      join: vi.fn(() => of({
        serverUrl: 'wss://sfu.test', accessToken: 'jwt', expiresAt: Math.trunc(Date.now() / 1000) + 30,
        roomId, membershipEpoch: 3, revision: 1, publication: null, subscription: null,
      })),
      authorizePublication: vi.fn((_hub: string, _mutation: unknown, request: { authorizedSubscriberIds: string[] }) => of({
        serverUrl: 'wss://sfu.test', accessToken: 'jwt', expiresAt: Math.trunc(Date.now() / 1000) + 30,
        roomId, membershipEpoch: 3, revision: 2,
        publication: { ...publication, authorized_subscriber_ids: [...request.authorizedSubscriberIds].sort() },
        subscription: null,
      })),
      authorizeSubscription: vi.fn(() => of({
        serverUrl: 'wss://sfu.test', accessToken: 'jwt', expiresAt: Math.trunc(Date.now() / 1000) + 30,
        roomId, membershipEpoch: 3, revision: 2, publication: null,
        subscription: {
          schema: 'ananta.webrtc.media-subscription.v1', subscription_id: 'sub-a', tenant_id: 'tenant-a',
          room_id: roomId, subscriber_id: 'alice', publisher_id: 'bob', publication_id: 'mic-bob',
          membership_epoch: 3, revision: 2, status: 'authorized',
        },
      })),
      leave: vi.fn(() => of({ roomId, revision: 3, reasonCode: 'sfu_participant_left' })),
    };
    sfu = {
      state$: new BehaviorSubject<any>({ status: 'idle', reasonCode: 'sfu_not_requested' }),
      subscriberLost$: new Subject<any>(),
      connect: vi.fn(async () => {
        sfu.state$.next({ status: 'connected', reasonCode: 'sfu_connected' });
        return 'sfu';
      }), publish: vi.fn(async () => undefined),
      waitForSubscriber: vi.fn(async () => true),
      confirmAuthorizedSubscriber: vi.fn(),
      disconnect: vi.fn(async () => undefined),
    };
    const audioState$ = new BehaviorSubject<any>({ status: 'active' });
    media = {
      audioState$,
      requestMicrophone: vi.fn(async () => { audioState$.next({ status: 'active' }); }),
      cloneActiveMicrophoneTrack: vi.fn(() => track),
      stopAudio: vi.fn(() => audioState$.next({ status: 'idle' })),
    };
    signaling = {
      publicationHint$: new Subject<any>(), bind: vi.fn(), clear: vi.fn(),
      sendPublicationHint: vi.fn(async () => undefined),
    };
    groupKeys = {
      clear: vi.fn(), purge: vi.fn(), acknowledge: vi.fn(async () => undefined),
      receiveAvailable: vi.fn(async (_context: unknown, cursor: string) => ({ cursor, installed: null })),
      status: vi.fn(async () => ({ acknowledgedMemberIds: ['bob', 'carol'], pendingMemberIds: [] })),
      createPublisherEpoch: vi.fn(async (_context: unknown, publicationId: string, members: string[]) => ({
        authorization: {
          version: 1, authorization_id: 'group-auth', tenant_id: 'tenant-a', room_id: roomId,
          publication_id: publicationId, epoch: 1, previous_epoch: 0,
          member_set_digest: 'a'.repeat(64), member_ids: members,
          key_package_refs: Object.fromEntries(members.map(member => [member, `pkg-${member}`])),
          valid_from_ms: Date.now(), expires_at_ms: Date.now() + 60_000,
          rekey_deadline_ms: Date.now() + 10_000, reason: 'create', hub_key_id: 'hub',
          membership_epoch: 3, signature_b64: 'a'.repeat(88),
        },
        keyMaterial: new Uint8Array(32).fill(9),
      })),
    };
    ordinaryHealth = {
      requireReady: vi.fn(async () => undefined),
      reset: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SemanticSfuPathCoordinatorService,
      { provide: SemanticSfuAdmissionApiService, useValue: api },
      { provide: WebrtcPeerKeyService, useValue: { requireBinding: () => pair } },
      { provide: E2eEncryptionService, useValue: { derivePurposeKeyMaterial: vi.fn(async () => key) } },
      { provide: WebrtcMediaSessionService, useValue: media },
      { provide: LivekitSfuTransportService, useValue: sfu },
      { provide: SemanticSfuPairSignalingService, useValue: signaling },
      { provide: SemanticSfuGroupKeyService, useValue: groupKeys },
      { provide: WebrtcOrdinaryHealthMonitorService, useValue: ordinaryHealth },
      { provide: SEMANTIC_MEDIA_PATH_CLOCK, useValue: () => nowMs },
    ] });
    coordinator = TestBed.inject(SemanticSfuPathCoordinatorService);
    coordinator.bind(context);
  });

  afterEach(() => {
    coordinator.ngOnDestroy();
    vi.unstubAllGlobals();
  });

  it('switches a confirmed pair microphone only after Hub join and publication CAS admission', async () => {
    const result = await coordinator.switchReceiver('bob', 'sfu');
    expect(ordinaryHealth.requireReady).toHaveBeenCalledWith(
      'session-a', 'bob', expect.any(Function),
    );
    expect(api.state).toHaveBeenCalledWith('http://hub.test', 'session-a', 3);
    expect(api.join).toHaveBeenCalledWith(
      'http://hub.test', expect.objectContaining({ expectedRevision: 0 }), true,
    );
    expect(api.authorizePublication).toHaveBeenCalledWith(
      'http://hub.test', expect.objectContaining({ expectedRevision: 1 }),
      expect.objectContaining({ source: 'microphone', authorizedSubscriberIds: ['bob'] }),
    );
    expect(sfu.connect).toHaveBeenCalledWith(
      expect.objectContaining({ strict_e2ee: true, subscription_publication_ids: [] }),
      expect.any(Uint8Array), 'supported',
    );
    expect(sfu.publish).toHaveBeenCalledWith(expect.stringMatching(/^mic-3-/), track);
    expect(signaling.sendPublicationHint).toHaveBeenCalledWith(
      expect.stringMatching(/^mic-3-/), roomId, expect.any(Number),
    );
    expect(sfu.waitForSubscriber).toHaveBeenCalledWith(expect.stringMatching(/^mic-3-/), 'bob');
    expect(media.stopAudio).toHaveBeenCalledWith('sfu_path_transition_connecting');
    expect(media.stopAudio.mock.invocationCallOrder[0]).toBeLessThan(sfu.publish.mock.invocationCallOrder[0]);
    expect(coordinator.transportState$.value).toMatchObject({
      mode: 'sfu_active', ordinaryBulkEnabled: false, sfuBulkEnabled: true,
    });
    expect(key.every(value => value === 0)).toBe(true);
    expect(result).toEqual({ effectivePath: 'sfu', reasonCode: 'receiver_path_sfu_admitted' });
    sfu.subscriberLost$.next({ receiverId: 'bob' });
    await vi.waitFor(() => expect(media.requestMicrophone).toHaveBeenCalled());
    expect(sfu.disconnect).toHaveBeenCalledWith('sfu_subscriber_fallback');
  });

  it('never starts Hub/SFU admission while the Ordinary fallback is unhealthy', async () => {
    ordinaryHealth.requireReady.mockRejectedValueOnce(new Error('ordinary_fallback_not_healthy'));
    await expect(coordinator.switchReceiver('bob', 'sfu')).rejects.toThrow(
      'ordinary_fallback_not_healthy',
    );
    expect(api.state).not.toHaveBeenCalled();
    expect(api.join).not.toHaveBeenCalled();
    expect(sfu.connect).not.toHaveBeenCalled();
    expect(media.stopAudio).not.toHaveBeenCalled();
  });

  it('uses one common group publication while receiver ACKs and Ordinary fallback stay independent', async () => {
    coordinator.bind({ ...context, remotePeerIds: ['bob', 'carol'] });
    const bob = await coordinator.switchReceiver('bob', 'sfu');
    expect(bob.effectivePath).toBe('sfu');
    expect(api.authorizePublication).toHaveBeenLastCalledWith(
      'http://hub.test', expect.anything(), expect.objectContaining({ authorizedSubscriberIds: ['bob'] }),
    );
    expect(media.stopAudio).not.toHaveBeenCalledWith('sfu_group_path_active');

    const carol = await coordinator.switchReceiver('carol', 'sfu');
    expect(carol.effectivePath).toBe('sfu');
    expect(api.authorizePublication).toHaveBeenLastCalledWith(
      'http://hub.test', expect.anything(), expect.objectContaining({ authorizedSubscriberIds: ['bob', 'carol'] }),
    );
    expect(groupKeys.createPublisherEpoch).toHaveBeenLastCalledWith(
      expect.objectContaining({ tenantId: 'tenant-a' }), expect.stringMatching(/^mic-3-/), ['alice', 'bob', 'carol'],
    );
    expect(sfu.publish).toHaveBeenCalledTimes(2);
    expect(sfu.confirmAuthorizedSubscriber).toHaveBeenCalledWith(expect.stringMatching(/^mic-3-/), 'bob');
    expect(sfu.confirmAuthorizedSubscriber).toHaveBeenCalledWith(expect.stringMatching(/^mic-3-/), 'carol');
    expect(media.stopAudio).toHaveBeenCalledWith('sfu_group_path_transition_connecting');
  });

  it('turns a pair hint into a Hub-authorized subscription before connecting', async () => {
    signaling.publicationHint$.next({
      schema: 'ananta.semantic-sfu-pair-signal.v1', kind: 'publication_hint', signal_id: 'signal-a',
      session_id: 'session-a', epoch: 3, sender_id: 'bob', audience_id: 'alice',
      publication_id: 'mic-bob', room_id: roomId, expires_at_ms: Date.now() + 30_000,
    });
    await vi.waitFor(() => expect(api.authorizeSubscription).toHaveBeenCalled());
    expect(api.authorizeSubscription).toHaveBeenCalledWith(
      'http://hub.test', expect.objectContaining({ expectedRevision: 1 }),
      expect.stringMatching(/^sub-3-/), 'mic-bob',
    );
    expect(sfu.connect).toHaveBeenCalledWith(
      expect.objectContaining({ publications: [], subscription_publication_ids: ['mic-bob'] }),
      expect.any(Uint8Array), 'supported',
    );
  });

  it('keeps unknown E2EE capability on ordinary without starting Hub or SFU work', async () => {
    vi.stubGlobal('Worker', undefined);
    const result = await coordinator.switchReceiver('bob', 'sfu');
    expect(result).toEqual({ effectivePath: 'ordinary', reasonCode: 'capability_unknown' });
    expect(api.state).not.toHaveBeenCalled();
    expect(sfu.connect).not.toHaveBeenCalled();
    expect(coordinator.transportState$.value).toMatchObject({
      mode: 'ordinary', ordinaryBulkEnabled: true, sfuBulkEnabled: false,
    });
  });

  it('serializes duplicate/churn intents and enforces fallback cooldown before re-admission', async () => {
    const first = await coordinator.switchReceiver('bob', 'sfu');
    const publishCalls = sfu.publish.mock.calls.length;
    const duplicate = await coordinator.switchReceiver('bob', 'sfu');
    expect(first.effectivePath).toBe('sfu');
    expect(duplicate.reasonCode).toBe('receiver_path_sfu_already_active');
    expect(sfu.publish).toHaveBeenCalledTimes(publishCalls);

    sfu.state$.next({ status: 'fallback', reasonCode: 'sfu_disconnected' });
    await vi.waitFor(() => expect(coordinator.transportState$.value.mode).toBe('ordinary_cooldown'));
    const stateCalls = api.state.mock.calls.length;
    const cooled = await coordinator.switchReceiver('bob', 'sfu');
    expect(cooled).toEqual({ effectivePath: 'ordinary', reasonCode: 'sfu_cooldown_active' });
    expect(api.state).toHaveBeenCalledTimes(stateCalls);

    nowMs += SFU_COOLDOWN_MS + 100;
    const retried = await coordinator.switchReceiver('bob', 'sfu');
    expect(retried.effectivePath).toBe('sfu');
  });

  it('normalizes reordered and duplicate membership snapshots without a topology restart', async () => {
    coordinator.bind({ ...context, remotePeerIds: ['carol', 'bob', 'bob'] });
    await Promise.resolve();
    const disconnects = sfu.disconnect.mock.calls.length;
    coordinator.bind({ ...context, remotePeerIds: ['bob', 'carol'] });
    await Promise.resolve();
    expect(sfu.disconnect).toHaveBeenCalledTimes(disconnects);
  });

  it('finishes context cleanup before admitting a publication for the replacement context', async () => {
    await coordinator.switchReceiver('bob', 'ordinary');
    let releaseCleanup!: () => void;
    sfu.disconnect.mockImplementationOnce(() => new Promise<void>(resolve => { releaseCleanup = resolve; }));
    const joinCalls = api.join.mock.calls.length;
    coordinator.bind({ ...context, hubUrl: 'http://replacement-hub.test' });

    const admission = coordinator.switchReceiver('bob', 'sfu');
    await Promise.resolve();
    await Promise.resolve();
    expect(api.join).toHaveBeenCalledTimes(joinCalls);

    releaseCleanup();
    await expect(admission).resolves.toMatchObject({ effectivePath: 'sfu' });
    expect(api.join).toHaveBeenCalledTimes(joinCalls + 1);
  });
});
