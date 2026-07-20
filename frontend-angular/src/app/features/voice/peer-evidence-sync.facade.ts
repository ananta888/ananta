import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription, firstValueFrom } from 'rxjs';

import {
  SpeechEvidenceConsentReadModel,
} from '../../services/speech-evidence-consent-api.service';
import { SpeechEvidenceDatachannelTransportService } from '../../services/speech-evidence-datachannel-transport.service';
import {
  SpeechEvidenceQuarantineGroupSnapshot,
  SpeechEvidenceQuarantineStore,
} from '../../services/speech-evidence-quarantine.store';
import {
  SpeechEvidenceConsentPairAuthority,
  SpeechEvidenceHubCurationResponse,
  SpeechEvidenceOfferRecord,
  SpeechEvidenceSyncApiService,
} from '../../services/speech-evidence-sync-api.service';
import {
  SpeechEvidenceHubCurationBinding,
  SpeechEvidenceHubCurationFacade,
} from '../../services/speech-evidence-hub-curation.facade';
import { SpeechEvidenceSyncCryptoContext } from '../../services/speech-evidence-sync.providers';
import {
  SpeechEvidenceTransferSnapshot,
  SpeechEvidenceSyncService,
} from '../../services/speech-evidence-sync.service';
import {
  SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
  SpeechEvidenceCandidateProjection,
  SpeechEvidenceGroupPreview,
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  canonicalJson,
  sha256Canonical,
  speechEvidenceComparisonDigest,
  speechEvidenceGroupId,
  speechEvidenceGroupPreviews,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceResolutionDigest,
  speechEvidenceSpeakerScopeDigest,
} from '../../services/speech-evidence-sync.validators';
import {
  SpeechTranscriptRevisionStore,
  SpeechTranscriptTurn,
} from '../../services/speech-transcript-revision.store';
import { WebrtcTransportService } from '../../services/webrtc-transport.service';
import {
  PeerEvidenceAcceptanceOffer,
  buildPeerEvidenceAcceptancePayload,
  peerEvidenceBulkAcceptForbidden,
  verifyPeerEvidenceOfferPreview,
} from './peer-evidence-acceptance';
import {
  PeerEvidenceLineageView,
  PeerEvidenceLocalGroupView,
  PeerEvidenceOfferView,
  PeerEvidenceProposalIntent,
  PeerEvidenceQuarantineView,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';
import {
  PeerTranscriptCandidateView,
  PeerTranscriptRegionView,
} from './peer-transcript-conflict-panel.component';
import type { SpeechDatasetLineageNodeView } from '../ml-intern/speech-dataset-lineage.component';

export interface PeerEvidenceSyncContext {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly pairId: string;
  readonly epoch: number;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly consent: SpeechEvidenceConsentReadModel;
}

export interface PeerEvidenceFlowView {
  readonly offer: PeerEvidenceOfferView | null;
  readonly sync: PeerEvidenceSyncView;
  readonly reasonCode: string;
}

interface LocalEvidenceArtifact {
  readonly view: PeerEvidenceLocalGroupView;
  readonly bytes: Uint8Array;
  readonly contentDigest: string;
  readonly sourceGroupDigest: string;
  readonly originalCandidates: readonly SpeechEvidenceCandidateProjection[];
  readonly resolutionState: 'resolved' | 'unresolved';
  readonly selectedCandidateDigest: string | null;
  readonly unresolvedRegionDigests: readonly string[];
  readonly comparisonDigest: string;
}

interface ActiveOffer extends PeerEvidenceAcceptanceOffer {
  readonly offerId: string;
  readonly sessionId: string;
  readonly pairId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly recipientId: string;
  readonly inventoryRootDigest: string;
  readonly direction: string;
  readonly purpose: string;
  readonly dataClasses: readonly string[];
  readonly fields: readonly string[];
  readonly retentionSeconds: number;
  readonly trainerClass: string;
  readonly groupIds: readonly string[];
  readonly groupPreviews: readonly SpeechEvidenceGroupPreview[];
  readonly groupPreviewDigest: string;
  readonly previewVerified: boolean;
  readonly totalBytes: number;
  readonly senderConsentDigest: string;
  readonly recipientConsentDigest: string;
  readonly scopeDigest: string;
  readonly expiresAtMs: number;
  readonly state: string;
  readonly transferStarted: boolean;
  readonly senderConsentVersion: number;
  readonly recipientConsentVersion: number;
}

interface PendingRevocation {
  readonly revocationId: string;
  readonly offerId: string;
  readonly groupIds: readonly string[];
  readonly scopeDigest: string;
  readonly revocationEpoch: number;
  readonly deadlineAtMs: number;
  attempts: number;
  resolved: boolean;
}

const MAX_REVOCATION_ATTEMPTS = 5;
const REVOCATION_RETRY_MS = 2_000;
const STATUS_POLL_MS = 1_500;
const PEER_CURATION_REQUEST_POLICY_DIGEST = 'bd02f0ea6843e13b4be73b3742f1d196a054522ffa4377ce0ddc339d39c46c19';
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
  private conflictCandidates: readonly PeerTranscriptCandidateView[] = Object.freeze([]);
  private conflictRegions: readonly PeerTranscriptRegionView[] = Object.freeze([]);
  private resolutionHash = '';
  private resolutionPolicyVersion = '';
  private lineage: readonly PeerEvidenceLineageView[] = Object.freeze([]);
  private quarantineRows: readonly PeerEvidenceQuarantineView[] = Object.freeze([]);

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
    this.quarantineRows = Object.freeze([]);
    this.lineage = Object.freeze([]);
    this.conflictCandidates = Object.freeze([]);
    this.conflictRegions = Object.freeze([]);
    this.resolutionHash = '';
    this.resolutionPolicyVersion = '';
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
      await this.restoreHubCuration(context, generation);
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
      if (!artifacts.length || new Set(artifacts.map(value => value.view.dataClass)).size !== 1) {
        throw new SpeechEvidenceValidationError('speech_evidence_offer_single_class_required');
      }
      if (artifacts.some(value => !consentPair.remote.dataClasses.includes(value.view.dataClass))) {
        throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
      }
      this.requireTrainerClass(context, intent.trainerClass, consentPair);
      const consent = context.consent.consent;
      const expiresAtMs = Math.min(
        consent.expires_at_ms,
        consentPair.remote.expiresAtMs,
        Date.now() + 5 * 60_000,
      );
      const inventoryRootDigest = await sha256Canonical(artifacts.map(value => ({
        group_id: value.view.groupId,
        content_digest: value.contentDigest,
        data_class: value.view.dataClass,
        bytes: value.view.byteLength,
      })));
      const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest(
        context.pairId,
        context.epoch,
        context.consent.consent.speaker_id,
      );
      const qualityDigest = await speechEvidenceQualityPolicyDigest();
      const groupPreviews = await Promise.all(artifacts.map(async artifact => ({
        preview_version: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
        group_id: artifact.view.groupId,
        source_group_digest: artifact.sourceGroupDigest,
        speaker_scope_digest: speakerScopeDigest,
        quality_basis: 'policy',
        quality_digest: qualityDigest,
        resolution_digest: await speechEvidenceResolutionDigest(
          artifact.sourceGroupDigest,
          artifact.view.revision,
        ),
        original_candidates: artifact.originalCandidates.map(candidate => ({
          ordinal: candidate.ordinal,
          candidate_digest: candidate.candidateDigest,
          authority_digest: candidate.authorityDigest,
          revision: candidate.revision,
        })),
        resolution_state: artifact.resolutionState,
        selected_candidate_digest: artifact.selectedCandidateDigest,
        unresolved_region_digests: [...artifact.unresolvedRegionDigests],
        comparison_digest: artifact.comparisonDigest,
        revision: artifact.view.revision,
        size_bytes: artifact.view.byteLength,
      })));
      const payload = {
        traffic_class: 'control',
        offer_id: `speech-offer-${crypto.randomUUID()}`,
        stage: 'proposal',
        inventory_root_digest: inventoryRootDigest,
        direction: consent.direction,
        purpose: consent.purpose,
        data_classes: [artifacts[0].view.dataClass],
        fields: ['transcript'],
        retention_seconds: Math.min(consent.retention_seconds, consentPair.remote.maximumRetentionSeconds),
        trainer_class: intent.trainerClass,
        group_ids: artifacts.map(value => value.view.groupId).sort(),
        group_previews: groupPreviews.sort((left, right) => left.group_id.localeCompare(right.group_id)),
        total_bytes: artifacts.reduce((total, value) => total + value.view.byteLength, 0),
        sender_consent_digest: context.consent.consentDigest,
        recipient_consent_digest: consentPair.remote.digest,
        scope_digest: context.consent.scopeDigest,
      };
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
        trainerClass: this.allowedTrainerClass(context, offer.trainerClass, consentPair),
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

  async requestHubCuration(): Promise<void> {
    const context = this.requireActiveContext();
    const offer = this.requireOffer();
    const generation = this.generation;
    const transferRecipient = offer.direction === 'sender_to_receiver' ? offer.recipientId : offer.senderId;
    if (
      transferRecipient !== context.localPeerId
      || offer.trainerClass !== 'speech_adaptation'
      || offer.state !== 'accepted'
      || this.view$.value.sync.pending
    ) {
      this.fail('speech_evidence_hub_curation_not_authorized');
      return;
    }
    this.patchSync({ pending: true, state: 'curation_uploading', reasonCode: null });
    const clearChunks: Uint8Array[] = [];
    try {
      const summaries = await this.quarantine.summaries(
        context.sessionId, context.pairId, context.epoch, offer.offerId,
      );
      const complete = summaries.filter(value => value.complete && value.conflictCount === 0);
      if (
        complete.length !== offer.groupIds.length
        || complete.some(value => !offer.groupIds.includes(value.groupId))
        || this.quarantineRows.some(value =>
          offer.groupIds.includes(value.groupId) && value.state !== 'quarantined')
      ) throw new SpeechEvidenceValidationError('speech_evidence_curation_transfer_incomplete');
      const groups: { groupId: string; chunksB64: string[] }[] = [];
      for (const groupId of [...offer.groupIds].sort()) {
        const messages = await this.quarantine.group(
          context.sessionId, context.pairId, context.epoch, offer.offerId, groupId,
        );
        const chunksB64: string[] = [];
        for (const message of messages) {
          const clear = await this.evidence.decryptChunk(message);
          clearChunks.push(clear);
          chunksB64.push(bytesToBase64(clear));
        }
        groups.push({ groupId, chunksB64 });
      }
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      const groupIds = [...offer.groupIds].sort();
      const resolutionDigest = await sha256Canonical({
        offer_id: offer.offerId,
        inventory_root_digest: offer.inventoryRootDigest,
        quarantined_group_ids: groupIds,
      });
      const resultDigest = await sha256Canonical({
        accepted: [],
        quarantined: groupIds,
        rejected: [],
      });
      const requestMessage = await this.crypto.sign('receipt', {
        traffic_class: 'control',
        receipt_id: `curation-request-${crypto.randomUUID()}`,
        offer_id: offer.offerId,
        inventory_root_digest: offer.inventoryRootDigest,
        resolution_digest: resolutionDigest,
        accepted_group_ids: [],
        rejected_group_ids: [],
        quarantined_group_ids: groupIds,
        consent_digest: context.consent.consentDigest,
        policy_digest: PEER_CURATION_REQUEST_POLICY_DIGEST,
        result_digest: resultDigest,
      }, Math.min(offer.expiresAtMs, context.consent.consent.expires_at_ms, Date.now() + 5 * 60_000));
      const response = await this.hubCuration.request({
        hubUrl: context.hubUrl,
        binding: hubCurationBinding(context, offer),
        message: requestMessage,
        groups,
      });
      if (!this.isCurrentContext(context, generation) || !this.active) return;
      await this.applyHubCuration(context, offer, response, generation);
    } catch (error) {
      if (!this.isCurrentContext(context, generation)) return;
      this.fail(reason(error, 'speech_evidence_hub_curation_failed'));
    } finally {
      for (const clear of clearChunks) clear.fill(0);
    }
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
    this.lineage = Object.freeze(this.lineage.map(row =>
      offer.groupIds.includes(row.groupId) ? Object.freeze({ ...row, state: 'revoked' as const }) : row));
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
    this.conflictRegions = Object.freeze(this.conflictRegions.map(region =>
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
    if (
      message.session_id !== context.sessionId
      || message.pair_id !== context.pairId
      || message.epoch !== context.epoch
      || message.sender_id !== context.remotePeerId
      || message.audience_id !== context.localPeerId
      || message.consent_version !== context.consent.consent.consent_version
    ) throw new SpeechEvidenceValidationError('speech_evidence_context_mismatch');
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
      const allowedClasses = allowedDataClasses(context.consent);
      if (
        incoming.recipientId !== context.localPeerId
        || incoming.senderConsentDigest !== consentPair.remote.digest
        || incoming.recipientConsentDigest !== context.consent.consentDigest
        || incoming.direction !== context.consent.consent.direction
        || incoming.purpose !== context.consent.consent.purpose
        || incoming.dataClasses.some(value => peerEvidenceBulkAcceptForbidden(value)
          || !allowedClasses.has(value as 'transcript' | 'text_corrections')
          || !consentPair.remote.dataClasses.includes(value))
        || incoming.fields.some(value => value !== 'transcript' || !consentPair.remote.fields.includes(value))
        || incoming.retentionSeconds > Math.min(
          context.consent.consent.retention_seconds,
          consentPair.remote.maximumRetentionSeconds,
        )
        || (incoming.trainerClass === 'speech_adaptation'
          && (!context.consent.consent.grants.dataset_import || !context.consent.consent.grants.training))
      ) throw new SpeechEvidenceValidationError('speech_evidence_offer_scope_denied');
      this.offer = incoming;
      this.emit('speech_evidence_offer_received', { state: 'offered', reasonCode: null });
      return;
    }
    const current = this.requireOffer();
    if (
      stage !== 'acceptance'
      || current.senderId !== context.localPeerId
      || incoming.offerId !== current.offerId
      || incoming.senderConsentDigest !== current.senderConsentDigest
      || incoming.recipientConsentDigest !== consentPair.remote.digest
      || incoming.scopeDigest !== current.scopeDigest
      || incoming.inventoryRootDigest !== current.inventoryRootDigest
      || incoming.direction !== current.direction
      || incoming.purpose !== current.purpose
      || incoming.groupIds.some(value => !current.groupIds.includes(value))
      || incoming.groupPreviews.some(value => {
        const proposed = current.groupPreviews.find(row => row.groupId === value.groupId);
        return !proposed || canonicalJson(value.value) !== canonicalJson(proposed.value);
      })
      || incoming.dataClasses.some(value => !current.dataClasses.includes(value))
      || incoming.fields.some(value => !current.fields.includes(value))
      || incoming.retentionSeconds > current.retentionSeconds
      || (current.trainerClass === 'none' && incoming.trainerClass !== 'none')
      || incoming.totalBytes > current.totalBytes
    ) throw new SpeechEvidenceValidationError('speech_evidence_offer_acceptance_invalid');
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
    const payload = message.payload;
    const ack = await this.crypto.sign('chunk_ack', {
      traffic_class: 'control',
      offer_id: offerId,
      group_id: groupId,
      acknowledged_indices: [Number(payload['chunk_index'])],
      first_missing_index: stored.snapshot.firstMissingIndex,
      received_bytes: stored.snapshot.receivedBytes,
      complete: stored.snapshot.complete,
    }, Math.min(message.expires_at_ms, Date.now() + 5 * 60_000));
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
    const accepted = new Set(stringArray(message.payload['accepted_group_ids']));
    const rejected = new Set(stringArray(message.payload['rejected_group_ids']));
    const quarantined = new Set(stringArray(message.payload['quarantined_group_ids']));
    if ([...accepted, ...rejected, ...quarantined].some(groupId => !offer.groupIds.includes(groupId))) {
      throw new SpeechEvidenceValidationError('speech_evidence_receipt_groups_invalid');
    }
    this.lineage = Object.freeze(this.lineage.map(row => Object.freeze({
      ...row,
      state: accepted.has(row.groupId) ? 'accepted' as const
        : rejected.has(row.groupId) ? 'rejected' as const
          : quarantined.has(row.groupId) ? 'quarantined' as const : row.state,
    })));
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
    const candidates = message.payload['candidates'];
    if (!Array.isArray(candidates)) throw new SpeechEvidenceValidationError('speech_evidence_candidates_invalid');
    const candidateIds = stringArray(message.payload['candidate_ids']);
    if (candidateIds.length !== candidates.length) {
      throw new SpeechEvidenceValidationError('speech_evidence_candidate_ids_invalid');
    }
    const projected = Object.freeze(await Promise.all(candidates.map(async (raw, index) => {
      const value = object(raw, 'speech_evidence_candidate_invalid');
      const text = boundedText(value['text']);
      return Object.freeze({
        candidateId: candidateIds[index],
        contributorLabel: 'Peer',
        sourceLabel: 'signierte Resolution-Evidence',
        revision: 1,
        text,
        verified: true,
      });
    })));
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.conflictCandidates = projected;
    this.resolutionHash = String(message.payload['result_digest']);
    this.resolutionPolicyVersion = String(message.payload['policy_version']);
    const unresolved = stringArray(message.payload['unresolved_region_ids']);
    this.conflictRegions = Object.freeze(unresolved.map(regionId => Object.freeze({
      regionId,
      kind: 'unresolved',
      candidateIds: Object.freeze(this.conflictCandidates.map(value => value.candidateId)),
      selectedCandidateId: null,
      unresolved: true,
      reasonCode: 'hub_curation_required',
    })));
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
    this.lineage = Object.freeze(this.lineage.map(row =>
      groups.includes(row.groupId) ? Object.freeze({ ...row, state: 'revoked' as const }) : row));
    await this.restoreQuarantine(context, generation);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const groupResults = groups.map(groupId => ({
      group_id: groupId,
      state: 'deleted',
      reason_code: 'local_cleanup_complete',
    }));
    const impactDigest = await sha256Canonical(groupResults);
    const ack = await this.crypto.sign('revocation_ack', {
      traffic_class: 'control',
      revocation_id: message.payload['revocation_id'],
      scope_digest: offer.scopeDigest,
      revocation_epoch: message.payload['revocation_epoch'],
      impact_digest: impactDigest,
      group_results: groupResults,
      decision: 'complete',
    }, Math.min(message.expires_at_ms, Date.now() + 5 * 60_000));
    const delivered = await this.evidenceTransport.send('control', JSON.stringify(ack), ack.expires_at_ms);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.emit('speech_evidence_remote_revocation_applied', {
      state: 'revoked',
      revocationState: delivered ? 'acknowledged' : 'unresolved',
      reasonCode: delivered ? null : 'speech_evidence_revocation_ack_delivery_failed',
    });
  }

  private handleRevocationAck(message: SpeechEvidenceMessage): void {
    const pending = this.pendingRevocation;
    if (
      !pending
      || message.payload['revocation_id'] !== pending.revocationId
      || message.payload['scope_digest'] !== pending.scopeDigest
      || message.payload['revocation_epoch'] !== pending.revocationEpoch
    ) throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_binding_mismatch');
    const results = message.payload['group_results'];
    if (!Array.isArray(results)) throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_invalid');
    const resolved = new Set(results
      .map(value => object(value, 'speech_evidence_revocation_ack_invalid'))
      .filter(value => ['deleted', 'use_stopped', 'not_found'].includes(String(value['state'])))
      .map(value => String(value['group_id'])));
    if ([...resolved].some(groupId => !pending.groupIds.includes(groupId))) {
      throw new SpeechEvidenceValidationError('speech_evidence_revocation_ack_groups_invalid');
    }
    pending.resolved = resolved.size === pending.groupIds.length && message.payload['decision'] === 'complete';
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
    const sourceRevisions = new Map<string, number>();
    for (const turn of this.transcripts.turns$.value) {
      if (typeof turn.sourceDigest !== 'string' || !/^[a-f0-9]{64}$/.test(turn.sourceDigest)) continue;
      sourceRevisions.set(
        turn.sourceDigest,
        Math.max(sourceRevisions.get(turn.sourceDigest) ?? 0, turn.revision),
      );
    }
    return sourceRevisions;
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
    let payload: ReturnType<typeof parseEvidenceGroup>;
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
    const remoteCandidates: PeerTranscriptCandidateView[] = payload.candidates.map((candidate, index) => ({
      candidateId: `${snapshot.groupId}-remote-${index}`,
      contributorLabel: 'Peer',
      sourceLabel: candidate.authority,
      revision: candidate.revision,
      text: candidate.text,
      verified: true,
    }));
    const local = this.transcripts.turns$.value.find(turn => turn.turnId === payload.turnId);
    const localCandidates: PeerTranscriptCandidateView[] = (local?.originalCandidates ?? []).map((candidate, index) => ({
      candidateId: `${snapshot.groupId}-local-${index}`,
      contributorLabel: 'Lokal',
      sourceLabel: candidate.authority,
      revision: candidate.revision,
      text: candidate.text,
      verified: true,
    }));
    const candidates = [...localCandidates, ...remoteCandidates];
    this.conflictCandidates = Object.freeze(candidates.map(value => Object.freeze(value)));
    const texts = new Set(candidates.map(value => value.text));
    this.conflictRegions = Object.freeze([Object.freeze({
      regionId: `region-${snapshot.groupId}`,
      kind: texts.size <= 1 ? 'exact' : 'lexical',
      candidateIds: Object.freeze(candidates.map(value => value.candidateId)),
      selectedCandidateId: texts.size <= 1 ? candidates[0]?.candidateId ?? null : null,
      unresolved: texts.size > 1,
      reasonCode: texts.size <= 1 ? 'exact_match' : 'hub_resolution_required',
    })]);
    this.resolutionHash = snapshot.lineageDigests.join(':');
    this.resolutionPolicyVersion = 'display-only-no-admission-v1';
    const contributorDigest = await sha256Text(`peer\0${context.remotePeerId}`);
    const fieldDigest = await sha256Canonical(['transcript']);
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.lineage = Object.freeze([
      ...this.lineage.filter(value => value.groupId !== snapshot.groupId),
      Object.freeze({
        groupId: snapshot.groupId,
        contributorDigest,
        consentDigest: context.consent.consentDigest,
        fieldProvenanceDigests: Object.freeze([fieldDigest]),
        state: 'quarantined' as const,
      }),
    ]);
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

  private async restoreHubCuration(context: PeerEvidenceSyncContext, generation: number): Promise<void> {
    const offer = this.offer;
    if (!offer || offer.trainerClass !== 'speech_adaptation' || offer.state !== 'accepted') return;
    const response = await this.hubCuration.get(
      context.hubUrl,
      hubCurationBinding(context, offer),
    ).catch(() => undefined);
    if (!response || !this.isCurrentContext(context, generation) || offer !== this.offer) return;
    await this.applyHubCuration(context, offer, response, generation);
  }

  private async applyHubCuration(
    context: PeerEvidenceSyncContext,
    offer: ActiveOffer,
    response: SpeechEvidenceHubCurationResponse,
    generation: number,
  ): Promise<void> {
    const receipt = response.curation.receipt;
    const expected = [...offer.groupIds].sort();
    const accepted = new Set(receipt.acceptedGroupIds);
    const rejected = new Set(receipt.rejectedGroupIds);
    const quarantined = new Set(receipt.quarantinedGroupIds);
    this.lineage = Object.freeze(this.lineage.map(row => Object.freeze({
      ...row,
      state: accepted.has(row.groupId) ? 'accepted' as const
        : rejected.has(row.groupId) ? 'rejected' as const
          : quarantined.has(row.groupId) ? 'quarantined' as const : row.state,
    })));
    this.resolutionHash = receipt.resolutionDigest;
    this.resolutionPolicyVersion = receipt.policyDigest;
    await this.quarantine.removeGroups(
      context.sessionId, context.pairId, context.epoch, offer.offerId, expected,
    );
    if (!this.isCurrentContext(context, generation) || offer !== this.offer) return;
    await this.restoreQuarantine(context, generation);
    if (!this.isCurrentContext(context, generation) || offer !== this.offer) return;
    const datasetLineageNodes = await buildDatasetLineageNodes(response, offer, this.lineage);
    if (!this.isCurrentContext(context, generation) || offer !== this.offer) return;
    this.emit('speech_evidence_hub_receipt_verified', {
      pending: false,
      state: response.curation.state === 'dataset_published' ? 'dataset_published'
        : response.curation.state === 'admitted' ? 'curation_queued' : response.curation.state,
      receiptId: receipt.receiptId,
      receiptVerification: 'hub_verified',
      curationTaskId: response.curation.curationTaskId,
      datasetId: response.curation.datasetId,
      datasetManifestDigest: response.curation.datasetManifestDigest,
      datasetLineageNodes,
      reasonCode: null,
    });
  }

  private async restoreQuarantine(context: PeerEvidenceSyncContext, generation = this.generation): Promise<void> {
    await this.quarantine.pruneExpired();
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    const summaries = await this.quarantine.summaries(
      context.sessionId, context.pairId, context.epoch, this.offer?.offerId,
    );
    if (!this.isCurrentContext(context, generation) || !this.active) return;
    this.quarantineRows = Object.freeze(summaries.map(row => {
      const localReason = this.localPreAdmissionReasons.get(row.groupId) ?? null;
      return Object.freeze({
        offerId: row.offerId,
        groupId: row.groupId,
        receivedChunks: row.receivedChunks,
        chunkCount: row.chunkCount,
        firstMissingIndex: row.firstMissingIndex,
        receivedBytes: row.receivedBytes,
        state: row.conflictCount || localReason ? 'conflict' as const
          : row.complete ? 'quarantined' as const : 'receiving' as const,
        reasonCode: row.conflictCount ? 'speech_evidence_chunk_index_conflict' : localReason,
      });
    }));
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
        this.outboundSnapshots.set(status.groupId, {
          offerId: status.offerId,
          groupId: status.groupId,
          state: status.state === 'completed' ? 'completed' : status.state === 'active' ? 'active' : 'failed',
          chunkCount: status.chunkCount,
          acknowledgedChunks: status.acknowledgedChunks,
          firstMissingIndex: status.firstMissingIndex,
          inFlightBytes: status.inFlightBytes,
          retries: this.outboundSnapshots.get(status.groupId)?.retries ?? 0,
          reasonCode: status.reasonCode,
        });
      }
      if (this.outboundSnapshots.size) this.emitAggregatedOutbound('speech_evidence_transfer_status_refreshed');
      if (this.view$.value.sync.receiptVerification === 'hub_verified'
        && !this.view$.value.sync.datasetManifestDigest) {
        await this.restoreHubCuration(context, generation);
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
      const message = await this.crypto.sign('revocation', {
        traffic_class: 'control',
        revocation_id: pending.revocationId,
        group_ids: [...pending.groupIds],
        scope_digest: pending.scopeDigest,
        reason_code: 'speech_evidence_user_revoked',
        revocation_epoch: pending.revocationEpoch,
        deadline_at_ms: pending.deadlineAtMs,
        requested_action: 'delete',
      }, pending.deadlineAtMs);
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
      .filter(turn => ['final', 'corrected', 'correction_failed'].includes(turn.state)
        && typeof turn.sourceDigest === 'string' && /^[a-f0-9]{64}$/.test(turn.sourceDigest))
      .map(async turn => {
        const dataClass = turn.state === 'corrected' ? 'text_corrections' as const : 'transcript' as const;
        if (!allowed.has(dataClass)) return null;
        const payload = {
          schema: 'ananta.peer-transcript-evidence.v1',
          turn_id: turn.turnId,
          revision: turn.revision,
          state: turn.state,
          source_digest: turn.sourceDigest,
          candidates: turn.originalCandidates.map(candidate => ({
            revision: candidate.revision,
            authority: candidate.authority,
            text: candidate.text,
          })),
        };
        const bytes = new TextEncoder().encode(canonicalJson(payload));
        if (!bytes.byteLength || bytes.byteLength > 1024 * 1024) return null;
        const contentDigest = await sha256Bytes(bytes);
        const groupId = await speechEvidenceGroupId(turn.sourceDigest, turn.revision);
        const comparison = await contentFreeComparisonProjection(turn);
        return Object.freeze({
          view: Object.freeze({
            groupId,
            turnId: turn.turnId,
            revision: turn.revision,
            dataClass,
            fields: Object.freeze(['transcript']),
            byteLength: bytes.byteLength,
            sourceState: turn.state,
          }),
          bytes,
          contentDigest,
          sourceGroupDigest: turn.sourceDigest,
          ...comparison,
        } satisfies LocalEvidenceArtifact);
      }));
    if (generation !== this.localBuildGeneration || context !== this.context) return;
    this.localArtifacts.clear();
    for (const artifact of built) if (artifact) this.localArtifacts.set(artifact.view.groupId, artifact);
    this.emit(this.view$.value.reasonCode);
  }

  private emitAggregatedOutbound(reasonCode: string): void {
    const snapshots = [...this.outboundSnapshots.values()];
    const acknowledged = snapshots.reduce((total, value) => total + value.acknowledgedChunks, 0);
    const count = snapshots.reduce((total, value) => total + value.chunkCount, 0);
    this.emit(reasonCode, {
      state: snapshots.length && snapshots.every(value => value.state === 'completed') ? 'completed'
        : snapshots.some(value => value.state === 'failed') ? 'failed' : 'transferring',
      acknowledgedChunks: acknowledged,
      chunkCount: count,
      firstMissingIndex: snapshots.length ? Math.min(...snapshots.map(value => value.firstMissingIndex)) : 0,
      inFlightBytes: snapshots.reduce((total, value) => total + value.inFlightBytes, 0),
      retries: snapshots.reduce((total, value) => total + value.retries, 0),
      reasonCode: snapshots.find(value => value.reasonCode)?.reasonCode ?? null,
    });
  }

  private emit(reasonCode: string, patch: Partial<PeerEvidenceSyncView> = {}): void {
    const previous = this.view$.value.sync;
    const sync = Object.freeze({
      ...previous,
      ...patch,
      localGroups: Object.freeze([...this.localArtifacts.values()].map(value => value.view)),
      quarantine: this.quarantineRows,
      quarantineCount: this.quarantineRows.filter(value => value.state === 'quarantined' || value.state === 'conflict').length,
      lineage: this.lineage,
      candidates: this.conflictCandidates,
      regions: this.conflictRegions,
      resolutionHash: this.resolutionHash,
      resolutionPolicyVersion: this.resolutionPolicyVersion,
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

  private requireTrainerClass(
    context: PeerEvidenceSyncContext,
    value: string,
    consentPair: SpeechEvidenceConsentPairAuthority,
  ): void {
    if (value !== 'none' && value !== 'speech_adaptation') {
      throw new SpeechEvidenceValidationError('speech_evidence_trainer_class_invalid');
    }
    if (value === 'speech_adaptation'
      && (!context.consent.consent.grants.dataset_import
        || !context.consent.consent.grants.training
        || !consentPair.remote.trainerClasses.includes('speech_adaptation'))) {
      throw new SpeechEvidenceValidationError('speech_evidence_training_consent_required');
    }
  }

  private allowedTrainerClass(
    context: PeerEvidenceSyncContext,
    requested: string,
    consentPair: SpeechEvidenceConsentPairAuthority,
  ): 'none' | 'speech_adaptation' {
    if (requested === 'speech_adaptation'
      && context.consent.consent.grants.dataset_import
      && context.consent.consent.grants.training
      && consentPair.remote.trainerClasses.includes('speech_adaptation')) return 'speech_adaptation';
    return 'none';
  }
}

function emptySync(state: string): PeerEvidenceSyncView {
  return Object.freeze({
    state,
    pending: false,
    acknowledgedChunks: 0,
    chunkCount: 0,
    firstMissingIndex: 0,
    inFlightBytes: 0,
    retries: 0,
    quarantineCount: 0,
    receiptId: null,
    receiptVerification: 'none',
    curationTaskId: null,
    datasetId: null,
    datasetManifestDigest: null,
    datasetLineageNodes: Object.freeze([]),
    revocationState: null,
    reasonCode: null,
    localGroups: Object.freeze([]),
    quarantine: Object.freeze([]),
    lineage: Object.freeze([]),
    candidates: Object.freeze([]),
    regions: Object.freeze([]),
    resolutionHash: '',
    resolutionPolicyVersion: '',
  });
}

function validateContext(value: PeerEvidenceSyncContext): PeerEvidenceSyncContext {
  const consent = value.consent.consent;
  const participants = new Set([consent.speaker_id, consent.recipient_id]);
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || value.pairId !== value.sessionId
    || consent.session_id !== value.sessionId
    || consent.pair_id !== value.pairId
    || consent.session_epoch !== value.epoch
    || consent.owner_subject !== consent.speaker_id
    || participants.size !== 2
    || !participants.has(value.localPeerId)
    || !participants.has(value.remotePeerId)
    || consent.required_signers.length !== 2
    || consent.required_signers.some(signer => !participants.has(signer))
    || consent.direction !== 'sender_to_receiver'
    || consent.state !== 'active'
    || consent.expires_at_ms <= Date.now()
    || (!consent.grants.transcript_share && !consent.grants.feature_share)
  ) throw new SpeechEvidenceValidationError('peer_evidence_sync_context_invalid');
  return Object.freeze({ ...value, hubUrl: value.hubUrl.replace(/\/+$/, '') });
}

function contextKey(value: PeerEvidenceSyncContext | null): string {
  return value ? [
    value.hubUrl, value.sessionId, value.pairId, value.epoch, value.localPeerId, value.remotePeerId,
    value.consent.consent.consent_version, value.consent.consentDigest,
  ].join('\0') : '';
}

function validatePairConsentAuthority(
  value: SpeechEvidenceConsentPairAuthority,
  context: PeerEvidenceSyncContext,
): SpeechEvidenceConsentPairAuthority {
  const local = value.local;
  const remote = value.remote;
  const consent = context.consent.consent;
  if (
    local.peerId !== context.localPeerId
    || remote.peerId !== context.remotePeerId
    || local.pairId !== context.pairId
    || remote.pairId !== context.pairId
    || local.version !== consent.consent_version
    || remote.version !== local.version
    || local.digest !== context.consent.consentDigest
    || local.expiresAtMs > consent.expires_at_ms
    || local.expiresAtMs <= Date.now()
    || remote.expiresAtMs <= Date.now()
    || !local.directions.includes(consent.direction)
    || !remote.directions.includes(consent.direction)
    || !local.purposes.includes(consent.purpose)
    || !remote.purposes.includes(consent.purpose)
  ) throw new SpeechEvidenceValidationError('speech_evidence_consent_authority_stale');
  return value;
}

function offerFromMessage(message: SpeechEvidenceMessage, localConsentVersion: number): ActiveOffer {
  const payload = message.payload;
  const stage = String(payload['stage']);
  if (stage !== 'proposal' && stage !== 'acceptance') {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_stage_invalid');
  }
  const groupPreviews = speechEvidenceGroupPreviews(payload);
  return Object.freeze({
    offerId: identifier(payload['offer_id']),
    sessionId: message.session_id,
    pairId: message.pair_id,
    epoch: message.epoch,
    senderId: stage === 'proposal' ? message.sender_id : message.audience_id,
    recipientId: stage === 'proposal' ? message.audience_id : message.sender_id,
    inventoryRootDigest: digest(payload['inventory_root_digest']),
    direction: identifier(payload['direction']),
    purpose: identifier(payload['purpose']),
    dataClasses: Object.freeze(stringArray(payload['data_classes'])),
    fields: Object.freeze(stringArray(payload['fields'])),
    retentionSeconds: positiveInteger(payload['retention_seconds']),
    trainerClass: identifier(payload['trainer_class']),
    groupIds: Object.freeze(stringArray(payload['group_ids'])),
    groupPreviews,
    groupPreviewDigest: message.payload_digest,
    previewVerified: false,
    totalBytes: positiveInteger(payload['total_bytes']),
    senderConsentDigest: digest(payload['sender_consent_digest']),
    recipientConsentDigest: digest(payload['recipient_consent_digest']),
    scopeDigest: digest(payload['scope_digest']),
    expiresAtMs: message.expires_at_ms,
    state: stage === 'proposal' ? 'proposed' : 'accepted',
    transferStarted: false,
    senderConsentVersion: stage === 'proposal' ? message.consent_version : localConsentVersion,
    recipientConsentVersion: stage === 'acceptance' ? message.consent_version : localConsentVersion,
  });
}

function offerFromRecord(
  record: SpeechEvidenceOfferRecord,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): ActiveOffer {
  const versionFor = (peerId: string): number => peerId === context.localPeerId
    ? consentPair.local.version
    : peerId === context.remotePeerId
      ? consentPair.remote.version
      : 0;
  const senderConsentVersion = versionFor(record.senderId);
  const recipientConsentVersion = versionFor(record.recipientId);
  if (!senderConsentVersion || !recipientConsentVersion) {
    throw new SpeechEvidenceValidationError('speech_evidence_offer_pair_invalid');
  }
  return Object.freeze({
    offerId: record.offerId,
    sessionId: record.sessionId,
    pairId: record.pairId,
    epoch: record.epoch,
    senderId: record.senderId,
    recipientId: record.recipientId,
    inventoryRootDigest: record.inventoryRootDigest,
    direction: record.direction,
    purpose: record.purpose,
    dataClasses: record.dataClasses,
    fields: record.fields,
    retentionSeconds: record.retentionSeconds,
    trainerClass: record.trainerClass,
    groupIds: record.groupIds,
    groupPreviews: record.groupPreviews,
    groupPreviewDigest: record.groupPreviewDigest,
    previewVerified: false,
    totalBytes: record.totalBytes,
    senderConsentDigest: record.senderConsentDigest,
    recipientConsentDigest: record.recipientConsentDigest,
    scopeDigest: record.scopeDigest,
    expiresAtMs: record.expiresAtMs,
    state: record.state,
    transferStarted: record.transferStarted,
    senderConsentVersion,
    recipientConsentVersion,
  });
}

function consentDigestForPeer(
  peerId: string,
  context: PeerEvidenceSyncContext,
  consentPair: SpeechEvidenceConsentPairAuthority,
): string {
  if (peerId === context.localPeerId) return consentPair.local.digest;
  if (peerId === context.remotePeerId) return consentPair.remote.digest;
  return '';
}

function hubCurationBinding(
  context: PeerEvidenceSyncContext,
  offer: ActiveOffer,
): SpeechEvidenceHubCurationBinding {
  return Object.freeze({
    offerId: offer.offerId,
    inventoryRootDigest: offer.inventoryRootDigest,
    pairId: offer.pairId,
    direction: offer.direction,
    consentDigest: context.consent.consentDigest,
    groupIds: Object.freeze([...offer.groupIds]),
  });
}

function offerView(offer: ActiveOffer, localPeerId: string): PeerEvidenceOfferView {
  const action: PeerEvidenceOfferView['action'] = ['invalidated', 'expired', 'rejected'].includes(offer.state)
    ? 'terminal'
    : offer.state === 'proposed' && offer.recipientId === localPeerId
      ? 'accept'
      : offer.state === 'proposed'
        ? 'awaiting_peer'
        : 'transfer';
  return Object.freeze({
    offerId: offer.offerId,
    direction: offer.direction,
    purpose: offer.purpose,
    dataClasses: offer.dataClasses,
    fields: offer.fields,
    retentionSeconds: offer.retentionSeconds,
    trainerClass: offer.trainerClass,
    groupCount: offer.groupIds.length,
    groupPreviews: offer.groupPreviews,
    previewVerified: offer.previewVerified,
    totalBytes: offer.totalBytes,
    senderConsentVersion: offer.senderConsentVersion,
    recipientConsentVersion: offer.recipientConsentVersion,
    state: offer.state,
    action,
    expiresAtMs: offer.expiresAtMs,
  });
}

function allowedDataClasses(consent: SpeechEvidenceConsentReadModel): Set<'transcript' | 'text_corrections'> {
  const values = new Set<'transcript' | 'text_corrections'>();
  if (consent.consent.grants.transcript_share && consent.consent.data_classes.includes('transcript')) {
    values.add('transcript');
    values.add('text_corrections');
  }
  if (consent.consent.grants.transcript_share && consent.consent.data_classes.includes('correction')) {
    values.add('text_corrections');
  }
  return values;
}

function parseEvidenceGroup(bytes: Uint8Array): Readonly<{
  turnId: string;
  revision: number;
  state: 'final' | 'corrected' | 'correction_failed';
  sourceDigest: string;
  candidates: readonly Readonly<{ revision: number; authority: string; text: string }>[];
}> {
  let raw: unknown;
  try { raw = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid'); }
  const row = object(raw, 'speech_evidence_group_payload_invalid');
  const fields = ['schema', 'turn_id', 'revision', 'state', 'source_digest', 'candidates'];
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !(key in row))) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid');
  }
  if (row['schema'] !== 'ananta.peer-transcript-evidence.v1' || !Array.isArray(row['candidates'])
    || !row['candidates'].length || row['candidates'].length > 32) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_payload_invalid');
  }
  const revision = positiveInteger(row['revision']);
  const state = String(row['state']);
  if (!['final', 'corrected', 'correction_failed'].includes(state)) {
    throw new SpeechEvidenceValidationError('speech_evidence_group_state_invalid');
  }
  const sourceDigest = digest(row['source_digest']);
  const candidates = row['candidates'].map(value => {
    const candidate = object(value, 'speech_evidence_candidate_invalid');
    const expected = ['revision', 'authority', 'text'];
    if (Object.keys(candidate).some(key => !expected.includes(key)) || expected.some(key => !(key in candidate))) {
      throw new SpeechEvidenceValidationError('speech_evidence_candidate_invalid');
    }
    const candidateRevision = positiveInteger(candidate['revision']);
    if (candidateRevision > revision) {
      throw new SpeechEvidenceValidationError('speech_evidence_candidate_revision_invalid');
    }
    return Object.freeze({
      revision: candidateRevision,
      authority: identifier(candidate['authority']),
      text: boundedText(candidate['text']),
    });
  });
  return Object.freeze({
    turnId: identifier(row['turn_id']),
    revision,
    state: state as 'final' | 'corrected' | 'correction_failed',
    sourceDigest,
    candidates: Object.freeze(candidates),
  });
}

async function recipientPreAdmissionReason(
  payload: ReturnType<typeof parseEvidenceGroup>,
  snapshot: SpeechEvidenceQuarantineGroupSnapshot,
  expectedGroupId: string,
  preview: SpeechEvidenceGroupPreview | undefined,
): Promise<string | null> {
  if (!preview) return 'speech_evidence_offer_preview_required';
  if (
    preview.sourceGroupDigest !== payload.sourceDigest
    || preview.groupId !== expectedGroupId
  ) return 'speech_evidence_source_group_mismatch';
  if (preview.revision !== payload.revision) return 'speech_evidence_offer_preview_stale';
  if (preview.sizeBytes !== snapshot.receivedBytes) return 'speech_evidence_offer_preview_size_mismatch';
  const actualComparison = await contentFreeComparisonProjection(payload);
  if (
    preview.comparisonDigest !== actualComparison.comparisonDigest
    || preview.resolutionState !== actualComparison.resolutionState
    || preview.selectedCandidateDigest !== actualComparison.selectedCandidateDigest
    || canonicalJson(preview.originalCandidates) !== canonicalJson(actualComparison.originalCandidates)
    || canonicalJson(preview.unresolvedRegionDigests) !== canonicalJson(actualComparison.unresolvedRegionDigests)
  ) return 'speech_evidence_comparison_projection_mismatch';
  if (snapshot.groupId !== expectedGroupId || snapshot.conflictCount !== 0 || !snapshot.complete) {
    return 'speech_evidence_local_digest_binding_failed';
  }
  const candidateBindings = new Set<string>();
  for (const candidate of payload.candidates) {
    const binding = `${candidate.authority}\0${candidate.revision}\0${candidate.text}`;
    if (candidateBindings.has(binding)) return 'speech_evidence_local_candidate_replay';
    candidateBindings.add(binding);
    const text = candidate.text.toLowerCase();
    if (/\bignore\s+(?:all\s+|any\s+)?(?:previous|prior)\s+(?:system\s+)?(?:instructions?|prompts?)\b/.test(text)
      || /\btargeted[\s_-]+trigger\b/.test(text)) {
      return 'speech_evidence_local_prompt_injection_risk';
    }
    if (/\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b/i.test(candidate.text)
      || /\b(?:api[_ -]?key|access[_ -]?token|password|private[_ -]?key)\s*[:=]\s*\S+/i.test(candidate.text)) {
      return 'speech_evidence_local_privacy_risk';
    }
  }
  return null;
}

async function contentFreeComparisonProjection(value: Readonly<{
  revision: number;
  state: string;
  sourceDigest: string | null;
  originalCandidates?: readonly Readonly<{ revision: number; authority: string; text: string }>[];
  candidates?: readonly Readonly<{ revision: number; authority: string; text: string }>[];
}>): Promise<Readonly<{
  originalCandidates: readonly SpeechEvidenceCandidateProjection[];
  resolutionState: 'resolved' | 'unresolved';
  selectedCandidateDigest: string | null;
  unresolvedRegionDigests: readonly string[];
  comparisonDigest: string;
}>> {
  const sourceGroupDigest = value.sourceDigest;
  const rawCandidates = value.originalCandidates ?? value.candidates ?? [];
  if (!sourceGroupDigest || !/^[a-f0-9]{64}$/.test(sourceGroupDigest) || !rawCandidates.length || rawCandidates.length > 32) {
    throw new SpeechEvidenceValidationError('speech_evidence_candidate_projection_invalid');
  }
  const originalCandidates = Object.freeze(await Promise.all(rawCandidates.map(async (candidate, index) => Object.freeze({
    ordinal: index + 1,
    candidateDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-original-candidate.v1',
      source_group_digest: sourceGroupDigest,
      ordinal: index + 1,
      revision: candidate.revision,
      authority: candidate.authority,
      candidate_value: candidate.text,
    }),
    authorityDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-candidate-authority.v1',
      authority: candidate.authority,
    }),
    revision: candidate.revision,
  }))));
  if (new Set(originalCandidates.map(candidate => candidate.candidateDigest)).size !== originalCandidates.length) {
    throw new SpeechEvidenceValidationError('speech_evidence_candidate_projection_invalid');
  }
  const resolutionState = value.state === 'correction_failed' ? 'unresolved' as const : 'resolved' as const;
  const selectedCandidateDigest = resolutionState === 'resolved'
    ? ([...originalCandidates].reverse().find(candidate => candidate.revision === value.revision)
      ?? originalCandidates[originalCandidates.length - 1]).candidateDigest
    : null;
  const unresolvedRegionDigests = resolutionState === 'unresolved'
    ? Object.freeze([await sha256Canonical({
      domain: 'ananta.speech-evidence-unresolved-region.v1',
      source_group_digest: sourceGroupDigest,
      candidate_digests: originalCandidates.map(candidate => candidate.candidateDigest).sort(),
    })])
    : Object.freeze([] as string[]);
  const comparisonDigest = await speechEvidenceComparisonDigest({
    sourceGroupDigest,
    revision: value.revision,
    originalCandidates,
    resolutionState,
    selectedCandidateDigest,
    unresolvedRegionDigests,
  });
  return Object.freeze({
    originalCandidates,
    resolutionState,
    selectedCandidateDigest,
    unresolvedRegionDigests,
    comparisonDigest,
  });
}

function concatenate(chunks: readonly Uint8Array[]): Uint8Array {
  const size = chunks.reduce((total, value) => total + value.byteLength, 0);
  if (!size || size > 1024 * 1024) throw new SpeechEvidenceValidationError('speech_evidence_group_size_invalid');
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

function unique(values: readonly string[]): string[] {
  if (!Array.isArray(values) || values.length > 4096) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  const result = [...new Set(values.map(identifier))];
  if (result.length !== values.length) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  return result;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 4096) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
  return unique(value.map(identifier));
}

function object(value: unknown, reasonCode: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new SpeechEvidenceValidationError(reasonCode);
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)) {
    throw new SpeechEvidenceValidationError('speech_evidence_identifier_invalid');
  }
  return value;
}

function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) {
    throw new SpeechEvidenceValidationError('speech_evidence_digest_invalid');
  }
  return value;
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) {
    throw new SpeechEvidenceValidationError('speech_evidence_integer_invalid');
  }
  return Number(value);
}

function boundedText(value: unknown): string {
  if (typeof value !== 'string' || !value.length || value.length > 32_768) {
    throw new SpeechEvidenceValidationError('speech_evidence_text_invalid');
  }
  return value;
}

async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digestBytes = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digestBytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

async function buildDatasetLineageNodes(
  response: SpeechEvidenceHubCurationResponse,
  offer: ActiveOffer,
  evidenceLineage: readonly PeerEvidenceLineageView[],
): Promise<readonly SpeechDatasetLineageNodeView[]> {
  const curation = response.curation;
  const manifestDigest = curation.datasetManifestDigest;
  const taskId = curation.curationTaskId;
  if (curation.state !== 'dataset_published' || !manifestDigest || !taskId) return Object.freeze([]);
  const accepted = evidenceLineage.filter(value => value.state === 'accepted');
  const contributors = [...new Set(accepted.map(value => value.contributorDigest))];
  if (!contributors.length) contributors.push(await sha256Text(`peer\0${offer.senderId}`));
  const fieldProvenance = [...new Set(accepted.flatMap(value => value.fieldProvenanceDigests))];
  if (!fieldProvenance.length) fieldProvenance.push(await sha256Canonical([...offer.fields].sort()));
  return Object.freeze([Object.freeze({
    datasetId: curation.datasetId,
    version: `sha256:${manifestDigest}`,
    parentVersion: curation.datasetParentDigest ? `sha256:${curation.datasetParentDigest}` : null,
    manifestDigest,
    receiptId: curation.receipt.receiptId,
    contributorDigests: Object.freeze(contributors.sort()),
    direction: curation.receipt.direction,
    consentDigest: curation.receipt.consentDigest,
    fieldProvenanceDigests: Object.freeze(fieldProvenance.sort()),
    createdByTaskId: taskId,
  })]);
}

function bytesToBase64(value: Uint8Array): string {
  let binary = '';
  for (let offset = 0; offset < value.byteLength; offset += 0x8000) {
    binary += String.fromCharCode(...value.subarray(offset, Math.min(value.byteLength, offset + 0x8000)));
  }
  return btoa(binary);
}

function sameSourceRevisions(
  first: ReadonlyMap<string, number>,
  second: ReadonlyMap<string, number>,
): boolean {
  if (first.size !== second.size) return false;
  for (const [sourceDigest, revision] of first) {
    if (second.get(sourceDigest) !== revision) return false;
  }
  return true;
}

function reason(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const value = error as { error?: { error?: { code?: unknown } | string }; message?: unknown };
    const nested = value.error?.error;
    if (typeof nested === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(nested)) return nested;
    if (nested && typeof nested === 'object' && typeof nested.code === 'string') return nested.code;
    if (typeof value.message === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(value.message)) return value.message;
  }
  return fallback;
}
