import { AsyncPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnInit, inject } from '@angular/core';

import { SemanticComputeIntentFacade } from '../pair-view/semantic-compute-intent.facade';
import { provideSpeechEvidenceSync } from '../../services/speech-evidence-sync.providers';
import { SemanticReceiverPathService } from '../../services/semantic-receiver-path.service';
import { SemanticSfuPathCoordinatorService } from '../../services/semantic-sfu-path-coordinator.service';
import { SemanticSfuPairSignalingService } from '../../services/semantic-sfu-pair-signaling.service';
import { PeerCapabilityService } from '../../services/peer-capability.service';
import { PeerEvidenceSyncFacade } from './peer-evidence-sync.facade';
import { SemanticMediaProgramFacade } from './semantic-media-program.facade';
import {
  SemanticMediaProgramDisplayMode,
  SemanticMediaProgramShellComponent,
} from './semantic-media-program-shell.component';
import { SpeechEvidenceConsentFacade } from './speech-evidence-consent.facade';
import { SemanticSpeechRuntimeCoordinatorService } from '../../services/semantic-speech-runtime-coordinator.service';
import { SemanticSpeechCaptureProducerService } from '../../services/semantic-speech-capture-producer.service';
import { SemanticSpeechQualityControllerService } from '../../services/semantic-speech-quality-controller.service';
import { WebrtcMediaPublicationService } from '../../services/webrtc-media-publication.service';

@Component({
  selector: 'app-semantic-media-program-host',
  standalone: true,
  imports: [AsyncPipe, SemanticMediaProgramShellComponent],
  providers: [
    SemanticComputeIntentFacade,
    SemanticMediaProgramFacade,
    SemanticReceiverPathService,
    SemanticSfuPathCoordinatorService,
    SemanticSfuPairSignalingService,
    SpeechEvidenceConsentFacade,
    PeerCapabilityService,
    PeerEvidenceSyncFacade,
    SemanticSpeechCaptureProducerService,
    SemanticSpeechRuntimeCoordinatorService,
    SemanticSpeechQualityControllerService,
    WebrtcMediaPublicationService,
    ...provideSpeechEvidenceSync(),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (facade.view$ | async; as view) {
      <app-semantic-media-program-shell
        [displayMode]="displayMode"
        [scope]="view.scope"
        [capabilities]="view.capabilities"
        [online]="view.online"
        [hubUrl]="view.hubUrl"
        [ordinaryMediaAuthority]="view.ordinaryMediaAuthority"
        [ordinaryMediaActivationEnabled]="view.ordinaryMediaActivationEnabled"
        [computeVisible]="view.computeVisible"
        [computeContract]="view.compute.contract"
        [computeLocalMeasurement]="view.compute.localMeasurement"
        [computePeerClaim]="view.compute.peerClaim"
        [computeLeases]="view.compute.leases"
        [computeExplanation]="view.compute.explanation"
        [computeSuggestion]="view.compute.suggestion"
        [computePending]="view.compute.pending"
        [computeErrorCode]="view.compute.errorCode"
        [receiverPaths]="view.receiverPaths"
        [ordinaryMediaCaptureEnabled]="view.ordinaryMediaCaptureEnabled"
        [ordinaryMediaVideoCaptureEnabled]="view.ordinaryMediaVideoCaptureEnabled"
        [ordinaryMediaReason]="view.ordinaryMediaReason"
        [ordinaryAudioState]="view.ordinaryAudioState"
        [ordinaryMediaPublications]="view.ordinaryMediaPublications"
        [sfuRemoteVideos]="view.sfuRemoteVideos"
        [speechTransportState]="view.speechTransportState"
        [speechTransportReason]="view.speechTransportReason"
        [speechTransportCanStart]="view.speechTransportCanStart"
        [speechSettings]="view.speechSettings"
        [speechQualityMode]="view.speechQuality.mode"
        [speechQualityReason]="view.speechQuality.reasonCode"
        [speechReconciliationHubAuthorized]="view.speechReconciliationHubAuthorized"
        [speechAdapters]="view.speechAdapters"
        [evidenceOffer]="view.evidenceOffer"
        [evidenceSync]="view.evidenceSync"
        [evidenceAvailableReason]="view.evidenceAvailableReason"
        [evidenceConsentState]="view.evidenceConsent"
        (intent)="facade.handleProgramIntent($event)"
        (computeIntent)="facade.handleComputeIntent($event)"
        (computeSuggestionRequest)="facade.requestComputeSuggestion()"
        (receiverPathIntent)="facade.handleReceiverPathIntent($event)"
        (ordinaryMicrophoneStart)="facade.startOrdinaryMicrophone()"
        (ordinaryMicrophoneStop)="facade.stopOrdinaryMicrophone()"
        (ordinaryMicrophoneMute)="facade.setOrdinaryMicrophoneMuted($event)"
        (ordinaryCameraStart)="facade.startOrdinaryVideo('camera')"
        (ordinaryScreenStart)="facade.startOrdinaryVideo('screen')"
        (ordinaryVideoStop)="facade.stopOrdinaryVideo($event)"
        (ordinaryVideoReplace)="facade.replaceOrdinaryVideo($event)"
        (ordinaryVideoMute)="facade.setOrdinaryVideoMuted($event)"
        (evidenceConsentIntent)="facade.handleEvidenceConsentIntent($event)"
        (speechStart)="facade.startSpeech()"
        (speechStop)="facade.stopSpeech()"
        (speechSettingsChange)="facade.handleSpeechSettings($event)"
        (evidencePropose)="facade.handleEvidencePropose($event)"
        (evidenceAccept)="facade.handleEvidenceAccept($event)"
        (evidencePause)="facade.pauseEvidence()"
        (evidenceResume)="facade.resumeEvidence()"
        (evidenceReject)="facade.rejectEvidence()"
        (evidenceRevoke)="facade.revokeEvidence()"
        (evidenceCuration)="facade.requestEvidenceCuration()"
        (evidenceLocalOverride)="facade.handleEvidenceLocalOverride($event)" />
    }
  `,
  styles: [`:host { display: block; margin-block-start: 1.5rem; }`],
})
export class SemanticMediaProgramHostComponent implements OnInit {
  readonly facade = inject(SemanticMediaProgramFacade);
  @Input() displayMode: SemanticMediaProgramDisplayMode = 'full';

  ngOnInit(): void { void this.facade.start(); }
}
