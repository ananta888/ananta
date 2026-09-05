export type PeerEdgePrivacyMode = 'direct_preferred' | 'automatic' | 'relay_only';

export interface PeerEdgeEpochs {
  readonly membership: number;
  readonly route: number;
  readonly key: number;
}

export interface AcceptedPeerEdgeTicket {
  readonly validation: 'hub-edge-ticket-accepted-v1';
  readonly ticketId: string;
  readonly tenantId: string;
  readonly roomId: string;
  readonly publicationId: string;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly epochs: PeerEdgeEpochs;
  readonly icePolicy: 'all' | 'relay';
  readonly expiresAtMs: number;
}

export interface AcceptedPeerTurnCredential {
  readonly validation: 'hub-turn-credential-accepted-v1';
  readonly tenantId: string;
  readonly roomId: string;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly epochs: PeerEdgeEpochs;
  readonly urls: string | readonly string[];
  readonly username: string;
  readonly credential: string;
  readonly issuedAtMs: number;
  readonly expiresAtMs: number;
}

export interface PeerEdgeNetworkDecision {
  readonly rtcConfiguration: RTCConfiguration;
  readonly privacyMode: PeerEdgePrivacyMode;
  readonly neighborIpVisible: boolean;
  readonly turnRequired: boolean;
  readonly notice: string;
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;
const MAX_TURN_TTL_MS = 10 * 60 * 1_000;

/** Builds an independent RTC configuration for one exact Hub-authorized edge. */
export class PeerEdgeNetworkPolicy {
  constructor(private readonly clock: () => number = () => Date.now()) {}

  decide(
    ticket: AcceptedPeerEdgeTicket,
    privacyMode: PeerEdgePrivacyMode,
    baseIceServers: readonly RTCIceServer[],
    turnCredential?: AcceptedPeerTurnCredential,
  ): PeerEdgeNetworkDecision {
    this.validateTicket(ticket);
    const turn = turnCredential ? this.validateTurn(ticket, turnCredential) : null;
    if (privacyMode === 'relay_only' && ticket.icePolicy !== 'relay') {
      throw new Error('peer_edge_relay_only_ticket_required');
    }
    if (ticket.icePolicy === 'relay' && privacyMode !== 'relay_only') {
      throw new Error('peer_edge_privacy_ticket_mismatch');
    }
    if (privacyMode === 'relay_only' && !turn) throw new Error('peer_edge_turn_credential_required');
    const sanitized = baseIceServers.flatMap(server => cloneStunServer(server));
    if (turn) sanitized.push({ urls: [...turn.urls], username: turn.username, credential: turn.credential });
    if (privacyMode === 'relay_only' && !sanitized.some(hasTurnUrl)) {
      throw new Error('peer_edge_turn_server_required');
    }
    return Object.freeze({
      rtcConfiguration: Object.freeze({
        iceServers: sanitized,
        iceTransportPolicy: privacyMode === 'relay_only' ? 'relay' : 'all',
      }),
      privacyMode,
      neighborIpVisible: privacyMode !== 'relay_only',
      turnRequired: privacyMode === 'relay_only',
      notice: privacyMode === 'relay_only'
        ? 'Relay-only verhindert direkte Nachbarverbindungen und benötigt erreichbares TURN; Kosten und Latenz können steigen.'
        : 'Bei einer direkten Verbindung sehen unmittelbare Nachbarn Verbindungsmetadaten einschließlich der verwendeten IP. mDNS und E2EE verbergen diese nicht vor dem verbundenen Nachbarn.',
    });
  }

  authorizeIceRestart(previous: AcceptedPeerEdgeTicket, successor: AcceptedPeerEdgeTicket): void {
    this.validateTicket(previous);
    this.validateTicket(successor);
    const immutable = ['tenantId', 'roomId', 'publicationId', 'localPeerId', 'remotePeerId'] as const;
    if (immutable.some(field => previous[field] !== successor[field])
        || successor.ticketId === previous.ticketId
        || successor.epochs.membership !== previous.epochs.membership
        || successor.epochs.key !== previous.epochs.key
        || successor.epochs.route < previous.epochs.route) {
      throw new Error('peer_edge_ice_restart_fence_invalid');
    }
  }

  private validateTicket(ticket: AcceptedPeerEdgeTicket): void {
    if (ticket.validation !== 'hub-edge-ticket-accepted-v1'
        || ![ticket.ticketId, ticket.tenantId, ticket.roomId, ticket.publicationId,
          ticket.localPeerId, ticket.remotePeerId].every(value => ID_RE.test(value))
        || ticket.localPeerId === ticket.remotePeerId
        || !validEpochs(ticket.epochs)
        || !['all', 'relay'].includes(ticket.icePolicy)
        || ticket.expiresAtMs <= this.clock()) throw new Error('peer_edge_ticket_invalid');
  }

  private validateTurn(
    ticket: AcceptedPeerEdgeTicket,
    credential: AcceptedPeerTurnCredential,
  ): { readonly urls: readonly string[]; readonly username: string; readonly credential: string } {
    const urls = typeof credential.urls === 'string' ? [credential.urls] : [...credential.urls];
    if (credential.validation !== 'hub-turn-credential-accepted-v1'
        || credential.tenantId !== ticket.tenantId || credential.roomId !== ticket.roomId
        || credential.localPeerId !== ticket.localPeerId || credential.remotePeerId !== ticket.remotePeerId
        || !sameEpochs(credential.epochs, ticket.epochs)
        || credential.issuedAtMs > this.clock() || credential.expiresAtMs <= this.clock()
        || credential.expiresAtMs > ticket.expiresAtMs
        || credential.expiresAtMs - credential.issuedAtMs > MAX_TURN_TTL_MS
        || !urls.length || urls.some(url => !/^turns?:[^\s]+$/i.test(url))
        || !credential.username || !credential.credential) throw new Error('peer_edge_turn_credential_invalid');
    return Object.freeze({ urls: Object.freeze(urls), username: credential.username, credential: credential.credential });
  }
}

function validEpochs(value: PeerEdgeEpochs): boolean {
  return [value.membership, value.route, value.key]
    .every(epoch => Number.isSafeInteger(epoch) && epoch >= 1);
}

function sameEpochs(left: PeerEdgeEpochs, right: PeerEdgeEpochs): boolean {
  return left.membership === right.membership && left.route === right.route && left.key === right.key;
}

function cloneStunServer(server: RTCIceServer): RTCIceServer[] {
  const input = typeof server.urls === 'string' ? [server.urls] : [...server.urls];
  const urls = input.filter(url => /^stuns?:/i.test(url));
  if (!urls.length) return [];
  return [{ urls: typeof server.urls === 'string' ? urls[0] : urls }];
}

function hasTurnUrl(server: RTCIceServer): boolean {
  const urls = typeof server.urls === 'string' ? [server.urls] : server.urls;
  return [...urls].some(url => /^turns?:/i.test(url));
}
