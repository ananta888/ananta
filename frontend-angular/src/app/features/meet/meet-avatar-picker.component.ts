import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile, PersonaOwnerKind, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';
import { avatarCandidate } from './meet-avatar-candidate';
import { MeetProfileCandidateController, meetProfileScope } from './meet-profile-candidate-controller';

@Component({ selector: 'app-meet-avatar-picker', standalone: true, imports: [FormsModule], template: `
  <fieldset [disabled]="disabled || busy()">
    <legend>Avatar-Profil für diesen Auftrag auswählen</legend>
    <label>Organisations-ID <input [ngModel]="organization" (ngModelChange)="edit('organization', $event)" maxlength="160" /></label>
    <label>Profilebene <select [ngModel]="kind" (ngModelChange)="chooseKind($event)">
      <option value="organization">Organisation</option><option value="team">Team</option><option value="agent">Agent</option>
    </select></label>
    @if (kind !== 'organization') {
      <label>Team-/Agent-ID <input [ngModel]="owner" (ngModelChange)="edit('owner', $event)" maxlength="160" /></label>
    }
    <button type="button" (click)="resolve()">Aktuelles Profil beim Hub prüfen</button>
    @if (candidate(); as selected) {
      @if (selected.mode === 'persona-image-v1') {
        <p>Geprüfte Bildreferenz: {{ selected.reference.artifact_id }} · {{ selected.reference.classification }}.</p>
        <button type="button" (click)="select()">Dieses Profilbild auswählen</button>
      }
    }
    <button type="button" (click)="neutral()">Ausdrücklich festes KI-Symbol auswählen</button>
    <p>Die Prüfung lädt nur Profilmetadaten, kein Bild. Sie erteilt keine Veröffentlichungsfreigabe.
      Eine pausierte Quelle bleibt pausiert; bei laufendem Avatar ersetzt der Hub das Bild nach erneuter Prüfung.</p>
  </fieldset>
  @if (message()) { <p role="status">{{ message() }}</p> }
` })
export class MeetAvatarPickerComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) projectId = '';
  @Input() disabled = false;
  @Output() avatarSelected = new EventEmitter<PersonaEffectiveProfile['selection'] | null>();
  private readonly api = inject(PersonaProfileApiClient);
  private readonly directory = inject(AgentDirectoryService);
  private readonly controller = new MeetProfileCandidateController(scope => this.api.effective(scope), avatarCandidate, () => this.scope());
  readonly candidate = this.controller.candidate;
  readonly busy = this.controller.busy;
  readonly message = this.controller.message;
  organization = ''; owner = ''; kind: PersonaOwnerKind = 'organization';

  ngOnChanges(): void { this.clear(); }
  ngOnDestroy(): void { this.clear(); }
  edit(field: 'organization' | 'owner', value: string): void {
    this.clear(); this[field] = value;
  }
  chooseKind(value: PersonaOwnerKind): void {
    this.clear(); this.kind = value; this.owner = '';
  }
  private clear(): void {
    this.controller.clear();
  }
  private scope(): PersonaProfileScope | null {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    return meetProfileScope(hub, this.projectId, this.organization, this.kind, this.owner);
  }
  resolve(): void {
    if (this.disabled || this.busy()) return;
    this.controller.resolve();
  }
  select(): void {
    const value = this.controller.current();
    if (this.disabled || this.busy() || value?.mode !== 'persona-image-v1') return;
    this.avatarSelected.emit({ ...value.profile });
  }
  neutral(): void {
    if (this.disabled || this.busy()) return;
    this.clear(); this.avatarSelected.emit(null);
  }
}
