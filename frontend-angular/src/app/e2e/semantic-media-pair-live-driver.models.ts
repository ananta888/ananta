import type { PeerEvidenceOfferView } from '../features/voice/peer-evidence-sync-panel.component';
import { SpeechEvidenceHubCurationFacade } from '../services/speech-evidence-hub-curation.facade';
import {
  SpeechEvidenceOfferRecord,
  SpeechEvidenceSyncApiService,
} from '../services/speech-evidence-sync-api.service';
import { SpeechEvidenceMessage } from '../services/speech-evidence-sync.validators';
import { WebrtcTransportService } from '../services/webrtc-transport.service';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';
import { PairSessionControlPlaneService } from '../services/pair-session-control-plane.service';
import { SemanticSpeechQualityControllerService } from '../services/semantic-speech-quality-controller.service';
import { VoiceApiService } from '../features/voice/voice-api.service';

/** Contract between the semantic-media Pair live E2E harness and the in-page product driver. */

export interface SemanticPeerHubFixture {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly recipientId: string;
  readonly senderConsentDigest: string;
  readonly recipientConsentDigest: string;
  readonly scopeDigest: string;
  readonly consentVersion: number;
  readonly expiresAtMs: number;
  readonly transport: 'hub_relay' | 'webrtc';
}

export interface SemanticPeerHubOffer {
  readonly offerId: string;
  readonly inventoryRootDigest: string;
  readonly groupId: string;
  readonly groupB64: string;
  readonly totalBytes: number;
  readonly expiresAtMs: number;
  readonly value: Readonly<Record<string, unknown>>;
  readonly signedMessage: SpeechEvidenceMessage;
  readonly record: SpeechEvidenceOfferRecord;
}

export interface SemanticPeerHubAcceptanceResult {
  readonly signedPreviewVisible: boolean;
  readonly comparisonPreviewVisible: boolean;
  readonly productAcceptPolicyUsed: boolean;
  readonly productUiProjectionVisible: boolean;
}

export interface SemanticPeerHubCurationResult {
  readonly receiptVerified: boolean;
  readonly state: string;
  readonly acceptedGroupCount: number;
  readonly curationTaskQueued: boolean;
  readonly datasetReserved: boolean;
}

export interface SemanticPeerHubTransferExerciseResult {
  readonly backendRouteCount: number;
  readonly forcedRelayUsed: boolean;
  readonly duplicateRejected: boolean;
  readonly reorderRecovered: boolean;
}

export interface SemanticVoiceObservationCursor {
  readonly hubUrl: string;
  readonly profileId: string;
  readonly streamId: string;
  readonly liveRunId: string;
  readonly partialLatencyMs: number;
  readonly partialObserved: boolean;
}

export interface SemanticVoiceObservationResult {
  readonly partialLatencyMs: number;
  readonly partialObservationCount: number;
  readonly segmentModeCount: number;
  readonly finalSegmentCount: number;
  readonly correctionAfterFinalCount: number;
  readonly acceleratedRotationCount: number;
  readonly reconnectCount: number;
  readonly stream404Count: number;
  readonly stop409Count: number;
  readonly chunk413Count: number;
  readonly backpressureCount: number;
  readonly ordinaryFallbackCount: number;
  readonly transcriptContinuityCount: number;
}

export interface SemanticMediaPairHubProductDriver {
  prepare(fixture: SemanticPeerHubFixture): Promise<void>;
  propose(fixture: SemanticPeerHubFixture, relayCanary: string): Promise<SemanticPeerHubOffer>;
  accept(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubAcceptanceResult>;
  transfer(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<void>;
  exerciseTransfer(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubTransferExerciseResult>;
  recover(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  revoke(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  acknowledge(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<void>;
  curate(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubCurationResult>;
  openDirect(fixture: SemanticPeerHubFixture, initiator: boolean): Promise<'webrtc'>;
  sendDirect(fixture: SemanticPeerHubFixture, directCanary: string): Promise<string>;
  receiveDirect(messageId: string): Promise<boolean>;
  closeDirect(): Promise<void>;
  startVoiceObservation(hubUrl: string): Promise<SemanticVoiceObservationCursor>;
  resumeVoiceObservation(cursor: SemanticVoiceObservationCursor): Promise<SemanticVoiceObservationResult>;
}

export interface SemanticMediaPairProductPorts {
  readonly syncApi: SpeechEvidenceSyncApiService;
  readonly curation: SpeechEvidenceHubCurationFacade;
  readonly transport: WebrtcTransportService;
  readonly signaling: WebrtcSignalingService;
  /**
   * The live driver may only reuse sessions that the product control plane
   * has explicitly bound. Optional keeps stale E2E bootstraps fail-closed with
   * a useful setup error instead of silently selecting Hub relay signaling.
   */
  readonly controlPlane?: Pick<PairSessionControlPlaneService, 'assertSessionAvailable'>;
  readonly voiceApi: VoiceApiService;
  readonly speechQuality: SemanticSpeechQualityControllerService;
  readonly renderOfferPreview: (offer: PeerEvidenceOfferView) => boolean;
}
