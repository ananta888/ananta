export type SfuPublicationSource = 'microphone' | 'camera' | 'screen';
export type SfuStatsCapability = 'available' | 'unsupported';
export type SfuRelease = () => void;

export interface SfuRemotePublication {
  readonly publicationId: string;
  readonly publisherId: string;
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

export interface SfuLifecyclePort {
  readonly e2eeSupported: boolean;
  connect(serverUrl: string, accessToken: string): Promise<void>;
  disconnect(): Promise<void>;
  destroy(): Promise<void>;
}

export interface SfuKeyPort {
  rotateKey(keyMaterial: Uint8Array): Promise<void>;
}

export interface SfuPublicationPort {
  publish(
    publicationId: string,
    source: SfuPublicationSource,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack>;
  unpublish(publication: SfuPublishedTrack): Promise<void>;
  denySubscriptionsByDefault(): void;
  setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void;
}

export interface SfuDataPort {
  publishOpaqueData(payload: Uint8Array, topic: string, destinationIds: readonly string[]): Promise<void>;
}

export interface SfuSubscriptionLayerPort {
  applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void;
  setRemotePublicationSubscribed(publicationId: string, subscribed: boolean): void;
}

/**
 * QOS-018 deliberately exposes only capability discovery. RTCStatsReport
 * projection belongs to QOS-012 and must not be approximated here.
 */
export interface SfuStatsPort {
  readonly capability: SfuStatsCapability;
}

export interface SfuVideoRenderPort {
  attachRemoteTrack(track: SfuRemoteTrack, target: HTMLMediaElement): SfuRelease;
  clear(): void;
}

export interface SfuRoomEventPort {
  onRemotePublication(callback: (publication: SfuRemotePublication) => void): SfuRelease;
  onRemoteTrackSubscribed(callback: (track: SfuRemoteTrack) => void): SfuRelease;
  onLocalTrackSubscribed(callback: (publicationId: string) => void): SfuRelease;
  onRemoteParticipantDisconnected(callback: (participantId: string) => void): SfuRelease;
  onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): SfuRelease;
  onDisconnected(callback: () => void): SfuRelease;
}

/** One room-scoped composition root. Consumers receive only the port they use. */
export interface SfuRoomSession {
  readonly lifecycle: SfuLifecyclePort;
  readonly key: SfuKeyPort;
  readonly publications: SfuPublicationPort;
  readonly data: SfuDataPort;
  readonly subscriptions: SfuSubscriptionLayerPort;
  readonly stats: SfuStatsPort;
  readonly videoRender: SfuVideoRenderPort;
  readonly events: SfuRoomEventPort;
}

export interface SfuRoomSessionFactory {
  create(keyMaterial: Uint8Array): Promise<SfuRoomSession>;
}
