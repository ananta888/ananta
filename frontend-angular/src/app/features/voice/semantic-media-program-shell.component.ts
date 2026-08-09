import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import {
  CapabilityView,
  ComputeContractIntent,
  ComputeContractView,
  ComputeExplanationView,
  ComputeLeaseView,
  ComputeSuggestionView,
  PairComputeContractPanelComponent,
} from '../pair-view/pair-compute-contract-panel.component';
import {
  SemanticReceiverPathIntent,
  SemanticReceiverPathPanelComponent,
} from '../pair-view/semantic-receiver-path-panel.component';
import { SemanticReceiverPathView } from '../../services/semantic-receiver-path.service';
import {
  PeerEvidenceOfferView,
  PeerEvidenceSyncPanelComponent,
  PeerEvidenceProposalIntent,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';
import {
  SemanticSpeechPanelComponent,
  SemanticSpeechPanelSettings,
  SemanticSpeechTransportState,
} from './semantic-speech-panel.component';
import { SpeechReconciliationPanelComponent } from './speech-reconciliation-panel.component';
import { SpeechEvidenceConsentPanelComponent } from './speech-evidence-consent-panel.component';
import { SemanticRemoteAudioComponent } from './semantic-remote-audio.component';
import { SemanticRemoteVideoComponent } from './semantic-remote-video.component';
import type { SfuRemoteVideoView } from '../../services/sfu-broadcast-video-render.facade';
import {
  SpeechEvidenceConsentIntent,
  SpeechEvidenceConsentPanelState,
} from './speech-evidence-consent.facade';
import { SemanticDebugHostComponent } from '../pair-view/semantic-debug-host.component';
import { DEFAULT_SEMANTIC_SPEECH_SETTINGS } from '../../services/semantic-speech-settings';
import { SemanticSpeechQualityMode } from '../../services/semantic-speech-quality-controller.service';
import { OrdinaryAudioState } from '../../services/webrtc-media-session.service';
import { MediaPublicationView } from '../../services/webrtc-media-publication.service';
import { WebrtcMediaPanelComponent } from '../pair-view/webrtc-media-panel.component';

export type SemanticProgramState =
  | 'locally_desired'
  | 'sent_to_hub'
  | 'sent_to_authority'
  | 'authoritatively_active'
  | 'pausing'
  | 'revoked'
  | 'expired'
  | 'degraded'
  | 'failed';

export type SemanticMediaProgramDisplayMode = 'full' | 'pair_media';
export type OrdinaryMediaAuthorityKind = 'hub' | 'public' | 'unbound';

export type SemanticProgramCapability =
  | 'ordinary_media'
  | 'semantic_video'
  | 'live_speech'
  | 'evidence_text'
  | 'raw_audio'
  | 'training'
  | 'speech_reconciliation'
  | 'adapter_activation'
  | 'export';

export interface SemanticProgramScopeView {
  direction: string;
  dataClass: string;
  purpose: string;
  retentionLabel: string;
  trainerLocation: string;
  e2eeMode: string;
  ordinaryFallback: string;
  grantLabel?: string;
}

export interface SemanticProgramCapabilityView {
  readonly capability: SemanticProgramCapability;
  readonly label: string;
  readonly sensitive: boolean;
  readonly state: SemanticProgramState;
  readonly requestId: string | null;
  readonly reasonCode?: string | null;
  readonly scope?: SemanticProgramScopeView;
}

export interface SpeechAdapterActivationOption {
  readonly adapterId: string;
  readonly direction: 'sender_to_receiver' | 'receiver_to_sender';
  readonly label: string;
  readonly expiresAtMs: number;
}

interface SemanticProgramCapabilityProjection {
  capability: SemanticProgramCapability;
  label: string;
  sensitive: boolean;
  state: SemanticProgramState;
  requestId: string | null;
  reasonCode?: string | null;
}

export interface SemanticProgramIntent {
  capability: SemanticProgramCapability;
  requestId: string;
  desired: 'activate' | 'pause' | 'revoke';
  adapterId?: string;
  direction?: 'sender_to_receiver' | 'receiver_to_sender';
}

const SENSITIVE = new Set<SemanticProgramCapability>([
  'raw_audio', 'training', 'speech_reconciliation', 'adapter_activation', 'export',
]);

@Component({
  selector: 'app-semantic-media-program-shell',
  standalone: true,
  imports: [
    PairComputeContractPanelComponent,
    SemanticReceiverPathPanelComponent,
    PeerEvidenceSyncPanelComponent,
    SemanticSpeechPanelComponent,
    SpeechEvidenceConsentPanelComponent,
    SemanticRemoteAudioComponent,
    SemanticRemoteVideoComponent,
    SpeechReconciliationPanelComponent,
    SemanticDebugHostComponent,
    WebrtcMediaPanelComponent,
  ],
  templateUrl: './semantic-media-program-shell.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    :host { display: block; container-type: inline-size; }
    .program-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 19rem), 1fr)); }
    article, dl { border: 1px solid currentColor; border-radius: .5rem; padding: .75rem; }
    dl { display: grid; grid-template-columns: minmax(7rem, auto) 1fr; gap: .35rem .75rem; }
    dt { font-weight: 600; } dd { margin: 0; overflow-wrap: anywhere; }
    button { min-height: 2.75rem; }
    button:focus-visible { outline: 3px solid currentColor; outline-offset: 2px; }
    .sensitive { border-width: 2px; }
    @container (max-width: 30rem) { dl { grid-template-columns: 1fr; } }
  `],
})
export class SemanticMediaProgramShellComponent {
  @Input() displayMode: SemanticMediaProgramDisplayMode = 'full';
  @Input({ required: true }) scope!: SemanticProgramScopeView;
  @Input() online = true;
  @Input() hubUrl = '';
  @Input() ordinaryMediaAuthority: OrdinaryMediaAuthorityKind = 'unbound';
  @Input() ordinaryMediaActivationEnabled = false;
  @Input() computeVisible = false;
  @Input() computeContract: ComputeContractView = {
    contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {},
  };
  @Input() computeLocalMeasurement?: CapabilityView;
  @Input() computePeerClaim?: CapabilityView;
  @Input() computeLeases: readonly ComputeLeaseView[] = [];
  @Input() computeExplanation?: ComputeExplanationView;
  @Input() computeSuggestion?: ComputeSuggestionView;
  @Input() computePending = false;
  @Input() computeErrorCode: string | null = null;
  @Input() receiverPaths: readonly SemanticReceiverPathView[] = [];
  @Input() ordinaryMediaCaptureEnabled = false;
  @Input() ordinaryMediaVideoCaptureEnabled = false;
  @Input() ordinaryMediaReason = 'ordinary_media_activation_required';
  @Input() ordinaryAudioState: OrdinaryAudioState = {
    status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null,
  };
  @Input() ordinaryMediaPublications: readonly MediaPublicationView[] = [];
  private sfuVideoRows: readonly SfuRemoteVideoView[] = Object.freeze([]);
  @Input()
  set sfuRemoteVideos(value: readonly SfuRemoteVideoView[] | null | undefined) {
    this.sfuVideoRows = Object.freeze([...(value ?? [])]);
  }
  get sfuRemoteVideos(): readonly SfuRemoteVideoView[] { return this.sfuVideoRows; }
  @Input() speechTransportState: SemanticSpeechTransportState = 'stopped';
  @Input() speechTransportReason = 'semantic_speech_not_started';
  @Input() speechTransportCanStart = false;
  @Input() speechSettings: SemanticSpeechPanelSettings = DEFAULT_SEMANTIC_SPEECH_SETTINGS;
  @Input() speechQualityMode: SemanticSpeechQualityMode = 'ordinary_audio';
  @Input() speechQualityReason = 'quality_initial';
  @Input() speechReconciliationHubAuthorized = false;
  private adapterRows: readonly SpeechAdapterActivationOption[] = Object.freeze([]);
  selectedAdapterKey = '';
  @Input()
  set speechAdapters(value: readonly SpeechAdapterActivationOption[]) {
    this.adapterRows = Object.freeze([...(value ?? [])]);
    if (!this.adapterRows.some(row => this.adapterKey(row) === this.selectedAdapterKey)) {
      this.selectedAdapterKey = '';
    }
  }
  get speechAdapters(): readonly SpeechAdapterActivationOption[] { return this.adapterRows; }
  @Input() evidenceOffer: PeerEvidenceOfferView | null = null;
  @Input() evidenceSync: PeerEvidenceSyncView | null = null;
  @Input() evidenceAvailableReason = 'Noch kein Hub-autorisierter Evidence-Offer vorhanden.';
  private consentState: SpeechEvidenceConsentPanelState = {
    bound: false, signerIds: [], consent: null, pending: false,
    errorCode: 'speech_consent_context_missing',
  };
  @Input()
  set evidenceConsentState(value: SpeechEvidenceConsentPanelState | null | undefined) {
    this.consentState = value ?? {
      bound: false, signerIds: [], consent: null, pending: false,
      errorCode: 'speech_consent_context_missing',
    };
  }
  get evidenceConsentState(): SpeechEvidenceConsentPanelState { return this.consentState; }
  @Output() readonly intent = new EventEmitter<SemanticProgramIntent>();
  @Output() readonly computeIntent = new EventEmitter<ComputeContractIntent>();
  @Output() readonly computeSuggestionRequest = new EventEmitter<void>();
  @Output() readonly receiverPathIntent = new EventEmitter<SemanticReceiverPathIntent>();
  @Output() readonly ordinaryMicrophoneStart = new EventEmitter<void>();
  @Output() readonly ordinaryMicrophoneStop = new EventEmitter<void>();
  @Output() readonly ordinaryMicrophoneMute = new EventEmitter<boolean>();
  @Output() readonly ordinaryCameraStart = new EventEmitter<void>();
  @Output() readonly ordinaryScreenStart = new EventEmitter<void>();
  @Output() readonly ordinaryVideoStop = new EventEmitter<string>();
  @Output() readonly ordinaryVideoReplace = new EventEmitter<string>();
  @Output() readonly ordinaryVideoMute = new EventEmitter<Readonly<{ publicationId: string; muted: boolean }>>();
  @Output() readonly speechStart = new EventEmitter<void>();
  @Output() readonly speechStop = new EventEmitter<void>();
  @Output() readonly speechSettingsChange = new EventEmitter<SemanticSpeechPanelSettings>();
  @Output() readonly evidenceAccept = new EventEmitter<readonly string[]>();
  @Output() readonly evidencePropose = new EventEmitter<PeerEvidenceProposalIntent>();
  @Output() readonly evidencePause = new EventEmitter<void>();
  @Output() readonly evidenceResume = new EventEmitter<void>();
  @Output() readonly evidenceReject = new EventEmitter<void>();
  @Output() readonly evidenceRevoke = new EventEmitter<void>();
  @Output() readonly evidenceCuration = new EventEmitter<void>();
  @Output() readonly evidenceLocalOverride = new EventEmitter<{ regionId: string; candidateId: string }>();
  @Output() readonly evidenceConsentIntent = new EventEmitter<SpeechEvidenceConsentIntent>();

  private capabilityRows: SemanticProgramCapabilityProjection[] = [];
  private serial = 0;

  @Input()
  set capabilities(value: readonly SemanticProgramCapabilityView[]) {
    this.capabilityRows = (value ?? []).map(row => ({ ...row }));
  }

  get capabilities(): readonly SemanticProgramCapabilityView[] {
    return this.capabilityRows;
  }

  displayedCapabilities(): readonly SemanticProgramCapabilityView[] {
    return this.displayMode === 'pair_media'
      ? this.capabilityRows.filter(row => row.capability === 'ordinary_media')
      : this.capabilityRows;
  }

  heading(): string {
    return this.displayMode === 'pair_media' ? 'Audio und Video für Pair Dev' : 'Semantic Media und Speech';
  }

  request(capability: SemanticProgramCapability, desired: SemanticProgramIntent['desired']): void {
    const row = this.capabilityRows.find(item => item.capability === capability);
    if (!row || row.requestId || (!this.activationAvailable(row) && desired === 'activate')) return;
    const requestId = `semantic-program-request-${++this.serial}`;
    row.state = desired === 'activate' ? 'locally_desired' : 'pausing';
    row.requestId = requestId;
    if (this.activationAvailable(row)) {
      row.state = capability === 'ordinary_media' && this.ordinaryMediaAuthority === 'public'
        ? 'sent_to_authority'
        : 'sent_to_hub';
    }
    else row.state = 'failed';
    const selected = capability === 'adapter_activation'
      ? this.adapterRows.find(value => this.adapterKey(value) === this.selectedAdapterKey)
      : undefined;
    if (capability === 'adapter_activation' && desired === 'activate' && !selected) {
      row.state = 'failed';
      row.requestId = null;
      row.reasonCode = 'speech_adapter_explicit_selection_required';
      return;
    }
    this.intent.emit({
      capability,
      requestId,
      desired,
      ...(selected ? { adapterId: selected.adapterId, direction: selected.direction } : {}),
    });
  }

  applyHubState(
    capability: SemanticProgramCapability,
    requestId: string,
    state: Exclude<SemanticProgramState, 'locally_desired' | 'sent_to_hub' | 'sent_to_authority'>,
  ): boolean {
    const row = this.capabilityRows.find(item => item.capability === capability);
    if (!row || row.requestId !== requestId) return false;
    row.state = state;
    row.requestId = null;
    return true;
  }

  isSensitive(capability: SemanticProgramCapability): boolean {
    return SENSITIVE.has(capability);
  }

  pending(row: SemanticProgramCapabilityView): boolean {
    return row.requestId !== null;
  }

  activationAvailable(row: Pick<SemanticProgramCapabilityView, 'capability'>): boolean {
    return row.capability === 'ordinary_media' ? this.ordinaryMediaActivationEnabled : this.online;
  }

  adapterActivationUnavailable(capability: SemanticProgramCapability): boolean {
    return capability === 'adapter_activation' && !this.selectedAdapterKey;
  }

  selectAdapter(event: Event): void {
    this.selectedAdapterKey = String((event.target as HTMLSelectElement | null)?.value ?? '');
  }

  adapterKey(value: Pick<SpeechAdapterActivationOption, 'adapterId' | 'direction'>): string {
    return `${value.direction}\u001f${value.adapterId}`;
  }

  scopeForRow(row: SemanticProgramCapabilityView): SemanticProgramScopeView {
    return row.scope ?? this.scope;
  }

  speechRuntimeVisible(): boolean {
    return this.capabilityRows.some(row =>
      row.capability === 'live_speech'
      && (row.state === 'authoritatively_active' || row.state === 'degraded')
    );
  }

  ordinaryMediaVisible(): boolean {
    return this.capabilityRows.some(row => row.capability === 'ordinary_media'
      && (row.state === 'authoritatively_active' || row.state === 'degraded'));
  }

  speechReconciliationVisible(): boolean {
    return this.capabilityRows.some(row =>
      row.capability === 'speech_reconciliation'
      && (row.state === 'authoritatively_active' || row.state === 'degraded')
    );
  }

  peerEvidenceVisible(): boolean {
    return this.capabilityRows.some(row =>
      row.capability === 'evidence_text'
      && (row.state === 'authoritatively_active' || row.state === 'degraded')
    );
  }
}
