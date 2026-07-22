import {
  BaseKeyProvider,
  Room,
  RoomEvent,
  Track,
  createKeyMaterialFromBuffer,
  isE2EESupported,
  type LocalTrackPublication,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client';

import { SFU_BROADCAST_MAX_DATA_DESTINATIONS } from './sfu-broadcast-limits';
import type {
  SfuOpaqueDataPacket,
  SfuPublishedTrack,
  SfuRelease,
  SfuRemotePublication,
  SfuRemoteTrack,
  SfuRoomSession,
  SfuStatsPort,
} from './sfu-room-session.ports';

interface LivekitKeyController {
  rotate(keyMaterial: Uint8Array): Promise<void>;
}

const UNSUPPORTED_STATS: SfuStatsPort = Object.freeze({ capability: 'unsupported' });

export async function createLivekitSfuRoomSession(keyMaterial: Uint8Array): Promise<SfuRoomSession> {
  if (!isE2EESupported()) throw new Error('sfu_e2ee_unsupported');
  const keyProvider = new IndexedSharedE2eeKeyProvider();
  await keyProvider.initialize(keyMaterial);
  const worker = new Worker(
    new URL('../../../node_modules/livekit-client/dist/livekit-client.e2ee.worker.mjs', import.meta.url),
    { type: 'module' },
  );
  const room = new Room({
    adaptiveStream: true,
    dynacast: true,
    singlePeerConnection: true,
    encryption: { keyProvider, worker },
    disconnectOnPageLeave: true,
  });
  return new LivekitSfuRoomAdapter(room, keyProvider, worker).createSession();
}

/**
 * This is the sole SDK boundary. Its Room never leaves this adapter; each
 * returned object exposes only one focused application port.
 */
export class LivekitSfuRoomAdapter {
  private readonly published = new Map<string, LocalTrackPublication>();
  private readonly remotePublications = new Map<
    string,
    Readonly<{ publisherId: string; publication: RemoteTrackPublication }>
  >();
  private readonly eventReleases = new Set<SfuRelease>();
  private readonly renderReleases = new Set<SfuRelease>();
  private connected = false;
  private closed = false;
  private connectPromise: Promise<void> | null = null;
  private closePromise: Promise<void> | null = null;
  private session: SfuRoomSession | null = null;

  constructor(
    private readonly room: Room,
    private readonly keyProvider: LivekitKeyController,
    private readonly worker: Pick<Worker, 'terminate'>,
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
    });
    const publications = Object.freeze({
      publish: (
        publicationId: string,
        source: 'microphone' | 'camera' | 'screen',
        track: MediaStreamTrack,
      ) => this.publish(publicationId, source, track),
      unpublish: (publication: SfuPublishedTrack) => this.unpublish(publication),
      denySubscriptionsByDefault: () => this.denySubscriptionsByDefault(),
      setTrackAudience: (permissions: ReadonlyMap<string, readonly string[]>) => {
        this.setTrackAudience(permissions);
      },
    });
    const data = Object.freeze({
      publishOpaqueData: (payload: Uint8Array, topic: string, destinationIds: readonly string[]) => (
        this.publishOpaqueData(payload, topic, destinationIds)
      ),
    });
    const subscriptions = Object.freeze({
      applyRemoteSubscriptions: (publicationIds: ReadonlySet<string>) => {
        this.applyRemoteSubscriptions(publicationIds);
      },
      setRemotePublicationSubscribed: (publicationId: string, subscribed: boolean) => {
        this.setRemotePublicationSubscribed(publicationId, subscribed);
      },
    });
    const videoRender = Object.freeze({
      attachRemoteTrack: (track: SfuRemoteTrack, target: HTMLMediaElement) => (
        this.attachRemoteTrack(track, target)
      ),
      clear: () => this.clearRenderedTracks(),
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
      stats: UNSUPPORTED_STATS,
      videoRender,
      events,
    });
    return this.session;
  }

  private async connect(serverUrl: string, accessToken: string): Promise<void> {
    this.ensureOpen();
    if (this.connected) return;
    if (!this.connectPromise) {
      this.connectPromise = (async () => {
        await this.room.connect(serverUrl, accessToken, { autoSubscribe: false, maxRetries: 2 });
        await this.room.setE2EEEnabled(true);
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
    this.closePromise = this.cleanupInOrder();
    return this.closePromise;
  }

  private async rotateKey(keyMaterial: Uint8Array): Promise<void> {
    this.ensureConnected();
    await this.keyProvider.rotate(keyMaterial);
  }

  private async publish(
    publicationId: string,
    source: 'microphone' | 'camera' | 'screen',
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    this.ensureConnected();
    const publication = await this.room.localParticipant.publishTrack(track, {
      name: publicationId,
      source: toLivekitSource(source),
      stream: 'ananta-ordinary-media',
    });
    this.published.set(publicationId, publication);
    return { publicationId, trackSid: publication.trackSid, track };
  }

  private async unpublish(publication: SfuPublishedTrack): Promise<void> {
    const found = this.published.get(publication.publicationId);
    if (!found) return;
    this.published.delete(publication.publicationId);
    try {
      if (found.track) await this.room.localParticipant.unpublishTrack(found.track, true);
    } finally {
      safeStop(publication.track);
    }
  }

  private async publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
  ): Promise<void> {
    this.ensureConnected();
    if (payload.byteLength < 1 || payload.byteLength > 16_384) {
      throw new Error('sfu_data_payload_size_invalid');
    }
    if (!/^ananta\.(control|transcript|private-recovery)\.v1$/.test(topic)) {
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
    await this.room.localParticipant.publishData(copy, {
      reliable: true,
      topic,
      destinationIdentities: destinations,
    });
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
    ) => callback(Object.freeze({
      publicationId: publication.trackName,
      publisherId: participant.identity,
      track: track.mediaStreamTrack,
    }));
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
      if (!participant || !topic || !/^ananta\.(control|transcript|private-recovery)\.v1$/.test(topic)) return;
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
    const stream = new MediaStream([value.track]);
    target.srcObject = stream;
    void target.play().catch(() => undefined);
    return this.trackRenderRelease(() => {
      if (target.srcObject === stream) target.srcObject = null;
    });
  }

  private clearRenderedTracks(): void {
    for (const release of [...this.renderReleases].reverse()) release();
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

    for (const release of [...this.eventReleases].reverse()) {
      try { release(); } catch (error) { remember(error); }
    }
    for (const release of [...this.renderReleases].reverse()) {
      try { release(); } catch (error) { remember(error); }
    }
    for (const [publicationId, publication] of [...this.published].sort(([left], [right]) => (
      left.localeCompare(right)
    ))) {
      try {
        if (publication.track) await this.room.localParticipant.unpublishTrack(publication.track, true);
      } catch (error) {
        remember(error);
      } finally {
        if (publication.track) safeStop(publication.track.mediaStreamTrack);
        this.published.delete(publicationId);
      }
    }
    this.remotePublications.clear();
    try { await this.room.disconnect(true); } catch (error) { remember(error); }
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

  constructor() {
    super({ sharedKey: true, keySize: 256, ratchetWindowSize: 0, failureTolerance: -1 });
  }

  async initialize(keyMaterial: Uint8Array): Promise<void> {
    this.keyIndex = 0;
    await this.install(keyMaterial);
  }

  async rotate(keyMaterial: Uint8Array): Promise<void> {
    this.keyIndex = (this.keyIndex + 1) % 16;
    await this.install(keyMaterial);
  }

  private async install(keyMaterial: Uint8Array): Promise<void> {
    if (keyMaterial.byteLength !== 32) throw new Error('sfu_e2ee_key_invalid');
    const key = await createKeyMaterialFromBuffer(arrayBuffer(keyMaterial));
    this.onSetEncryptionKey(key, undefined, this.keyIndex);
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

function safeStop(track: MediaStreamTrack): void {
  try { track.stop(); } catch { /* deterministic cleanup */ }
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
