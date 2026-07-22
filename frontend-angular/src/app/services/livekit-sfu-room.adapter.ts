import {
  BaseKeyProvider,
  Room,
  RoomEvent,
  Track,
  VideoPreset,
  createKeyMaterialFromBuffer,
  isE2EESupported,
  type LocalTrackPublication,
  type RemoteTrack,
  type RemoteTrackPublication,
  type TrackPublishOptions,
} from 'livekit-client';

import { SFU_BROADCAST_MAX_DATA_DESTINATIONS } from './sfu-broadcast-limits';
import { SFU_BROADCAST_DATA_LIMITS } from './sfu-broadcast-data-limits';
import { LivekitSfuReceiverLayerAdapter, type SfuReceiverVideoQuality } from './livekit-sfu-receiver-layer.adapter';
import { LivekitSfuVideoRenderAdapter } from './livekit-sfu-video-render.adapter';
import type {
  SfuOpaqueDataPacket,
  SfuPublishedTrack,
  SfuPublicationObservation,
  SfuRelease,
  SfuRemotePublication,
  SfuRemoteTrack,
  SfuRemoteVideoHandle,
  SfuRoomSession,
  SfuStatsPort,
  SfuStatsSnapshot,
  SfuValidatedPublisherProjection,
} from './sfu-room-session.ports';

interface LivekitKeyController {
  rotate(keyMaterial: Uint8Array): Promise<void>;
  rotateAtEpoch?(keyMaterial: Uint8Array, keyEpoch: number): Promise<void>;
  destroy?(): void;
}

export async function createLivekitSfuRoomSession(
  keyMaterial: Uint8Array,
  options: Readonly<{ layerControlMode: 'adaptive_stream' | 'manual_quality' }> = {
    layerControlMode: 'adaptive_stream',
  },
): Promise<SfuRoomSession> {
  if (!isE2EESupported()) throw new Error('sfu_e2ee_unsupported');
  const keyProvider = new IndexedSharedE2eeKeyProvider();
  await keyProvider.initialize(keyMaterial);
  const worker = new Worker(
    new URL('../../../node_modules/livekit-client/dist/livekit-client.e2ee.worker.mjs', import.meta.url),
    { type: 'module' },
  );
  const room = new Room({
    adaptiveStream: options.layerControlMode === 'adaptive_stream',
    dynacast: true,
    singlePeerConnection: true,
    encryption: { keyProvider, worker },
    disconnectOnPageLeave: true,
  });
  return new LivekitSfuRoomAdapter(room, keyProvider, worker, options.layerControlMode).createSession();
}

/**
 * This is the sole SDK boundary. Its Room never leaves this adapter; each
 * returned object exposes only one focused application port.
 */
export class LivekitSfuRoomAdapter {
  private readonly receiverLayers = new LivekitSfuReceiverLayerAdapter();
  private readonly published = new Map<string, LocalTrackPublication>();
  private readonly ownedLocalTracks = new Map<string, MediaStreamTrack>();
  private readonly remotePublications = new Map<
    string,
    Readonly<{ publisherId: string; publication: RemoteTrackPublication }>
  >();
  private readonly eventReleases = new Set<SfuRelease>();
  private readonly renderReleases = new Set<SfuRelease>();
  private readonly videoRenderer = new LivekitSfuVideoRenderAdapter();
  private connected = false;
  private closed = false;
  private connectPromise: Promise<void> | null = null;
  private closePromise: Promise<void> | null = null;
  private lifecycleGeneration = 0;
  private session: SfuRoomSession | null = null;

  constructor(
    private readonly room: Room,
    private readonly keyProvider: LivekitKeyController,
    private readonly worker: Pick<Worker, 'terminate'>,
    private readonly layerControlMode: 'adaptive_stream' | 'manual_quality' = 'adaptive_stream',
  ) {}

  createSession(): SfuRoomSession {
    if (this.session) return this.session;
    const lifecycle = Object.freeze({
      e2eeSupported: true,
      connect: (serverUrl: string, accessToken: string) => this.connect(serverUrl, accessToken),
      disconnect: () => this.destroy(),
      destroy: () => this.destroy(),
    });
    const key = Object.freeze({
      rotateKey: (keyMaterial: Uint8Array) => this.rotateKey(keyMaterial),
      rotateKeyAtEpoch: (keyMaterial: Uint8Array, keyEpoch: number) => (
        this.rotateKeyAtEpoch(keyMaterial, keyEpoch)
      ),
    });
    const publications = Object.freeze({
      publish: (
        publicationId: string,
        source: 'microphone' | 'camera' | 'screen',
        track: MediaStreamTrack,
      ) => this.publish(publicationId, source, track),
      publishProjected: (projection: SfuValidatedPublisherProjection, track: MediaStreamTrack) => (
        this.publishProjected(projection, track)
      ),
      replaceProjectedTrack: (publication: SfuPublishedTrack, track: MediaStreamTrack) => (
        this.replaceProjectedTrack(publication, track)
      ),
      setProjectedTrackPaused: (publication: SfuPublishedTrack, paused: boolean) => (
        this.setProjectedTrackPaused(publication, paused)
      ),
      unpublish: (publication: SfuPublishedTrack) => this.unpublish(publication),
      denySubscriptionsByDefault: () => this.denySubscriptionsByDefault(),
      setTrackAudience: (permissions: ReadonlyMap<string, readonly string[]>) => {
        this.setTrackAudience(permissions);
      },
    });
    const data = Object.freeze({
      publishOpaqueData: (
        payload: Uint8Array,
        topic: string,
        destinationIds: readonly string[],
        publishOptions?: Readonly<{ reliable: boolean }>,
      ) => (
        this.publishOpaqueData(payload, topic, destinationIds, publishOptions)
      ),
      onOpaqueDataReceived: (callback: (packet: SfuOpaqueDataPacket) => void) => (
        this.onOpaqueDataReceived(callback)
      ),
    });
    const subscriptions = Object.freeze({
      layerControlMode: this.layerControlMode,
      applyRemoteSubscriptions: (publicationIds: ReadonlySet<string>) => {
        this.applyRemoteSubscriptions(publicationIds);
      },
      setRemotePublicationSubscribed: (publicationId: string, subscribed: boolean) => {
        this.setRemotePublicationSubscribed(publicationId, subscribed);
      },
      setRemotePublicationQuality: async (publicationId: string, quality: SfuReceiverVideoQuality) => {
        this.setRemotePublicationQuality(publicationId, quality);
      },
    });
    const videoRender = Object.freeze({
      attachRemoteTrack: (track: SfuRemoteTrack, target: HTMLMediaElement) => (
        this.attachRemoteTrack(track, target)
      ),
      attachRemoteVideo: (handle: SfuRemoteVideoHandle, target: HTMLVideoElement) => (
        this.attachRemoteVideo(handle, target)
      ),
      matchesPublication: (handle: SfuRemoteVideoHandle, publicationId: string) => (
        this.videoRenderer.matchesPublication(handle, publicationId)
      ),
      onRemoteVideoAvailable: (callback: (handle: SfuRemoteVideoHandle) => void) => (
        this.onRemoteVideoAvailable(callback)
      ),
      onRemoteVideoUnavailable: (
        callback: (value: Readonly<{ handleId: string; reason: 'unsubscribed' | 'ended' }>) => void,
      ) => this.onRemoteVideoUnavailable(callback),
      onRemoteVideoStateChanged: (
        callback: (value: Readonly<{ handleId: string; state: 'active' | 'muted' }>) => void,
      ) => this.onRemoteVideoStateChanged(callback),
      clear: () => this.clearRenderedTracks(),
    });
    const stats: SfuStatsPort = Object.freeze({
      capability: 'available' as const,
      read: (handle: SfuRemoteVideoHandle) => this.readStats(handle),
    });
    const events = Object.freeze({
      onRemotePublication: (callback: (publication: SfuRemotePublication) => void) => (
        this.onRemotePublication(callback)
      ),
      onRemoteTrackSubscribed: (callback: (track: SfuRemoteTrack) => void) => (
        this.onRemoteTrackSubscribed(callback)
      ),
      onLocalTrackSubscribed: (callback: (publicationId: string) => void) => (
        this.onLocalTrackSubscribed(callback)
      ),
      onRemoteParticipantDisconnected: (callback: (participantId: string) => void) => (
        this.onRemoteParticipantDisconnected(callback)
      ),
      onOpaqueDataReceived: (callback: (packet: SfuOpaqueDataPacket) => void) => (
        this.onOpaqueDataReceived(callback)
      ),
      onDisconnected: (callback: () => void) => this.onDisconnected(callback),
    });
    this.session = Object.freeze({
      lifecycle,
      key,
      publications,
      data,
      subscriptions,
      stats,
      videoRender,
      events,
    });
    return this.session;
  }

  private async connect(serverUrl: string, accessToken: string): Promise<void> {
    this.ensureOpen();
    if (this.connected) return;
    if (!this.connectPromise) {
      const lifecycleGeneration = this.lifecycleGeneration;
      this.connectPromise = (async () => {
        await this.room.connect(serverUrl, accessToken, { autoSubscribe: false, maxRetries: 2 });
        if (this.closed || this.lifecycleGeneration !== lifecycleGeneration) {
          await this.room.disconnect(true).catch(() => undefined);
          throw new Error('sfu_session_connect_fenced');
        }
        await this.room.setE2EEEnabled(true);
        if (this.closed || this.lifecycleGeneration !== lifecycleGeneration) {
          await this.room.disconnect(true).catch(() => undefined);
          throw new Error('sfu_session_connect_fenced');
        }
        this.connected = true;
      })();
    }
    try {
      await this.connectPromise;
    } finally {
      if (!this.connected) this.connectPromise = null;
    }
  }

  private destroy(): Promise<void> {
    if (this.closePromise) return this.closePromise;
    this.closed = true;
    this.connected = false;
    ++this.lifecycleGeneration;
    this.closePromise = this.cleanupInOrder();
    return this.closePromise;
  }

  private async rotateKey(keyMaterial: Uint8Array): Promise<void> {
    this.ensureConnected();
    await this.keyProvider.rotate(keyMaterial);
  }

  private async rotateKeyAtEpoch(keyMaterial: Uint8Array, keyEpoch: number): Promise<void> {
    this.ensureConnected();
    if (!this.keyProvider.rotateAtEpoch) throw new Error('sfu_e2ee_epoch_rotation_unsupported');
    await this.keyProvider.rotateAtEpoch(keyMaterial, keyEpoch);
  }

  private async publish(
    publicationId: string,
    source: 'microphone' | 'camera' | 'screen',
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    this.ensureConnected();
    if (this.published.has(publicationId)) throw new Error('sfu_publication_duplicate');
    const publication = await this.room.localParticipant.publishTrack(track, {
      name: publicationId,
      source: toLivekitSource(source),
      stream: 'ananta-ordinary-media',
    });
    this.published.set(publicationId, publication);
    this.ownedLocalTracks.set(publicationId, track);
    return { publicationId, trackSid: publication.trackSid, track };
  }

  private async publishProjected(
    projection: SfuValidatedPublisherProjection,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    this.ensureConnected();
    if (projection.validation !== 'hub-contract-accepted-v1') {
      throw new Error('sfu_publisher_projection_unvalidated');
    }
    if (this.published.has(projection.publicationId)) throw new Error('sfu_publication_duplicate');
    const publication = await this.room.localParticipant.publishTrack(
      track,
      projectedPublishOptions(projection),
    );
    this.published.set(projection.publicationId, publication);
    this.ownedLocalTracks.set(projection.publicationId, track);
    const observation = await observePublication(publication);
    return Object.freeze({
      publicationId: projection.publicationId,
      trackSid: publication.trackSid,
      track,
      projectionVersion: projection.projectionVersion,
      routeEpoch: projection.routeEpoch,
      keyEpoch: projection.keyEpoch,
      observation,
    });
  }

  private async replaceProjectedTrack(
    publication: SfuPublishedTrack,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    this.ensureConnected();
    const found = this.published.get(publication.publicationId);
    const localTrack = found?.track;
    if (!found || !localTrack || typeof localTrack.replaceTrack !== 'function') {
      throw new Error('sfu_publication_replace_unsupported');
    }
    if (track.kind !== publication.track.kind) throw new Error('sfu_publication_kind_mismatch');
    await localTrack.replaceTrack(track, true);
    const previous = this.ownedLocalTracks.get(publication.publicationId) ?? publication.track;
    this.ownedLocalTracks.set(publication.publicationId, track);
    if (previous !== track) safeStop(previous);
    return Object.freeze({
      ...publication,
      track,
      observation: await observePublication(found),
    });
  }

  private async setProjectedTrackPaused(
    publication: SfuPublishedTrack,
    paused: boolean,
  ): Promise<void> {
    this.ensureConnected();
    const localTrack = this.published.get(publication.publicationId)?.track;
    if (!localTrack) throw new Error('sfu_publication_missing');
    if (paused) await localTrack.pauseUpstream();
    else await localTrack.resumeUpstream();
  }

  private async unpublish(publication: SfuPublishedTrack): Promise<void> {
    const found = this.published.get(publication.publicationId);
    if (!found) return;
    this.published.delete(publication.publicationId);
    const ownedTrack = this.ownedLocalTracks.get(publication.publicationId) ?? publication.track;
    this.ownedLocalTracks.delete(publication.publicationId);
    try {
      if (found.track) await this.room.localParticipant.unpublishTrack(found.track, true);
    } finally {
      safeStop(ownedTrack);
    }
  }

  private async publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
    options: Readonly<{ reliable: boolean }> = { reliable: true },
  ): Promise<void> {
    this.ensureConnected();
    if (typeof options.reliable !== 'boolean') throw new Error('sfu_data_delivery_invalid');
    const packetBytesMax = options.reliable
      ? SFU_BROADCAST_DATA_LIMITS.publish.reliable_packet_bytes_max
      : SFU_BROADCAST_DATA_LIMITS.publish.lossy_packet_bytes_max;
    if (payload.byteLength < 1 || payload.byteLength > packetBytesMax) {
      throw new Error('sfu_data_payload_size_invalid');
    }
    if (!dataTopic(topic)) {
      throw new Error('sfu_data_topic_invalid');
    }
    const destinations = [...new Set(destinationIds)].sort();
    if (
      destinations.length < 1
      || destinations.length > SFU_BROADCAST_MAX_DATA_DESTINATIONS
      || destinations.some(value => !identifier(value))
    ) {
      throw new Error('sfu_data_audience_invalid');
    }
    const copy = new Uint8Array(payload.byteLength);
    copy.set(payload);
    try {
      await this.room.localParticipant.publishData(Uint8Array.from(copy), {
        reliable: options.reliable,
        topic,
        destinationIdentities: destinations,
      });
    } finally {
      copy.fill(0);
    }
  }

  private denySubscriptionsByDefault(): void {
    this.ensureOpen();
    this.room.localParticipant.setTrackSubscriptionPermissions(false, []);
  }

  private setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void {
    this.ensureOpen();
    const byParticipant = new Map<string, string[]>();
    for (const [trackSid, participants] of permissions) {
      for (const participantIdentity of participants) {
        const trackSids = byParticipant.get(participantIdentity) ?? [];
        trackSids.push(trackSid);
        byParticipant.set(participantIdentity, trackSids);
      }
    }
    this.room.localParticipant.setTrackSubscriptionPermissions(
      false,
      [...byParticipant].map(([participantIdentity, allowedTrackSids]) => ({
        participantIdentity,
        allowAll: false,
        allowedTrackSids: [...new Set(allowedTrackSids)].sort(),
      })),
    );
  }

  private applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void {
    this.ensureOpen();
    for (const participant of this.room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        publication.setSubscribed(publicationIds.has(publication.trackName));
      }
    }
  }

  private setRemotePublicationQuality(publicationId: string, quality: SfuReceiverVideoQuality): void {
    this.ensureOpen();
    if (this.layerControlMode !== 'manual_quality') throw new Error('sfu_manual_quality_not_authorized');
    const known = this.remotePublications.get(publicationId);
    if (!known) throw new Error('sfu_remote_publication_unknown');
    this.receiverLayers.apply(known.publication, quality);
  }

  private setRemotePublicationSubscribed(publicationId: string, subscribed: boolean): void {
    this.ensureOpen();
    const known = this.remotePublications.get(publicationId);
    if (known) {
      known.publication.setSubscribed(subscribed);
      return;
    }
    for (const participant of this.room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        if (publication.trackName === publicationId) publication.setSubscribed(subscribed);
      }
    }
  }

  private onRemotePublication(callback: (publication: SfuRemotePublication) => void): SfuRelease {
    this.ensureOpen();
    const listener = (publication: RemoteTrackPublication, participant: { identity: string }) => {
      this.remotePublications.set(publication.trackName, {
        publisherId: participant.identity,
        publication,
      });
      callback(Object.freeze({
        publicationId: publication.trackName,
        publisherId: participant.identity,
      }));
    };
    this.room.on(RoomEvent.TrackPublished, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.TrackPublished, listener));
  }

  private onRemoteTrackSubscribed(callback: (value: SfuRemoteTrack) => void): SfuRelease {
    this.ensureOpen();
    const listener = (
      track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: { identity: string },
    ) => {
      this.rememberRemoteVideo(track, publication);
      callback(Object.freeze({
        publicationId: publication.trackName,
        publisherId: participant.identity,
        track: track.mediaStreamTrack,
      }));
    };
    this.room.on(RoomEvent.TrackSubscribed, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.TrackSubscribed, listener));
  }

  private onLocalTrackSubscribed(callback: (publicationId: string) => void): SfuRelease {
    this.ensureOpen();
    const listener = (publication: LocalTrackPublication) => callback(publication.trackName);
    this.room.on(RoomEvent.LocalTrackSubscribed, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.LocalTrackSubscribed, listener));
  }

  private onRemoteParticipantDisconnected(callback: (participantId: string) => void): SfuRelease {
    this.ensureOpen();
    const listener = (participant: { identity: string }) => {
      for (const [publicationId, value] of this.remotePublications) {
        if (value.publisherId === participant.identity) this.remotePublications.delete(publicationId);
      }
      callback(participant.identity);
    };
    this.room.on(RoomEvent.ParticipantDisconnected, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.ParticipantDisconnected, listener));
  }

  private onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): SfuRelease {
    this.ensureOpen();
    const listener = (
      payload: Uint8Array,
      participant: { identity: string } | undefined,
      _kind: unknown,
      topic: string | undefined,
    ) => {
      if (!participant || !topic || !dataTopic(topic)
          || payload.byteLength < 1
          || payload.byteLength > SFU_BROADCAST_DATA_LIMITS.publish.reliable_packet_bytes_max) return;
      const copy = new Uint8Array(payload.byteLength);
      copy.set(payload);
      callback(Object.freeze({ senderId: participant.identity, topic, payload: copy }));
    };
    this.room.on(RoomEvent.DataReceived, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.DataReceived, listener));
  }

  private onDisconnected(callback: () => void): SfuRelease {
    this.ensureOpen();
    this.room.on(RoomEvent.Disconnected, callback);
    return this.trackEventRelease(() => this.room.off(RoomEvent.Disconnected, callback));
  }

  private attachRemoteTrack(value: SfuRemoteTrack, target: HTMLMediaElement): SfuRelease {
    this.ensureOpen();
    const handle = this.videoRenderer.handleForMediaTrack(value.track);
    if (handle && target instanceof HTMLVideoElement) {
      return this.attachRemoteVideo(handle, target);
    }
    const stream = new MediaStream([value.track]);
    target.srcObject = stream;
    void target.play().catch(() => undefined);
    return this.trackRenderRelease(() => {
      if (target.srcObject === stream) target.srcObject = null;
    });
  }

  private onRemoteVideoAvailable(callback: (handle: SfuRemoteVideoHandle) => void): SfuRelease {
    this.ensureOpen();
    const listener = (
      track: RemoteTrack,
      publication: RemoteTrackPublication,
      _participant: { identity: string },
    ) => {
      const handle = this.rememberRemoteVideo(track, publication);
      if (handle) callback(handle);
    };
    this.room.on(RoomEvent.TrackSubscribed, listener);
    return this.trackEventRelease(() => this.room.off(RoomEvent.TrackSubscribed, listener));
  }

  private onRemoteVideoUnavailable(
    callback: (value: Readonly<{ handleId: string; reason: 'unsubscribed' | 'ended' }>) => void,
  ): SfuRelease {
    this.ensureOpen();
    const listener = (track: RemoteTrack) => {
      this.videoRenderer.releaseTrack(track as object, 'unsubscribed');
    };
    const observerRelease = this.videoRenderer.onUnavailable(callback);
    this.room.on(RoomEvent.TrackUnsubscribed, listener);
    return this.trackEventRelease(() => {
      this.room.off(RoomEvent.TrackUnsubscribed, listener);
      observerRelease();
    });
  }

  private onRemoteVideoStateChanged(
    callback: (value: Readonly<{ handleId: string; state: 'active' | 'muted' }>) => void,
  ): SfuRelease {
    this.ensureOpen();
    const emit = (publication: RemoteTrackPublication, state: 'active' | 'muted') => {
      const handle = this.videoRenderer.handleForPublication(publication.trackName);
      if (handle) callback(Object.freeze({ handleId: handle.handleId, state }));
    };
    const muted = (publication: RemoteTrackPublication) => emit(publication, 'muted');
    const active = (publication: RemoteTrackPublication) => emit(publication, 'active');
    this.room.on(RoomEvent.TrackMuted, muted);
    this.room.on(RoomEvent.TrackUnmuted, active);
    return this.trackEventRelease(() => {
      this.room.off(RoomEvent.TrackMuted, muted);
      this.room.off(RoomEvent.TrackUnmuted, active);
    });
  }

  private attachRemoteVideo(handle: SfuRemoteVideoHandle, target: HTMLVideoElement): SfuRelease {
    this.ensureOpen();
    return this.videoRenderer.attach(handle, target);
  }

  private rememberRemoteVideo(
    track: RemoteTrack,
    publication: RemoteTrackPublication,
  ): SfuRemoteVideoHandle | null {
    if (track.kind !== Track.Kind.Video) return null;
    const source = publication.source === Track.Source.ScreenShare ? 'screen' : 'camera';
    return this.videoRenderer.register(track as import('livekit-client').RemoteVideoTrack, publication.trackName, source);
  }

  private async readStats(handle: SfuRemoteVideoHandle): Promise<SfuStatsSnapshot | null> {
    this.ensureOpen();
    const track = this.videoRenderer.trackFor(handle);
    if (!track) return null;
    let report: RTCStatsReport | undefined;
    try { report = await track.getRTCStatsReport(); } catch { return null; }
    if (!report) return null;
    const rows: RTCStats[] = [];
    report.forEach(value => rows.push(value));
    const inbound = rows.filter(row => row.type === 'inbound-rtp' && (row as any).isRemote !== true);
    if (!inbound.length) return null;
    const sum = (field: string) => inbound.reduce((total, row) => total + finiteStat((row as any)[field]), 0);
    const pairs = rows.filter(row => row.type === 'candidate-pair'
      && ['succeeded', 'in-progress'].includes(String((row as any).state)));
    const rtt = pairs.map(row => nullableStat((row as any).currentRoundTripTime))
      .find(value => value !== null) ?? null;
    const jitters = inbound.map(row => nullableStat((row as any).jitter))
      .filter((value): value is number => value !== null);
    return Object.freeze({
      observedAtMs: Date.now(),
      bytesReceived: sum('bytesReceived'),
      packetsReceived: sum('packetsReceived'),
      packetsLost: sum('packetsLost'),
      framesDecoded: sum('framesDecoded'),
      totalDecodeTimeSeconds: sum('totalDecodeTime'),
      totalFreezeDurationSeconds: sum('totalFreezesDuration'),
      jitterSeconds: jitters.length ? Math.max(...jitters) : null,
      roundTripTimeSeconds: rtt,
    });
  }

  private clearRenderedTracks(): void {
    for (const release of [...this.renderReleases].reverse()) release();
    this.videoRenderer.clear();
  }

  private trackEventRelease(remove: SfuRelease): SfuRelease {
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      this.eventReleases.delete(release);
      remove();
    };
    this.eventReleases.add(release);
    return release;
  }

  private trackRenderRelease(remove: SfuRelease): SfuRelease {
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      this.renderReleases.delete(release);
      remove();
    };
    this.renderReleases.add(release);
    return release;
  }

  private async cleanupInOrder(): Promise<void> {
    let firstFailure: unknown = null;
    const remember = (error: unknown) => { firstFailure ??= error; };

    if (this.connectPromise) {
      try {
        await this.connectPromise;
      } catch (error) {
        if (!(error instanceof Error && error.message === 'sfu_session_connect_fenced')) remember(error);
      }
    }

    for (const release of [...this.eventReleases].reverse()) {
      try { release(); } catch (error) { remember(error); }
    }
    for (const release of [...this.renderReleases].reverse()) {
      try { release(); } catch (error) { remember(error); }
    }
    this.videoRenderer.destroy();
    for (const [publicationId, publication] of [...this.published].sort(([left], [right]) => (
      left.localeCompare(right)
    ))) {
      try {
        if (publication.track) await this.room.localParticipant.unpublishTrack(publication.track, true);
      } catch (error) {
        remember(error);
      } finally {
        safeStop(this.ownedLocalTracks.get(publicationId) ?? publication.track?.mediaStreamTrack);
        this.published.delete(publicationId);
        this.ownedLocalTracks.delete(publicationId);
      }
    }
    this.remotePublications.clear();
    try { await this.room.disconnect(true); } catch (error) { remember(error); }
    try { this.keyProvider.destroy?.(); } catch (error) { remember(error); }
    try { this.worker.terminate(); } catch (error) { remember(error); }
    if (firstFailure) throw firstFailure;
  }

  private ensureOpen(): void {
    if (this.closed) throw new Error('sfu_session_destroyed');
  }

  private ensureConnected(): void {
    this.ensureOpen();
    if (!this.connected) throw new Error('sfu_not_connected');
  }
}

/**
 * LiveKit's ExternalE2EEKeyProvider does not advance the active key index when
 * setKey() is called after connect. Explicit indices ensure revocation changes
 * the active frame-key slot instead of merely caching adjacent key material.
 */
class IndexedSharedE2eeKeyProvider extends BaseKeyProvider implements LivekitKeyController {
  private keyIndex = 0;
  private activeEpoch = 0;
  private fenced = false;

  constructor() {
    super({ sharedKey: true, keySize: 256, ratchetWindowSize: 0, failureTolerance: -1 });
  }

  async initialize(keyMaterial: Uint8Array): Promise<void> {
    this.keyIndex = 0;
    this.activeEpoch = 0;
    this.fenced = false;
    await this.install(keyMaterial);
  }

  async rotate(keyMaterial: Uint8Array): Promise<void> {
    await this.rotateAtEpoch(keyMaterial, this.activeEpoch + 1);
  }

  async rotateAtEpoch(keyMaterial: Uint8Array, keyEpoch: number): Promise<void> {
    if (this.fenced) throw new Error('sfu_e2ee_key_provider_fenced');
    if (!Number.isSafeInteger(keyEpoch) || keyEpoch < 1 || keyEpoch <= this.activeEpoch) {
      throw new Error('sfu_e2ee_key_epoch_stale');
    }
    if (this.keyIndex + 1 >= SFU_BROADCAST_DATA_LIMITS.lifecycle.livekit_key_indices_max) {
      throw new Error('sfu_e2ee_key_index_exhausted');
    }
    this.keyIndex += 1;
    await this.install(keyMaterial);
    this.activeEpoch = keyEpoch;
  }

  destroy(): void {
    this.fenced = true;
    this.activeEpoch = 0;
  }

  private async install(keyMaterial: Uint8Array): Promise<void> {
    if (keyMaterial.byteLength !== 32) throw new Error('sfu_e2ee_key_invalid');
    const owned = Uint8Array.from(keyMaterial);
    try {
      const key = await createKeyMaterialFromBuffer(owned.buffer);
      this.onSetEncryptionKey(key, undefined, this.keyIndex);
    } finally {
      owned.fill(0);
    }
  }
}

function toLivekitSource(source: 'microphone' | 'camera' | 'screen'): Track.Source {
  if (source === 'microphone') return Track.Source.Microphone;
  if (source === 'camera') return Track.Source.Camera;
  return Track.Source.ScreenShare;
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}

function identifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function dataTopic(value: string): boolean {
  return /^ananta\.(control|transcript|private-recovery|sfu-data)\.v1$/.test(value);
}

function safeStop(track: MediaStreamTrack | undefined): void {
  if (!track) return;
  try { track.stop(); } catch { /* deterministic cleanup */ }
}

function projectedPublishOptions(projection: SfuValidatedPublisherProjection): TrackPublishOptions {
  const base: TrackPublishOptions = {
    name: projection.publicationId,
    source: toLivekitSource(projection.source),
    stream: 'ananta-sfu-broadcast-media',
  };
  if (projection.mediaKind === 'audio') return base;
  const encodings = [...projection.encodings];
  const primary = encodings[encodings.length - 1];
  if (!primary || primary.maxWidth === null || primary.maxHeight === null || primary.maxFps === null) {
    throw new Error('sfu_publisher_projection_encoding_invalid');
  }
  const codec = codecForLivekit(primary.codecClass);
  const svc = primary.scalabilityClass.startsWith('svc_');
  const layers = encodings.slice(0, -1).map(value => {
    if (value.maxWidth === null || value.maxHeight === null || value.maxFps === null) {
      throw new Error('sfu_publisher_projection_encoding_invalid');
    }
    return new VideoPreset({
      width: value.maxWidth,
      height: value.maxHeight,
      maxBitrate: value.maxBitrateBps,
      maxFramerate: value.maxFps,
    });
  });
  const video = {
    videoCodec: codec,
    simulcast: !svc && encodings.length > 1,
    scalabilityMode: svc ? scalabilityForLivekit(primary.scalabilityClass) : undefined,
  };
  if (projection.source === 'screen') {
    return {
      ...base,
      ...video,
      screenShareEncoding: { maxBitrate: primary.maxBitrateBps, maxFramerate: primary.maxFps },
      screenShareSimulcastLayers: layers,
    };
  }
  return {
    ...base,
    ...video,
    videoEncoding: { maxBitrate: primary.maxBitrateBps, maxFramerate: primary.maxFps },
    videoSimulcastLayers: layers,
  };
}

async function observePublication(publication: LocalTrackPublication): Promise<SfuPublicationObservation> {
  let report: RTCStatsReport | undefined;
  const track = publication.track;
  try { report = track ? await track.getRTCStatsReport() : undefined; } catch { report = undefined; }
  const rows: RTCStats[] = [];
  report?.forEach(value => rows.push(value));
  const outbound = rows.filter(row => row.type === 'outbound-rtp' && (row as any).isRemote !== true);
  const active = outbound.filter(row => finiteStat((row as any).bytesSent) > 0
    || finiteStat((row as any).packetsSent) > 0);
  const rids = new Set(outbound.flatMap(row => typeof (row as any).rid === 'string' ? [(row as any).rid] : []));
  const scalability = outbound.map(row => scalabilityFromSdk((row as any).scalabilityMode))
    .find(value => value !== null) ?? null;
  const codecClass = codecFromMime(publication.mimeType);
  const simulcasted = typeof publication.simulcasted === 'boolean' ? publication.simulcasted : null;
  const hasObservation = codecClass !== null || simulcasted !== null || outbound.length > 0;
  return Object.freeze({
    status: hasObservation ? 'observed' : 'unsupported',
    codecClass,
    simulcasted,
    activeEncodingCount: outbound.length ? active.length : null,
    observedRidCount: outbound.length ? rids.size : null,
    scalabilityClass: scalability,
  });
}

function codecForLivekit(value: string): 'vp8' | 'h264' | 'vp9' | 'av1' {
  if (value === 'video_vp8') return 'vp8';
  if (value === 'video_h264') return 'h264';
  if (value === 'video_vp9') return 'vp9';
  if (value === 'video_av1') return 'av1';
  throw new Error('sfu_publisher_projection_codec_invalid');
}

function codecFromMime(value: string | undefined): SfuPublicationObservation['codecClass'] {
  const normalized = String(value || '').toLowerCase();
  if (normalized === 'audio/opus') return 'audio_opus';
  if (normalized === 'video/vp8') return 'video_vp8';
  if (normalized === 'video/h264') return 'video_h264';
  if (normalized === 'video/vp9') return 'video_vp9';
  if (normalized === 'video/av1') return 'video_av1';
  return null;
}

function scalabilityForLivekit(value: string): 'L1T2' | 'L1T3' | 'L2T2' | 'L3T3' {
  if (value === 'svc_l1t2') return 'L1T2';
  if (value === 'svc_l1t3') return 'L1T3';
  if (value === 'svc_l2t2') return 'L2T2';
  if (value === 'svc_l3t3') return 'L3T3';
  throw new Error('sfu_publisher_projection_scalability_invalid');
}

function scalabilityFromSdk(value: unknown): SfuPublicationObservation['scalabilityClass'] {
  const normalized = String(value || '').toUpperCase().replace(/_KEY$/, '');
  if (normalized === 'L1T2') return 'svc_l1t2';
  if (normalized === 'L1T3') return 'svc_l1t3';
  if (normalized === 'L2T2') return 'svc_l2t2';
  if (normalized === 'L3T3') return 'svc_l3t3';
  return null;
}

function finiteStat(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
}

function nullableStat(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

export type {
  SfuOpaqueDataPacket,
  SfuPublishedTrack,
  SfuRemotePublication,
  SfuRemoteTrack,
} from './sfu-room-session.ports';
export {
  SFU_ROOM_FACTORY,
  LegacySfuRoomFacade,
  LivekitSfuRoomFactory,
  legacySfuRoomFacade,
} from './sfu-room-session.factory';
export type {
  LegacySfuRemotePublication,
  SfuRoomFactory,
  SfuRoomPort,
} from './sfu-room-session.factory';
