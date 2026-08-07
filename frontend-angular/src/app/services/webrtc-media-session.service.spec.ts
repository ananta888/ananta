import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { WEBRTC_MEDIA_DEVICES, WebrtcMediaSessionService } from './webrtc-media-session.service';
import { PeerState, WebrtcSessionService } from './webrtc-session.service';

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

  beforeEach(() => {
    tracks = [];
    peer = {
      state$: new BehaviorSubject<PeerState>('connected'), sessionStarted$: new Subject<string>(),
      remoteTrack$: new Subject<RTCTrackEvent>(), sender: { track: null }, addMediaTrack: vi.fn((track: MediaStreamTrack) => {
        peer.sender.track = track; return peer.sender;
      }),
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
      { provide: WEBRTC_MEDIA_DEVICES, useValue: devices },
    ] });
    service = TestBed.inject(WebrtcMediaSessionService);
  });

  afterEach(() => service.ngOnDestroy());

  it('keeps data-channel-only sessions compatible until explicit microphone permission', () => {
    expect(peer.addMediaTrack).not.toHaveBeenCalled();
    expect(service.audioState$.value.status).toBe('idle');
  });

  it('adds, mutes, replaces and reconnects audio without disturbing control transport', async () => {
    await service.requestMicrophone();
    expect(peer.addMediaTrack).toHaveBeenCalledTimes(1);
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
    expect(peer.addMediaTrack).not.toHaveBeenCalled();
    expect(service.audioState$.value).toMatchObject({ status: 'idle', reasonCode: 'microphone_user_stop' });
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
