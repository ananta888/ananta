import { installSemanticMediaGroupLiveDriver } from './app/e2e/semantic-media-group-live-driver';
import { installSemanticMediaPairLiveDriver } from './app/e2e/semantic-media-pair-live-driver';
import { createPeerEvidencePreviewObserver } from './app/e2e/semantic-media-pair-ui-observer';
import { installSemanticVisualLifecycleLiveDriver } from './app/e2e/semantic-visual-lifecycle-live-driver';
import { AgentDirectoryService } from './app/services/agent-directory.service';
import { SpeechEvidenceHubCurationFacade } from './app/services/speech-evidence-hub-curation.facade';
import { SpeechEvidenceSyncApiService } from './app/services/speech-evidence-sync-api.service';
import { E2eEncryptionService } from './app/services/e2e-encryption.service';
import { SemanticSfuAdmissionApiService } from './app/services/semantic-sfu-admission-api.service';
import { SemanticSfuGroupKeyService } from './app/services/semantic-sfu-group-key.service';
import { UserAuthService } from './app/services/user-auth.service';
import { WebrtcGroupKeyService } from './app/services/webrtc-group-key.service';
import { WebrtcTransportService } from './app/services/webrtc-transport.service';
import { WebrtcSignalingService } from './app/services/webrtc-signaling.service';
import { PairSessionControlPlaneService } from './app/services/pair-session-control-plane.service';
import { SemanticSpeechQualityControllerService } from './app/services/semantic-speech-quality-controller.service';
import { VoiceApiService } from './app/features/voice/voice-api.service';
import { bootstrapAnantaApplication } from './bootstrap-ananta-application';

bootstrapAnantaApplication()
  .then(application => {
    installSemanticMediaPairLiveDriver({
      syncApi: application.injector.get(SpeechEvidenceSyncApiService),
      curation: application.injector.get(SpeechEvidenceHubCurationFacade),
      transport: application.injector.get(WebrtcTransportService),
      signaling: application.injector.get(WebrtcSignalingService),
      controlPlane: application.injector.get(PairSessionControlPlaneService),
      voiceApi: application.injector.get(VoiceApiService),
      speechQuality: application.injector.get(SemanticSpeechQualityControllerService),
      renderOfferPreview: createPeerEvidencePreviewObserver(application, application.injector),
    });
    installSemanticMediaGroupLiveDriver({
      admission: application.injector.get(SemanticSfuAdmissionApiService),
      agentDirectory: application.injector.get(AgentDirectoryService),
      authentication: application.injector.get(UserAuthService),
      deviceEncryption: application.injector.get(E2eEncryptionService),
      groupKeys: application.injector.get(SemanticSfuGroupKeyService),
      keyRegistry: application.injector.get(WebrtcGroupKeyService),
    });
    installSemanticVisualLifecycleLiveDriver();
  })
  .catch(error => console.error(error));
