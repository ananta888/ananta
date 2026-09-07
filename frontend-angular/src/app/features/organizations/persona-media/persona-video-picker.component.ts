import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FormFieldComponent } from '../../../shared/ui/forms/form-field.component';
import { PersonaProfileFacade } from './persona-profile.facade';

@Component({
  selector: 'app-persona-video-picker',
  standalone: true,
  imports: [FormsModule, FormFieldComponent],
  template: `
    <div class="picker">
      <button type="button" (click)="facade.listVideos()" [disabled]="facade.busy()">Zugelassene Clips auflisten</button>
      @if (facade.videosLoaded()) {
        @if (facade.videoOptions().length) {
          <app-form-field label="Für deine Vorschau freigegebene Clips">
            <select [ngModel]="facade.videoId()" (ngModelChange)="facade.chooseListedVideo($event)" [disabled]="facade.busy()">
              <option value="">Clip auswählen …</option>
              @for (video of facade.videoOptions(); track video.artifact_id) {
                <option [value]="video.artifact_id">{{ video.classification }} · {{ video.artifact_id }} · v{{ video.revision }}</option>
              }
            </select>
          </app-form-field>
        } @else { <small>In diesem Ausschnitt sind keine Clips für deine Vorschau freigegeben.</small> }
        @if (facade.videoCursor()) { <button type="button" (click)="facade.listVideos(true)" [disabled]="facade.busy()">Nächsten Clip-Ausschnitt laden</button> }
      }
      <app-form-field label="Zugelassene Video-ID" hint="Ein bereits über die Hub-Video-API zugelassener Clip, kein Dateipfad oder externer Link.">
        <input [ngModel]="facade.videoId()" (ngModelChange)="facade.changeVideoId($event)" maxlength="160" [disabled]="facade.busy()" autocomplete="off" />
      </app-form-field>
      <button type="button" (click)="facade.inspectVideo()" [disabled]="facade.busy() || !facade.videoId().trim()">Clip prüfen & Vorschauframe laden</button>
      <small>Die private PNG-Vorschau erteilt keine Veröffentlichungsrechte. Der Clip wird hier nicht abgespielt oder automatisch veröffentlicht.</small>
      @if (facade.video(); as video) { <p>{{ video.classification }} · Videorevision {{ video.revision }}</p> }
    </div>
  `,
  styles: [`
    .picker { display: grid; gap: .5rem; } small { color: #a9b9d4; } p { margin: 0; }
    input, select { min-width: 0; width: 100%; box-sizing: border-box; background: #101b2e; border: 1px solid #3c5275; border-radius: .4rem; color: #f3f7ff; padding: .55rem; }
    button { justify-self: start; background: #2a6ec5; border: 0; border-radius: .4rem; color: white; padding: .55rem .8rem; cursor: pointer; }
    button:disabled { opacity: .55; cursor: default; } button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
  `],
})
export class PersonaVideoPickerComponent {
  readonly facade = inject(PersonaProfileFacade);
}
