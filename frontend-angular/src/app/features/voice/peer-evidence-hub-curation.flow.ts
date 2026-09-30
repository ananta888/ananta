import type { SpeechEvidenceQuarantineStore } from '../../services/speech-evidence-quarantine.store';
import type { SpeechEvidenceHubCurationResponse } from '../../services/speech-evidence-sync-api.service';
import type { SpeechEvidenceHubCurationFacade } from '../../services/speech-evidence-hub-curation.facade';
import type { SpeechEvidenceSyncCryptoContext } from '../../services/speech-evidence-sync.providers';
import type { SpeechEvidenceSyncService } from '../../services/speech-evidence-sync.service';
import { SpeechEvidenceValidationError } from '../../services/speech-evidence-sync.validators';
import { buildHubCurationRequestPayload } from './peer-evidence-control-messages';
import { hubCurationBinding } from './peer-evidence-offer.mappers';
import { buildDatasetLineageNodes, lineageWithReceiptStates } from './peer-evidence-projections';
import type { PeerEvidenceSyncView } from './peer-evidence-sync-panel.component';
import type { ActiveOffer, PeerEvidenceSyncContext } from './peer-evidence-sync.models';
import { bytesToBase64, reason } from './peer-evidence-sync-primitives';
import type { PeerEvidenceViewState } from './peer-evidence-view-state';

/** Facade runtime the Hub curation flow reads and reports through. */
export interface PeerEvidenceHubCurationPort {
  readonly evidenceView: PeerEvidenceViewState;
  requireActiveContext(): PeerEvidenceSyncContext;
  requireOffer(): ActiveOffer;
  generation(): number;
  active(): boolean;
  currentOffer(): ActiveOffer | null;
  syncPending(): boolean;
  isCurrentContext(context: PeerEvidenceSyncContext, generation: number): boolean;
  restoreQuarantine(context: PeerEvidenceSyncContext, generation: number): Promise<void>;
  emit(reasonCode: string, patch: Partial<PeerEvidenceSyncView>): void;
  patchSync(patch: Partial<PeerEvidenceSyncView>): void;
  fail(reasonCode: string): void;
}

/** Collaborators the Hub curation flow uses (narrow views). */
export interface PeerEvidenceHubCurationDeps {
  readonly quarantine: Pick<SpeechEvidenceQuarantineStore, 'summaries' | 'group' | 'removeGroups'>;
  readonly evidence: Pick<SpeechEvidenceSyncService, 'decryptChunk'>;
  readonly crypto: Pick<SpeechEvidenceSyncCryptoContext, 'sign'>;
  readonly hubCuration: Pick<SpeechEvidenceHubCurationFacade, 'request' | 'get'>;
}

/**
 * Recipient-side Hub curation of a completely quarantined speech adaptation
 * transfer: decrypts the exact accepted groups, submits them with a signed
 * receipt request, and applies (or restores) the Hub-verified receipt,
 * lineage and dataset projection. Clear chunks are zeroed after upload.
 */
export class PeerEvidenceHubCurationFlow {
  constructor(
    private readonly deps: PeerEvidenceHubCurationDeps,
    private readonly port: PeerEvidenceHubCurationPort,
  ) {}

  async request(): Promise<void> {
    const context = this.port.requireActiveContext();
    const offer = this.port.requireOffer();
    const generation = this.port.generation();
    const transferRecipient = offer.direction === 'sender_to_receiver' ? offer.recipientId : offer.senderId;
    if (
      transferRecipient !== context.localPeerId
      || offer.trainerClass !== 'speech_adaptation'
      || offer.state !== 'accepted'
      || this.port.syncPending()
    ) {
      this.port.fail('speech_evidence_hub_curation_not_authorized');
      return;
    }
    this.port.patchSync({ pending: true, state: 'curation_uploading', reasonCode: null });
    const clearChunks: Uint8Array[] = [];
    try {
      const summaries = await this.deps.quarantine.summaries(
        context.sessionId, context.pairId, context.epoch, offer.offerId,
      );
      const complete = summaries.filter(value => value.complete && value.conflictCount === 0);
      if (
        complete.length !== offer.groupIds.length
        || complete.some(value => !offer.groupIds.includes(value.groupId))
        || this.port.evidenceView.quarantineRows.some(value =>
          offer.groupIds.includes(value.groupId) && value.state !== 'quarantined')
      ) throw new SpeechEvidenceValidationError('speech_evidence_curation_transfer_incomplete');
      const groups: { groupId: string; chunksB64: string[] }[] = [];
      for (const groupId of [...offer.groupIds].sort()) {
        const messages = await this.deps.quarantine.group(
          context.sessionId, context.pairId, context.epoch, offer.offerId, groupId,
        );
        const chunksB64: string[] = [];
        for (const message of messages) {
          const clear = await this.deps.evidence.decryptChunk(message);
          clearChunks.push(clear);
          chunksB64.push(bytesToBase64(clear));
        }
        groups.push({ groupId, chunksB64 });
      }
      if (!this.port.isCurrentContext(context, generation) || !this.port.active()) return;
      const requestPayload = await buildHubCurationRequestPayload(offer, context);
      const requestMessage = await this.deps.crypto.sign(
        'receipt',
        requestPayload,
        Math.min(offer.expiresAtMs, context.consent.consent.expires_at_ms, Date.now() + 5 * 60_000),
      );
      const response = await this.deps.hubCuration.request({
        hubUrl: context.hubUrl,
        binding: hubCurationBinding(context, offer),
        message: requestMessage,
        groups,
      });
      if (!this.port.isCurrentContext(context, generation) || !this.port.active()) return;
      await this.apply(context, offer, response, generation);
    } catch (error) {
      if (!this.port.isCurrentContext(context, generation)) return;
      this.port.fail(reason(error, 'speech_evidence_hub_curation_failed'));
    } finally {
      for (const clear of clearChunks) clear.fill(0);
    }
  }

  /** Re-applies an existing Hub curation of the current accepted offer. */
  async restore(context: PeerEvidenceSyncContext, generation: number): Promise<void> {
    const offer = this.port.currentOffer();
    if (!offer || offer.trainerClass !== 'speech_adaptation' || offer.state !== 'accepted') return;
    const response = await this.deps.hubCuration.get(
      context.hubUrl,
      hubCurationBinding(context, offer),
    ).catch(() => undefined);
    if (!response || !this.port.isCurrentContext(context, generation) || offer !== this.port.currentOffer()) return;
    await this.apply(context, offer, response, generation);
  }

  private async apply(
    context: PeerEvidenceSyncContext,
    offer: ActiveOffer,
    response: SpeechEvidenceHubCurationResponse,
    generation: number,
  ): Promise<void> {
    const view = this.port.evidenceView;
    const receipt = response.curation.receipt;
    const expected = [...offer.groupIds].sort();
    view.lineage = lineageWithReceiptStates(
      view.lineage, receipt.acceptedGroupIds, receipt.rejectedGroupIds, receipt.quarantinedGroupIds,
    );
    view.resolutionHash = receipt.resolutionDigest;
    view.resolutionPolicyVersion = receipt.policyDigest;
    await this.deps.quarantine.removeGroups(
      context.sessionId, context.pairId, context.epoch, offer.offerId, expected,
    );
    if (!this.port.isCurrentContext(context, generation) || offer !== this.port.currentOffer()) return;
    await this.port.restoreQuarantine(context, generation);
    if (!this.port.isCurrentContext(context, generation) || offer !== this.port.currentOffer()) return;
    const datasetLineageNodes = await buildDatasetLineageNodes(response, offer, view.lineage);
    if (!this.port.isCurrentContext(context, generation) || offer !== this.port.currentOffer()) return;
    this.port.emit('speech_evidence_hub_receipt_verified', {
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
}
