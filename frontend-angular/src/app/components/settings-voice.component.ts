import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  VoiceCandidateReviewComponent,
  VoiceLearningContext,
} from '../features/voice/voice-candidate-review.component';
import { VoiceConfigurationEditorComponent } from '../features/voice/voice-configuration-editor.component';
import { VoicePersonalizationComponent } from '../features/voice/voice-personalization.component';
import { VoiceRuntimeStatusComponent } from '../features/voice/voice-runtime-status.component';

@Component({
  selector: 'app-settings-voice',
  standalone: true,
  imports: [
    RouterLink,
    VoiceConfigurationEditorComponent,
    VoiceRuntimeStatusComponent,
    VoiceCandidateReviewComponent,
    VoicePersonalizationComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card card-info mb-md" data-testid="voice-settings-boundary">
      <h3>Voice-Ausführungsgrenze</h3>
      <p>
        Konfiguration, Transkription, Fusion, Restricted Inference, Review und Personalisierung laufen über den Hub.
        Die Android-Funktion <a routerLink="/voxtral-offline">Voxtral Offline</a> bleibt ein klar abgegrenzter
        Mobile-Local-Sonderpfad und ist keine zweite Quelle für die Hub-Konfiguration.
      </p>
    </section>
    <app-voice-configuration-editor [hubUrl]="hubUrl" />
    <app-voice-runtime-status [hubUrl]="hubUrl" />
    <app-voice-candidate-review [hubUrl]="hubUrl" (learningContextChange)="learningContext = $event" />
    <app-voice-personalization [hubUrl]="hubUrl" [learningContext]="learningContext" />
  `,
})
export class SettingsVoiceComponent {
  @Input({ required: true }) hubUrl = '';
  learningContext: VoiceLearningContext | null = null;
}
