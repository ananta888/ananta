import type { SfuRemoteVideoView } from '../../services/sfu-broadcast-video-render.facade';
import { SemanticReceiverPathView } from '../../services/semantic-receiver-path.service';
import { SemanticSpeechQualityState } from '../../services/semantic-speech-quality-controller.service';
import { ShareSession } from '../../services/share-session.service';
import { SpeechEvidenceConsentDocument } from '../../services/speech-evidence-consent-api.service';
import { OrdinaryAudioState } from '../../services/webrtc-media-session.service';
import {
  MediaPublicationView,
  UserMediaPreference,
} from '../../services/webrtc-media-publication.service';
import type { PublicPairMediaPublicationConsentState } from '../../services/public-pair-media-publication-consent.service';
import { SemanticComputePanelState } from '../pair-view/semantic-compute-intent.facade';
import { PeerEvidenceOfferView, PeerEvidenceSyncView } from './peer-evidence-sync-panel.component';
import { SpeechEvidenceConsentPanelState } from './speech-evidence-consent.facade';
import {
  OrdinaryMediaAuthorityKind,
  SemanticProgramCapability,
  SemanticProgramCapabilityView,
  SemanticProgramScopeView,
  SemanticProgramState,
  SpeechAdapterActivationOption,
} from './semantic-media-program-shell.component';
import { SpeechAdapterMetadata } from './reconstruction/personalized-speech-reconstructor.service';
import { SemanticSpeechPanelSettings, SemanticSpeechTransportState } from './semantic-speech-panel.component';

/** View model, constants and pure view helpers of the semantic media program host. */

export interface SemanticMediaProgramHostView {
  readonly scope: SemanticProgramScopeView;
  readonly capabilities: readonly SemanticProgramCapabilityView[];
  readonly online: boolean;
  readonly hubUrl: string;
  readonly ordinaryMediaAuthority: OrdinaryMediaAuthorityKind;
  readonly ordinaryMediaActivationEnabled: boolean;
  readonly computeVisible: boolean;
  readonly compute: SemanticComputePanelState;
  readonly receiverPaths: readonly SemanticReceiverPathView[];
  readonly ordinaryMediaCaptureEnabled: boolean;
  readonly ordinaryMediaVideoCaptureEnabled: boolean;
  readonly ordinaryMediaE2eeReady: boolean;
  readonly ordinaryMediaReason: string;
  readonly ordinaryMediaPublicationConsent: PublicPairMediaPublicationConsentState;
  readonly ordinaryMediaPublicationPreparationPending: boolean;
  readonly ordinaryAudioState: OrdinaryAudioState;
  readonly ordinaryMediaPublications: readonly MediaPublicationView[];
  readonly sfuRemoteVideos: readonly SfuRemoteVideoView[];
  readonly speechTransportState: SemanticSpeechTransportState;
  readonly speechTransportReason: string;
  readonly speechTransportCanStart: boolean;
  readonly speechSettings: SemanticSpeechPanelSettings;
  readonly speechQuality: SemanticSpeechQualityState;
  readonly speechReconciliationHubAuthorized: boolean;
  readonly speechAdapters: readonly SpeechAdapterActivationOption[];
  readonly evidenceOffer: PeerEvidenceOfferView | null;
  readonly evidenceSync: PeerEvidenceSyncView | null;
  readonly evidenceAvailableReason: string;
  readonly evidenceConsent: SpeechEvidenceConsentPanelState;
}

export const LABELS: Readonly<Record<SemanticProgramCapability, string>> = Object.freeze({
  ordinary_media: 'Ordinary Audio/Video',
  semantic_video: 'Semantisches Video',
  live_speech: 'Semantische Live-Sprache',
  evidence_text: 'Peer-Text-Evidence',
  raw_audio: 'Peer-Roh-Audio',
  training: 'Speech-Training',
  speech_reconciliation: 'Offline-Sprachabstimmung',
  adapter_activation: 'Speech-Adapter aktivieren',
  export: 'Speech-Daten exportieren',
});

export const SENSITIVE = new Set<SemanticProgramCapability>([
  'raw_audio', 'training', 'speech_reconciliation', 'adapter_activation', 'export',
]);

export const EMPTY_COMPUTE: SemanticComputePanelState = Object.freeze({
  contract: Object.freeze({ contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {} }),
  leases: Object.freeze([]), pending: false, errorCode: 'compute_session_missing',
});

export const EMPTY_CONSENT: SpeechEvidenceConsentPanelState = Object.freeze({
  bound: false, signerIds: Object.freeze([]), consent: null, pending: false,
  errorCode: 'speech_consent_context_missing',
});

export interface BoundSemanticMediaAuthorityRoute {
  readonly sessionId: string;
  readonly kind: OrdinaryMediaAuthorityKind;
  readonly baseUrl: string;
}

export interface SpeechActivationFence {
  readonly sessionId: string;
  readonly securityEpoch: number;
  readonly authorityBaseUrl: string;
  readonly contextGeneration: number;
  readonly activationGeneration: number;
}

export type SetSemanticCapability = (
  capability: SemanticProgramCapability,
  state: SemanticProgramState,
  requestId: string | null,
  reasonCode?: string | null,
) => void;

export function capabilityView(
  capability: SemanticProgramCapability,
  state: SemanticProgramState,
  requestId: string | null,
  reasonCode: string | null,
): SemanticProgramCapabilityView {
  return Object.freeze({
    capability,
    label: LABELS[capability],
    sensitive: SENSITIVE.has(capability),
    state,
    requestId,
    ...(reasonCode ? { reasonCode } : {}),
  });
}

export function speechAdapterOptions(
  rows: readonly SpeechAdapterMetadata[],
): readonly SpeechAdapterActivationOption[] {
  return Object.freeze(rows.map(row => Object.freeze({
    adapterId: row.adapter_id,
    direction: row.direction,
    label: `${row.adapter_id} · ${row.base_model_id}`,
    expiresAtMs: Math.min(row.expires_at_ms, row.consent_expires_at_ms),
  })));
}

export function sessionUsable(session: ShareSession | null): session is ShareSession {
  return Boolean(
    session
    && session.revoked_at === null
    && (session.expires_at ?? Number.MAX_SAFE_INTEGER) * 1_000 > Date.now(),
  );
}

export function scopeFor(session: ShareSession | null): SemanticProgramScopeView {
  return Object.freeze({
    direction: 'bidirectional',
    dataClass: 'Transcript, semantische Features und separat freigegebene Evidence',
    purpose: 'Live-Kommunikation und explizit freigegebene Sprachverbesserung',
    retentionLabel: 'Vertragsspezifisch; keine implizite Langzeitspeicherung',
    trainerLocation: 'Isolierter lokaler Worker',
    e2eeMode: session?.security_mode || 'strict_e2ee',
    ordinaryFallback: 'Verschlüsseltes Ordinary Media bleibt sicherer Standard',
  });
}

export function scopeForConsent(
  consent: SpeechEvidenceConsentDocument,
  session: ShareSession | null,
): SemanticProgramScopeView {
  const trainingEnabled = consent.grants.training === true;
  const granted = [
    consent.grants.raw_audio_share ? 'Roh-Audio' : null,
    consent.grants.dataset_import ? 'Dataset-Import' : null,
    trainingEnabled ? 'Training' : 'Training nicht freigegeben',
  ].filter((value): value is string => Boolean(value));
  return Object.freeze({
    direction: consent.direction,
    dataClass: consent.data_classes.join(', ') || 'Keine Datenklasse',
    purpose: consent.purpose,
    retentionLabel: formatRetention(consent.retention_seconds),
    trainerLocation: trainingEnabled
      ? consent.trainer_locations.join(', ') || 'Kein Trainerstandort gebunden'
      : 'Kein Training freigegeben',
    e2eeMode: session?.security_mode || 'strict_e2ee',
    ordinaryFallback: 'Verschlüsseltes Ordinary Media bleibt sicherer Standard',
    grantLabel: granted.join(', '),
  });
}

export function formatRetention(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} Tag(e)`;
  if (seconds % 3_600 === 0) return `${seconds / 3_600} Stunde(n)`;
  if (seconds % 60 === 0) return `${seconds / 60} Minute(n)`;
  return `${seconds} Sekunde(n)`;
}

export function mediaPreference(source: 'camera' | 'screen'): UserMediaPreference {
  return source === 'camera'
    ? Object.freeze({ maxWidth: 1280, maxHeight: 720, maxFramesPerSecond: 30, maxBitrateBps: 1_500_000 })
    : Object.freeze({ maxWidth: 1920, maxHeight: 1080, maxFramesPerSecond: 20, maxBitrateBps: 3_000_000 });
}

export function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message) ? error.message : fallback;
}
