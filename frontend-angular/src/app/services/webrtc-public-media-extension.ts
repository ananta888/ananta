import { Subscription } from 'rxjs';

import type { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import type { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import type { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import type { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';
import type { SemanticDataChannelMessage } from './webrtc-datachannel.service';
import type {
  ActivePublicMediaContext,
  SemanticSendContext,
  WebrtcSessionContextPort,
} from './webrtc-session.types';

/** Session operations the Public media extension may use. */
export interface WebrtcPublicMediaSessionPort extends WebrtcSessionContextPort {
  readonly dataChannel: RTCDataChannel | null;
  sendSemantic(message: SemanticDataChannelMessage, context: SemanticSendContext): Promise<unknown>;
  /** Fails the whole WebRTC session closed. */
  failSession(): void;
  emitRemoteTrack(event: RTCTrackEvent): void;
}

export interface WebrtcPublicMediaDeps {
  readonly controlPlane: PairSessionControlPlaneService;
  readonly securityBootstrap: PairViewSecurityBootstrapService;
  readonly pairMediaE2ee: PairMediaE2eeCoordinatorService;
  readonly pairMediaTransforms: PairMediaE2eeTransformAdapter;
}

export type PublicMediaPreparation =
  | 'continue'
  | 'superseded'
  | Readonly<{ recreateReason: string }>;

/**
 * Optional Public Pair media E2EE extension of one WebRTC session service:
 * prepares the transform adapter and consent transport per peer generation,
 * gates remote tracks DROP-first until the coordinator is ready, binds the
 * offered topology and remembers contracts on which media was disabled.
 * The peer connection and its lifecycle stay owned by WebrtcSessionService.
 */
export class WebrtcPublicMediaExtension {
  private readonly pendingTracks = new Map<PublicPairMediaSlot, RTCTrackEvent>();
  private readonly disabledContracts = new Map<string, string>();
  private activeContext: ActivePublicMediaContext | null = null;
  private readonly statusSubscription: Subscription;

  constructor(
    private readonly session: WebrtcPublicMediaSessionPort,
    private readonly deps: WebrtcPublicMediaDeps,
  ) {
    this.statusSubscription = this.deps.pairMediaE2ee.status$.subscribe(status => {
      if (!this.session.sessionId || status.sessionId !== this.session.sessionId) return;
      if (status.state === 'ready') {
        this.releasePendingTracks();
        return;
      }
      if (status.state === 'failed') {
        this.stopPendingTracks();
      }
    });
  }

  /** Detaches the active context for teardown; see releaseSession. */
  takeActiveContext(): ActivePublicMediaContext | null {
    const context = this.activeContext;
    this.activeContext = null;
    return context;
  }

  releaseSession(closingSessionId: string, mediaContext: ActivePublicMediaContext | null): void {
    if (mediaContext?.sessionId === closingSessionId) {
      this.deps.pairMediaTransforms.releaseSession(closingSessionId, mediaContext.adapterGeneration);
    } else {
      // Covers cancellation while prepareSession is still awaiting worker
      // ACKs. This is the current generation and cannot target a replacement.
      this.deps.pairMediaTransforms.releaseSession(closingSessionId);
    }
  }

  /**
   * Prepares the optional Public media E2EE extension on a fresh peer. The
   * session must stop on 'superseded' and recreate a data-only peer when a
   * recreate reason is returned.
   */
  async prepare(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
    isInitiator: boolean,
    publicSession: boolean,
  ): Promise<PublicMediaPreparation> {
    const publicMediaContract = publicSession
      ? this.deps.securityBootstrap.mediaContractFor(sessionId) : null;
    if (publicMediaContract
        && this.disabledContracts.get(sessionId) !== publicMediaContract.digest) {
      let adapterGeneration: number | null = null;
      try {
        adapterGeneration = await this.deps.pairMediaTransforms.prepareSession(
          pc,
          sessionId,
          publicMediaContract,
          reasonCode => {
            const context = this.activeContext;
            if (
              adapterGeneration === null
              || !context
              || context.peer !== pc
              || context.sessionId !== sessionId
              || context.sessionGeneration !== generation
              || context.adapterGeneration !== adapterGeneration
              || !this.session.isCurrentSession(pc, sessionId, generation)
            ) return;
            this.deps.pairMediaE2ee.failMediaExtension(sessionId, reasonCode);
          },
          isInitiator ? 'offerer' : 'answerer',
        );
        if (!this.session.isCurrentSession(pc, sessionId, generation)) {
          this.deps.pairMediaTransforms.releaseSession(sessionId, adapterGeneration);
          pc.close();
          return 'superseded';
        }
        const preparedPeer = pc;
        const preparedAdapterGeneration = adapterGeneration;
        const matchesPreparedContext = (): boolean => {
          const context = this.activeContext;
          return !!context
            && context.peer === preparedPeer
            && context.sessionId === sessionId
            && context.sessionGeneration === generation
            && context.adapterGeneration === preparedAdapterGeneration
            && this.session.isCurrentSession(preparedPeer, sessionId, generation);
        };
        this.deps.pairMediaE2ee.bindTransport(sessionId, {
          isOpen: () => matchesPreparedContext()
            && this.session.dataChannel?.readyState === 'open',
          send: async message => {
            if (!matchesPreparedContext()) throw new Error('public_media_runtime_superseded');
            const channel = this.session.dataChannel;
            if (!channel) throw new Error('public_media_consent_channel_unavailable');
            const sendContext: SemanticSendContext = Object.freeze({
              peer: preparedPeer,
              sessionId,
              sessionGeneration: generation,
              channel,
            });
            await this.session.sendSemantic(message, sendContext);
            if (!matchesPreparedContext()) throw new Error('public_media_runtime_superseded');
          },
          disableMedia: reasonCode => {
            if (!matchesPreparedContext()) return;
            this.session.audit('public_media_disabled', reasonCode);
            this.disabledContracts.set(sessionId, publicMediaContract.digest);
            this.activeContext = null;
            this.deps.pairMediaTransforms.releaseSession(sessionId, preparedAdapterGeneration);
            this.stopPendingTracks();
          },
          failClosed: reasonCode => {
            if (!matchesPreparedContext()) return;
            this.session.audit('public_media_fail_closed', reasonCode);
            this.disabledContracts.set(sessionId, publicMediaContract.digest);
            this.session.failSession();
          },
        });
        this.activeContext = Object.freeze({
          sessionId,
          peer: preparedPeer,
          sessionGeneration: generation,
          adapterGeneration: preparedAdapterGeneration,
          contractDigest: publicMediaContract.digest,
        });
      } catch (error) {
        if (!this.session.isCurrentSession(pc, sessionId, generation)) {
          if (adapterGeneration !== null) {
            this.deps.pairMediaTransforms.releaseSession(sessionId, adapterGeneration);
          }
          pc.close();
          return 'superseded';
        }
        this.session.audit('public_media_prepare_failed', error instanceof Error ? error.message : String(error));
        this.disabledContracts.set(sessionId, publicMediaContract.digest);
        this.deps.pairMediaTransforms.releaseSession(sessionId, adapterGeneration ?? undefined);
        pc.close();
        // The optional extension failed before any media could leave DROP
        // mode. The session recreates a clean data-only PC so base Pair
        // chat/view remains, then fails the extension with this reason.
        return Object.freeze({
          recreateReason: error instanceof Error ? error.message : 'public_media_transform_prepare_failed',
        });
      }
    }
    return 'continue';
  }

  /**
   * Routes a Public-session remote track through the extension. Returns true
   * when the event was consumed (rendered, held DROP-first or rejected);
   * false leaves it to the ordinary-media policy.
   */
  routeRemoteTrack(event: RTCTrackEvent, sessionId: string): boolean {
    const isPublicSession = this.deps.controlPlane.isPublicSession(sessionId);
    const currentMediaContract = isPublicSession
      ? this.deps.securityBootstrap.mediaContractFor(sessionId) : null;
    if (
      isPublicSession
      && currentMediaContract
      && this.disabledContracts.get(sessionId) === currentMediaContract.digest
    ) {
      // The remote SDP may still contain rejected/muted media m-lines after
      // either peer downgraded the optional extension. Never route those
      // browser events through the ordinary-media policy or render them.
      try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
      this.session.audit('public_media_track_ignored', 'public_media_extension_disabled');
      return true;
    }
    if (isPublicSession && this.deps.pairMediaTransforms.isPrepared(sessionId)) {
      let slot = this.deps.pairMediaTransforms.slotForReceiver(sessionId, event.receiver);
      const adapterGeneration = this.activeContext?.adapterGeneration;
      if (!slot && this.deps.pairMediaTransforms.isAwaitingRemoteTopology(sessionId, adapterGeneration)) {
        try {
          if (adapterGeneration === undefined) throw new Error('public_media_topology_invalid');
          slot = this.deps.pairMediaTransforms.stageRemoteOfferTrack(
            sessionId, event.transceiver, event.receiver, adapterGeneration,
          );
        } catch {
          try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
          this.deps.pairMediaE2ee.failMediaExtension(sessionId, 'public_media_remote_slot_invalid');
          return true;
        }
      }
      if (!slot || this.pendingTracks.has(slot)) {
        try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
        this.session.audit('public_media_rejected', 'public_media_remote_slot_invalid');
        this.deps.pairMediaE2ee.failMediaExtension(sessionId, 'public_media_remote_slot_invalid');
        return true;
      }
      if (this.deps.pairMediaE2ee.statusFor(sessionId).state === 'ready') {
        this.session.emitRemoteTrack(event);
      } else {
        // The receiver transform is already DROP-first. Hold the browser
        // track event until hello/ack, exact topology, and worker key ACK.
        this.pendingTracks.set(slot, event);
      }
      return true;
    }
    return false;
  }

  /**
   * After a remote offer: rejects media of a disabled contract and binds the
   * offered topology. Returns false when the peer generation was superseded.
   */
  async prepareAnswerTopology(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): Promise<boolean> {
    const disabledContract = this.deps.securityBootstrap.mediaContractFor(sessionId);
    if (
      this.deps.controlPlane.isPublicSession(sessionId)
      && disabledContract
      && this.disabledContracts.get(sessionId) === disabledContract.digest
    ) {
      this.rejectOfferedMedia(pc);
    }
    const mediaContext = this.activeContext;
    if (
      this.isActiveContext(mediaContext, pc, sessionId, generation)
      && this.deps.pairMediaTransforms.isAwaitingRemoteTopology(
        sessionId, mediaContext.adapterGeneration,
      )
    ) {
      try {
        await this.deps.pairMediaTransforms.bindRemoteOfferTopology(
          sessionId, mediaContext.adapterGeneration,
        );
        if (!this.session.isCurrentSession(pc, sessionId, generation)) return false;
      } catch (error) {
        this.session.audit('public_media_topology_disabled', error instanceof Error ? error.message : String(error));
        this.deps.pairMediaE2ee.failMediaExtension(
          sessionId,
          error instanceof Error ? error.message : 'public_media_topology_invalid',
        );
      }
    }
    return true;
  }

  markTopologyNegotiated(pc: RTCPeerConnection, sessionId: string, generation: number): void {
    const currentMediaContext = this.activeContext;
    if (
      this.isActiveContext(currentMediaContext, pc, sessionId, generation)
      && this.deps.pairMediaTransforms.isPrepared(
        sessionId, undefined, currentMediaContext.contractDigest,
        currentMediaContext.adapterGeneration,
      )
    ) {
      this.deps.pairMediaE2ee.markTopologyNegotiated(sessionId);
    }
  }

  private isActiveContext(
    context: ActivePublicMediaContext | null,
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): context is ActivePublicMediaContext {
    return !!context
      && context.peer === pc
      && context.sessionId === sessionId
      && context.sessionGeneration === generation
      && this.session.isCurrentSession(pc, sessionId, generation);
  }

  private rejectOfferedMedia(pc: RTCPeerConnection): void {
    for (const transceiver of pc.getTransceivers()) {
      try { transceiver.direction = 'inactive'; } catch { /* Rejected/closed m-line is already safe. */ }
      void transceiver.sender.replaceTrack(null).catch(() => undefined);
    }
  }

  private releasePendingTracks(): void {
    if (!this.session.sessionId || this.deps.pairMediaE2ee.statusFor(this.session.sessionId).state !== 'ready') return;
    for (const definition of ['microphone-opus', 'camera-vp8', 'screen-vp8'] as const) {
      const event = this.pendingTracks.get(definition);
      if (!event) continue;
      this.pendingTracks.delete(definition);
      this.session.emitRemoteTrack(event);
    }
  }

  stopPendingTracks(): void {
    for (const event of this.pendingTracks.values()) {
      try { event.track.stop(); } catch { /* Deterministic local cleanup. */ }
    }
    this.pendingTracks.clear();
  }
}
