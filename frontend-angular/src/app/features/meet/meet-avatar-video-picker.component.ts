import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile, PersonaOwnerKind } from '../organizations/persona-media/persona-profile.models';
import { MeetProfileCandidateController, meetProfileScope } from './meet-profile-candidate-controller';
import { avatarVideoCandidate } from './meet-avatar-video-selection';

export interface AvatarVideoChoice { profile: PersonaEffectiveProfile['selection']; repeat_mode: 'loop' | 'hold_last' }

@Component({ selector: 'app-meet-avatar-video-picker', standalone: true, imports: [FormsModule], template: `
  <fieldset [disabled]="disabled || busy()">
    <legend>Stummes Avatar-Video für diesen Auftrag auswählen</legend>
    <label>Organisations-ID <input [ngModel]="organization" (ngModelChange)="edit('organization', $event)" maxlength="160" /></label>
    <label>Profilebene <select [ngModel]="kind" (ngModelChange)="chooseKind($event)">
      <option value="organization">Organisation</option><option value="team">Team</option><option value="agent">Agent</option>
    </select></label>
    @if (kind !== 'organization') {
      <label>Team-/Agent-ID <input [ngModel]="owner" (ngModelChange)="edit('owner', $event)" maxlength="160" /></label>
    }
    <button type="button" (click)="resolve()">Aktuelles Videoprofil beim Hub prüfen</button>
    <label>Nach Clipende <select [(ngModel)]="repeatMode">
      <option value="hold_last">Letztes Bild halten</option><option value="loop">Clip wiederholen</option>
    </select></label>
    @if (candidate(); as selected) {
      <p>Geprüfte Videoreferenz: {{ selected.reference.artifact_id }} · {{ selected.reference.classification }}.</p>
      <button type="button" (click)="select()">Dieses stumme Video auswählen</button>
    }
    <p>Nur Profilmetadaten, kein automatischer Download oder Videostart. Der Hub prüft die Veröffentlichung separat.
      Eine pausierte Quelle bleibt pausiert. Kein Mikrofon, keine Kameraaufnahme und keine Ersatzquelle bei Widerruf.</p>
  </fieldset>
  @if (message()) { <p role="status">{{ message() }}</p> }
` })
export class MeetAvatarVideoPickerComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) projectId = '';
  @Input() disabled = false;
  @Output() videoSelected = new EventEmitter<AvatarVideoChoice>();
  private readonly api = inject(PersonaProfileApiClient);
  private readonly directory = inject(AgentDirectoryService);
  private readonly controller = new MeetProfileCandidateController(scope => this.api.effective(scope), avatarVideoCandidate, () => {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    return meetProfileScope(hub, this.projectId, this.organization, this.kind, this.owner);
  });
  readonly candidate = this.controller.candidate;
  readonly busy = this.controller.busy;
  readonly message = this.controller.message;
  organization = ''; owner = ''; kind: PersonaOwnerKind = 'organization'; repeatMode: 'loop' | 'hold_last' = 'hold_last';
  ngOnChanges(): void { this.controller.clear(); }
  ngOnDestroy(): void { this.controller.clear(); }
  edit(field: 'organization' | 'owner', value: string): void { this.controller.clear(); this[field] = value; }
  chooseKind(value: PersonaOwnerKind): void { this.controller.clear(); this.kind = value; this.owner = ''; }
  resolve(): void { if (!this.disabled && !this.busy()) this.controller.resolve(); }
  select(): void {
    if (this.disabled || this.busy() || !['loop', 'hold_last'].includes(this.repeatMode)) return;
    const value = this.controller.current();
    if (value) this.videoSelected.emit({ profile: { ...value.profile }, repeat_mode: this.repeatMode });
  }
}
