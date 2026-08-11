import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { WEBRTC_MEDIA_DEVICES, WebrtcMediaSessionService } from './webrtc-media-session.service';
import { PeerState, WebrtcSessionService } from './webrtc-session.service';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PublicPairMediaPublicationConsentService } from './public-pair-media-publication-consent.service';

class FakeTrack {
  id: string; kind = 'audio'; label = 'Explicit microphone'; enabled = true; onended: (() => void) | null = null;
  readyState: MediaStreamTrackState = 'live';
  stops = 0;
  constructor(id: string) { this.id = id; }
  stop(): void { this.stops += 1; this.readyState = 'ended'; }
  clone(): MediaStreamTrack { return new FakeTrack(`${this.id}-clone`) as unknown as MediaStreamTrack; }
}
class FakeStream {
  constructor(readonly track: FakeTrack) {}
  getAudioTracks(): MediaStreamTrack[] { return [this.track as unknown as MediaStreamTrack]; }
  getTracks(): MediaStreamTrack[] { return this.getAudioTracks(); }
}

describe('WebrtcMediaSessionService', () => {
  let service: WebrtcMediaSessionService;
  let tracks: FakeTrack[];
  let peer: any;
  let devices: any;
  let dispatchDeviceChange: () => void;
  const mediaPolicy = { assertAllowed: vi.fn() };
  const publicationPolicy = { assertAllowed: vi.fn() };
  const mediaE2eeStatus = new BehaviorSubject({ sessionId: '', state: 'inactive' as const });
  const publicationConsentState = new BehaviorSubject<any>({ status: 'unbound', binding: null });

  beforeEach(() => {
    tracks = [];
    mediaPolicy.assertAllowed.mockReset();
    publicationPolicy.assertAllowed.mockReset();
    publicationConsentState.next({ status: 'unbound', binding: null });
    peer = {
      state$: new BehaviorSubject<PeerState>('connected'), sessionStarted$: new Subject<string>(),
      remoteTrack$: new Subject<RTCTrackEvent>(), sender: { track: null }, addMediaTrack: vi.fn((track: MediaStreamTrack) => {
        peer.sender.track = track; return peer.sender;
      }),
      attachMediaTrack: vi.fn(async (_slot: string, track: MediaStreamTrack) => {
        peer.sender.track = track; return peer.sender;
      }),
      publicMediaSlotForReceiver: vi.fn(() => null),
      replaceMediaTrack: vi.fn(async (sender: any, track: MediaStreamTrack | null) => { sender.track = track; }),
      removeMediaSender: vi.fn(), restartMediaIce: vi.fn(),
    };
    let deviceChange: (() => void) | undefined;
    devices = {
      getUserMedia: vi.fn(async () => {
        const track = new FakeTrack(`track-${tracks.length + 1}`); tracks.push(track); return new FakeStream(track);
      }),
      addEventListener: vi.fn((kind: string, listener: () => void) => {
        if (kind === 'devicechange') deviceChange = listener;
      }),
      removeEventListener: vi.fn(),
    };
    dispatchDeviceChange = () => deviceChange?.();
    TestBed.configureTestingModule({ providers: [
      WebrtcMediaSessionService,
      { provide: WebrtcSessionService, useValue: peer },
      { provide: PairOrdinaryMediaPolicy, useValue: mediaPolicy },
      { provide: PairMediaPublicationPolicy, useValue: publicationPolicy },
      { provide: PairMediaE2eeCoordinatorService, useValue: { status$: mediaE2eeStatus } },
      { provide: PublicPairMediaPublicationConsentService, useValue: { state$: publicationConsentState } },
      { provide: WEBRTC_MEDIA_DEVICES, useValue: devices },
    ] });
    service = TestBed.inject(WebrtcMediaSessionService);
  });

  afterEach(() => service.ngOnDestroy());

  it('keeps data-channel-only sessions compatible until explicit microphone permission', () => {
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
    expect(service.audioState$.value.status).toBe('idle');
  });

  it('denies public capture before permission and drops a public remote track', async () => {
    peer.sessionStarted$.next('public-session');
    publicationPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_ordinary_media_e2ee_unavailable');
    });
    mediaPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_ordinary_media_e2ee_unavailable');
    });

    await expect(service.requestMicrophone())
      .rejects.toThrow('public_ordinary_media_e2ee_unavailable');
    expect(devices.getUserMedia).not.toHaveBeenCalled();
    const remote = new FakeTrack('public-remote');
    peer.remoteTrack$.next({ track: remote, streams: [] } as unknown as RTCTrackEvent);
    expect(remote.stops).toBe(1);
  });

  it('adds, mutes, replaces and reconnects audio without disturbing control transport', async () => {
    await service.requestMicrophone();
    expect(peer.attachMediaTrack).toHaveBeenCalledTimes(1);
    service.setMuted(true);
    expect(tracks[0].enabled).toBe(false);
    await service.replaceMicrophone({ deviceId: 'second' });
    expect(peer.replaceMediaTrack).toHaveBeenCalledWith(peer.sender, tracks[1]);
    expect(tracks[0].stops).toBeGreaterThan(0);
    service.reconnect();
    expect(peer.restartMediaIce).toHaveBeenCalledTimes(1);
  });

  it('releases every device on stop, browser-ended, error, session switch and destroy', async () => {
    peer.sessionStarted$.next('one');
    await service.requestMicrophone();
    peer.sessionStarted$.next('two');
    expect(tracks[0].stops).toBeGreaterThan(0);
    await service.requestMicrophone();
    tracks[1].onended?.();
    expect(tracks[1].stops).toBeGreaterThan(0);
    await service.requestMicrophone();
    peer.state$.next('closed');
    expect(tracks[2].stops).toBeGreaterThan(0);
  });

  it('forwards remote ontrack events independently of local capture', () => {
    const received: any[] = [];
    service.remoteTrack$.subscribe(value => received.push(value));
    const event = { track: new FakeTrack('remote'), streams: [new FakeStream(new FakeTrack('r'))] } as any;
    peer.remoteTrack$.next(event);
    expect(received[0].track.id).toBe('remote');
  });

  it('replays live remote tracks to late-mounted views and clears references without stopping receiver tracks', () => {
    const remote = new FakeTrack('remote-replay');
    peer.remoteTrack$.next({ track: remote, streams: [] } as any);

    const snapshots: ReadonlyArray<ReadonlyArray<{ track: MediaStreamTrack }>> = [];
    const subscription = service.remoteTracks$.subscribe(value => snapshots.push(value));
    expect(snapshots.at(-1)?.map(value => value.track.id)).toEqual(['remote-replay']);

    peer.state$.next('closed');
    expect(snapshots.at(-1)).toEqual([]);
    expect(remote.stops).toBe(0);
    subscription.unsubscribe();
  });

  it('hands SFU composition a separately owned live microphone clone', async () => {
    await service.requestMicrophone();
    const clone = service.cloneActiveMicrophoneTrack();
    expect(clone.id).toBe('track-1-clone');
    service.stopAudio();
    expect(clone.readyState).toBe('live');
    clone.stop();
  });

  it('cancels a pending permission result and rejects a duplicate permission prompt', async () => {
    let resolveCapture!: (stream: FakeStream) => void;
    devices.getUserMedia.mockReturnValueOnce(new Promise<FakeStream>(resolve => { resolveCapture = resolve; }));
    const pending = service.requestMicrophone();
    await expect(service.requestMicrophone()).rejects.toThrow('microphone_capture_pending');
    service.stopAudio('microphone_user_stop');
    const lateTrack = new FakeTrack('late-track');
    resolveCapture(new FakeStream(lateTrack));
    await expect(pending).rejects.toThrow('microphone_capture_superseded');
    expect(lateTrack.stops).toBeGreaterThan(0);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
    expect(service.audioState$.value).toMatchObject({ status: 'idle', reasonCode: 'microphone_user_stop' });
  });

  it('invalidates a pending permission prompt when local publication consent is revoked', async () => {
    peer.sessionStarted$.next('public-session');
    let resolveCapture!: (stream: FakeStream) => void;
    devices.getUserMedia.mockReturnValueOnce(new Promise<FakeStream>(resolve => { resolveCapture = resolve; }));

    const pending = service.requestMicrophone();
    publicationConsentState.next({
      status: 'revoking',
      binding: { sessionId: 'public-session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    const lateTrack = new FakeTrack('consent-revoked-track');
    resolveCapture(new FakeStream(lateTrack));

    await expect(pending).rejects.toThrow('microphone_capture_superseded');
    expect(lateTrack.stops).toBeGreaterThan(0);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
    expect(service.audioState$.value).toMatchObject({
      status: 'idle', reasonCode: 'public_media_publication_consent_revoked',
    });
  });

  it('detaches active audio on revoke and rejects unmute without fresh consent', async () => {
    peer.sessionStarted$.next('public-session');
    await service.requestMicrophone();
    service.setMuted(true);
    publicationPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_media_publication_consent_revoked');
    });

    expect(() => service.setMuted(false)).toThrow('public_media_publication_consent_revoked');
    expect(tracks[0].enabled).toBe(false);

    publicationConsentState.next({
      status: 'revoking',
      binding: { sessionId: 'public-session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(peer.replaceMediaTrack).toHaveBeenCalledWith(peer.sender, null);
    expect(peer.removeMediaSender).toHaveBeenCalledWith(peer.sender);
    expect(tracks[0].stops).toBeGreaterThan(0);
  });

  it('disables an active local microphone synchronously before revoke cleanup completes', async () => {
    peer.sessionStarted$.next('public-session');
    await service.requestMicrophone();
    expect(tracks[0].enabled).toBe(true);

    publicationConsentState.next({
      status: 'revoking',
      binding: { sessionId: 'public-session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });

    expect(tracks[0].enabled).toBe(false);
    expect(tracks[0].stops).toBeGreaterThan(0);
  });

  it('owns a microphone across deferred attach, releases it on revoke and fences the stale finally', async () => {
    peer.sessionStarted$.next('public-session');
    const attach = deferred<any>();
    peer.attachMediaTrack.mockImplementationOnce(async (_slot: string, track: MediaStreamTrack) => {
      peer.sender.track = track;
      return attach.promise;
    });

    const staleStart = service.requestMicrophone();
    await settle();
    const staleTrack = tracks[0];
    expect(peer.attachMediaTrack).toHaveBeenCalledOnce();

    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'public-session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(staleTrack.enabled).toBe(false);
    expect(staleTrack.stops).toBeGreaterThan(0);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'public-session' } });
    const regrantedAttach = deferred<any>();
    peer.attachMediaTrack.mockImplementationOnce(async (_slot: string, track: MediaStreamTrack) => {
      peer.sender.track = track;
      return regrantedAttach.promise;
    });
    const regrantedStart = service.requestMicrophone();
    await settle();
    const regrantedTrack = tracks[1];

    attach.resolve(peer.sender);
    await expect(staleStart).rejects.toThrow('microphone_capture_superseded');
    await expect(service.requestMicrophone()).rejects.toThrow('microphone_capture_pending');
    regrantedAttach.resolve(peer.sender);
    await regrantedStart;
    expect(peer.sender.track).toBe(regrantedTrack);
    expect(service.audioState$.value).toMatchObject({ status: 'active', trackId: regrantedTrack.id });
  });

  it('owns a replacement microphone across deferred replace and cannot commit it after revoke', async () => {
    peer.sessionStarted$.next('public-session');
    await service.requestMicrophone();
    const replace = deferred<void>();
    peer.replaceMediaTrack.mockImplementationOnce(async (target: any, track: MediaStreamTrack | null) => {
      target.track = track;
      await replace.promise;
    });

    const staleReplace = service.replaceMicrophone({ deviceId: 'replacement' });
    await settle();
    const oldTrack = tracks[0];
    const replacement = tracks[1];

    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'public-session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(oldTrack.enabled).toBe(false);
    expect(replacement.enabled).toBe(false);
    expect(oldTrack.stops).toBeGreaterThan(0);
    expect(replacement.stops).toBeGreaterThan(0);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'public-session' } });
    await service.requestMicrophone();
    const regrantedTrack = tracks[2];
    replace.resolve(undefined);

    await expect(staleReplace).rejects.toThrow('microphone_capture_superseded');
    expect(peer.sender.track).toBe(regrantedTrack);
    expect(service.audioState$.value).toMatchObject({ status: 'active', trackId: regrantedTrack.id });
  });

  it('stops a lost microphone on device change and unregisters the listener on destroy', async () => {
    await service.requestMicrophone();
    tracks[0].readyState = 'ended';
    dispatchDeviceChange();
    expect(service.audioState$.value).toMatchObject({ status: 'idle', reasonCode: 'microphone_device_lost' });
    expect(tracks[0].stops).toBeGreaterThan(0);

    service.ngOnDestroy();
    expect(devices.removeEventListener).toHaveBeenCalledWith('devicechange', expect.any(Function));
  });
});

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
