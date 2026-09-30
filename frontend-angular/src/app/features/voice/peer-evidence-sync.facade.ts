import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription, firstValueFrom } from 'rxjs';

import { SpeechEvidenceDatachannelTransportService } from '../../services/speech-evidence-datachannel-transport.service';
import {
  SpeechEvidenceQuarantineGroupSnapshot,
  SpeechEvidenceQuarantineStore,
} from '../../services/speech-evidence-quarantine.store';
import {
  SpeechEvidenceConsentPairAuthority,
  SpeechEvidenceSyncApiService,
} from '../../services/speech-evidence-sync-api.service';
import { SpeechEvidenceHubCurationFacade } from '../../services/speech-evidence-hub-curation.facade';
import { SpeechEvidenceSyncCryptoContext } from '../../services/speech-evidence-sync.providers';
import {
  SpeechEvidenceTransferSnapshot,
  SpeechEvidenceSyncService,
} from '../../services/speech-evidence-sync.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  sha256Canonical,
  speechEvidenceGroupId,
} from '../../services/speech-evidence-sync.validators';
import {
  SpeechTranscriptRevisionStore,
  SpeechTranscriptTurn,
} from '../../services/speech-transcript-revision.store';
import { WebrtcTransportService } from '../../services/webrtc-transport.service';
import {
  buildPeerEvidenceAcceptancePayload,
  verifyPeerEvidenceOfferPreview,
} from './peer-evidence-acceptance';
import {
  allowedDataClasses,
  allowedTrainerClass,
  assertAcceptanceNarrowsOffer,
  assertIncomingProposalInScope,
  consentDigestForPeer,
  contextKey,
  validateContext,
  validatePairConsentAuthority,
} from './peer-evidence-consent-policy';
import {
  assertInboundBoundToContext,
  buildProposalDraft,
  chunkAckPayload,
  currentSourceRevisions,
  revocationAckPayload,
  revocationRequestPayload,
} from './peer-evidence-control-messages';
import {
  buildLocalEvidenceArtifact,
  isShareableTranscriptTurn,
  parseEvidenceGroup,
  recipientPreAdmissionReason,
} from './peer-evidence-group-payload';
import {
  aggregateOutboundSnapshots,
  emptySync,
  offerFromMessage,
  offerFromRecord,
  offerView,
  outboundSnapshotFromHubStatus,
} from './peer-evidence-offer.mappers';
import {
  completedGroupConflict,
  lineageWithQuarantinedGroup,
  lineageWithReceiptStates,
  lineageWithRevokedGroups,
  quarantineRowsFromSummaries,
  resolutionCandidates,
  revocationAckResolves,
  verifiedRevocationAckTarget,
  unresolvedResolutionRegions,
} from './peer-evidence-projections';
import {
  PeerEvidenceProposalIntent,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';
import {
  ActiveOffer,
  LocalEvidenceArtifact,
  MAX_REVOCATION_ATTEMPTS,
  PeerEvidenceFlowView,
  PeerEvidenceGroupPayload,
  PeerEvidenceSyncContext,
  PendingRevocation,
  REVOCATION_RETRY_MS,
  STATUS_POLL_MS,
} from './peer-evidence-sync.models';
import {
  concatenate,
  reason,
  sameSourceRevisions,
  sha256Text,
  stringArray,
  unique,
} from './peer-evidence-sync-primitives';
import { PeerEvidenceViewState } from './peer-evidence-view-state';
import { PeerEvidenceHubCurationFlow } from './peer-evidence-hub-curation.flow';

export type { PeerEvidenceFlowView, PeerEvidenceSyncContext } from './peer-evidence-sync.models';

/**
 * Stateful orchestration of the peer evidence offer/transfer/curation flow.
 * Pure consent policy, wire codecs and view projections live in the
 * peer-evidence-* modules imported above (SRP); this facade only owns the
 * runtime state, timers and the ordering of async steps.
 */
@Injectable()
export class PeerEvidenceSyncFacade implements OnDestroy {
  private readonly api = inject(SpeechEvidenceSyncApiService);
  private readonly hubCuration = inject(SpeechEvidenceHubCurationFacade);
  private readonly crypto = inject(SpeechEvidenceSyncCryptoContext);
  private readonly evidence = inject(SpeechEvidenceSyncService);
  private readonly evidenceTransport = inject(SpeechEvidenceDatachannelTransportService);
  private readonly transport = inject(WebrtcTransportService);
  private readonly quarantine = inject(SpeechEvidenceQuarantineStore);
  private readonly transcripts = inject(SpeechTranscriptRevisionStore);
  private readonly subscriptions = new Subscription();
  private readonly localArtifacts = new Map<string, LocalEvidenceArtifact>();
  private readonly outboundSnapshots = new Map<string, SpeechEvidenceTransferSnapshot>();
  private readonly localPreAdmissionReasons = new Map<string, string>();
  private readonly evidenceView = new PeerEvidenceViewState();
  private context: PeerEvidenceSyncContext | null = null;
  private consentPair: SpeechEvidenceConsentPairAuthority | null = null;
  private offer: ActiveOffer | null = null;
  private active = false;
  private explicitPause = false;
  private pausedByTransport = false;
  private generation = 0;
  private localBuildGeneration = 0;
  private statusTimer: ReturnType<typeof setInterval> | null = null;
  private revocationTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingRevocation: PendingRevocation | null = null;
  private inboundChain: Promise<void> = Promise.resolve();
  private statusPollActive = false;

  private readonly hubCurationFlow = new PeerEvidenceHubCurationFlow({
    quarantine: this.quarantine,
    evidence: this.evidence,
    crypto: this.crypto,
    hubCuration: this.hubCuration,
  }, {
    evidenceView: this.evidenceView,
    requireActiveContext: () => this.requireActiveContext(),
    requireOffer: () => this.requireOffer(),
    generation: () => this.generation,
    active: () => this.active,
    currentOffer: () => this.offer,
    syncPending: () => this.view$.value.sync.pending,
    isCurrentContext: (context, generation) => this.isCurrentContext(context, generation),
    restoreQuarantine: (context, generation) => this.restoreQuarantine(context, generation),
    emit: (reasonCode, patch) => this.emit(reasonCode, patch),
    patchSync: patch => this.patchSync(patch),
    fail: reasonCode => this.fail(reasonCode),
  });

  readonly view$ = new BehaviorSubject<PeerEvidenceFlowView>(Object.freeze({
    offer: null,
    sync: emptySync('disabled'),
    reasonCode: 'peer_evidence_sync_disabled',
  }));

  constructor() {
    this.subscriptions.add(this.transcripts.turns$.subscribe(turns => {
      void this.rebuildLocalArtifacts(turns);
    }));
    this.subscriptions.add(this.evidenceTransport.verifiedInbound$.subscribe(message => {
      const generation = this.generation;
      this.inboundChain = this.inboundChain
        .then(() => this.handleInbound(message, generation))
        .catch(error => this.fail(reason(error, 'speech_evidence_inbound_processing_failed')));
    }));
    this.subscriptions.add(this.evidenceTransport.verificationRejected$.subscribe(rejected => {
      this.fail(rejected.reasonCode);
    }));
    this.subscriptions.add(this.transport.mode$.subscribe(mode => {
      if (!this.active) return;
      if (mode === 'idle') {
        this.pausedByTransport = true;
        this.evidence.pause(this.offer?.offerId);
        this.stopStatusPoll();
        this.patchSync({ state: 'reconnecting', reasonCode: 'speech_evidence_transport_offline' });
      } else if (this.pausedByTransport && !this.explicitPause) {
        this.pausedByTransport = false;
        void this.resume();
      }
    }));
  }

  bind(context: PeerEvidenceSyncContext | null): void {
    const next = context ? validateContext(context) : null;
    if (contextKey(next) === contextKey(this.context)) return;
    this.stopRuntime();
    this.context = next;
    this.generation += 1;
    this.offer = null;
    this.consentPair = null;
    this.localArtifacts.clear();
    this.outboundSnapshots.clear();
    this.localPreAdmissionReasons.clear();
    this.evidenceView.reset();
    this.emit(next ? 'peer_evidence_sync_ready_for_activation' : 'peer_evidence_sync_context_missing', {
      state: next ? 'inactive' : 'disabled',
      reasonCode: next ? null : 'peer_evidence_sync_context_missing',
    });
    void this.rebuildLocalArtifacts(this.transcripts.turns$.value);
  }

  async activate(): Promise<void> {
    const context = this.requireContext();
    const generation = this.generation;
    if (this.active || this.view$.value.sync.pending) return;
    this.patchSync({ pending: true, state: 'activating', reasonCode: null });
    try {
      this.crypto.configure(context.pairId, context.consent.consent.consent_version);
      this.evidenceTransport.bind(context.hubUrl, context.consent.consent.consent_version);
      const signing = await this.crypto.exportPublicSigningKey();
      const expiresAtMs = Math.min(context.consent.consent.expires_at_ms, Date.now() + 10 * 60_000);
      await firstValueFrom(this.api.registerKey(context.hubUrl, {
        sessionId: context.sessionId,
        pairId: context.pairId,
        audienceId: context.remotePeerId,
        epoch: context.epoch,
        consentVersion: context.consent.consent.consent_version,
        keyId: signing.keyId,
        publicKeyB64: signing.rawKeyB64,
        expiresAtMs,
      }));
      if (!this.isCurrentContext(context, generation)) return;
      const consentPair = await firstValueFrom(this.api.currentConsentPair(context.hubUrl, {
        sessionId: context.sessionId,
        pairId: context.pairId,
        remotePeerId: context.remotePeerId,
        epoch: context.epoch,
      }));
      if (!this.isCurrentContext(context, generation)) return;
      this.consentPair = validatePairConsentAuthority(consentPair, context);
      this.active = true;
      this.explicitPause = false;
      await this.restoreHubState(context, generation);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      await this.restoreQuarantine(context, generation);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      await this.hubCurationFlow.restore(context, generation);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.emit('peer_evidence_sync_active', { pending: false, state: 'active', reasonCode: null });
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.crypto.clear();
      this.evidenceTransport.clear();
      this.active = false;
      this.consentPair = null;
      this.fail(reason(error, 'peer_evidence_sync_activation_failed'));
      throw error;
    }
  }

  async propose(intent: PeerEvidenceProposalIntent): Promise<void> {
    const context = this.requireActiveContext();
    const consentPair = this.requireConsentPair();
    const generation = this.generation;
    if (this.view$.value.sync.pending) return;
    this.patchSync({ pending: true, reasonCode: null });
    try {
      const artifacts = unique(intent.groupIds).map(groupId => {
        const artifact = this.localArtifacts.get(groupId);
        if (!artifact) throw new SpeechEvidenceValidationError('speech_evidence_local_group_not_found');
        return artifact;
      });
      const { payload, expiresAtMs } = await buildProposalDraft(artifacts, intent, context, consentPair);
      const message = await this.crypto.sign('offer', payload, expiresAtMs);
      const delivered = await this.evidenceTransport.send('control', JSON.stringify(message), expiresAtMs);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      if (!delivered) {
        throw new SpeechEvidenceValidationError('speech_evidence_offer_delivery_failed');
      }
      this.offer = await this.verifyOfferPreview(
        offerFromMessage(message, context.consent.consent.consent_version),
        message.payload,
        context,
      );
      this.emit('speech_evidence_offer_proposed', { pending: false, state: 'offered', reasonCode: null });
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.fail(reason(error, 'speech_evidence_offer_failed'));
    }
  }

  async accept(dataClasses: readonly string[]): Promise<void> {
    const context = this.requireActiveContext();
    const consentPair = this.requireConsentPair();
    const generation = this.generation;
    const proposedOffer = this.requireOffer();
    if (proposedOffer.recipientId !== context.localPeerId || proposedOffer.state !== 'proposed') {
      this.fail('speech_evidence_offer_not_acceptable');
      return;
    }
    this.patchSync({ pending: true, reasonCode: null });
    try {
      const offer = await this.verifyOfferPreview(
        proposedOffer,
        { group_previews: proposedOffer.groupPreviews.map(value => value.value) },
        context,
        proposedOffer.groupPreviewDigest,
      );
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      const consent = context.consent.consent;
      const expiresAtMs = Math.min(
        offer.expiresAtMs,
        consent.expires_at_ms,
        consentPair.remote.expiresAtMs,
        Date.now() + 5 * 60_000,
      );
      const payload = buildPeerEvidenceAcceptancePayload({
        offer,
        acceptedClasses: dataClasses,
        retentionSeconds: Math.min(offer.retentionSeconds, consent.retention_seconds),
        trainerClass: allowedTrainerClass(context, offer.trainerClass, consentPair),
        recipientConsentDigest: context.consent.consentDigest,
      });
      const message = await this.crypto.sign('offer', payload, expiresAtMs);
      const delivered = await this.evidenceTransport.send('control', JSON.stringify(message), expiresAtMs);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      if (!delivered) {
        throw new SpeechEvidenceValidationError('speech_evidence_acceptance_delivery_failed');
      }
      const authorized = await firstValueFrom(this.api.authorizeTransfer(context.hubUrl, offer.offerId));
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.offer = await this.verifyOfferPreview(
        offerFromRecord(authorized, context, this.requireConsentPair()),
        { group_previews: authorized.groupPreviews.map(value => value.value) },
        context,
        authorized.groupPreviewDigest,
      );
      this.emit('speech_evidence_offer_accepted', { pending: false, state: 'receiving', reasonCode: null });
      this.startStatusPoll();
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.fail(reason(error, 'speech_evidence_acceptance_failed'));
    }
  }

  pause(): void {
    this.explicitPause = true;
    this.evidence.pause(this.offer?.offerId);
    this.stopStatusPoll();
    this.emit('speech_evidence_sync_paused', { state: 'paused', reasonCode: null });
  }

  async resume(): Promise<void> {
    if (!this.active || this.transport.mode$.value === 'idle') return;
    const context = this.requireActiveContext();
    const generation = this.generation;
    this.explicitPause = false;
    try {
      await this.evidence.resumeAll();
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.startStatusPoll();
      this.emit('speech_evidence_sync_resumed', { state: 'active', reasonCode: null });
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.fail(reason(error, 'speech_evidence_resume_failed'));
    }
  }

  async reject(): Promise<void> {
    const context = this.requireActiveContext();
    const generation = this.generation;
    const offer = this.requireOffer();
    this.patchSync({ pending: true, reasonCode: null });
    try {
      const invalidated = await firstValueFrom(this.api.invalidate(
        context.hubUrl, offer.offerId, 'speech_evidence_recipient_rejected',
      ));
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.offer = offerFromRecord(invalidated, context, this.requireConsentPair());
      this.evidence.revoke(offer.offerId, 'speech_evidence_recipient_rejected');
      this.emit('speech_evidence_offer_rejected', { pending: false, state: 'rejected', reasonCode: null });
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.fail(reason(error, 'speech_evidence_reject_failed'));
    }
  }

  requestHubCuration(): Promise<void> {
    return this.hubCurationFlow.request();
  }

  async revoke(): Promise<void> {
    const context = this.requireActiveContext();
    const generation = this.generation;
    const offer = this.requireOffer();
    if (this.view$.value.sync.pending) return;
    this.patchSync({ pending: true, state: 'revoking', reasonCode: null });
    this.evidence.revoke(offer.offerId, 'speech_evidence_user_revoked');
    await this.quarantine.removeGroups(
      context.sessionId, context.pairId, context.epoch, offer.offerId, offer.groupIds,
    ).catch(() => 0);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.evidenceView.lineage = lineageWithRevokedGroups(this.evidenceView.lineage, offer.groupIds);
    try {
      const invalidated = await firstValueFrom(this.api.invalidate(
        context.hubUrl, offer.offerId, 'speech_evidence_user_revoked',
      ));
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.offer = offerFromRecord(invalidated, context, this.requireConsentPair());
    } catch {
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      // Local fencing is authoritative for this client. A Hub outage is shown
      // explicitly while the bounded signed remote request still proceeds.
      this.patchSync({ reasonCode: 'speech_evidence_hub_invalidation_unconfirmed' });
    }
    const pending: PendingRevocation = {
      revocationId: `speech-revocation-${crypto.randomUUID()}`,
      offerId: offer.offerId,
      groupIds: Object.freeze([...offer.groupIds]),
      scopeDigest: offer.scopeDigest,
      revocationEpoch: context.consent.consent.revocation_epoch + 1,
      deadlineAtMs: Date.now() + MAX_REVOCATION_ATTEMPTS * REVOCATION_RETRY_MS,
      attempts: 0,
      resolved: false,
    };
    this.pendingRevocation = pending;
    this.patchSync({ pending: false, state: 'revoked', revocationState: 'requested' });
    await this.sendRevocationAttempt(pending);
  }

  localOverride(regionId: string, candidateId: string): void {
    this.evidenceView.conflictRegions = Object.freeze(this.evidenceView.conflictRegions.map(region =>
      region.regionId === regionId && region.candidateIds.includes(candidateId)
        ? Object.freeze({ ...region, selectedCandidateId: candidateId })
        : region));
    this.emit('speech_evidence_local_display_override_only');
  }

  clear(): void {
    this.stopRuntime();
    this.context = null;
    this.generation += 1;
    this.emit('peer_evidence_sync_context_missing', {
      state: 'disabled', pending: false, reasonCode: 'peer_evidence_sync_context_missing',
    });
  }

  ngOnDestroy(): void {
    this.clear();
    this.subscriptions.unsubscribe();
    this.view$.complete();
  }

  private async handleInbound(message: SpeechEvidenceMessage, generation: number): Promise<void> {
    if (generation !== this.generation) return;
    const context = this.requireActiveContext();
    assertInboundBoundToContext(message, context);
    if (message.message_type === 'offer') await this.handleOffer(message, context, generation);
    else if (message.message_type === 'chunk') await this.handleChunk(message, context, generation);
    else if (message.message_type === 'chunk_ack') await this.handleChunkAck(message, context, generation);
    else if (message.message_type === 'receipt') this.handleReceipt(message);
    else if (message.message_type === 'resolution') await this.handleResolution(message, context, generation);
    else if (message.message_type === 'revocation') await this.handleRevocation(message, context, generation);
    else if (message.message_type === 'revocation_ack') this.handleRevocationAck(message);
    else this.emit('speech_evidence_verified_control_received');
  }

  private async handleOffer(
    message: SpeechEvidenceMessage,
    context: PeerEvidenceSyncContext,
    generation: number,
  ): Promise<void> {
    const consentPair = this.requireConsentPair();
    const parsed = offerFromMessage(message, context.consent.consent.consent_version);
    const incoming = await this.verifyOfferPreview(parsed, message.payload, context);
    if (incoming.expiresAtMs <= Date.now()) throw new SpeechEvidenceValidationError('speech_evidence_offer_expired');
    const stage = message.payload['stage'];
    if (stage === 'proposal') {
      assertIncomingProposalInScope(incoming, context, consentPair);
      this.offer = incoming;
      this.emit('speech_evidence_offer_received', { state: 'offered', reasonCode: null });
      return;
    }
    const current = this.requireOffer();
    assertAcceptanceNarrowsOffer(stage, incoming, current, context, consentPair);
    const authorized = await firstValueFrom(this.api.authorizeTransfer(context.hubUrl, incoming.offerId));
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.offer = await this.verifyOfferPreview(
      offerFromRecord(authorized, context, consentPair),
      { group_previews: authorized.groupPreviews.map(value => value.value) },
      context,
      authorized.groupPreviewDigest,
    );
    await this.startOutboundTransfer(this.offer, generation);
  }

  private async handleChunk(
    message: SpeechEvidenceMessage,
    context: PeerEvidenceSyncContext,
    generation: number,
  ): Promise<void> {
    const offer = this.requireOffer();
    const offerId = String(message.payload['offer_id']);
    const groupId = String(message.payload['group_id']);
    if (
      offer.offerId !== offerId
      || offer.senderId !== context.remotePeerId
      || !offer.groupIds.includes(groupId)
      || offer.state !== 'accepted'
    ) throw new SpeechEvidenceValidationError('speech_evidence_transfer_binding_mismatch');
    const clearProbe = await this.evidence.decryptChunk(message);
    clearProbe.fill(0);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const stored = await this.quarantine.put(message);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    await this.restoreQuarantine(context, generation);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    if (stored.disposition === 'conflict') {
      this.fail('speech_evidence_chunk_index_conflict');
      return;
    }
    const ack = await this.crypto.sign(
      'chunk_ack',
      chunkAckPayload(offerId, groupId, message.payload['chunk_index'], stored.snapshot),
      Math.min(message.expires_at_ms, Date.now() + 5 * 60_000),
    );
    const delivered = await this.evidenceTransport.send('control', JSON.stringify(ack), ack.expires_at_ms);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    if (!delivered) {
      this.patchSync({ state: 'paused', reasonCode: 'speech_evidence_ack_delivery_deferred' });
      return;
    }
    if (stored.snapshot.complete) await this.projectCompletedGroup(context, stored.snapshot, generation);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.emit('speech_evidence_chunk_quarantined', { state: stored.snapshot.complete ? 'quarantined' : 'receiving' });
  }

  private async handleChunkAck(
    message: SpeechEvidenceMessage,
    context: PeerEvidenceSyncContext,
    generation: number,
  ): Promise<void> {
    const snapshot = await this.evidence.acknowledge(message);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.outboundSnapshots.set(snapshot.groupId, snapshot);
    this.emitAggregatedOutbound('speech_evidence_chunk_acknowledged');
  }

  private handleReceipt(message: SpeechEvidenceMessage): void {
    const offer = this.requireOffer();
    if (message.payload['offer_id'] !== offer.offerId) {
      throw new SpeechEvidenceValidationError('speech_evidence_receipt_offer_mismatch');
    }
    const accepted = stringArray(message.payload['accepted_group_ids']);
    const rejected = stringArray(message.payload['rejected_group_ids']);
    const quarantined = stringArray(message.payload['quarantined_group_ids']);
    if ([...accepted, ...rejected, ...quarantined].some(groupId => !offer.groupIds.includes(groupId))) {
      throw new SpeechEvidenceValidationError('speech_evidence_receipt_groups_invalid');
    }
    this.evidenceView.lineage = lineageWithReceiptStates(this.evidenceView.lineage, accepted, rejected, quarantined);
    this.emit('speech_evidence_peer_receipt_verified', {
      receiptId: String(message.payload['receipt_id']),
      receiptVerification: 'peer_verified',
    });
  }

  private async handleResolution(
    message: SpeechEvidenceMessage,
    context: PeerEvidenceSyncContext,
    generation: number,
  ): Promise<void> {
    const projected = await resolutionCandidates(message.payload);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.evidenceView.conflictCandidates = projected;
    this.evidenceView.resolutionHash = String(message.payload['result_digest']);
    this.evidenceView.resolutionPolicyVersion = String(message.payload['policy_version']);
    const unresolved = stringArray(message.payload['unresolved_region_ids']);
    this.evidenceView.conflictRegions = unresolvedResolutionRegions(unresolved, this.evidenceView.conflictCandidates);
    this.emit('speech_evidence_resolution_verified');
  }

  private async handleRevocation(
    message: SpeechEvidenceMessage,
    context: PeerEvidenceSyncContext,
    generation: number,
  ): Promise<void> {
    const offer = this.requireOffer();
    const groups = stringArray(message.payload['group_ids']);
    if (groups.some(groupId => !offer.groupIds.includes(groupId)) || message.payload['scope_digest'] !== offer.scopeDigest) {
      throw new SpeechEvidenceValidationError('speech_evidence_revocation_binding_mismatch');
    }
    await this.quarantine.removeGroups(context.sessionId, context.pairId, context.epoch, offer.offerId, groups);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.evidence.revoke(offer.offerId, 'speech_evidence_remote_revoked');
    this.evidenceView.lineage = lineageWithRevokedGroups(this.evidenceView.lineage, groups);
    await this.restoreQuarantine(context, generation);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const ackPayload = await revocationAckPayload(message, offer.scopeDigest, groups);
    const ack = await this.crypto.sign(
      'revocation_ack', ackPayload, Math.min(message.expires_at_ms, Date.now() + 5 * 60_000),
    );
    const delivered = await this.evidenceTransport.send('control', JSON.stringify(ack), ack.expires_at_ms);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.emit('speech_evidence_remote_revocation_applied', {
      state: 'revoked',
      revocationState: delivered ? 'acknowledged' : 'unresolved',
      reasonCode: delivered ? null : 'speech_evidence_revocation_ack_delivery_failed',
    });
  }

  private handleRevocationAck(message: SpeechEvidenceMessage): void {
    const pending = verifiedRevocationAckTarget(message, this.pendingRevocation);
    pending.resolved = revocationAckResolves(message, pending);
    if (pending.resolved) {
      this.stopRevocationTimer();
      this.emit('speech_evidence_revocation_acknowledged', {
        state: 'revoked', revocationState: 'acknowledged', reasonCode: null,
      });
    } else {
      this.patchSync({ revocationState: 'unresolved', reasonCode: 'speech_evidence_remote_ack_partial' });
    }
  }

  private async startOutboundTransfer(offer: ActiveOffer, generation = this.generation): Promise<void> {
    const context = this.requireActiveContext();
    const signing = await this.crypto.exportPublicSigningKey();
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.outboundSnapshots.clear();
    for (const groupId of offer.groupIds) {
      const artifact = this.localArtifacts.get(groupId);
      if (!artifact) throw new SpeechEvidenceValidationError('speech_evidence_resume_source_missing');
      const snapshot = await this.evidence.prepareTransfer({
        offerId: offer.offerId,
        groupId,
        epoch: context.epoch,
        keyId: signing.keyId,
        expiresAtMs: offer.expiresAtMs,
        dataClass: artifact.view.dataClass,
      }, artifact.bytes);
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      this.outboundSnapshots.set(groupId, snapshot);
    }
    this.emitAggregatedOutbound('speech_evidence_transfer_started');
    this.startStatusPoll();
  }

  private async verifyOfferPreview(
    offer: ActiveOffer,
    payload: Readonly<Record<string, unknown>>,
    context: PeerEvidenceSyncContext,
    expectedPreviewDigest?: string,
  ): Promise<ActiveOffer> {
    const sourceRevisions = this.currentSourceRevisions();
    const verified = await verifyPeerEvidenceOfferPreview({
      pairId: context.pairId,
      epoch: context.epoch,
      speakerId: context.consent.consent.speaker_id,
      groupIds: offer.groupIds,
      totalBytes: offer.totalBytes,
      payload,
      expectedPreviewDigest,
      currentSourceRevisions: sourceRevisions,
    });
    // Digest/WebCrypto verification yields to the event loop. Re-read the
    // authoritative local revisions afterwards so a concurrent correction
    // cannot be signed through a stale snapshot.
    if (!sameSourceRevisions(sourceRevisions, this.currentSourceRevisions())) {
      throw new SpeechEvidenceValidationError('speech_evidence_offer_preview_stale');
    }
    return Object.freeze({
      ...offer,
      groupPreviews: verified.previews,
      groupPreviewDigest: verified.previewDigest,
      previewVerified: true,
    });
  }

  private currentSourceRevisions(): ReadonlyMap<string, number> {
    return currentSourceRevisions(this.transcripts.turns$.value);
  }

  private async projectCompletedGroup(
    context: PeerEvidenceSyncContext,
    snapshot: SpeechEvidenceQuarantineGroupSnapshot,
    generation: number,
  ): Promise<void> {
    const messages = await this.quarantine.group(
      context.sessionId, context.pairId, context.epoch, snapshot.offerId, snapshot.groupId,
    );
    const chunks: Uint8Array[] = [];
    let bytes: Uint8Array | null = null;
    let payload: PeerEvidenceGroupPayload;
    try {
      for (const message of messages) {
        chunks.push(await this.evidence.decryptChunk(message));
        if (!this.isCurrentContext(context, generation) || !this.active) return;
      }
      bytes = concatenate(chunks);
      payload = parseEvidenceGroup(bytes);
      const expectedGroupId = await speechEvidenceGroupId(payload.sourceDigest, payload.revision);
      const preview = this.requireOffer().groupPreviews.find(value => value.groupId === snapshot.groupId);
      const localReason = await recipientPreAdmissionReason(payload, snapshot, expectedGroupId, preview);
      if (localReason) this.localPreAdmissionReasons.set(snapshot.groupId, localReason);
      else this.localPreAdmissionReasons.delete(snapshot.groupId);
    } finally {
      for (const chunk of chunks) chunk.fill(0);
      bytes?.fill(0);
    }
    await this.restoreQuarantine(context, generation);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const local = this.transcripts.turns$.value.find(turn => turn.turnId === payload.turnId);
    const conflict = completedGroupConflict(snapshot.groupId, payload, local);
    this.evidenceView.conflictCandidates = conflict.candidates;
    this.evidenceView.conflictRegions = conflict.regions;
    this.evidenceView.resolutionHash = snapshot.lineageDigests.join(':');
    this.evidenceView.resolutionPolicyVersion = 'display-only-no-admission-v1';
    const contributorDigest = await sha256Text(`peer\0${context.remotePeerId}`);
    const fieldDigest = await sha256Canonical(['transcript']);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.evidenceView.lineage = lineageWithQuarantinedGroup(
      this.evidenceView.lineage, snapshot.groupId, contributorDigest, fieldDigest, context.consent.consentDigest,
    );
  }

  private async restoreHubState(context: PeerEvidenceSyncContext, generation: number): Promise<void> {
    const offers = await firstValueFrom(this.api.listOffers(context.hubUrl, {
      sessionId: context.sessionId,
      pairId: context.pairId,
      epoch: context.epoch,
    }));
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const consentPair = this.requireConsentPair();
    const current = offers.find(value => value.expiresAtMs > Date.now()
      && value.state === 'accepted'
      && consentDigestForPeer(value.senderId, context, consentPair) === value.senderConsentDigest
      && consentDigestForPeer(value.recipientId, context, consentPair) === value.recipientConsentDigest);
    if (!current) return;
    this.offer = await this.verifyOfferPreview(
      offerFromRecord(current, context, this.requireConsentPair()),
      { group_previews: current.groupPreviews.map(value => value.value) },
      context,
      current.groupPreviewDigest,
    );
    if (this.offer.senderId === context.localPeerId && this.offer.state === 'accepted') {
      await this.startOutboundTransfer(this.offer, generation);
    }
  }

  private async restoreQuarantine(context: PeerEvidenceSyncContext, generation = this.generation): Promise<void> {
    await this.quarantine.pruneExpired();
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const summaries = await this.quarantine.summaries(
      context.sessionId, context.pairId, context.epoch, this.offer?.offerId,
    );
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.evidenceView.quarantineRows = quarantineRowsFromSummaries(summaries, this.localPreAdmissionReasons);
  }

  private startStatusPoll(): void {
    if (this.statusTimer || !this.offer || !this.active || this.explicitPause) return;
    this.statusTimer = setInterval(() => { void this.pollTransferStatus(); }, STATUS_POLL_MS);
    void this.pollTransferStatus();
  }

  private stopStatusPoll(): void {
    if (this.statusTimer) clearInterval(this.statusTimer);
    this.statusTimer = null;
    this.statusPollActive = false;
  }

  private async pollTransferStatus(): Promise<void> {
    const context = this.context;
    const offer = this.offer;
    const generation = this.generation;
    if (!context || !offer || this.statusPollActive || !this.active || this.explicitPause) return;
    this.statusPollActive = true;
    try {
      const statuses = await Promise.all(offer.groupIds.map(groupId => firstValueFrom(
        this.api.transferStatus(context.hubUrl, offer.offerId, groupId),
      ).catch(() => null)));
      if (!this.isCurrentContext(context, generation) || !this.active || offer !== this.offer) return;
      for (const status of statuses) {
        if (!status) continue;
        this.outboundSnapshots.set(status.groupId, outboundSnapshotFromHubStatus(
          status, this.outboundSnapshots.get(status.groupId)?.retries ?? 0,
        ));
      }
      if (this.outboundSnapshots.size) this.emitAggregatedOutbound('speech_evidence_transfer_status_refreshed');
      if (this.view$.value.sync.receiptVerification === 'hub_verified'
        && !this.view$.value.sync.datasetManifestDigest) {
        await this.hubCurationFlow.restore(context, generation);
      }
    } finally {
      if (generation === this.generation) this.statusPollActive = false;
    }
  }

  private async sendRevocationAttempt(pending: PendingRevocation): Promise<void> {
    const context = this.context;
    if (!context || pending !== this.pendingRevocation || pending.resolved) return;
    if (pending.attempts >= MAX_REVOCATION_ATTEMPTS || Date.now() >= pending.deadlineAtMs) {
      this.patchSync({ revocationState: 'unresolved', reasonCode: 'speech_evidence_remote_revocation_unresolved' });
      return;
    }
    pending.attempts += 1;
    try {
      const message = await this.crypto.sign('revocation', revocationRequestPayload(pending), pending.deadlineAtMs);
      await this.evidenceTransport.send('control', JSON.stringify(message), message.expires_at_ms);
    } catch {
      if (this.context && pending === this.pendingRevocation && !pending.resolved && this.active) {
        this.patchSync({ reasonCode: 'speech_evidence_peer_offline' });
      }
    }
    if (!this.context || pending !== this.pendingRevocation || pending.resolved || !this.active) return;
    this.stopRevocationTimer();
    this.revocationTimer = setTimeout(() => { void this.sendRevocationAttempt(pending); }, REVOCATION_RETRY_MS);
  }

  private stopRevocationTimer(): void {
    if (this.revocationTimer) clearTimeout(this.revocationTimer);
    this.revocationTimer = null;
  }

  private stopRuntime(): void {
    this.active = false;
    this.explicitPause = false;
    this.pausedByTransport = false;
    this.stopStatusPoll();
    this.stopRevocationTimer();
    this.pendingRevocation = null;
    this.localPreAdmissionReasons.clear();
    this.consentPair = null;
    this.evidence.clear();
    this.crypto.clear();
    this.evidenceTransport.clear();
  }

  private async rebuildLocalArtifacts(turns: readonly SpeechTranscriptTurn[]): Promise<void> {
    const context = this.context;
    const generation = ++this.localBuildGeneration;
    if (!context) return;
    const allowed = allowedDataClasses(context.consent);
    const built = await Promise.all(turns
      .filter(isShareableTranscriptTurn)
      .map(turn => buildLocalEvidenceArtifact(turn, allowed)));
    if (generation !== this.localBuildGeneration || context !== this.context) return;
    this.localArtifacts.clear();
    for (const artifact of built) if (artifact) this.localArtifacts.set(artifact.view.groupId, artifact);
    this.emit(this.view$.value.reasonCode);
  }

  private emitAggregatedOutbound(reasonCode: string): void {
    this.emit(reasonCode, aggregateOutboundSnapshots([...this.outboundSnapshots.values()]));
  }

  private emit(reasonCode: string, patch: Partial<PeerEvidenceSyncView> = {}): void {
    const previous = this.view$.value.sync;
    const sync = Object.freeze({
      ...previous,
      ...patch,
      localGroups: Object.freeze([...this.localArtifacts.values()].map(value => value.view)),
      ...this.evidenceView.project(),
    });
    this.view$.next(Object.freeze({ offer: this.offer ? offerView(this.offer, this.context?.localPeerId ?? '') : null, sync, reasonCode }));
  }

  private patchSync(patch: Partial<PeerEvidenceSyncView>): void { this.emit(this.view$.value.reasonCode, patch); }

  private fail(reasonCode: string): void {
    this.emit(reasonCode, { pending: false, state: 'failed', reasonCode });
  }

  private requireContext(): PeerEvidenceSyncContext {
    if (!this.context) throw new SpeechEvidenceValidationError('peer_evidence_sync_context_missing');
    return this.context;
  }

  private requireActiveContext(): PeerEvidenceSyncContext {
    const context = this.requireContext();
    if (!this.active) throw new SpeechEvidenceValidationError('peer_evidence_sync_inactive');
    return context;
  }

  private requireOffer(): ActiveOffer {
    if (!this.offer) throw new SpeechEvidenceValidationError('speech_evidence_offer_not_found');
    return this.offer;
  }

  private requireConsentPair(): SpeechEvidenceConsentPairAuthority {
    if (!this.consentPair) throw new SpeechEvidenceValidationError('speech_evidence_consent_authority_missing');
    return this.consentPair;
  }

  private isCurrentContext(context: PeerEvidenceSyncContext, generation: number): boolean {
    return generation === this.generation && context === this.context;
  }
}
