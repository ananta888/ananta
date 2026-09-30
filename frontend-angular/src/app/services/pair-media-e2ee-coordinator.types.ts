import { SemanticDataChannelMessage } from './webrtc-datachannel.service';

/** Public state and port contracts of the Public Pair media E2EE coordinator. */

export type PublicPairMediaE2eeStateName =
  | 'inactive'
  | 'awaiting-security'
  | 'awaiting-peer'
  | 'negotiating'
  | 'ready'
  | 'failed';

export interface PublicPairMediaE2eeState {
  readonly sessionId: string;
  readonly state: PublicPairMediaE2eeStateName;
  readonly reasonCode?: string;
  readonly contractDigest?: string;
}

/** Secret-free context for an exact local publication-consent generation. */
export interface PublicPairMediaPublicationContext {
  readonly sessionId: string;
  readonly securityEpoch: number;
  readonly contractDigest: string;
  readonly adapterGeneration: number;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly maxExpiresAtMs: number;
}

/** Lower-level port registered by WebrtcSessionService; no reverse DI edge. */
export interface PairMediaE2eeTransportPort {
  /** Generation-bound consent-channel readiness; never infer this from signaling. */
  isOpen(): boolean;
  send(message: SemanticDataChannelMessage): Promise<void>;
  disableMedia(reasonCode: string): void;
  failClosed(reasonCode: string): void;
}
