import { SpeechEvidenceConsentReadModel } from '../../services/speech-evidence-consent-api.service';
import {
  SpeechEvidenceCandidateProjection,
  SpeechEvidenceGroupPreview,
} from '../../services/speech-evidence-sync.validators';
import { PeerEvidenceAcceptanceOffer } from './peer-evidence-acceptance';
import {
  PeerEvidenceLocalGroupView,
  PeerEvidenceOfferView,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';

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

/** Content-free comparison projection of a transcript turn, shared by sender and recipient. */
export interface PeerEvidenceComparisonProjection {
  readonly originalCandidates: readonly SpeechEvidenceCandidateProjection[];
  readonly resolutionState: 'resolved' | 'unresolved';
  readonly selectedCandidateDigest: string | null;
  readonly unresolvedRegionDigests: readonly string[];
  readonly comparisonDigest: string;
}

export interface LocalEvidenceArtifact extends PeerEvidenceComparisonProjection {
  readonly view: PeerEvidenceLocalGroupView;
  readonly bytes: Uint8Array;
  readonly contentDigest: string;
  readonly sourceGroupDigest: string;
}

export interface ActiveOffer extends PeerEvidenceAcceptanceOffer {
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

export interface PendingRevocation {
  readonly revocationId: string;
  readonly offerId: string;
  readonly groupIds: readonly string[];
  readonly scopeDigest: string;
  readonly revocationEpoch: number;
  readonly deadlineAtMs: number;
  attempts: number;
  resolved: boolean;
}

export type PeerEvidenceGroupCandidate = Readonly<{ revision: number; authority: string; text: string }>;

export interface PeerEvidenceGroupPayload {
  readonly turnId: string;
  readonly revision: number;
  readonly state: 'final' | 'corrected' | 'correction_failed';
  readonly sourceDigest: string;
  readonly candidates: readonly PeerEvidenceGroupCandidate[];
}

export const MAX_REVOCATION_ATTEMPTS = 5;
export const REVOCATION_RETRY_MS = 2_000;
export const STATUS_POLL_MS = 1_500;
export const PEER_CURATION_REQUEST_POLICY_DIGEST = 'bd02f0ea6843e13b4be73b3742f1d196a054522ffa4377ce0ddc339d39c46c19';
