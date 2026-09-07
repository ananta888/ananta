import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, map, timeout } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PersonaProfileApiClient } from '../organizations/persona-media/persona-profile-api.client';
import type { PersonaEffectiveProfile, PersonaOwnerKind, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';
import { avatarCandidate } from './meet-avatar-candidate';
import { MeetAvatarSelection } from './meet-avatar-selection';

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
  private pending?: Subscription;
  private revision = 0;
  private selectedScope: PersonaProfileScope | null = null;
  readonly candidate = signal<MeetAvatarSelection | null>(null);
  readonly busy = signal(false);
  readonly message = signal('');
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
    ++this.revision; this.pending?.unsubscribe(); this.pending = undefined;
    this.selectedScope = null; this.candidate.set(null); this.busy.set(false); this.message.set('');
  }
  private scope(): PersonaProfileScope | null {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    const organization = this.organization.trim(), owner = this.kind === 'organization' ? organization : this.owner.trim();
    if (!hub || ![this.projectId, organization, owner].every(value => /^[A-Za-z0-9_.:-]{1,160}$/.test(value))
      || !['organization', 'team', 'agent'].includes(this.kind)) return null;
    return { hub, project: this.projectId, organization, kind: this.kind, owner };
  }
  resolve(): void {
    if (this.disabled || this.busy()) return;
    this.clear(); const scope = this.scope(), revision = this.revision;
    if (!scope) { this.message.set('Bitte erreichbaren Hub sowie passende Organisations- und Profil-ID wählen.'); return; }
    this.busy.set(true);
    this.pending = this.api.effective(scope).pipe(timeout(10_000), map(value => avatarCandidate(value, scope))).subscribe({
      next: value => {
        if (revision !== this.revision) return;
        if (JSON.stringify(this.scope()) !== JSON.stringify(scope)) { this.clear(); return; }
        this.selectedScope = scope; this.candidate.set(value); this.busy.set(false);
      },
      error: () => {
        if (revision !== this.revision) return;
        this.busy.set(false); this.message.set('Profil nicht verfügbar, deaktiviert oder Prüfung fehlgeschlagen. Keine Ersatzquelle ausgewählt.');
      },
    });
  }
  select(): void {
    const value = this.candidate();
    if (this.disabled || this.busy() || value?.mode !== 'persona-image-v1') return;
    if (JSON.stringify(this.scope()) !== JSON.stringify(this.selectedScope)) { this.clear(); return; }
    this.avatarSelected.emit({ ...value.profile });
  }
  neutral(): void {
    if (this.disabled || this.busy()) return;
    this.clear(); this.avatarSelected.emit(null);
  }
}
