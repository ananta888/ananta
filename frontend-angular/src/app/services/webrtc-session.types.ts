/**
 * Shared shapes of WebrtcSessionService and its collaborators: peer state,
 * generation-bound contexts and the narrow session port the media
 * collaborators use instead of reaching into the service.
 */

export type PeerState = 'idle' | 'connecting' | 'connected' | 'failed' | 'closed';

export type PublicSdpPhase = 'none' | 'awaiting-offer' | 'processing-offer'
  | 'awaiting-answer' | 'processing-answer' | 'established';

export interface OrdinaryMediaStatsSnapshot {
  readonly connection: RTCPeerConnectionState;
  readonly stats: RTCStatsReport;
}

export interface AuditEvent {
  ts: number;
  type: string;
  session_id: string;
  detail?: string;
}

export interface ActivePublicMediaContext {
  readonly sessionId: string;
  readonly peer: RTCPeerConnection;
  readonly sessionGeneration: number;
  readonly adapterGeneration: number;
  readonly contractDigest: string;
}

export interface DataChannelReceiveContext {
  readonly peer: RTCPeerConnection;
  readonly channel: RTCDataChannel;
  readonly sessionId: string;
  readonly sessionGeneration: number;
}

export interface SemanticSendContext {
  readonly peer: RTCPeerConnection | null;
  readonly sessionId: string;
  readonly sessionGeneration: number;
  readonly channel?: RTCDataChannel;
}

/**
 * Read-only view of the current peer generation plus the few session
 * operations collaborators may trigger. Implemented by WebrtcSessionService.
 */
export interface WebrtcSessionContextPort {
  readonly peer: RTCPeerConnection | null;
  readonly sessionId: string;
  readonly sessionGeneration: number;
  isCurrentSession(peer: RTCPeerConnection, sessionId: string, generation: number): boolean;
  negotiateMedia(): Promise<void>;
  audit(type: string, detail?: string): void;
}

export function errorReasonCode(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function validSecurityEpoch(epoch: number): number {
  if (!Number.isSafeInteger(epoch) || epoch < 1) {
    throw new Error('webrtc_signal_epoch_invalid');
  }
  return epoch;
}
