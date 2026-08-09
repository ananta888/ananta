import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription } from 'rxjs';
import {
  RemoteOrdinaryTrack,
  WEBRTC_MEDIA_DEVICES,
  WebrtcMediaSessionService,
} from './webrtc-media-session.service';
import { WebrtcSessionService } from './webrtc-session.service';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';

export type MediaPublicationSource = 'camera' | 'screen' | 'remote_video';
export type MediaPublicationStatus = 'requesting_permission' | 'active' | 'muted' | 'ended' | 'failed';

export interface MediaPublicationAuthorization {
  readonly publicationId: string;
  readonly sessionId: string;
  readonly source: 'camera' | 'screen';
  readonly permitted: boolean;
  readonly expiresAtMs: number;
  readonly maxWidth: number;
  readonly maxHeight: number;
  readonly maxFramesPerSecond: number;
  readonly maxBitrateBps: number;
}

export interface UserMediaPreference {
  readonly maxWidth: number;
  readonly maxHeight: number;
  readonly maxFramesPerSecond: number;
  readonly maxBitrateBps: number;
}

export interface MediaPublicationView {
  readonly publicationId: string;
  readonly source: MediaPublicationSource;
  readonly status: MediaPublicationStatus;
  readonly local: boolean;
  readonly trackId: string | null;
  readonly captureLabel: string;
  readonly reasonCode: string | null;
}

interface LocalPublication {
  authorization: MediaPublicationAuthorization;
  view: MediaPublicationView;
  operationId: number;
  stream?: MediaStream;
  track?: MediaStreamTrack;
  sender?: RTCRtpSender;
  expiresHandle?: ReturnType<typeof setTimeout>;
}

interface RemotePublication {
  view: MediaPublicationView;
  track: MediaStreamTrack;
  releaseListeners: () => void;
}

@Injectable({ providedIn: 'root' })
export class WebrtcMediaPublicationService implements OnDestroy {
  readonly publications$ = new BehaviorSubject<readonly MediaPublicationView[]>(Object.freeze([]));
  private readonly peer = inject(WebrtcSessionService);
  private readonly mediaPolicy = inject(PairOrdinaryMediaPolicy);
  private readonly pairMediaE2ee = inject(PairMediaE2eeCoordinatorService);
  private readonly mediaSession = inject(WebrtcMediaSessionService);
  private readonly devices = inject(WEBRTC_MEDIA_DEVICES);
  private readonly local = new Map<string, LocalPublication>();
  private readonly remote = new Map<string, RemotePublication>();
  private readonly subscriptions = new Subscription();
  private operationSerial = 0;
  private currentSessionId = '';
  private readonly deviceChangeListener = () => this.releaseLostDevices();

  constructor() {
    this.subscriptions.add(this.peer.sessionStarted$.subscribe(sessionId => {
      if (this.currentSessionId && sessionId !== this.currentSessionId) {
        this.stopAll('session_changed', true);
      }
      this.currentSessionId = sessionId;
    }));
    this.subscriptions.add(this.peer.state$.subscribe(state => {
      if (state === 'closed' || state === 'idle' || state === 'failed') this.stopAll('session_closed');
    }));
    this.subscriptions.add(this.pairMediaE2ee.status$.subscribe(status => {
      if (status.sessionId !== this.currentSessionId || status.state === 'ready') return;
      if (this.local.size || this.remote.size) {
        this.stopAll(status.reasonCode || 'public_media_e2ee_not_ready');
      }
    }));
    this.subscriptions.add(this.mediaSession.remoteTracks$.subscribe(tracks => this.reconcileRemoteTracks(tracks)));
    this.devices.addEventListener?.('devicechange', this.deviceChangeListener);
  }

  async startLocal(
    authorization: Readonly<MediaPublicationAuthorization>,
    preference: Readonly<UserMediaPreference>,
    nowMs = Date.now(),
  ): Promise<void> {
    this.mediaPolicy.assertAllowed(authorization.sessionId);
    validateAuthorization(authorization, preference, nowMs);
    const existing = this.local.get(authorization.publicationId);
    if (existing && existing.authorization.source !== authorization.source) throw new Error('publication_source_switch_denied');
    if (existing?.sender && existing.track) {
      existing.authorization = Object.freeze({ ...authorization });
      await this.replaceLocal(authorization.publicationId, authorization.source, preference, nowMs);
      return;
    }
    const operationId = ++this.operationSerial;
    const publication: LocalPublication = existing ?? {
      authorization: Object.freeze({ ...authorization }),
      view: requestingView(authorization),
      operationId,
    };
    publication.authorization = Object.freeze({ ...authorization });
    publication.operationId = operationId;
    publication.view = requestingView(authorization);
    this.local.set(authorization.publicationId, publication);
    this.emit();
    const limits = intersectLimits(authorization, preference);
    let stream: MediaStream | undefined;
    let sender: RTCRtpSender | undefined;
    try {
      stream = await capture(this.devices, authorization.source, limits);
      this.assertOperation(authorization.publicationId, operationId);
      const track = stream.getVideoTracks()[0];
      if (!track) throw new Error('video_track_missing');
      validateTrackSettings(track, limits);
      sender = await this.peer.attachMediaTrack(
        authorization.source === 'camera' ? 'camera-vp8' : 'screen-vp8',
        track,
        stream,
      );
      await applyBitrate(sender, limits.bitrate);
      this.assertOperation(authorization.publicationId, operationId);
      publication.view = Object.freeze({
        publicationId: authorization.publicationId, source: authorization.source,
        status: track.enabled ? 'active' : 'muted', local: true,
        trackId: track.id, captureLabel: captureLabel(authorization.source), reasonCode: null,
      });
      publication.stream = stream;
      publication.track = track;
      publication.sender = sender;
      wireLocalTrack(track, authorization.publicationId, this);
      this.armExpiry(publication, nowMs);
      stream = undefined;
      sender = undefined;
      this.emit();
    } catch (error) {
      if (sender) {
        void this.peer.replaceMediaTrack(sender, null).catch(() => undefined);
        this.peer.removeMediaSender(sender);
      }
      stopStream(stream);
      const current = this.local.get(authorization.publicationId);
      if (current?.operationId === operationId) {
        current.view = Object.freeze({
          publicationId: authorization.publicationId, source: authorization.source, status: 'failed', local: true,
          trackId: null, captureLabel: captureLabel(authorization.source), reasonCode: publicationFailureReason(error),
        });
        this.emit();
      }
      throw error;
    }
  }

  async replaceLocal(
    publicationId: string,
    source: 'camera' | 'screen',
    preference: Readonly<UserMediaPreference>,
    nowMs = Date.now(),
  ): Promise<void> {
    const publication = this.local.get(publicationId);
    if (!publication || publication.authorization.source !== source || !publication.sender || !publication.track) {
      throw new Error('publication_source_switch_denied');
    }
    const authorization = publication.authorization;
    this.mediaPolicy.assertAllowed(authorization.sessionId);
    validateAuthorization(authorization, preference, nowMs);
    const limits = intersectLimits(authorization, preference);
    const operationId = ++this.operationSerial;
    publication.operationId = operationId;
    const oldTrack = publication.track;
    const oldStream = publication.stream;
    publication.view = Object.freeze({ ...publication.view, status: 'requesting_permission', reasonCode: null });
    this.emit();
    let stream: MediaStream | undefined;
    let senderReplaced = false;
    try {
      stream = await capture(this.devices, source, limits);
      this.assertOperation(publicationId, operationId);
      const track = stream.getVideoTracks()[0];
      if (!track) throw new Error('video_track_missing');
      validateTrackSettings(track, limits);
      await this.peer.replaceMediaTrack(publication.sender, track);
      senderReplaced = true;
      try {
        await applyBitrate(publication.sender, limits.bitrate);
      } catch (error) {
        await this.peer.replaceMediaTrack(publication.sender, oldTrack).catch(() => undefined);
        senderReplaced = false;
        throw error;
      }
      this.assertOperation(publicationId, operationId);
      detachTrackHandlers(oldTrack);
      stopOwnedTrack(oldTrack, oldStream);
      publication.track = track;
      publication.stream = stream;
      publication.view = Object.freeze({
        ...publication.view, status: track.enabled ? 'active' : 'muted', trackId: track.id, reasonCode: null,
      });
      wireLocalTrack(track, publicationId, this);
      this.armExpiry(publication, nowMs);
      stream = undefined;
      this.emit();
    } catch (error) {
      stopStream(stream);
      const current = this.local.get(publicationId);
      if (current?.operationId === operationId) {
        if (senderReplaced) {
          this.stopPublication(publicationId, 'publication_replace_failed');
        } else {
          current.view = Object.freeze({
            ...current.view, status: oldTrack.enabled ? 'active' : 'muted',
            trackId: oldTrack.id, reasonCode: 'publication_replace_failed',
          });
          this.emit();
        }
      }
      throw error;
    }
  }

  setMuted(publicationId: string, muted: boolean): void {
    const publication = this.local.get(publicationId);
    if (!publication?.track) return;
    publication.track.enabled = !muted;
    publication.view = Object.freeze({ ...publication.view, status: muted ? 'muted' : 'active', reasonCode: null });
    this.emit();
  }

  registerRemote(publicationId: string, track: MediaStreamTrack, source: 'camera' | 'screen'): void {
    if (!this.mediaPolicy.allows(this.currentSessionId)) {
      safeStop(track);
      return;
    }
    if (!publicationId || track.kind !== 'video') throw new Error('remote_publication_invalid');
    const current = this.remote.get(publicationId);
    if (current?.track === track) return;
    current?.releaseListeners();
    const remote: RemotePublication = {
      view: Object.freeze({
        publicationId, source: 'remote_video', status: track.enabled ? 'active' : 'muted', local: false,
        trackId: track.id, captureLabel: source === 'camera' ? 'Remote-Kamera' : 'Remote-Bildschirm', reasonCode: null,
      }),
      track,
      releaseListeners: () => undefined,
    };
    this.remote.set(publicationId, remote);
    remote.releaseListeners = listenToRemoteTrack(track, {
      ended: () => {
        const active = this.remote.get(publicationId);
        if (active?.track !== track) return;
        active.view = Object.freeze({ ...active.view, status: 'ended', reasonCode: 'browser_capture_ended' });
        this.emit();
      },
      muted: () => this.updateRemoteStatus(publicationId, track, 'muted'),
      unmuted: () => this.updateRemoteStatus(publicationId, track, 'active'),
    });
    this.emit();
  }

  remoteVideoTrack(publicationId: string): MediaStreamTrack | null {
    const publication = this.remote.get(publicationId);
    return publication && publication.view.status !== 'ended' && publication.track.readyState !== 'ended'
      ? publication.track : null;
  }

  stopPublication(publicationId: string, reasonCode = 'user_stop'): void {
    const publication = this.local.get(publicationId);
    if (publication) {
      publication.operationId = ++this.operationSerial;
      this.clearExpiry(publication);
      if (publication.sender) {
        void this.peer.replaceMediaTrack(publication.sender, null).catch(() => undefined);
        this.peer.removeMediaSender(publication.sender);
      }
      detachTrackHandlers(publication.track);
      stopOwnedTrack(publication.track, publication.stream);
      publication.track = undefined;
      publication.stream = undefined;
      publication.sender = undefined;
      publication.view = Object.freeze({
        ...publication.view, status: 'ended', trackId: null, reasonCode: normalizedReason(reasonCode),
      });
      this.emit();
      return;
    }
    const remote = this.remote.get(publicationId);
    if (remote) {
      remote.releaseListeners();
      remote.view = Object.freeze({ ...remote.view, status: 'ended', reasonCode: normalizedReason(reasonCode) });
      this.emit();
    }
  }

  stopAll(reasonCode = 'session_ended', clear = false): void {
    for (const id of [...this.local.keys()]) this.stopPublication(id, reasonCode);
    for (const id of [...this.remote.keys()]) this.stopPublication(id, reasonCode);
    if (clear) {
      this.local.clear();
      this.remote.clear();
      this.emit();
    }
  }

  ngOnDestroy(): void {
    this.devices.removeEventListener?.('devicechange', this.deviceChangeListener);
    this.stopAll('service_destroyed', true);
    this.subscriptions.unsubscribe();
    this.publications$.complete();
  }

  private assertOperation(publicationId: string, operationId: number): void {
    if (this.local.get(publicationId)?.operationId !== operationId) {
      throw new Error('publication_operation_superseded');
    }
  }

  private armExpiry(publication: LocalPublication, nowMs: number): void {
    this.clearExpiry(publication);
    const delay = publication.authorization.expiresAtMs - nowMs;
    publication.expiresHandle = globalThis.setTimeout(() => {
      if (this.local.get(publication.authorization.publicationId) === publication) {
        this.stopPublication(publication.authorization.publicationId, 'publication_authorization_expired');
      }
    }, Math.max(1, Math.min(2_147_483_647, delay)));
  }

  private clearExpiry(publication: LocalPublication): void {
    if (publication.expiresHandle !== undefined) globalThis.clearTimeout(publication.expiresHandle);
    publication.expiresHandle = undefined;
  }

  private releaseLostDevices(): void {
    for (const [publicationId, publication] of this.local) {
      if (publication.track && publication.track.readyState !== 'live') {
        this.stopPublication(publicationId, 'publication_device_lost');
      }
    }
  }

  private updateRemoteStatus(
    publicationId: string,
    track: MediaStreamTrack,
    status: Extract<MediaPublicationStatus, 'active' | 'muted'>,
  ): void {
    const current = this.remote.get(publicationId);
    if (!current || current.track !== track || current.view.status === 'ended') return;
    current.view = Object.freeze({ ...current.view, status, reasonCode: null });
    this.emit();
  }

  private reconcileRemoteTracks(tracks: readonly RemoteOrdinaryTrack[]): void {
    const activePublicationIds = new Set<string>();
    for (const value of tracks) {
      if (value.track.kind !== 'video' || value.track.readyState === 'ended') continue;
      const publicSlot = value.slot === 'camera-vp8' || value.slot === 'screen-vp8'
        ? value.slot : null;
      const publicationId = publicSlot ? `remote-${publicSlot}` : `remote-${value.track.id}`;
      activePublicationIds.add(publicationId);
      const source = publicSlot === 'screen-vp8'
        ? 'screen' : publicSlot === 'camera-vp8'
          ? 'camera' : value.track.getSettings().displaySurface ? 'screen' : 'camera';
      this.registerRemote(publicationId, value.track, source);
    }
    for (const [publicationId, publication] of this.remote) {
      if (!publicationId.startsWith('remote-') || activePublicationIds.has(publicationId)
          || publication.view.status === 'ended') continue;
      publication.releaseListeners();
      publication.view = Object.freeze({
        ...publication.view, status: 'ended', reasonCode: 'remote_track_unavailable',
      });
      this.emit();
    }
  }

  private updateLocalStatus(
    publicationId: string,
    track: MediaStreamTrack,
    status: Extract<MediaPublicationStatus, 'active' | 'muted'>,
  ): void {
    const current = this.local.get(publicationId);
    if (!current || current.track !== track || current.view.status === 'ended') return;
    current.view = Object.freeze({ ...current.view, status, reasonCode: null });
    this.emit();
  }

  private emit(): void {
    const values = [...this.local.values()].map(item => item.view)
      .concat([...this.remote.values()].map(item => item.view));
    this.publications$.next(Object.freeze(values.sort((a, b) => a.publicationId.localeCompare(b.publicationId))));
  }

  /** Track callbacks are kept here so replacements cannot update a newer publication. */
  onLocalTrackEnded(publicationId: string, track: MediaStreamTrack): void {
    if (this.local.get(publicationId)?.track === track) {
      this.stopPublication(publicationId, 'browser_capture_ended');
    }
  }

  onLocalTrackMuted(publicationId: string, track: MediaStreamTrack, muted: boolean): void {
    this.updateLocalStatus(publicationId, track, muted ? 'muted' : 'active');
  }
}

function validateAuthorization(
  auth: Readonly<MediaPublicationAuthorization>, preference: Readonly<UserMediaPreference>, nowMs: number,
): void {
  if (!auth.publicationId || !auth.sessionId || !auth.permitted || !Number.isSafeInteger(auth.expiresAtMs)
      || auth.expiresAtMs <= nowMs || !['camera', 'screen'].includes(auth.source)) throw new Error('publication_not_authorized');
  const values = [auth.maxWidth, auth.maxHeight, auth.maxFramesPerSecond, auth.maxBitrateBps,
    preference.maxWidth, preference.maxHeight, preference.maxFramesPerSecond, preference.maxBitrateBps];
  if (values.some(value => !Number.isSafeInteger(value) || value < 1)
      || auth.maxWidth > 3840 || auth.maxHeight > 2160 || auth.maxFramesPerSecond > 60
      || auth.maxBitrateBps > 8_000_000) throw new Error('publication_limits_invalid');
}
function intersectLimits(auth: MediaPublicationAuthorization, preference: UserMediaPreference) {
  return {
    width: Math.min(auth.maxWidth, preference.maxWidth), height: Math.min(auth.maxHeight, preference.maxHeight),
    fps: Math.min(auth.maxFramesPerSecond, preference.maxFramesPerSecond),
    bitrate: Math.min(auth.maxBitrateBps, preference.maxBitrateBps),
  };
}

type EffectiveMediaLimits = ReturnType<typeof intersectLimits>;

async function capture(
  devices: MediaDevices,
  source: 'camera' | 'screen',
  limits: EffectiveMediaLimits,
): Promise<MediaStream> {
  const video = {
    width: { max: limits.width },
    height: { max: limits.height },
    frameRate: { max: limits.fps },
  };
  if (source === 'camera') return devices.getUserMedia({ audio: false, video });
  if (typeof devices.getDisplayMedia !== 'function') throw new Error('browser_display_capture_unavailable');
  return devices.getDisplayMedia({ audio: false, video });
}

function validateTrackSettings(track: MediaStreamTrack, limits: EffectiveMediaLimits): void {
  const settings = track.getSettings();
  if ((settings.width ?? 0) > limits.width || (settings.height ?? 0) > limits.height
      || (settings.frameRate ?? 0) > limits.fps) {
    throw new Error('browser_capture_exceeds_policy');
  }
}

async function applyBitrate(sender: RTCRtpSender, bitrate: number): Promise<void> {
  const parameters = sender.getParameters();
  parameters.encodings = parameters.encodings?.length ? parameters.encodings : [{}];
  for (const encoding of parameters.encodings) encoding.maxBitrate = bitrate;
  await sender.setParameters(parameters);
}

function requestingView(authorization: Readonly<MediaPublicationAuthorization>): MediaPublicationView {
  return Object.freeze({
    publicationId: authorization.publicationId,
    source: authorization.source,
    status: 'requesting_permission',
    local: true,
    trackId: null,
    captureLabel: captureLabel(authorization.source),
    reasonCode: null,
  });
}

function captureLabel(source: 'camera' | 'screen'): string {
  return source === 'camera' ? 'Kamera' : 'Bildschirm';
}

function publicationFailureReason(error: unknown): string {
  if (typeof DOMException === 'function' && error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'publication_permission_denied';
  }
  if (error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message)) return error.message;
  return 'publication_start_failed';
}

function normalizedReason(value: string): string {
  return /^[a-z][a-z0-9_]{2,119}$/.test(value) ? value : 'publication_stopped';
}

function wireLocalTrack(
  track: MediaStreamTrack,
  publicationId: string,
  owner: WebrtcMediaPublicationService,
): void {
  track.onended = () => owner.onLocalTrackEnded(publicationId, track);
  track.onmute = () => owner.onLocalTrackMuted(publicationId, track, true);
  track.onunmute = () => owner.onLocalTrackMuted(publicationId, track, false);
}

function detachTrackHandlers(track: MediaStreamTrack | undefined): void {
  if (!track) return;
  track.onended = null;
  track.onmute = null;
  track.onunmute = null;
}

function listenToRemoteTrack(
  track: MediaStreamTrack,
  listeners: Readonly<{ ended: () => void; muted: () => void; unmuted: () => void }>,
): () => void {
  if (typeof track.addEventListener === 'function') {
    track.addEventListener('ended', listeners.ended);
    track.addEventListener('mute', listeners.muted);
    track.addEventListener('unmute', listeners.unmuted);
    return () => {
      track.removeEventListener('ended', listeners.ended);
      track.removeEventListener('mute', listeners.muted);
      track.removeEventListener('unmute', listeners.unmuted);
    };
  }
  const previousEnded = track.onended;
  const previousMuted = track.onmute;
  const previousUnmuted = track.onunmute;
  const ended: typeof track.onended = event => {
    previousEnded?.call(track, event);
    listeners.ended();
  };
  const muted: typeof track.onmute = event => {
    previousMuted?.call(track, event);
    listeners.muted();
  };
  const unmuted: typeof track.onunmute = event => {
    previousUnmuted?.call(track, event);
    listeners.unmuted();
  };
  track.onended = ended;
  track.onmute = muted;
  track.onunmute = unmuted;
  return () => {
    if (track.onended === ended) track.onended = previousEnded;
    if (track.onmute === muted) track.onmute = previousMuted;
    if (track.onunmute === unmuted) track.onunmute = previousUnmuted;
  };
}

function safeStop(track: MediaStreamTrack | undefined): void { try { track?.stop(); } catch { /* cleanup */ } }
function stopStream(stream: MediaStream | undefined): void { for (const track of stream?.getTracks() ?? []) safeStop(track); }
function stopOwnedTrack(track: MediaStreamTrack | undefined, stream: MediaStream | undefined): void {
  if (stream) stopStream(stream);
  else safeStop(track);
}
