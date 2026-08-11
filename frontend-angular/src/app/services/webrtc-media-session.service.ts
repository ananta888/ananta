import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import { PeerState, WebrtcSessionService } from './webrtc-session.service';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PublicPairMediaPublicationConsentService } from './public-pair-media-publication-consent.service';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';

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
  readonly slot?: PublicPairMediaSlot | null;
}

interface RemoteTrackRegistration extends RemoteOrdinaryTrack {
  readonly releaseEndedListener: () => void;
}

interface PendingMicrophoneCapture {
  readonly generation: number;
  readonly stream: MediaStream;
  readonly track: MediaStreamTrack;
  sender?: RTCRtpSender;
  cancelled: boolean;
  released: boolean;
  senderDetached: boolean;
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
  private readonly mediaPolicy = inject(PairOrdinaryMediaPolicy);
  private readonly publicationPolicy = inject(PairMediaPublicationPolicy);
  private readonly pairMediaE2ee = inject(PairMediaE2eeCoordinatorService);
  private readonly publicationConsent = inject(PublicPairMediaPublicationConsentService);
  private readonly devices = inject(WEBRTC_MEDIA_DEVICES);
  private stream?: MediaStream;
  private track?: MediaStreamTrack;
  private sender?: RTCRtpSender;
  private activeCaptureGeneration: number | null = null;
  private currentSessionId = '';
  private captureGeneration = 0;
  private capturePendingGeneration: number | null = null;
  private pendingCapture?: PendingMicrophoneCapture;
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
      try {
        this.mediaPolicy.assertAllowed(this.currentSessionId);
      } catch {
        safeStop(event.track);
        return;
      }
      const value = Object.freeze({
        track: event.track,
        streams: Object.freeze([...event.streams]),
        slot: this.peer.publicMediaSlotForReceiver(event.receiver),
      });
      this.registerRemoteTrack(value);
      this.remoteTrack$.next(value);
    }));
    this.subscriptions.add(this.pairMediaE2ee.status$.subscribe(status => {
      if (status.sessionId !== this.currentSessionId || status.state === 'ready') return;
      if (this.capturePendingGeneration !== null || this.track || this.sender) {
        this.stopAudio(status.reasonCode || 'public_media_e2ee_not_ready');
      }
      if (this.remoteTracks.size) this.clearRemoteTracks();
    }));
    this.subscriptions.add(this.publicationConsent.state$.subscribe(consent => {
      if (consent.binding?.sessionId !== this.currentSessionId || consent.status === 'granted') return;
      if (this.capturePendingGeneration !== null || this.track || this.sender) {
        this.stopAudio(consent.reasonCode || 'public_media_publication_consent_not_granted');
      }
    }));
    this.devices.addEventListener?.('devicechange', this.deviceChangeListener);
  }

  /** The only microphone permission entry point; data-only sessions never call it. */
  async requestMicrophone(constraints: MediaTrackConstraints = {}): Promise<void> {
    this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
    if (this.capturePendingGeneration !== null) throw new Error('microphone_capture_pending');
    if (this.track || this.sender) throw new Error('microphone_already_active');
    const generation = ++this.captureGeneration;
    this.capturePendingGeneration = generation;
    this.audioState$.next({ status: 'requesting_permission', trackId: null, deviceLabelVisible: false, reasonCode: null });
    let acquired: MediaStream | undefined;
    let pending: PendingMicrophoneCapture | undefined;
    try {
      acquired = await this.devices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, ...constraints },
        video: false,
      });
      this.assertCaptureCurrent(generation);
      this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
      const track = acquired.getAudioTracks()[0];
      if (!track) throw new Error('microphone_track_missing');
      pending = this.beginPendingCapture(generation, acquired, track);
      const attachedSender = await this.peer.attachMediaTrack('microphone-opus', track, acquired);
      pending.sender = attachedSender;
      if (pending.cancelled) this.detachPendingSender(pending);
      this.assertPendingCaptureCurrent(pending);
      this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
      this.commitPendingCapture(pending);
      this.stream = acquired; this.track = track; this.sender = attachedSender;
      this.activeCaptureGeneration = generation;
      track.onended = () => {
        if (this.track === track) this.stopAudio('microphone_ended');
      };
      this.audioState$.next({
        status: 'active', trackId: track.id, deviceLabelVisible: Boolean(track.label), reasonCode: null,
      });
      acquired = undefined;
      pending = undefined;
    } catch (error) {
      if (pending) this.releasePendingCapture(pending, true, pending.cancelled);
      else stopStream(acquired);
      if (generation === this.captureGeneration) {
        this.audioState$.next({
          status: 'failed', trackId: null, deviceLabelVisible: false,
          reasonCode: typeof DOMException === 'function' && error instanceof DOMException && error.name === 'NotAllowedError'
            ? 'microphone_permission_denied' : microphoneFailureReason(error),
        });
      }
      throw error;
    } finally {
      if (this.capturePendingGeneration === generation) this.capturePendingGeneration = null;
    }
  }

  async replaceMicrophone(constraints: MediaTrackConstraints = {}): Promise<void> {
    this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
    if (this.capturePendingGeneration !== null) throw new Error('microphone_capture_pending');
    if (!this.sender || !this.track) throw new Error('microphone_not_active');
    const generation = ++this.captureGeneration;
    this.capturePendingGeneration = generation;
    const sender = this.sender;
    const oldTrack = this.track;
    const oldStream = this.stream;
    this.audioState$.next({ ...this.audioState$.value, status: 'requesting_permission', reasonCode: null });
    let acquired: MediaStream | undefined;
    let pending: PendingMicrophoneCapture | undefined;
    let senderReplaced = false;
    try {
      acquired = await this.devices.getUserMedia({ audio: constraints, video: false });
      this.assertCaptureCurrent(generation);
      this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
      const replacement = acquired.getAudioTracks()[0];
      if (!replacement) throw new Error('microphone_track_missing');
      pending = this.beginPendingCapture(generation, acquired, replacement, sender);
      await this.peer.replaceMediaTrack(sender, replacement);
      senderReplaced = true;
      this.assertPendingCaptureCurrent(pending);
      this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
      this.commitPendingCapture(pending);
      this.track = replacement; this.stream = acquired;
      this.activeCaptureGeneration = generation;
      replacement.onended = () => {
        if (this.track === replacement) this.stopAudio('microphone_ended');
      };
      stopOwnedTrack(oldTrack, oldStream);
      this.audioState$.next({
        status: replacement.enabled ? 'active' : 'muted', trackId: replacement.id,
        deviceLabelVisible: Boolean(replacement.label), reasonCode: null,
      });
      acquired = undefined;
      pending = undefined;
    } catch (error) {
      if (pending) {
        this.releasePendingCapture(
          pending, senderReplaced || pending.cancelled, pending.cancelled,
        );
      }
      else stopStream(acquired);
      if (senderReplaced && generation === this.captureGeneration && this.sender === sender) {
        this.stopAudio(microphoneFailureReason(error));
      } else if (generation === this.captureGeneration && this.track === oldTrack) {
        this.audioState$.next({
          status: oldTrack.enabled ? 'active' : 'muted', trackId: oldTrack.id,
          deviceLabelVisible: Boolean(oldTrack.label), reasonCode: 'microphone_replace_failed',
        });
      }
      throw error;
    } finally {
      if (this.capturePendingGeneration === generation) this.capturePendingGeneration = null;
    }
  }

  setMuted(muted: boolean): void {
    if (!this.track) return;
    if (!muted) this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
    this.track.enabled = !muted;
    this.audioState$.next({ ...this.audioState$.value, status: muted ? 'muted' : 'active' });
  }

  /**
   * Create a separately owned clone for an admitted SFU publication.  The
   * caller may switch the ordinary sender off afterwards without borrowing
   * or stopping this service's private track reference.
   */
  cloneActiveMicrophoneTrack(): MediaStreamTrack {
    this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
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
    this.publicationPolicy.assertAllowed(this.currentSessionId, 'microphone-opus');
    if (!this.track || !this.sender) throw new Error('microphone_not_active');
    this.peer.restartMediaIce();
  }

  stopAudio(reasonCode = 'user_stop'): void {
    this.captureGeneration += 1;
    this.capturePendingGeneration = null;
    this.cancelPendingCapture();
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
    if (state === 'closed' || state === 'idle' || state === 'failed') {
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
        .map(value => Object.freeze({ track: value.track, streams: value.streams, slot: value.slot })),
    ));
  }

  private assertCaptureCurrent(generation: number): void {
    if (generation !== this.captureGeneration) throw new Error('microphone_capture_superseded');
  }

  private beginPendingCapture(
    generation: number,
    stream: MediaStream,
    track: MediaStreamTrack,
    sender?: RTCRtpSender,
  ): PendingMicrophoneCapture {
    this.assertCaptureCurrent(generation);
    if (this.pendingCapture) throw new Error('microphone_capture_pending');
    const pending: PendingMicrophoneCapture = {
      generation, stream, track, sender,
      cancelled: false, released: false, senderDetached: false,
    };
    this.pendingCapture = pending;
    return pending;
  }

  private assertPendingCaptureCurrent(pending: PendingMicrophoneCapture): void {
    if (
      pending.cancelled
      || pending.released
      || this.pendingCapture !== pending
      || pending.generation !== this.captureGeneration
    ) throw new Error('microphone_capture_superseded');
  }

  private commitPendingCapture(pending: PendingMicrophoneCapture): void {
    this.assertPendingCaptureCurrent(pending);
    this.pendingCapture = undefined;
  }

  private cancelPendingCapture(): void {
    const pending = this.pendingCapture;
    if (!pending) return;
    pending.cancelled = true;
    this.releasePendingCapture(pending, true, true);
  }

  private releasePendingCapture(
    pending: PendingMicrophoneCapture,
    detachSender: boolean,
    forceDetach = false,
  ): void {
    if (this.pendingCapture === pending) this.pendingCapture = undefined;
    if (!pending.released) {
      pending.released = true;
      disableOwnedTrack(pending.track, pending.stream);
      stopOwnedTrack(pending.track, pending.stream);
    }
    if (detachSender) this.detachPendingSender(pending, forceDetach);
  }

  private detachPendingSender(pending: PendingMicrophoneCapture, force = false): void {
    if (!pending.sender || (pending.senderDetached && !force)) return;
    const newerTrack = this.newerOwnedTrackForSender(pending);
    if (newerTrack) {
      if (pending.sender.track !== newerTrack) {
        void this.peer.replaceMediaTrack(pending.sender, newerTrack).catch(() => undefined);
      }
      return;
    }
    if (!force && pending.sender.track && pending.sender.track !== pending.track) return;
    pending.senderDetached = true;
    void this.peer.replaceMediaTrack(pending.sender, null).catch(() => undefined);
    this.peer.removeMediaSender(pending.sender);
  }

  private newerOwnedTrackForSender(pending: PendingMicrophoneCapture): MediaStreamTrack | null {
    if (
      this.sender === pending.sender
      && this.track
      && this.activeCaptureGeneration !== null
      && this.activeCaptureGeneration > pending.generation
    ) return this.track;
    const newerPending = this.pendingCapture;
    return newerPending
      && newerPending !== pending
      && (newerPending.sender === pending.sender || pending.sender.track === newerPending.track)
      && newerPending.generation > pending.generation
      && !newerPending.cancelled
      && !newerPending.released
      ? newerPending.track : null;
  }

  private releaseCurrent(): void {
    const sender = this.sender;
    const track = this.track;
    const stream = this.stream;
    this.sender = undefined;
    this.track = undefined;
    this.stream = undefined;
    this.activeCaptureGeneration = null;
    if (track) track.onended = null;
    disableOwnedTrack(track, stream);
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
  for (const track of stream?.getTracks() ?? []) {
    track.enabled = false;
    safeStop(track);
  }
}

function stopOwnedTrack(track: MediaStreamTrack | undefined, stream: MediaStream | undefined): void {
  disableOwnedTrack(track, stream);
  if (stream) stopStream(stream);
  else safeStop(track);
}

function disableOwnedTrack(track: MediaStreamTrack | undefined, stream: MediaStream | undefined): void {
  if (stream) {
    for (const ownedTrack of stream.getTracks()) ownedTrack.enabled = false;
  } else if (track) track.enabled = false;
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
