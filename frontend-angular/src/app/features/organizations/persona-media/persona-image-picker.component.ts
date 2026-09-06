import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PersonaProfileFacade } from './persona-profile.facade';

@Component({
  selector: 'app-persona-image-picker',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="picker">
      <button type="button" (click)="facade.listImages()" [disabled]="facade.busy()">Zugelassene Bilder auflisten</button>
      @if (facade.imagesLoaded()) {
        @if (facade.imageOptions().length) {
          <label>Für deine Vorschau freigegebene Bilder
            <select [ngModel]="facade.imageId()" (ngModelChange)="facade.chooseListedImage($event)" [disabled]="facade.busy()">
              <option value="">Bild auswählen …</option>
              @for (image of facade.imageOptions(); track image.artifact_id) {
                <option [value]="image.artifact_id">{{ image.classification }} · {{ image.artifact_id }} · v{{ image.revision }}</option>
              }
            </select>
          </label>
        } @else { <small>In diesem Ausschnitt sind keine Bilder für deine Vorschau freigegeben.</small> }
        @if (facade.imageCursor()) { <button type="button" (click)="facade.listImages(true)" [disabled]="facade.busy()">Nächsten Ausschnitt laden</button> }
      }
      <label>Oder zugelassene Bild-ID eingeben
        <input [ngModel]="facade.imageId()" (ngModelChange)="facade.changeImageId($event)" maxlength="160" [disabled]="facade.busy()" autocomplete="off" />
      </label>
      <button type="button" (click)="facade.inspectImage()" [disabled]="facade.busy() || !facade.imageId()">Bild prüfen & Vorschau laden</button>
      <small>Nur bereits über die Hub-Bild-API zugelassene Bilder. Keine Dateipfade oder externen URLs. Eine Listenauswahl erteilt keine Veröffentlichungsrechte.</small>
      @if (facade.image(); as image) { <p>{{ image.classification }} · Bildrevision {{ image.revision }}</p> }
    </div>
  `,
  styles: [`
    .picker, label { display: grid; gap: .5rem; } small { color: #a9b9d4; } p { margin: 0; }
    input, select { min-width: 0; width: 100%; box-sizing: border-box; background: #101b2e; border: 1px solid #3c5275; border-radius: .4rem; color: #f3f7ff; padding: .55rem; }
    button { justify-self: start; background: #2a6ec5; border: 0; border-radius: .4rem; color: white; padding: .55rem .8rem; cursor: pointer; }
    button:disabled { opacity: .55; cursor: default; } button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
  `],
})
export class PersonaImagePickerComponent {
  readonly facade = inject(PersonaProfileFacade);
}
