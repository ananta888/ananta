import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FormFieldComponent } from '../../../shared/ui/forms/form-field.component';
import { PersonaProfileFacade } from './persona-profile.facade';

@Component({
  selector: 'app-persona-voice-picker', standalone: true, imports: [FormsModule, FormFieldComponent],
  template: `
    <div class="picker">
      <button type="button" (click)="facade.listVoices()" [disabled]="facade.busy()">Zugelassene Stimmprofile auflisten</button>
      @if (facade.voicesLoaded()) {
        @if (facade.voiceOptions().length) {
          <app-form-field label="Für deine Metadatenvorschau freigegebene Stimmen">
            <select [ngModel]="facade.voiceId()" (ngModelChange)="facade.chooseListedVoice($event)" [disabled]="facade.busy()">
              <option value="">Stimmprofil auswählen …</option>
              @for (voice of facade.voiceOptions(); track voice.artifact_id) {
                <option [value]="voice.artifact_id">{{ voice.classification }} · {{ voice.artifact_id }} · v{{ voice.revision }}</option>
              }
            </select>
          </app-form-field>
        } @else { <small>In diesem Ausschnitt sind keine Stimmprofile für deine Vorschau freigegeben.</small> }
        @if (facade.voiceCursor()) { <button type="button" (click)="facade.listVoices(true)" [disabled]="facade.busy()">Nächsten Stimm-Ausschnitt laden</button> }
      }
      <app-form-field label="Zugelassene Stimm-ID" hint="Eine bereits über die Hub-Voice-API zugelassene Referenz, kein Modellpfad, Link oder Klon-Auftrag.">
        <input [ngModel]="facade.voiceId()" (ngModelChange)="facade.changeVoiceId($event)" maxlength="160" [disabled]="facade.busy()" autocomplete="off" />
      </app-form-field>
      <button type="button" (click)="facade.inspectVoice()" [disabled]="facade.busy() || !facade.voiceId().trim()">Stimmreferenz beim Hub prüfen</button>
      <small>Nur Metadaten: keine Audiowiedergabe, kein Modelldownload und keine Veröffentlichungsfreigabe.</small>
      @if (facade.voice(); as voice) { <p>{{ voice.classification }} · Stimmrevision {{ voice.revision }}</p> }
    </div>
  `,
  styles: [` .picker { display: grid; gap: .5rem; } p { margin: 0; } `],
})
export class PersonaVoicePickerComponent {
  readonly facade = inject(PersonaProfileFacade);
}
