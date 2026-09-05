export type PeerOverlayDataClass = 'control' | 'rekey' | 'event' | 'semantic' | 'bulk';

export interface PeerOverlayTrafficProfile {
  readonly deliveryMode: 'reliable' | 'bounded_reliable';
  readonly ordering: 'ordered' | 'unordered';
  readonly queueMessages: number;
  readonly queueBytes: number;
  readonly retry: 'transport_only' | 'none';
  readonly overload: 'fail_closed' | 'drop_newest';
}

export const PEER_OVERLAY_DATA_CLASSES: readonly PeerOverlayDataClass[] = Object.freeze([
  'control', 'rekey', 'event', 'semantic', 'bulk',
]);

export const PEER_OVERLAY_PRIORITY: readonly PeerOverlayDataClass[] = Object.freeze([
  'control', 'rekey', 'event', 'semantic', 'bulk',
]);

export const PEER_OVERLAY_TRAFFIC_PROFILES: Readonly<Record<PeerOverlayDataClass, PeerOverlayTrafficProfile>>
  = Object.freeze({
    control: profile('reliable', 'ordered', 128, 512 * 1024, 'transport_only', 'fail_closed'),
    rekey: profile('reliable', 'ordered', 64, 256 * 1024, 'transport_only', 'fail_closed'),
    event: profile('bounded_reliable', 'ordered', 128, 2 * 1024 * 1024, 'none', 'drop_newest'),
    semantic: profile('bounded_reliable', 'ordered', 64, 4 * 1024 * 1024, 'none', 'drop_newest'),
    bulk: profile('bounded_reliable', 'unordered', 32, 8 * 1024 * 1024, 'none', 'drop_newest'),
  });

/** Peer-DAG media relay is deliberately unavailable under DG-01. */
export function requirePeerOverlayDataClass(value: unknown): PeerOverlayDataClass {
  if (typeof value !== 'string' || !PEER_OVERLAY_DATA_CLASSES.includes(value as PeerOverlayDataClass)) {
    if (value === 'audio' || value === 'video' || value === 'video_keyframe' || value === 'video_delta') {
      throw new Error('peer_overlay_media_class_forbidden');
    }
    throw new Error('peer_overlay_traffic_class_unknown');
  }
  return value as PeerOverlayDataClass;
}

function profile(
  deliveryMode: PeerOverlayTrafficProfile['deliveryMode'],
  ordering: PeerOverlayTrafficProfile['ordering'],
  queueMessages: number,
  queueBytes: number,
  retry: PeerOverlayTrafficProfile['retry'],
  overload: PeerOverlayTrafficProfile['overload'],
): PeerOverlayTrafficProfile {
  return Object.freeze({ deliveryMode, ordering, queueMessages, queueBytes, retry, overload });
}
