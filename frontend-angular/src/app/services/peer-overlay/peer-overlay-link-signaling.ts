export type PeerLinkSignalKind = 'offer' | 'answer' | 'ice_candidate' | 'end_of_candidates';

export interface AcceptedPeerLinkSignalingTicket {
  readonly validation: 'hub-link-ticket-accepted-v1';
  readonly ticketId: string;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly publicationId: string;
  readonly routeEpoch: number;
  readonly offererPeerId: string;
  readonly expiresAtMs: number;
}

export interface PeerLinkSignalV1 {
  readonly version: 1;
  readonly ticketId: string;
  readonly routeEpoch: number;
  readonly senderPeerId: string;
  readonly recipientPeerId: string;
  readonly kind: PeerLinkSignalKind;
  readonly sequence: number;
  readonly payload: string | null;
}

export interface PeerLinkSignalingTransportPort {
  inBandReady(remotePeerId: string): boolean;
  sendInBand(signal: PeerLinkSignalV1): Promise<void>;
  sendViaHub(signal: PeerLinkSignalV1): Promise<void>;
}

export interface PeerLinkSignalResult {
  readonly transport: 'in_band' | 'hub_fallback';
  readonly signal: PeerLinkSignalV1;
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;
const FORBIDDEN_SECRET = /(?:authorization\s*:|bearer\s+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)/i;
const MAX_SIGNAL_BYTES = 64 * 1024;
const MAX_TICKETS = 1_024;
const MAX_SIGNALS_PER_TICKET = 128;
const MAX_ICE_CANDIDATES_PER_TICKET = 64;

/** Routes exact-edge signaling; it never creates topology or ticket authority. */
export class PeerOverlayLinkSignaling {
  private readonly sequences = new Map<string, number>();
  private readonly iceCandidates = new Map<string, number>();
  private readonly consumedOffers = new Set<string>();

  constructor(
    private readonly transport: PeerLinkSignalingTransportPort,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  async send(
    ticket: AcceptedPeerLinkSignalingTicket,
    kind: PeerLinkSignalKind,
    payload: string | null,
  ): Promise<PeerLinkSignalResult> {
    this.validateTicket(ticket);
    this.validatePayload(kind, payload);
    if (kind === 'offer') {
      if (ticket.localPeerId !== ticket.offererPeerId) throw new Error('peer_link_offer_role_denied');
      if (this.consumedOffers.has(ticket.ticketId)) throw new Error('peer_link_ticket_already_consumed');
      if (this.consumedOffers.size >= MAX_TICKETS) throw new Error('peer_link_ticket_budget_exceeded');
      this.consumedOffers.add(ticket.ticketId);
    }
    const sequence = (this.sequences.get(ticket.ticketId) ?? 0) + 1;
    const candidateCount = kind === 'ice_candidate' ? (this.iceCandidates.get(ticket.ticketId) ?? 0) + 1 : 0;
    if (sequence > MAX_SIGNALS_PER_TICKET || candidateCount > MAX_ICE_CANDIDATES_PER_TICKET) {
      throw new Error('peer_link_signal_budget_exceeded');
    }
    this.sequences.set(ticket.ticketId, sequence);
    if (kind === 'ice_candidate') this.iceCandidates.set(ticket.ticketId, candidateCount);
    const signal = Object.freeze({
      version: 1 as const,
      ticketId: ticket.ticketId,
      routeEpoch: ticket.routeEpoch,
      senderPeerId: ticket.localPeerId,
      recipientPeerId: ticket.remotePeerId,
      kind,
      sequence,
      payload,
    });
    if (this.transport.inBandReady(ticket.remotePeerId)) {
      try {
        await this.transport.sendInBand(signal);
        return Object.freeze({ transport: 'in_band', signal });
      } catch {
        // A partition can race with readiness; the Hub remains rendezvous owner.
      }
    }
    await this.transport.sendViaHub(signal);
    return Object.freeze({ transport: 'hub_fallback', signal });
  }

  private validateTicket(ticket: AcceptedPeerLinkSignalingTicket): void {
    if (ticket.validation !== 'hub-link-ticket-accepted-v1'
        || ![ticket.ticketId, ticket.localPeerId, ticket.remotePeerId, ticket.publicationId, ticket.offererPeerId]
          .every(value => ID_RE.test(value))
        || ticket.localPeerId === ticket.remotePeerId
        || ![ticket.localPeerId, ticket.remotePeerId].includes(ticket.offererPeerId)
        || !Number.isSafeInteger(ticket.routeEpoch) || ticket.routeEpoch < 1
        || ticket.expiresAtMs <= this.clock()) throw new Error('peer_link_ticket_invalid');
  }

  private validatePayload(kind: PeerLinkSignalKind, payload: string | null): void {
    if ((kind === 'end_of_candidates') !== (payload === null)) throw new Error('peer_link_signal_payload_invalid');
    if (payload !== null) {
      const bytes = new TextEncoder().encode(payload).byteLength;
      if (!bytes || bytes > MAX_SIGNAL_BYTES || FORBIDDEN_SECRET.test(payload)) {
        throw new Error('peer_link_signal_payload_invalid');
      }
    }
  }
}
