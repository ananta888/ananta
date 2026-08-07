import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import { PeerState, WebrtcSessionService } from './webrtc-session.service';

export const WEBRTC_MEDIA_DEVICES = new InjectionToken<MediaDevices>('WEBRTC_MEDIA_DEVICES', {
  providedIn: 'root',
  factory: () => {
    const devices = globalThis.navigator?.mediaDevices;
    if (devices) return devices;
    // Service construction stays data-only and permission-free (including
    // SSR/tests). The explicit capture entry point still fails closed.
    return {
      getUserMedia: async () => { throw new Error('browser_media_devices_unavailable'); },
    } as Pick<MediaDevices, 'getUserMedia'> as MediaDevices;
  },
});

export interface OrdinaryAudioState {
  readonly status: 'idle' | 'requesting_permission' | 'active' | 'muted' | 'failed';
  readonly trackId: string | null;
  readonly deviceLabelVisible: boolean;
  readonly reasonCode: string | null;
}

export interface RemoteOrdinaryTrack {
  readonly track: MediaStreamTrack;
  readonly streams: readonly MediaStream[];
}

interface RemoteTrackRegistration extends RemoteOrdinaryTrack {
  readonly releaseEndedListener: () => void;
}

@Injectable({ providedIn: 'root' })
export class WebrtcMediaSessionService implements OnDestroy {
  readonly audioState$ = new BehaviorSubject<OrdinaryAudioState>({
    status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null,
  });
  /**
   * Read-only snapshot for views which mount after the browser's `track`
   * event.  `remoteTrack$` stays available as the backwards-compatible event
   * stream; renderers should prefer this replayable state.
   */
  private readonly remoteTrackState = new BehaviorSubject<readonly RemoteOrdinaryTrack[]>(Object.freeze([]));
  readonly remoteTracks$ = this.remoteTrackState.asObservable();
  readonly remoteTrack$ = new Subject<RemoteOrdinaryTrack>();

  private readonly peer = inject(WebrtcSessionService);
  private readonly devices = inject(WEBRTC_MEDIA_DEVICES);
  private stream?: MediaStream;
  private track?: MediaStreamTrack;
  private sender?: RTCRtpSender;
  private currentSessionId = '';
  private captureGeneration = 0;
  private capturePending = false;
  private readonly remoteTracks = new Map<string, RemoteTrackRegistration>();
  private readonly subscriptions = new Subscription();
  private readonly deviceChangeListener = () => {
    if (this.track && this.track.readyState !== 'live') this.stopAudio('microphone_device_lost');
  };

  constructor() {
    this.subscriptions.add(this.peer.sessionStarted$.subscribe(sessionId => {
      if (this.currentSessionId && sessionId !== this.currentSessionId) {
        this.stopAudio('session_changed');
        this.clearRemoteTracks();
      }
      this.currentSessionId = sessionId;
    }));
    this.subscriptions.add(this.peer.state$.subscribe(state => this.onPeerState(state))
    );
    this.subscriptions.add(this.peer.remoteTrack$.subscribe(event => {
      const value = Object.freeze({ track: event.track, streams: Object.freeze([...event.streams]) });
      this.registerRemoteTrack(value);
      this.remoteTrack$.next(value);
    }));
    this.devices.addEventListener?.('devicechange', this.deviceChangeListener);
  }

  /** The only microphone permission entry point; data-only sessions never call it. */
  async requestMicrophone(constraints: MediaTrackConstraints = {}): Promise<void> {
    if (this.capturePending) throw new Error('microphone_capture_pending');
    if (this.track || this.sender) throw new Error('microphone_already_active');
    this.capturePending = true;
    const generation = ++this.captureGeneration;
    this.audioState$.next({ status: 'requesting_permission', trackId: null, deviceLabelVisible: false, reasonCode: null });
    let acquired: MediaStream | undefined;
    try {
      acquired = await this.devices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, ...constraints },
        video: false,
      });
      this.assertCaptureCurrent(generation);
      const track = acquired.getAudioTracks()[0];
      if (!track) throw new Error('microphone_track_missing');
      const sender = this.peer.addMediaTrack(track, acquired);
      this.releaseCurrent();
      this.assertCaptureCurrent(generation);
      this.stream = acquired; this.track = track; this.sender = sender;
      track.onended = () => {
        if (this.track === track) this.stopAudio('microphone_ended');
      };
      this.audioState$.next({
        status: 'active', trackId: track.id, deviceLabelVisible: Boolean(track.label), reasonCode: null,
      });
      acquired = undefined;
    } catch (error) {
      stopStream(acquired);
      if (generation === this.captureGeneration) {
        this.audioState$.next({
          status: 'failed', trackId: null, deviceLabelVisible: false,
          reasonCode: typeof DOMException === 'function' && error instanceof DOMException && error.name === 'NotAllowedError'
            ? 'microphone_permission_denied' : microphoneFailureReason(error),
        });
      }
      throw error;
    } finally {
      this.capturePending = false;
    }
  }

  async replaceMicrophone(constraints: MediaTrackConstraints = {}): Promise<void> {
    if (this.capturePending) throw new Error('microphone_capture_pending');
    if (!this.sender || !this.track) throw new Error('microphone_not_active');
    this.capturePending = true;
    const generation = ++this.captureGeneration;
    const sender = this.sender;
    const oldTrack = this.track;
    const oldStream = this.stream;
    this.audioState$.next({ ...this.audioState$.value, status: 'requesting_permission', reasonCode: null });
    let acquired: MediaStream | undefined;
    try {
      acquired = await this.devices.getUserMedia({ audio: constraints, video: false });
      this.assertCaptureCurrent(generation);
      const replacement = acquired.getAudioTracks()[0];
      if (!replacement) throw new Error('microphone_track_missing');
      await this.peer.replaceMediaTrack(sender, replacement);
      this.assertCaptureCurrent(generation);
      this.track = replacement; this.stream = acquired;
      replacement.onended = () => {
        if (this.track === replacement) this.stopAudio('microphone_ended');
      };
      stopOwnedTrack(oldTrack, oldStream);
      this.audioState$.next({
        status: replacement.enabled ? 'active' : 'muted', trackId: replacement.id,
        deviceLabelVisible: Boolean(replacement.label), reasonCode: null,
      });
      acquired = undefined;
    } catch (error) {
      stopStream(acquired);
      if (generation === this.captureGeneration && this.track === oldTrack) {
        this.audioState$.next({
          status: oldTrack.enabled ? 'active' : 'muted', trackId: oldTrack.id,
          deviceLabelVisible: Boolean(oldTrack.label), reasonCode: 'microphone_replace_failed',
        });
      }
      throw error;
    } finally {
      this.capturePending = false;
    }
  }

  setMuted(muted: boolean): void {
    if (!this.track) return;
    this.track.enabled = !muted;
    this.audioState$.next({ ...this.audioState$.value, status: muted ? 'muted' : 'active' });
  }

  /**
   * Create a separately owned clone for an admitted SFU publication.  The
   * caller may switch the ordinary sender off afterwards without borrowing
   * or stopping this service's private track reference.
   */
  cloneActiveMicrophoneTrack(): MediaStreamTrack {
    if (!this.track || this.track.readyState !== 'live' || this.audioState$.value.status === 'failed') {
      throw new Error('microphone_not_active');
    }
    const clone = this.track.clone();
    if (clone.kind !== 'audio' || clone.readyState !== 'live') {
      safeStop(clone);
      throw new Error('microphone_clone_failed');
    }
    return clone;
  }

  reconnect(): void {
    if (!this.track || !this.sender) throw new Error('microphone_not_active');
    this.peer.restartMediaIce();
  }

  stopAudio(reasonCode = 'user_stop'): void {
    this.captureGeneration += 1;
    this.releaseCurrent();
    this.audioState$.next({ status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode });
  }

  ngOnDestroy(): void {
    this.devices.removeEventListener?.('devicechange', this.deviceChangeListener);
    this.stopAudio('service_destroyed');
    this.clearRemoteTracks();
    this.subscriptions.unsubscribe();
    this.remoteTrack$.complete(); this.remoteTrackState.complete(); this.audioState$.complete();
  }

  private onPeerState(state: PeerState): void {
    if (state === 'closed' || state === 'idle') {
      this.stopAudio('session_closed');
      this.clearRemoteTracks();
    }
  }

  private registerRemoteTrack(value: RemoteOrdinaryTrack): void {
    const key = remoteTrackKey(value.track);
    const previous = this.remoteTracks.get(key);
    if (previous?.track === value.track) return;
    previous?.releaseEndedListener();
    const releaseEndedListener = listenForTrackEnd(value.track, () => {
      const current = this.remoteTracks.get(key);
      if (current?.track !== value.track) return;
      current.releaseEndedListener();
      this.remoteTracks.delete(key);
      this.emitRemoteTracks();
    });
    this.remoteTracks.set(key, Object.freeze({ ...value, releaseEndedListener }));
    this.emitRemoteTracks();
  }

  private clearRemoteTracks(): void {
    for (const value of this.remoteTracks.values()) value.releaseEndedListener();
    this.remoteTracks.clear();
    this.emitRemoteTracks();
  }

  private emitRemoteTracks(): void {
    this.remoteTrackState.next(Object.freeze(
      [...this.remoteTracks.values()]
        .filter(value => value.track.readyState !== 'ended')
        .map(value => Object.freeze({ track: value.track, streams: value.streams })),
    ));
  }

  private assertCaptureCurrent(generation: number): void {
    if (generation !== this.captureGeneration) throw new Error('microphone_capture_superseded');
  }

  private releaseCurrent(): void {
    const sender = this.sender;
    const track = this.track;
    const stream = this.stream;
    this.sender = undefined;
    this.track = undefined;
    this.stream = undefined;
    if (track) track.onended = null;
    if (sender) {
      void this.peer.replaceMediaTrack(sender, null).catch(() => undefined);
      this.peer.removeMediaSender(sender);
    }
    stopOwnedTrack(track, stream);
  }
}

function safeStop(track: MediaStreamTrack | undefined): void {
  try { track?.stop(); } catch { /* Continue deterministic cleanup. */ }
}
function stopStream(stream: MediaStream | undefined): void {
  for (const track of stream?.getTracks() ?? []) safeStop(track);
}

function stopOwnedTrack(track: MediaStreamTrack | undefined, stream: MediaStream | undefined): void {
  if (stream) stopStream(stream);
  else safeStop(track);
}

function microphoneFailureReason(error: unknown): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message)
    ? error.message : 'microphone_start_failed';
}

function remoteTrackKey(track: MediaStreamTrack): string {
  return `${track.kind}:${track.id}`;
}

function listenForTrackEnd(track: MediaStreamTrack, listener: () => void): () => void {
  if (typeof track.addEventListener === 'function') {
    track.addEventListener('ended', listener);
    return () => track.removeEventListener('ended', listener);
  }
  const previous = track.onended;
  const combined = () => {
    previous?.call(track, new Event('ended'));
    listener();
  };
  track.onended = combined;
  return () => {
    if (track.onended === combined) track.onended = previous;
  };
}
