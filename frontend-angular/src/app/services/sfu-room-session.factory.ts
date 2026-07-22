import { InjectionToken, inject } from '@angular/core';

import { createLivekitSfuRoomSession } from './livekit-sfu-room.adapter';
import type {
  SfuDataPort,
  SfuKeyPort,
  SfuLifecyclePort,
  SfuOpaqueDataPacket,
  SfuPublicationPort,
  SfuPublishedTrack,
  SfuRelease,
  SfuRemotePublication,
  SfuRemoteTrack,
  SfuRoomEventPort,
  SfuRoomSession,
  SfuRoomSessionFactory,
  SfuStatsPort,
  SfuSubscriptionLayerPort,
  SfuVideoRenderPort,
} from './sfu-room-session.ports';

export const SFU_ROOM_SESSION_FACTORY = new InjectionToken<SfuRoomSessionFactory>(
  'SFU_ROOM_SESSION_FACTORY',
  { providedIn: 'root', factory: () => new LivekitSfuRoomSessionFactory() },
);

export class LivekitSfuRoomSessionFactory implements SfuRoomSessionFactory {
  create(
    keyMaterial: Uint8Array,
    options?: Readonly<{ layerControlMode: 'adaptive_stream' | 'manual_quality' }>,
  ): Promise<SfuRoomSession> {
    return createLivekitSfuRoomSession(keyMaterial, options);
  }
}

export interface LegacySfuRemotePublication extends SfuRemotePublication {
  setSubscribed(value: boolean): void;
}

/**
 * @deprecated Use one of the focused SfuRoomSession ports. This compatibility
 * surface exists only for the pre-QOS-018 group E2E driver.
 */
export interface SfuRoomPort extends
  SfuLifecyclePort,
  SfuKeyPort,
  SfuPublicationPort,
  SfuDataPort,
  SfuSubscriptionLayerPort,
  SfuStatsPort,
  SfuVideoRenderPort {
  onRemotePublication(callback: (publication: LegacySfuRemotePublication) => void): SfuRelease;
  onRemoteTrackSubscribed(callback: (track: SfuRemoteTrack) => void): SfuRelease;
  onLocalTrackSubscribed(callback: (publicationId: string) => void): SfuRelease;
  onRemoteParticipantDisconnected(callback: (participantId: string) => void): SfuRelease;
  onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): SfuRelease;
  onDisconnected(callback: () => void): SfuRelease;
}

/** @deprecated Use SfuRoomSessionFactory. */
export interface SfuRoomFactory {
  create(keyMaterial: Uint8Array): Promise<SfuRoomPort>;
}

const legacyFacades = new WeakMap<SfuRoomSession, SfuRoomPort>();

/** @deprecated Kept as an adapter at the compatibility boundary only. */
export function legacySfuRoomFacade(session: SfuRoomSession): SfuRoomPort {
  const existing = legacyFacades.get(session);
  if (existing) return existing;
  const facade = new LegacySfuRoomFacade(session);
  legacyFacades.set(session, facade);
  return facade;
}

/** @deprecated Contains no state and registers no listeners of its own. */
export class LegacySfuRoomFacade implements SfuRoomPort {
  constructor(private readonly session: SfuRoomSession) {}

  get e2eeSupported(): boolean { return this.session.lifecycle.e2eeSupported; }
  get capability(): SfuStatsPort['capability'] { return this.session.stats.capability; }

  connect(serverUrl: string, accessToken: string): Promise<void> {
    return this.session.lifecycle.connect(serverUrl, accessToken);
  }

  disconnect(): Promise<void> { return this.session.lifecycle.disconnect(); }
  destroy(): Promise<void> { return this.session.lifecycle.destroy(); }
  rotateKey(keyMaterial: Uint8Array): Promise<void> { return this.session.key.rotateKey(keyMaterial); }

  publish(
    publicationId: string,
    source: 'microphone' | 'camera' | 'screen',
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    return this.session.publications.publish(publicationId, source, track);
  }

  unpublish(publication: SfuPublishedTrack): Promise<void> {
    return this.session.publications.unpublish(publication);
  }

  publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
    options?: Readonly<{ reliable: boolean }>,
  ): Promise<void> {
    return this.session.data.publishOpaqueData(payload, topic, destinationIds, options);
  }

  denySubscriptionsByDefault(): void { this.session.publications.denySubscriptionsByDefault(); }

  setTrackAudience(permissions: ReadonlyMap<string, readonly string[]>): void {
    this.session.publications.setTrackAudience(permissions);
  }

  applyRemoteSubscriptions(publicationIds: ReadonlySet<string>): void {
    this.session.subscriptions.applyRemoteSubscriptions(publicationIds);
  }

  setRemotePublicationSubscribed(publicationId: string, subscribed: boolean): void {
    this.session.subscriptions.setRemotePublicationSubscribed(publicationId, subscribed);
  }

  attachRemoteTrack(track: SfuRemoteTrack, target: HTMLMediaElement): SfuRelease {
    return onceRelease(this.session.videoRender.attachRemoteTrack(track, target));
  }

  clear(): void { this.session.videoRender.clear(); }

  onRemotePublication(callback: (publication: LegacySfuRemotePublication) => void): SfuRelease {
    return onceRelease(this.session.events.onRemotePublication(publication => callback(Object.freeze({
      ...publication,
      setSubscribed: (value: boolean) => {
        this.session.subscriptions.setRemotePublicationSubscribed(publication.publicationId, value);
      },
    }))));
  }

  onRemoteTrackSubscribed(callback: (track: SfuRemoteTrack) => void): SfuRelease {
    return onceRelease(this.session.events.onRemoteTrackSubscribed(callback));
  }

  onLocalTrackSubscribed(callback: (publicationId: string) => void): SfuRelease {
    return onceRelease(this.session.events.onLocalTrackSubscribed(callback));
  }

  onRemoteParticipantDisconnected(callback: (participantId: string) => void): SfuRelease {
    return onceRelease(this.session.events.onRemoteParticipantDisconnected(callback));
  }

  onOpaqueDataReceived(callback: (packet: SfuOpaqueDataPacket) => void): SfuRelease {
    return onceRelease(this.session.data.onOpaqueDataReceived?.(callback)
      ?? this.session.events.onOpaqueDataReceived(callback));
  }

  onDisconnected(callback: () => void): SfuRelease {
    return onceRelease(this.session.events.onDisconnected(callback));
  }
}

function onceRelease(release: SfuRelease): SfuRelease {
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    release();
  };
}

/** @deprecated Existing integrations should migrate to SFU_ROOM_SESSION_FACTORY. */
export class LivekitSfuRoomFactory implements SfuRoomFactory {
  constructor(
    private readonly sessions: SfuRoomSessionFactory = new LivekitSfuRoomSessionFactory(),
  ) {}

  async create(keyMaterial: Uint8Array): Promise<SfuRoomPort> {
    return legacySfuRoomFacade(await this.sessions.create(keyMaterial));
  }
}

/** @deprecated Compatibility injection token; new consumers must not import it. */
export const SFU_ROOM_FACTORY = new InjectionToken<SfuRoomFactory>('SFU_ROOM_FACTORY', {
  providedIn: 'root',
  factory: () => new LivekitSfuRoomFactory(inject(SFU_ROOM_SESSION_FACTORY)),
});
