import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { WEBRTC_MEDIA_DEVICES } from './webrtc-media-session.service';
import {
  MediaPublicationAuthorization,
  UserMediaPreference,
  WebrtcMediaPublicationService,
} from './webrtc-media-publication.service';
import { WebrtcSessionService } from './webrtc-session.service';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PublicPairMediaPublicationConsentService } from './public-pair-media-publication-consent.service';

class Track {
  kind = 'video'; id: string; enabled = true; readyState: MediaStreamTrackState = 'live';
  onended: (() => void) | null = null; onmute: (() => void) | null = null; onunmute: (() => void) | null = null;
  stops = 0;
  constructor(id: string, private readonly width = 640, private readonly height = 360) { this.id = id; }
  stop(): void { this.stops += 1; this.readyState = 'ended'; }
  getSettings(): MediaTrackSettings { return { width: this.width, height: this.height, frameRate: 15 }; }
}
class Stream {
  constructor(readonly track: Track) {}
  getVideoTracks(): MediaStreamTrack[] { return [this.track as unknown as MediaStreamTrack]; }
  getTracks(): MediaStreamTrack[] { return this.getVideoTracks(); }
}
const AUTH: MediaPublicationAuthorization = {
  publicationId: 'camera-publication', sessionId: 'session', source: 'camera', permitted: true,
  expiresAtMs: 2000, maxWidth: 1280, maxHeight: 720, maxFramesPerSecond: 30, maxBitrateBps: 1_000_000,
};
const PREF: UserMediaPreference = {
  maxWidth: 640, maxHeight: 360, maxFramesPerSecond: 15, maxBitrateBps: 400_000,
};

describe('WebrtcMediaPublicationService', () => {
  let service: WebrtcMediaPublicationService;
  let camera: Track[]; let screens: Track[]; let sender: any; let peer: any;
  let dispatchDeviceChange: () => void;
  const mediaPolicy = { assertAllowed: vi.fn(), allows: vi.fn(() => true) };
  const publicationPolicy = { assertAllowed: vi.fn() };
  const mediaE2eeStatus = new BehaviorSubject({ sessionId: '', state: 'inactive' as const });
  const publicationConsentState = new BehaviorSubject<any>({ status: 'unbound', binding: null });
  beforeEach(() => {
    camera = []; screens = [];
    mediaPolicy.assertAllowed.mockReset();
    mediaPolicy.allows.mockReset();
    mediaPolicy.allows.mockReturnValue(true);
    publicationPolicy.assertAllowed.mockReset();
    publicationConsentState.next({ status: 'unbound', binding: null });
    sender = {
      track: null,
      getParameters: () => ({ encodings: [{}] }),
      setParameters: vi.fn(async () => undefined),
      replaceTrack: vi.fn(),
    };
    peer = {
      addMediaTrack: vi.fn(() => sender),
      replaceMediaTrack: vi.fn(async (target: any, track: MediaStreamTrack | null) => { target.track = track; }),
      removeMediaSender: vi.fn(),
      attachMediaTrack: vi.fn(async (_slot: string, track: MediaStreamTrack) => {
        sender.track = track;
        return sender;
      }),
      publicMediaSlotForReceiver: vi.fn(() => null),
      sessionStarted$: new Subject<string>(), state$: new BehaviorSubject('active'), remoteTrack$: new Subject<any>(),
    };
    let deviceChange: (() => void) | undefined;
    const devices = {
      getUserMedia: vi.fn(async () => { const track = new Track(`camera-${camera.length}`); camera.push(track); return new Stream(track); }),
      getDisplayMedia: vi.fn(async () => { const track = new Track(`screen-${screens.length}`); screens.push(track); return new Stream(track); }),
      addEventListener: vi.fn((kind: string, listener: () => void) => { if (kind === 'devicechange') deviceChange = listener; }),
      removeEventListener: vi.fn(),
    };
    dispatchDeviceChange = () => deviceChange?.();
    TestBed.configureTestingModule({ providers: [
      WebrtcMediaPublicationService, { provide: WebrtcSessionService, useValue: peer },
      { provide: PairOrdinaryMediaPolicy, useValue: mediaPolicy },
      { provide: PairMediaPublicationPolicy, useValue: publicationPolicy },
      { provide: PairMediaE2eeCoordinatorService, useValue: { status$: mediaE2eeStatus } },
      { provide: PublicPairMediaPublicationConsentService, useValue: { state$: publicationConsentState } },
      { provide: WEBRTC_MEDIA_DEVICES, useValue: devices },
    ] });
    service = TestBed.inject(WebrtcMediaPublicationService);
  });
  afterEach(() => service?.ngOnDestroy());

  it('keeps camera, screen and remote publications separate with visible sources and stop actions', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    await service.startLocal({ ...AUTH, publicationId: 'screen-publication', source: 'screen' }, PREF, 1000);
    service.registerRemote('remote-publication', new Track('remote') as any, 'camera');
    expect(service.publications$.value.map(item => [item.publicationId, item.captureLabel])).toEqual([
      ['camera-publication', 'Kamera'], ['remote-publication', 'Remote-Kamera'], ['screen-publication', 'Bildschirm'],
    ]);
    service.stopPublication('screen-publication');
    expect(screens[0].stops).toBeGreaterThan(0);
    expect(peer.removeMediaSender).toHaveBeenCalledTimes(1);
    expect(service.publications$.value.find(item => item.publicationId === 'screen-publication'))
      .toMatchObject({ status: 'ended', reasonCode: 'user_stop' });
  });

  it('denies public video before browser capture despite an admitted authorization flag', async () => {
    publicationPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_ordinary_media_e2ee_unavailable');
    });

    await expect(service.startLocal(AUTH, PREF, 1000))
      .rejects.toThrow('public_ordinary_media_e2ee_unavailable');
    expect(camera).toEqual([]);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
  });

  it('intersects Hub and user bounds and refuses silent source switches', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    expect(sender.setParameters).toHaveBeenCalledWith({ encodings: [{ maxBitrate: 400_000 }] });
    await expect(service.replaceLocal('camera-publication', 'screen', PREF, 1000))
      .rejects.toThrow('publication_source_switch_denied');
    await expect(service.startLocal({ ...AUTH, source: 'screen' }, PREF, 1000))
      .rejects.toThrow('publication_source_switch_denied');
  });

  it('handles browser-ended, revoke/device loss and replace without orphaned sender or stream', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    await service.replaceLocal(AUTH.publicationId, 'camera', PREF, 1000);
    expect(camera[0].stops).toBeGreaterThan(0);
    camera[1].onended?.();
    expect(camera[1].stops).toBeGreaterThan(0);
    expect(peer.removeMediaSender).toHaveBeenCalledTimes(1);
    expect(service.publications$.value[0]).toMatchObject({ status: 'ended', reasonCode: 'browser_capture_ended' });
  });

  it('keeps the previous publication owner when its native replacement rejects', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    const oldTrack = camera[0] as unknown as MediaStreamTrack;
    peer.replaceMediaTrack.mockImplementationOnce(async () => {
      throw new Error('native_replace_failed');
    });

    await expect(service.replaceLocal(AUTH.publicationId, 'camera', PREF, 1000))
      .rejects.toThrow('native_replace_failed');

    expect(sender.track).toBe(oldTrack);
    expect(peer.replaceMediaTrack).toHaveBeenCalledOnce();
    expect(peer.replaceMediaTrack).not.toHaveBeenCalledWith(sender, null);
    expect(camera[0].stops).toBe(0);
    expect(camera[1].stops).toBeGreaterThan(0);
    expect(service.publications$.value.find(item => item.publicationId === AUTH.publicationId))
      .toMatchObject({ status: 'active', trackId: oldTrack.id, reasonCode: 'publication_replace_failed' });
  });

  it('rejects browser settings beyond policy and releases the denied capture', async () => {
    const devices = TestBed.inject(WEBRTC_MEDIA_DEVICES) as any;
    const oversized = new Track('oversized', 1920, 1080);
    devices.getUserMedia.mockResolvedValueOnce(new Stream(oversized));
    await expect(service.startLocal(AUTH, PREF, 1000)).rejects.toThrow('browser_capture_exceeds_policy');
    expect(oversized.stops).toBeGreaterThan(0);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
  });

  it('cancels a pending permission result and never attaches its late track', async () => {
    let resolveCapture!: (stream: Stream) => void;
    const pending = new Promise<Stream>(resolve => { resolveCapture = resolve; });
    const devices = TestBed.inject(WEBRTC_MEDIA_DEVICES) as any;
    devices.getUserMedia.mockReturnValueOnce(pending);
    const start = service.startLocal(AUTH, PREF, 1000);
    service.stopPublication(AUTH.publicationId, 'publication_user_stop');
    const late = new Track('late-camera');
    resolveCapture(new Stream(late));
    await expect(start).rejects.toThrow('publication_operation_superseded');
    expect(late.stops).toBeGreaterThan(0);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
    expect(service.publications$.value[0]).toMatchObject({ status: 'ended', reasonCode: 'publication_user_stop' });
  });

  it('cancels pending capture on consent revoke before attaching its late track', async () => {
    peer.sessionStarted$.next('session');
    let resolveCapture!: (stream: Stream) => void;
    const devices = TestBed.inject(WEBRTC_MEDIA_DEVICES) as any;
    devices.getUserMedia.mockReturnValueOnce(new Promise<Stream>(resolve => { resolveCapture = resolve; }));

    const start = service.startLocal(AUTH, PREF, 1000);
    publicationConsentState.next({
      status: 'revoking',
      binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    const late = new Track('late-after-revoke');
    resolveCapture(new Stream(late));

    await expect(start).rejects.toThrow('publication_operation_superseded');
    expect(late.stops).toBeGreaterThan(0);
    expect(peer.attachMediaTrack).not.toHaveBeenCalled();
  });

  it('owns a screen across deferred attach and fences its stale continuation after regrant', async () => {
    peer.sessionStarted$.next('session');
    const screenAuth: MediaPublicationAuthorization = {
      ...AUTH, publicationId: 'screen-publication', source: 'screen',
    };
    const attach = deferred<any>();
    peer.attachMediaTrack.mockImplementationOnce(async (_slot: string, track: MediaStreamTrack) => {
      sender.track = track;
      return attach.promise;
    });

    const staleStart = service.startLocal(screenAuth, PREF, 1000);
    await settle();
    const staleTrack = screens[0];

    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(staleTrack.enabled).toBe(false);
    expect(staleTrack.stops).toBeGreaterThan(0);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'session' } });
    const regrantedAttach = deferred<any>();
    peer.attachMediaTrack.mockImplementationOnce(async (_slot: string, track: MediaStreamTrack) => {
      sender.track = track;
      return regrantedAttach.promise;
    });
    const regrantedStart = service.startLocal(screenAuth, PREF, 1000);
    await settle();
    const regrantedTrack = screens[1];
    attach.resolve(sender);

    await expect(staleStart).rejects.toThrow('publication_operation_superseded');
    await expect(service.startLocal(screenAuth, PREF, 1000))
      .rejects.toThrow('publication_operation_pending');
    regrantedAttach.resolve(sender);
    await regrantedStart;
    expect(sender.track).toBe(regrantedTrack);
    expect(service.publications$.value.find(item => item.publicationId === screenAuth.publicationId))
      .toMatchObject({ status: 'active', trackId: regrantedTrack.id });
  });

  it('owns a camera while bitrate application is deferred and stops it immediately on revoke', async () => {
    peer.sessionStarted$.next('session');
    const bitrate = deferred<void>();
    sender.setParameters.mockImplementationOnce(async () => bitrate.promise);

    const staleStart = service.startLocal(AUTH, PREF, 1000);
    await settle();
    const staleTrack = camera[0];
    expect(sender.setParameters).toHaveBeenCalledOnce();

    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(staleTrack.enabled).toBe(false);
    expect(staleTrack.stops).toBeGreaterThan(0);
    expect(peer.replaceMediaTrack).toHaveBeenCalledWith(sender, null);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'session' } });
    await service.startLocal(AUTH, PREF, 1000);
    const regrantedTrack = camera[1];
    bitrate.resolve(undefined);

    await expect(staleStart).rejects.toThrow('publication_operation_superseded');
    expect(sender.track).toBe(regrantedTrack);
  });

  it('owns a replacement camera across deferred replace and cannot commit it after revoke', async () => {
    peer.sessionStarted$.next('session');
    await service.startLocal(AUTH, PREF, 1000);
    const replace = deferred<void>();
    peer.replaceMediaTrack.mockImplementationOnce(async (target: any, track: MediaStreamTrack | null) => {
      target.track = track;
      await replace.promise;
    });

    const staleReplace = service.replaceLocal(AUTH.publicationId, 'camera', PREF, 1000);
    await settle();
    const oldTrack = camera[0];
    const replacement = camera[1];

    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    expect(oldTrack.enabled).toBe(false);
    expect(replacement.enabled).toBe(false);
    expect(oldTrack.stops).toBeGreaterThan(0);
    expect(replacement.stops).toBeGreaterThan(0);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'session' } });
    await service.startLocal(AUTH, PREF, 1000);
    const regrantedTrack = camera[2];
    replace.resolve(undefined);

    await expect(staleReplace).rejects.toThrow('publication_operation_superseded');
    expect(sender.track).toBe(regrantedTrack);
    expect(service.publications$.value.find(item => item.publicationId === AUTH.publicationId))
      .toMatchObject({ status: 'active', trackId: regrantedTrack.id });
  });

  it('never rolls an old camera back after revoke crosses a rejected bitrate await', async () => {
    peer.sessionStarted$.next('session');
    await service.startLocal(AUTH, PREF, 1000);
    const oldTrack = camera[0] as unknown as MediaStreamTrack;
    const bitrate = deferred<void>();
    sender.setParameters.mockImplementationOnce(async () => bitrate.promise);

    const staleReplace = service.replaceLocal(AUTH.publicationId, 'camera', PREF, 1000);
    await settle();
    publicationConsentState.next({
      status: 'revoking', binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });
    const nullCallsBeforeRejection = peer.replaceMediaTrack.mock.calls
      .filter(([, track]: [unknown, MediaStreamTrack | null]) => track === null).length;
    const rejected = expect(staleReplace).rejects.toThrow('bitrate_apply_failed');

    bitrate.reject(new Error('bitrate_apply_failed'));
    await rejected;

    expect(peer.replaceMediaTrack.mock.calls.some(
      ([, track]: [unknown, MediaStreamTrack | null]) => track === oldTrack,
    )).toBe(false);
    expect(peer.replaceMediaTrack.mock.calls
      .filter(([, track]: [unknown, MediaStreamTrack | null]) => track === null).length)
      .toBeGreaterThan(nullCallsBeforeRejection);
    expect(sender.track).toBeNull();
  });

  it('re-detaches a stopped source when a deferred bitrate rollback resumes late', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    const oldTrack = camera[0] as unknown as MediaStreamTrack;
    const rollback = deferred<void>();
    sender.setParameters.mockRejectedValueOnce(new Error('bitrate_apply_failed'));
    peer.replaceMediaTrack.mockImplementation(async (target: any, track: MediaStreamTrack | null) => {
      if (track === oldTrack) {
        await rollback.promise;
        target.track = track;
        return;
      }
      target.track = track;
    });

    const staleReplace = service.replaceLocal(AUTH.publicationId, 'camera', PREF, 1000);
    await settle();
    expect(peer.replaceMediaTrack.mock.calls.some(
      ([, track]: [unknown, MediaStreamTrack | null]) => track === oldTrack,
    )).toBe(true);

    service.stopPublication(AUTH.publicationId, 'publication_user_stop');
    expect(sender.track).toBeNull();
    expect(camera[0].stops).toBeGreaterThan(0);
    expect(camera[1].stops).toBeGreaterThan(0);
    const rejected = expect(staleReplace).rejects.toThrow('bitrate_apply_failed');
    rollback.resolve(undefined);
    await rejected;

    expect(sender.track).toBeNull();
    expect(peer.replaceMediaTrack.mock.calls.at(-1)?.[1]).toBeNull();
    expect(service.publications$.value.find(item => item.publicationId === AUTH.publicationId))
      .toMatchObject({ status: 'ended', reasonCode: 'publication_user_stop' });
  });

  it('stops only local publications on revoke, preserves remote reception and reuses the peer on regrant', async () => {
    peer.sessionStarted$.next('session');
    await service.startLocal(AUTH, PREF, 1000);
    const remote = new Track('remote-preserved');
    service.registerRemote('remote-preserved', remote as unknown as MediaStreamTrack, 'camera');

    publicationConsentState.next({
      status: 'revoking',
      binding: { sessionId: 'session' },
      reasonCode: 'public_media_publication_consent_revoked',
    });

    expect(service.publications$.value.find(item => item.publicationId === AUTH.publicationId))
      .toMatchObject({ status: 'ended', reasonCode: 'public_media_publication_consent_revoked' });
    expect(camera[0].enabled).toBe(false);
    expect(service.publications$.value.find(item => item.publicationId === 'remote-preserved'))
      .toMatchObject({ status: 'active' });
    expect(remote.stops).toBe(0);

    publicationConsentState.next({ status: 'granted', binding: { sessionId: 'session' } });
    await service.startLocal(AUTH, PREF, 1000);
    expect(peer.attachMediaTrack).toHaveBeenCalledTimes(2);
    expect(service.publications$.value.find(item => item.publicationId === 'remote-preserved'))
      .toMatchObject({ status: 'active' });
  });

  it('keeps a muted camera disabled when publication consent denies unmute', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    service.setMuted(AUTH.publicationId, true);
    publicationPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_media_publication_consent_revoked');
    });

    expect(() => service.setMuted(AUTH.publicationId, false))
      .toThrow('public_media_publication_consent_revoked');
    expect(camera[0].enabled).toBe(false);
  });

  it('cleans up device loss, remote ended and session destruction deterministically', async () => {
    await service.startLocal(AUTH, PREF, 1000);
    camera[0].readyState = 'ended';
    dispatchDeviceChange();
    expect(service.publications$.value[0]).toMatchObject({ status: 'ended', reasonCode: 'publication_device_lost' });

    const remote = new Track('remote-track');
    peer.remoteTrack$.next({ track: remote, streams: [] });
    remote.onended?.();
    expect(service.publications$.value.find(item => item.publicationId === 'remote-remote-track'))
      .toMatchObject({ status: 'ended', reasonCode: 'browser_capture_ended' });

    peer.state$.next('closed');
    expect(remote.stops).toBe(0);
    expect(service.publications$.value.every(item => item.status === 'ended')).toBe(true);
  });

  it('exposes a borrowed remote video track for rendering without transferring ownership', () => {
    const remote = new Track('remote-render');
    peer.remoteTrack$.next({ track: remote, streams: [] });

    expect(service.remoteVideoTrack('remote-remote-render')).toBe(remote as unknown as MediaStreamTrack);
    service.ngOnDestroy();
    expect(remote.stops).toBe(0);
    expect(service.remoteVideoTrack('remote-remote-render')).toBeNull();
  });
});

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, deny) => { resolve = accept; reject = deny; });
  return { promise, resolve, reject };
}

async function settle(turns = 5): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}
