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
    input { min-width: 0; width: 100%; box-sizing: border-box; background: #101b2e; border: 1px solid #3c5275; border-radius: .4rem; color: #f3f7ff; padding: .55rem; }
    button { justify-self: start; background: #2a6ec5; border: 0; border-radius: .4rem; color: white; padding: .55rem .8rem; cursor: pointer; }
    button:disabled { opacity: .55; cursor: default; } button:focus-visible, input:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
  `],
})
export class PersonaVideoPickerComponent {
  readonly facade = inject(PersonaProfileFacade);
}
