import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile, PersonaOwnerKind } from '../organizations/persona-media/persona-profile.models';
import { MeetProfileCandidateController, meetProfileScope } from './meet-profile-candidate-controller';
import { voiceCandidate } from './meet-voice-candidate';

@Component({ selector: 'app-meet-voice-picker', standalone: true, imports: [FormsModule], template: `
  <fieldset [disabled]="disabled || busy()">
    <legend>Stimmprofil für diesen Auftrag auswählen</legend>
    <label>Organisations-ID <input [ngModel]="organization" (ngModelChange)="edit('organization', $event)" maxlength="160" /></label>
    <label>Profilebene <select [ngModel]="kind" (ngModelChange)="chooseKind($event)">
      <option value="organization">Organisation</option><option value="team">Team</option><option value="agent">Agent</option>
    </select></label>
    @if (kind !== 'organization') {
      <label>Team-/Agent-ID <input [ngModel]="owner" (ngModelChange)="edit('owner', $event)" maxlength="160" /></label>
    }
    <button type="button" (click)="resolve()">Aktuelles Stimmprofil beim Hub prüfen</button>
    @if (candidate(); as selected) {
      @if (selected.mode === 'persona-voice-v1') {
        <p>Geprüfte Stimmreferenz: {{ selected.reference.artifact_id }} · {{ selected.reference.classification }}.</p>
        <button type="button" (click)="select()">Dieses Stimmprofil auswählen</button>
      }
    }
    <button type="button" (click)="configured()">Ausdrücklich konfigurierte KI-Stimme auswählen</button>
    <p>Nur Profilmetadaten, keine Audio-Vorschau und kein Modelldownload. Die Prüfung erteilt keine Veröffentlichungsfreigabe.
      Pausierte Sprache bleibt pausiert. Ein Wechsel verwirft alte Sprachantworten; bei Widerruf keine Ersatzstimme.</p>
  </fieldset>
  @if (message()) { <p role="status">{{ message() }}</p> }
` })
export class MeetVoicePickerComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) projectId = '';
  @Input() disabled = false;
  @Output() voiceSelected = new EventEmitter<PersonaEffectiveProfile['selection'] | null>();
  private readonly api = inject(PersonaProfileApiClient);
  private readonly directory = inject(AgentDirectoryService);
  private readonly controller = new MeetProfileCandidateController(scope => this.api.effective(scope), voiceCandidate, () => {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    return meetProfileScope(hub, this.projectId, this.organization, this.kind, this.owner);
  });
  readonly candidate = this.controller.candidate;
  readonly busy = this.controller.busy;
  readonly message = this.controller.message;
  organization = ''; owner = ''; kind: PersonaOwnerKind = 'organization';

  ngOnChanges(): void { this.controller.clear(); }
  ngOnDestroy(): void { this.controller.clear(); }
  edit(field: 'organization' | 'owner', value: string): void { this.controller.clear(); this[field] = value; }
  chooseKind(value: PersonaOwnerKind): void { this.controller.clear(); this.kind = value; this.owner = ''; }
  resolve(): void { if (!this.disabled && !this.busy()) this.controller.resolve(); }
  select(): void {
    if (this.disabled || this.busy()) return;
    const value = this.controller.current();
    if (value?.mode === 'persona-voice-v1') this.voiceSelected.emit({ ...value.profile });
  }
  configured(): void {
    if (this.disabled || this.busy()) return;
    this.controller.clear(); this.voiceSelected.emit(null);
  }
}
