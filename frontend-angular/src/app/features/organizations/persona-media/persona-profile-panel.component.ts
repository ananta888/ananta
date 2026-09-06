import { Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { PersonaProfileFacade } from './persona-profile.facade';
import { PersonaOwnerKind } from './persona-profile.models';
import { PersonaImagePickerComponent } from './persona-image-picker.component';

@Component({
  selector: 'app-persona-profile-panel',
  standalone: true,
  imports: [FormsModule, PersonaImagePickerComponent],
  providers: [PersonaProfileFacade],
  template: `
    <section aria-labelledby="persona-title">
      <h2 id="persona-title">Persona & Medien</h2>
      <p>Die Persona ist eine KI-Darstellung, keine Identität oder Freigabe. Hier wird weder ein Meet-Raum betreten noch Audio, Kamera oder Bildschirm aufgenommen.</p>
      @if (state.selectedOrganizationId()) {
        <label>Profilinhaber
          <select [ngModel]="facade.kind() + ':' + facade.owner()" (ngModelChange)="choose($event)" [disabled]="facade.busy()">
            @for (owner of owners(); track owner.key) { <option [value]="owner.key">{{ owner.label }}</option> }
          </select>
        </label>
        <small>Teams und Agentenbesetzungen stammen aus der geladenen Topologie. Weitere Knoten bei Bedarf dort nachladen.</small>
        <button type="button" (click)="facade.load()" [disabled]="facade.busy()">Profil neu laden</button>
        @if (facade.snapshot(); as snapshot) {
          <p>Revision {{ snapshot.revision }} · Vererbung: Agentenbesetzung → Team → Organisation.</p>
          @if (facade.effective(); as effective) {
            <section aria-label="Gespeicherte effektive Darstellung">
              <h3>Gespeicherte effektive Darstellung</h3>
              <small>Topologierevision {{ effective.topology_revision }} · Nur Vorschau geprüft, keine Veröffentlichungsfreigabe und keine aktive Sitzung.</small>
              @for (medium of effective.media; track medium.kind) {
                <p>{{ medium.kind }}: {{ medium.state }} · {{ medium.preview_allowed ? 'Vorschau zulässig' : medium.available ? 'deaktiviert' : 'nicht verfügbar' }}</p>
                @for (origin of medium.origins; track origin.owner_kind) {
                  <small>{{ origin.owner_kind }} · {{ origin.persona_id }} · Revision {{ origin.profile_revision }} · {{ origin.selection_state }}</small>
                }
                @if (medium.asset) { <small>{{ medium.asset.classification }} · {{ medium.asset.artifact_id }}</small> }
                @if (medium.kind === 'image' && medium.preview_allowed) {
                  <button type="button" (click)="facade.previewEffective()" [disabled]="facade.busy()">Geerbtes / effektives Bild ansehen</button>
                }
              }
            </section>
          }
          <label>Persona-Kennung
            <input [ngModel]="facade.personaId()" (ngModelChange)="facade.personaId.set($event)" maxlength="160" [disabled]="facade.busy()" autocomplete="off" />
          </label>
          <label>Bildauswahl
            <select [ngModel]="facade.imageState()" (ngModelChange)="facade.selectImageState($event)" [disabled]="facade.busy()">
              <option value="missing">Nicht gesetzt (Fallback zulassen)</option>
              <option value="inherit">Explizit vererben</option>
              <option value="disabled">Deaktiviert (Fallback stoppen)</option>
              <option value="asset">Zugelassenes Bild auswählen</option>
            </select>
          </label>
          @if (facade.imageState() === 'asset') {
            <app-persona-image-picker />
          }
          @if (facade.previewUrl()) { <img [src]="facade.previewUrl()" alt="Private Vorschau des ausgewählten oder effektiven Persona-Bilds" width="256" height="256" /> }
          <button type="button" (click)="facade.save()" [disabled]="facade.busy() || !facade.personaId().trim()">Profil speichern</button>
          <small>Speichern benötigt Projektverwaltung und einen Organisationsgrant. Stimme, Animation und laufende Meet-Sitzungen werden hier noch nicht konfiguriert.</small>
        }
      } @else { <p>Bitte zuerst eine Organisation auswählen.</p> }
      @if (facade.busy()) { <p role="status">Hub-Anfrage läuft …</p> }
      @if (facade.message()) { <p role="status">{{ facade.message() }}</p> }
      @if (facade.error()) { <p role="alert">{{ facade.error() }}</p> }
    </section>
  `,
  styles: [`
    section { display: grid; gap: .8rem; max-width: 52rem; padding: 1.2rem; background: #0d1829; border: 1px solid #304464; border-radius: .7rem; }
    h2, p { margin: 0; } label { display: grid; gap: .3rem; } small { color: #a9b9d4; }
    input, select { background: #101b2e; border: 1px solid #3c5275; border-radius: .4rem; color: #f3f7ff; padding: .55rem; }
    button { justify-self: start; background: #2a6ec5; border: 0; border-radius: .4rem; color: white; padding: .55rem .8rem; cursor: pointer; }
    button:disabled { opacity: .55; cursor: default; } [role='alert'] { color: #f2aeb5; }
    img { object-fit: contain; max-width: 100%; } button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
  `],
})
export class PersonaProfilePanelComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly facade = inject(PersonaProfileFacade);
  readonly owners = computed(() => {
    const organization = this.state.selectedOrganizationId();
    if (!organization) return [];
    const result: { key: string; kind: PersonaOwnerKind; id: string; label: string }[] = [
      { key: `organization:${organization}`, kind: 'organization', id: organization, label: 'Organisation' },
    ];
    const page = this.state.topology();
    for (const node of page?.organization_id === organization ? page.nodes : []) {
      const id = node.kind === 'team' ? node.team_id : node.kind === 'assignment' ? node.assignment_id : null;
      if (!id) continue;
      const kind = node.kind === 'team' ? 'team' : 'agent';
      const key = `${kind}:${id}`;
      if (!result.some(item => item.key === key)) result.push({ key, kind, id, label: `${kind === 'team' ? 'Team' : 'Agentenbesetzung'} · ${node.label}` });
    }
    return result;
  });

  choose(key: string): void {
    const owner = this.owners().find(item => item.key === key);
    if (owner) this.facade.chooseOwner(owner.kind, owner.id);
  }
}
