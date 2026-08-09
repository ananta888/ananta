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
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';

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
  const mediaE2eeStatus = new BehaviorSubject({ sessionId: '', state: 'inactive' as const });
  beforeEach(() => {
    camera = []; screens = [];
    mediaPolicy.assertAllowed.mockReset();
    mediaPolicy.allows.mockReset();
    mediaPolicy.allows.mockReturnValue(true);
    sender = { getParameters: () => ({ encodings: [{}] }), setParameters: vi.fn(async () => undefined), replaceTrack: vi.fn() };
    peer = {
      addMediaTrack: vi.fn(() => sender), replaceMediaTrack: vi.fn(async () => undefined), removeMediaSender: vi.fn(),
      attachMediaTrack: vi.fn(async () => sender), publicMediaSlotForReceiver: vi.fn(() => null),
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
      { provide: PairMediaE2eeCoordinatorService, useValue: { status$: mediaE2eeStatus } },
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
    mediaPolicy.assertAllowed.mockImplementation(() => {
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
