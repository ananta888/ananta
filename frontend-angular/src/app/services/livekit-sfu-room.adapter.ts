import { InjectionToken } from '@angular/core';
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

export interface SfuRemotePublication {
  readonly publicationId: string;
  readonly publisherId: string;
  setSubscribed(value: boolean): void;
}

export interface SfuRemoteTrack {
  readonly publicationId: string;
  readonly publisherId: string;
  readonly track: MediaStreamTrack;
}

export interface SfuPublishedTrack {
  readonly publicationId: string;
  readonly trackSid: string;
  readonly track: MediaStreamTrack;
}

export interface SfuOpaqueDataPacket {
  readonly senderId: string;
  readonly topic: string;
  readonly payload: Uint8Array;
}

export interface SfuRoomPort {
  readonly e2eeSupported: boolean;
  connect(serverUrl: string, accessToken: string): Promise<void>;
  disconnect(): Promise<void>;
  rotateKey(keyMaterial: Uint8Array): Promise<void>;
  publish(publicationId: string, source: 'microphone' | 'camera' | 'screen', track: MediaStreamTrack): Promise<SfuPublishedTrack>;
  unpublish(publication: SfuPublishedTrack): Promise<void>;
  publishOpaqueData(payload: Uint8Array, topic: string, destinationIds: readonly string[]): Promise<void>;
  denySubscriptionsByDefault(): void;
  setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void;
  applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void;
  onRemotePublication(callback: (publication: SfuRemotePublication) => void): () => void;
  onRemoteTrackSubscribed(callback: (track: SfuRemoteTrack) => void): () => void;
  onLocalTrackSubscribed(callback: (publicationId: string) => void): () => void;
  onRemoteParticipantDisconnected(callback: (participantId: string) => void): () => void;
  onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): () => void;
  onDisconnected(callback: () => void): () => void;
}

export interface SfuRoomFactory { create(keyMaterial: Uint8Array): Promise<SfuRoomPort>; }

export const SFU_ROOM_FACTORY = new InjectionToken<SfuRoomFactory>('SFU_ROOM_FACTORY', {
  providedIn: 'root', factory: () => new LivekitSfuRoomFactory(),
});

export class LivekitSfuRoomFactory implements SfuRoomFactory {
  async create(keyMaterial: Uint8Array): Promise<SfuRoomPort> {
    if (!isE2EESupported()) throw new Error('sfu_e2ee_unsupported');
    const keyProvider = new IndexedSharedE2eeKeyProvider();
    await keyProvider.initialize(keyMaterial);
    const worker = new Worker(
      new URL('../../../node_modules/livekit-client/dist/livekit-client.e2ee.worker.mjs', import.meta.url),
      { type: 'module' },
    );
    return new LivekitSfuRoomAdapter(new Room({
      adaptiveStream: true, dynacast: true, singlePeerConnection: true,
      encryption: { keyProvider, worker }, disconnectOnPageLeave: true,
    }), keyProvider, worker);
  }
}

class LivekitSfuRoomAdapter implements SfuRoomPort {
  readonly e2eeSupported = true;
  private readonly published = new Map<string, LocalTrackPublication>();

  constructor(
    private readonly room: Room,
    private readonly keyProvider: IndexedSharedE2eeKeyProvider,
    private readonly worker: Worker,
  ) {}

  async connect(serverUrl: string, accessToken: string): Promise<void> {
    await this.room.connect(serverUrl, accessToken, { autoSubscribe: false, maxRetries: 2 });
    await this.room.setE2EEEnabled(true);
  }

  async disconnect(): Promise<void> {
    try { await this.room.disconnect(true); } finally { this.worker.terminate(); this.published.clear(); }
  }

  async rotateKey(keyMaterial: Uint8Array): Promise<void> { await this.keyProvider.rotate(keyMaterial); }

  async publish(
    publicationId: string,
    source: 'microphone' | 'camera' | 'screen',
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    const publication = await this.room.localParticipant.publishTrack(track, {
      name: publicationId, source: toLivekitSource(source), stream: 'ananta-ordinary-media',
    });
    this.published.set(publicationId, publication);
    return { publicationId, trackSid: publication.trackSid, track };
  }

  async unpublish(publication: SfuPublishedTrack): Promise<void> {
    const found = this.published.get(publication.publicationId);
    if (found?.track) await this.room.localParticipant.unpublishTrack(found.track, true);
    this.published.delete(publication.publicationId);
  }

  async publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
  ): Promise<void> {
    if (payload.byteLength < 1 || payload.byteLength > 16_384) {
      throw new Error('sfu_data_payload_size_invalid');
    }
    if (!/^ananta\.(control|transcript|private-recovery)\.v1$/.test(topic)) {
      throw new Error('sfu_data_topic_invalid');
    }
    const destinations = [...new Set(destinationIds)].sort();
    if (
      destinations.length < 1 ||
      destinations.length > 7 ||
      destinations.some(value => !identifier(value))
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

  denySubscriptionsByDefault(): void { this.room.localParticipant.setTrackSubscriptionPermissions(false, []); }

  setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void {
    const byParticipant = new Map<string, string[]>();
    for (const [trackSid, participants] of permissions) {
      for (const participantIdentity of participants) {
        const trackSids = byParticipant.get(participantIdentity) ?? [];
        trackSids.push(trackSid); byParticipant.set(participantIdentity, trackSids);
      }
    }
    this.room.localParticipant.setTrackSubscriptionPermissions(
      false,
      [...byParticipant].map(([participantIdentity, allowedTrackSids]) => ({
        participantIdentity, allowAll: false, allowedTrackSids: [...new Set(allowedTrackSids)].sort(),
      })),
    );
  }

  applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void {
    for (const participant of this.room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        publication.setSubscribed(publicationIds.has(publication.trackName));
      }
    }
  }

  onRemotePublication(callback: (publication: SfuRemotePublication) => void): () => void {
    const listener = (publication: RemoteTrackPublication, participant: { identity: string }) => callback({
      publicationId: publication.trackName,
      publisherId: participant.identity,
      setSubscribed: value => publication.setSubscribed(value),
    });
    this.room.on(RoomEvent.TrackPublished, listener);
    return () => { this.room.off(RoomEvent.TrackPublished, listener); };
  }

  onRemoteTrackSubscribed(callback: (value: SfuRemoteTrack) => void): () => void {
    const listener = (
      track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: { identity: string },
    ) => callback({
      publicationId: publication.trackName,
      publisherId: participant.identity,
      track: track.mediaStreamTrack,
    });
    this.room.on(RoomEvent.TrackSubscribed, listener);
    return () => { this.room.off(RoomEvent.TrackSubscribed, listener); };
  }

  onLocalTrackSubscribed(callback: (publicationId: string) => void): () => void {
    const listener = (publication: LocalTrackPublication) => callback(publication.trackName);
    this.room.on(RoomEvent.LocalTrackSubscribed, listener);
    return () => { this.room.off(RoomEvent.LocalTrackSubscribed, listener); };
  }

  onRemoteParticipantDisconnected(callback: (participantId: string) => void): () => void {
    const listener = (participant: { identity: string }) => callback(participant.identity);
    this.room.on(RoomEvent.ParticipantDisconnected, listener);
    return () => { this.room.off(RoomEvent.ParticipantDisconnected, listener); };
  }

  onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): () => void {
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
    return () => { this.room.off(RoomEvent.DataReceived, listener); };
  }

  onDisconnected(callback: () => void): () => void {
    this.room.on(RoomEvent.Disconnected, callback);
    return () => { this.room.off(RoomEvent.Disconnected, callback); };
  }
}

/**
 * LiveKit's ExternalE2EEKeyProvider does not advance the active key index when
 * setKey() is called after connect. Explicit indices are required so a random
 * membership-epoch key replaces, rather than merely caches beside, the old
 * sender key. A revoked member has no material at the new frame key index.
 */
class IndexedSharedE2eeKeyProvider extends BaseKeyProvider {
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
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}

function identifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}
