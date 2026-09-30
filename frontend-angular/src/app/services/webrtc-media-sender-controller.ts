import type { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import type { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import type { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import type { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import type { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import type { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';
import type { OrdinaryMediaStatsSnapshot, WebrtcSessionContextPort } from './webrtc-session.types';

/** Desired state of one RTP sender, fenced by a monotonically increasing revision. */
interface MediaSenderOperation {
  readonly revision: number;
  readonly track: MediaStreamTrack | null;
  readonly sessionId: string;
  readonly sessionGeneration: number;
  readonly peer: RTCPeerConnection;
}

/** Collaborators the sender controller needs; supplied by WebrtcSessionService. */
export interface WebrtcMediaSenderDeps {
  readonly controlPlane: PairSessionControlPlaneService;
  readonly mediaPolicy: PairOrdinaryMediaPolicy;
  readonly publicationPolicy: PairMediaPublicationPolicy;
  readonly securityBootstrap: PairViewSecurityBootstrapService;
  readonly pairMediaE2ee: PairMediaE2eeCoordinatorService;
  readonly pairMediaTransforms: PairMediaE2eeTransformAdapter;
}

/**
 * Local outgoing media of a WebRTC session: Hub tracks via add/remove plus
 * renegotiation, and fixed Public Pair E2EE slots via fenced replaceTrack.
 *
 * Every Public sender mutation records a revisioned desired state. A native
 * replace that is overtaken, rejected or leaves the sender in an untrusted
 * state is reconciled to the latest desired track or fails the Public media
 * extension closed. The peer connection itself stays owned by the session.
 */
export class WebrtcMediaSenderController {
  private mediaSenderOperationSerial = 0;
  private readonly mediaSenderOperations = new WeakMap<RTCRtpSender, MediaSenderOperation>();

  constructor(
    private readonly session: WebrtcSessionContextPort,
    private readonly deps: WebrtcMediaSenderDeps,
  ) {}

  addMediaTrack(track: MediaStreamTrack, stream: MediaStream): RTCRtpSender {
    this.deps.mediaPolicy.assertAllowed(this.session.sessionId);
    if (!this.session.peer || this.session.peer.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (this.deps.controlPlane.isPublicSession(this.session.sessionId)) {
      throw new Error('public_media_slot_required');
    }
    const sender = this.session.peer.addTrack(track, stream);
    void this.session.negotiateMedia();
    return sender;
  }

  async attachMediaTrack(
    slot: PublicPairMediaSlot,
    track: MediaStreamTrack,
    stream: MediaStream,
  ): Promise<RTCRtpSender> {
    const sessionId = this.session.sessionId;
    const generation = this.session.sessionGeneration;
    const peer = this.session.peer;
    this.deps.publicationPolicy.assertAllowed(sessionId, slot);
    if (!peer || peer.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (!this.deps.controlPlane.isPublicSession(sessionId)) {
      const sender = peer.addTrack(track, stream);
      void this.session.negotiateMedia();
      return sender;
    }
    const status = this.deps.pairMediaE2ee.statusFor(sessionId);
    if (status.state !== 'ready' || !this.deps.pairMediaTransforms.isKeyed(
      sessionId,
      this.deps.securityBootstrap.mediaContractFor(sessionId)?.epoch,
      status.contractDigest,
    )) throw new Error(status.reasonCode || 'public_media_e2ee_not_ready');
    const sender = this.deps.pairMediaTransforms.senderForSlot(sessionId, slot);
    const expectedKind = slot === 'microphone-opus' ? 'audio' : 'video';
    if (track.kind !== expectedKind) throw new Error('public_media_track_kind_invalid');
    const previousTrack = this.initialAttachPredecessor(
      sender, sessionId, generation, peer,
    );
    const senderOperation = this.beginMediaSenderOperation(
      sender, track, sessionId, generation, peer,
    );
    let nativeReplaceApplied = false;
    try {
      await sender.replaceTrack(track);
      nativeReplaceApplied = true;
      this.assertMediaSenderOperationCurrent(sender, senderOperation);
      if (!this.session.isCurrentSession(peer, sessionId, generation)) {
        throw new Error('webrtc_media_session_superseded');
      }
      this.deps.publicationPolicy.assertAllowed(sessionId, slot);
      this.assertMediaSenderOperationCurrent(sender, senderOperation);
    } catch (error) {
      if (nativeReplaceApplied) {
        await this.failMediaSenderOperation(sender, senderOperation);
      } else {
        await this.preservePreviousTrackAfterRejectedReplacement(
          sender, senderOperation, previousTrack,
        );
      }
      throw error;
    }
    return sender;
  }

  publicMediaSlotForReceiver(receiver: RTCRtpReceiver): PublicPairMediaSlot | null {
    return this.deps.pairMediaTransforms.slotForReceiver(this.session.sessionId, receiver);
  }

  async replaceMediaTrack(sender: RTCRtpSender, track: MediaStreamTrack | null): Promise<void> {
    const sessionId = this.session.sessionId;
    const publicSession = this.deps.controlPlane.isPublicSession(sessionId);
    if (!publicSession && track !== null) {
      // Keep the established Hub contract intact. Hub senders are not fixed
      // Public slots and therefore do not participate in Public reconciliation.
      this.deps.mediaPolicy.assertAllowed(sessionId);
      if (!this.session.peer || !this.session.peer.getSenders().includes(sender)) {
        throw new Error('webrtc_media_sender_stale');
      }
      await sender.replaceTrack(track);
      return;
    }
    const peer = this.session.peer;
    if (!peer || !peer.getSenders().includes(sender)) throw new Error('webrtc_media_sender_stale');
    if (!publicSession) {
      // Hub cleanup has historically remained possible after policy loss.
      // It is not a fixed-slot operation and needs no Public reconciliation.
      await sender.replaceTrack(null);
      return;
    }
    const generation = this.session.sessionGeneration;
    // Detaching a sender is cleanup and must remain possible after authority,
    // consent or E2EE readiness has already been lost.
    if (track === null) {
      const senderOperation = this.beginMediaSenderOperation(
        sender, null, sessionId, generation, peer,
      );
      try {
        await sender.replaceTrack(null);
      } catch (error) {
        if (!this.isMediaSenderOperationCurrent(sender, senderOperation)) {
          await this.reconcileLatestMediaSenderOperation(sender, senderOperation);
          throw error;
        }
        this.failPublicMediaSenderReconciliation(sender, senderOperation);
      }
      if (!this.isMediaSenderOperationCurrent(sender, senderOperation)) {
        await this.reconcileLatestMediaSenderOperation(sender, senderOperation);
      }
      return;
    }
    const slot = this.deps.pairMediaTransforms.slotForSender(sessionId, sender);
    if (!slot) throw new Error('public_media_slot_invalid');
    this.deps.publicationPolicy.assertAllowed(sessionId, slot);
    const previousTrack = sender.track;
    const senderOperation = this.beginMediaSenderOperation(
      sender, track, sessionId, generation, peer,
    );
    let nativeReplaceApplied = false;
    try {
      await sender.replaceTrack(track);
      nativeReplaceApplied = true;
      this.assertMediaSenderOperationCurrent(sender, senderOperation);
      if (!this.session.isCurrentSession(peer, sessionId, generation)) {
        throw new Error('webrtc_media_session_superseded');
      }
      this.deps.publicationPolicy.assertAllowed(sessionId, slot);
      this.assertMediaSenderOperationCurrent(sender, senderOperation);
    } catch (error) {
      if (nativeReplaceApplied) {
        await this.failMediaSenderOperation(sender, senderOperation);
      } else {
        await this.preservePreviousTrackAfterRejectedReplacement(
          sender, senderOperation, previousTrack,
        );
      }
      throw error;
    }
  }

  removeMediaSender(sender: RTCRtpSender): void {
    if (!this.session.peer || !this.session.peer.getSenders().includes(sender)) return;
    if (this.deps.controlPlane.isPublicSession(this.session.sessionId)) {
      // Public callers detach through replaceMediaTrack immediately before
      // removal. Fixed transceivers stay installed; a second unfenced null
      // mutation could otherwise overtake a regrant.
      return;
    }
    this.session.peer.removeTrack(sender);
    void this.session.negotiateMedia();
  }

  restartMediaIce(): void {
    if (!this.session.peer || this.session.peer.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (this.deps.controlPlane.isPublicSession(this.session.sessionId)) {
      const sessionId = this.session.sessionId;
      this.deps.pairMediaE2ee.deactivate(sessionId, 'public_media_fresh_connection_required');
      throw new Error('public_media_fresh_connection_required');
    }
    this.session.peer.restartIce();
    void this.session.negotiateMedia();
  }

  async ordinaryMediaStats(): Promise<OrdinaryMediaStatsSnapshot> {
    const peer = this.session.peer;
    if (!peer || peer.connectionState === 'closed') {
      throw new Error('webrtc_session_not_open');
    }
    return Object.freeze({
      connection: peer.connectionState,
      stats: await peer.getStats(),
    });
  }

  private beginMediaSenderOperation(
    sender: RTCRtpSender,
    track: MediaStreamTrack | null,
    sessionId: string,
    sessionGeneration: number,
    peer: RTCPeerConnection,
  ): MediaSenderOperation {
    this.mediaSenderOperationSerial += 1;
    if (!Number.isSafeInteger(this.mediaSenderOperationSerial)) {
      throw new Error('webrtc_media_sender_operation_exhausted');
    }
    const operation = Object.freeze({
      revision: this.mediaSenderOperationSerial,
      track,
      sessionId,
      sessionGeneration,
      peer,
    });
    this.mediaSenderOperations.set(sender, operation);
    return operation;
  }

  private isMediaSenderOperationCurrent(
    sender: RTCRtpSender,
    operation: MediaSenderOperation,
  ): boolean {
    return this.mediaSenderOperations.get(sender) === operation;
  }

  private assertMediaSenderOperationCurrent(
    sender: RTCRtpSender,
    operation: MediaSenderOperation,
  ): void {
    if (!this.isMediaSenderOperationCurrent(sender, operation)) {
      throw new Error('webrtc_media_sender_operation_superseded');
    }
  }

  private async failMediaSenderOperation(
    sender: RTCRtpSender,
    operation: MediaSenderOperation,
  ): Promise<void> {
    if (!this.isMediaSenderOperationCurrent(sender, operation)) {
      await this.reconcileLatestMediaSenderOperation(sender, operation);
      return;
    }
    const detach = this.beginMediaSenderOperation(
      sender,
      null,
      operation.sessionId,
      operation.sessionGeneration,
      operation.peer,
    );
    try {
      await sender.replaceTrack(null);
    } catch {
      if (!this.isMediaSenderOperationCurrent(sender, detach)) {
        await this.reconcileLatestMediaSenderOperation(sender, detach);
        return;
      }
      this.failPublicMediaSenderReconciliation(sender, detach);
    }
    if (!this.isMediaSenderOperationCurrent(sender, detach)) {
      await this.reconcileLatestMediaSenderOperation(sender, detach);
    }
  }

  private async preservePreviousTrackAfterRejectedReplacement(
    sender: RTCRtpSender,
    operation: MediaSenderOperation,
    previousTrack: MediaStreamTrack | null,
  ): Promise<void> {
    if (!this.isMediaSenderOperationCurrent(sender, operation)) {
      await this.reconcileLatestMediaSenderOperation(sender, operation);
      return;
    }
    const preserved = this.beginMediaSenderOperation(
      sender,
      previousTrack,
      operation.sessionId,
      operation.sessionGeneration,
      operation.peer,
    );
    // A rejected native replace must retain its previous track. If the
    // platform mutated anyway, the sender state is no longer trustworthy.
    if (sender.track !== previousTrack) {
      this.failPublicMediaSenderReconciliation(sender, preserved);
    }
  }

  private initialAttachPredecessor(
    sender: RTCRtpSender,
    sessionId: string,
    sessionGeneration: number,
    peer: RTCPeerConnection,
  ): null {
    const desired = this.mediaSenderOperations.get(sender);
    if (!desired) {
      if (sender.track === null) return null;
      this.failPublicMediaSenderContext(
        sender, sessionId, sessionGeneration, peer,
      );
    }
    if (
      desired.sessionId === sessionId
      && desired.sessionGeneration === sessionGeneration
      && desired.peer === peer
      && desired.track === null
    ) return null;
    this.failPublicMediaSenderContext(
      sender, sessionId, sessionGeneration, peer,
    );
  }

  private async reconcileLatestMediaSenderOperation(
    sender: RTCRtpSender,
    stale: MediaSenderOperation,
  ): Promise<void> {
    let observed = this.mediaSenderOperations.get(sender);
    // A very small bounded loop handles another operation crossing the
    // reconciliation await without creating an independent retry loop.
    for (let attempt = 0; attempt < 3 && observed && observed !== stale; attempt += 1) {
      const target = observed;
      try {
        await sender.replaceTrack(target.track);
      } catch {
        observed = this.mediaSenderOperations.get(sender);
        if (observed !== target) continue;
        this.failPublicMediaSenderReconciliation(sender, target);
      }
      observed = this.mediaSenderOperations.get(sender);
      if (observed === target) return;
    }
    const latest = this.mediaSenderOperations.get(sender);
    this.failPublicMediaSenderReconciliation(
      sender,
      latest && latest !== stale ? latest : stale,
    );
  }

  private failPublicMediaSenderReconciliation(
    sender: RTCRtpSender,
    operation: MediaSenderOperation,
  ): never {
    return this.failPublicMediaSenderContext(
      sender,
      operation.sessionId,
      operation.sessionGeneration,
      operation.peer,
    );
  }

  private failPublicMediaSenderContext(
    sender: RTCRtpSender,
    sessionId: string,
    sessionGeneration: number,
    peer: RTCPeerConnection,
  ): never {
    const reasonCode = 'public_media_sender_reconciliation_failed';
    if (
      this.session.isCurrentSession(peer, sessionId, sessionGeneration)
      && peer.getSenders().includes(sender)
    ) {
      this.session.audit('public_media_fail_closed', reasonCode);
      this.deps.pairMediaE2ee.fail(sessionId, reasonCode);
    }
    throw new Error(reasonCode);
  }
}
