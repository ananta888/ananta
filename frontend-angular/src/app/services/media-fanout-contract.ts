export const MEDIA_FANOUT_FOCUSED_PORTS = Object.freeze([
  'lifecycle',
  'publications',
  'subscriptions',
  'stats',
  'events',
] as const);

export type MediaFanoutFocusedPortName = typeof MEDIA_FANOUT_FOCUSED_PORTS[number];

/** Shared structural boundary used by direct/mesh/peer and SFU adapters. */
export type MediaFanoutPortSet = Readonly<Record<MediaFanoutFocusedPortName, object>>;

export class MediaFanoutUnsupportedError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

export function assertMediaFanoutPortSet(value: object): asserts value is MediaFanoutPortSet {
  const candidate = value as Partial<Record<MediaFanoutFocusedPortName, unknown>>;
  for (const port of MEDIA_FANOUT_FOCUSED_PORTS) {
    if (!candidate[port] || typeof candidate[port] !== 'object') {
      throw new MediaFanoutUnsupportedError(`media_fanout_${port}_unsupported`);
    }
  }
}
