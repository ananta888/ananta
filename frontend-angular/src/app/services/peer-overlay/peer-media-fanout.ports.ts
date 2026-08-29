export type PeerMediaSlot = 'microphone' | 'camera' | 'screen';
export type PeerLinkState = 'connecting' | 'connected' | 'failed' | 'closed';

/** Opaque browser track handle; the manager does not own WebRTC SDK types. */
export interface PeerMediaTrackHandle {
  readonly trackId: string;
  readonly kind: 'audio' | 'video';
  readonly value: unknown;
}

export interface ValidatedPeerLinkTicket {
  readonly validation: 'hub-link-ticket-accepted-v1';
  readonly ticketId: string;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly publicationId: string;
  readonly routeEpoch: number;
  readonly icePolicy: 'all' | 'relay';
  readonly expiresAtMs: number;
}

export interface PeerLinkLifecyclePort {
  readonly remotePeerId: string;
  readonly state: PeerLinkState;
  close(): void;
  restartIce(): void;
}

export interface PeerLinkPublicationPort {
  setTrack(slot: PeerMediaSlot, track: PeerMediaTrackHandle | null): Promise<void>;
  setMuted(slot: PeerMediaSlot, muted: boolean): Promise<void>;
}

export interface PeerLinkDataPort {
  readonly bufferedAmount: number;
  sendOpaque(payload: Uint8Array): void;
}

export interface PeerLinkObservation {
  readonly observedAtMs: number;
  readonly roundTripTimeMs: number | null;
  readonly availableOutgoingBitrate: number | null;
  readonly packetsLost: number | null;
  readonly framesDropped: number | null;
  readonly qualityLimitationReason: string | null;
}

export interface PeerLinkObservationPort {
  observe(): Promise<PeerLinkObservation>;
}

export interface PeerLinkSession {
  readonly lifecycle: PeerLinkLifecyclePort;
  readonly publications: PeerLinkPublicationPort;
  readonly data: PeerLinkDataPort;
  readonly observations: PeerLinkObservationPort;
}

export interface PeerLinkSessionFactory {
  create(ticket: ValidatedPeerLinkTicket): Promise<PeerLinkSession>;
}

export const PEER_LINK_SESSION_FACTORY = new InjectionToken<PeerLinkSessionFactory>(
  'PEER_LINK_SESSION_FACTORY',
  {
    providedIn: 'root',
    factory: () => ({
      create: async () => { throw new PeerFanoutUnsupportedError('peer_link_adapter_unavailable'); },
    }),
  },
);

export class PeerFanoutUnsupportedError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}
import { InjectionToken } from '@angular/core';
