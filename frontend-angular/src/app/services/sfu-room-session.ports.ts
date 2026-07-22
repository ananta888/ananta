export type SfuPublicationSource = 'microphone' | 'camera' | 'screen';
export type SfuStatsCapability = 'available' | 'unsupported';
export type SfuRelease = () => void;

export type SfuPublicationCodecClass =
  | 'audio_opus' | 'video_vp8' | 'video_h264' | 'video_vp9' | 'video_av1';
export type SfuPublicationEncodingClass =
  | 'audio_primary' | 'video_baseline' | 'video_enhancement' | 'screenshare_detail';
export type SfuPublicationRidClass = 'none' | 'low' | 'medium' | 'high';
export type SfuPublicationScalabilityClass =
  | 'none' | 'simulcast' | 'svc_l1t2' | 'svc_l1t3' | 'svc_l2t2' | 'svc_l3t3';

export interface SfuPublisherEncodingProjection {
  readonly encodingClass: SfuPublicationEncodingClass;
  readonly codecClass: SfuPublicationCodecClass;
  readonly ridClass: SfuPublicationRidClass;
  readonly scalabilityClass: SfuPublicationScalabilityClass;
  readonly maxBitrateBps: number;
  readonly maxWidth: number | null;
  readonly maxHeight: number | null;
  readonly maxFps: number | null;
}

/** Created only from a signature-validated Hub publisher projection. */
export interface SfuValidatedPublisherProjection {
  readonly validation: 'hub-contract-accepted-v1';
  readonly publicationId: string;
  readonly source: SfuPublicationSource;
  readonly mediaKind: 'audio' | 'video';
  readonly projectionVersion: number;
  readonly routeEpoch: number;
  readonly keyEpoch: number;
  readonly encodings: readonly SfuPublisherEncodingProjection[];
}

/** Values are nullable unless the public SDK exposed the corresponding fact. */
export interface SfuPublicationObservation {
  readonly status: 'observed' | 'unsupported';
  readonly codecClass: SfuPublicationCodecClass | null;
  readonly simulcasted: boolean | null;
  readonly activeEncodingCount: number | null;
  readonly observedRidCount: number | null;
  readonly scalabilityClass: SfuPublicationScalabilityClass | null;
}

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
  readonly projectionVersion?: number;
  readonly routeEpoch?: number;
  readonly keyEpoch?: number;
  readonly observation?: SfuPublicationObservation;
}

/** Opaque adapter-owned token. It deliberately contains no SDK or media track. */
export interface SfuRemoteVideoHandle {
  readonly handleId: string;
  readonly source: 'camera' | 'screen';
}

export interface SfuStatsSnapshot {
  readonly observedAtMs: number;
  readonly bytesReceived: number;
  readonly packetsReceived: number;
  readonly packetsLost: number;
  readonly framesDecoded: number;
  readonly totalDecodeTimeSeconds: number;
  readonly totalFreezeDurationSeconds: number;
  readonly jitterSeconds: number | null;
  readonly roundTripTimeSeconds: number | null;
}

export interface SfuStatsBinding {
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly subscriptionRef: string;
  readonly publicationRef: string;
  readonly routeEpoch: number;
  readonly membershipEpoch: number;
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
  rotateKeyAtEpoch?(keyMaterial: Uint8Array, keyEpoch: number): Promise<void>;
}

export interface SfuPublicationPort {
  /** @deprecated Compatibility path until every caller consumes a Hub projection. */
  publish(
    publicationId: string,
    source: SfuPublicationSource,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack>;
  publishProjected?(
    projection: SfuValidatedPublisherProjection,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack>;
  replaceProjectedTrack?(
    publication: SfuPublishedTrack,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack>;
  setProjectedTrackPaused?(publication: SfuPublishedTrack, paused: boolean): Promise<void>;
  unpublish(publication: SfuPublishedTrack): Promise<void>;
  denySubscriptionsByDefault(): void;
  setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void;
}

export interface SfuDataPort {
  publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
    options?: Readonly<{ reliable: boolean }>,
  ): Promise<void>;
  onOpaqueDataReceived?(callback: (packet: SfuOpaqueDataPacket) => void): SfuRelease;
}

export interface SfuSubscriptionLayerPort {
  /** Locked before Room construction. Adaptive mode never accepts manual writes. */
  readonly layerControlMode?: 'adaptive_stream' | 'manual_quality';
  applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void;
  setRemotePublicationSubscribed(publicationId: string, subscribed: boolean): void;
  setRemotePublicationQuality?(
    publicationId: string,
    quality: 'low' | 'medium' | 'high',
  ): Promise<void>;
}

/**
 * QOS-018 deliberately exposes only capability discovery. RTCStatsReport
 * projection belongs to QOS-012 and must not be approximated here.
 */
export interface SfuStatsPort {
  readonly capability: SfuStatsCapability;
  read?(handle: SfuRemoteVideoHandle): Promise<SfuStatsSnapshot | null>;
  authorizesQualityBinding?(
    handle: SfuRemoteVideoHandle,
    binding: SfuStatsBinding,
  ): boolean;
}

export interface SfuVideoRenderPort {
  /** @deprecated Raw-track compatibility boundary; new UI uses attachRemoteVideo. */
  attachRemoteTrack(track: SfuRemoteTrack, target: HTMLMediaElement): SfuRelease;
  attachRemoteVideo?(handle: SfuRemoteVideoHandle, target: HTMLVideoElement): SfuRelease;
  matchesPublication?(handle: SfuRemoteVideoHandle, publicationId: string): boolean;
  onRemoteVideoAvailable?(callback: (handle: SfuRemoteVideoHandle) => void): SfuRelease;
  onRemoteVideoUnavailable?(
    callback: (value: Readonly<{ handleId: string; reason: 'unsubscribed' | 'ended' }>) => void,
  ): SfuRelease;
  onRemoteVideoStateChanged?(
    callback: (value: Readonly<{ handleId: string; state: 'active' | 'muted' }>) => void,
  ): SfuRelease;
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
  create(
    keyMaterial: Uint8Array,
    options?: Readonly<{ layerControlMode: 'adaptive_stream' | 'manual_quality' }>,
  ): Promise<SfuRoomSession>;
}
