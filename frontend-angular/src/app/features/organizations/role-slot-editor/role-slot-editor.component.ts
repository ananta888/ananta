import { CommonModule } from '@angular/common';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import {
  OrganizationAssignmentCandidate,
  OrganizationRoleSlot,
} from '../models/organization-topology.models';
import { OrganizationApiClient } from '../services/organization-api.client';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-role-slot-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="slots" aria-labelledby="role-slot-editor-heading">
      <header>
        <div><p class="eyebrow">Rollen &amp; Besetzung</p><h2 id="role-slot-editor-heading">Role Slots</h2></div>
        <button type="button" (click)="loadSlots()" [disabled]="loading()">Neu laden</button>
      </header>

      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      <div class="layout">
        <div class="slot-list" role="listbox" aria-label="Role Slots">
          @for (slot of slots(); track slot.id) {
            <button
              type="button"
              role="option"
              [attr.aria-selected]="selectedSlot()?.id === slot.id"
              (click)="selectSlot(slot)">
              <span>{{ slot.scrum_accountability || 'specialization' }}</span>
              <strong>{{ slot.label }}</strong>
              <small>{{ slot.min_count }} / {{ slot.default_count }} / {{ slot.max_count }} · {{ slot.risk_level }}</small>
            </button>
          } @empty { <p>Keine Role Slots geladen.</p> }
        </div>

        @if (selectedSlot(); as slot) {
          <article class="detail">
            <p class="eyebrow">{{ slot.role_template_key }} · v{{ slot.role_template_version }}</p>
            <h3>{{ slot.label }}</h3>
            <dl>
              <div><dt>Scrum-Accountability</dt><dd>{{ slot.scrum_accountability || 'keine offizielle Accountability' }}</dd></div>
              <div><dt>Spezialisierung</dt><dd>{{ slot.specialization || '–' }}</dd></div>
              <div><dt>Cardinality</dt><dd>min {{ slot.min_count }}, default {{ slot.default_count }}, max {{ slot.max_count }}</dd></div>
              <div><dt>Risiko</dt><dd>{{ slot.risk_level }}</dd></div>
              <div><dt>Unabhängige Verifikation</dt><dd>{{ slot.independent_verification_required ? 'erforderlich' : 'nicht erzwungen' }}</dd></div>
            </dl>
            <h4>Benötigte Fähigkeiten</h4>
            <ul class="chips">@for (capability of slot.required_capabilities; track capability) { <li>{{ capability }}</li> }</ul>

            <h4>Servergeprüfte Kandidaten</h4>
            <div class="candidates">
              @for (candidate of candidates(); track candidate.agent_id) {
                <label [class.incompatible]="!candidate.compatible">
                  <input
                    type="radio"
                    name="assignment-candidate"
                    [value]="candidate.agent_id"
                    [(ngModel)]="selectedAgentId"
                    [disabled]="!candidate.compatible" />
                  <span>
                    <strong>{{ candidate.label }}</strong>
                    <small>Kapazität {{ candidate.capacity_used }} / {{ candidate.capacity_limit }} · Teams: {{ candidate.affected_teams.join(', ') || 'keine' }}</small>
                    @if (candidate.reasons.length) { <small>{{ candidate.reasons.join(' · ') }}</small> }
                  </span>
                </label>
              } @empty { <p>Keine kompatiblen oder bekannten Agenten.</p> }
            </div>
            <button type="button" (click)="previewAssignment()" [disabled]="!selectedAgentId || loading()">Assignment-Dry-run</button>
          </article>
        }
      </div>

      @if (state.patchPreview(); as preview) {
        <section class="apply">
          <h3>Assignment-Prüfung: {{ preview.applicable ? 'anwendbar' : 'blockiert' }}</h3>
          <ul>@for (diagnostic of preview.diagnostics; track diagnostic.reason_code + diagnostic.message) { <li>{{ diagnostic.severity }} · {{ diagnostic.message }}</li> }</ul>
          <label>Parent Organization-Admin-Grant <input type="password" [(ngModel)]="adminGrant" autocomplete="off" /></label>
          <label class="confirm"><input type="checkbox" [(ngModel)]="confirmed" /> Capability-, Kapazitäts- und SoD-Prüfung bestätigen</label>
          <button type="button" (click)="state.issuePreviewGrant(adminGrant)" [disabled]="!confirmed || !adminGrant.trim() || !preview.applicable || state.mutating()">One-shot Grant binden</button>
          <button type="button" (click)="state.applyPreview()" [disabled]="!confirmed || !state.topologyPatchGrant() || !preview.applicable || state.mutating()">Assignment exakt gebunden anwenden</button>
        </section>
      }
    </section>
  `,
  styles: [`
    .slots { display: grid; gap: .8rem; } header { align-items: end; display: flex; justify-content: space-between; }
    h2, h3, h4, p { margin: 0; } .eyebrow { color: #77a9ee; font-size: .7rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    button { background: #263e60; border: 1px solid #496787; border-radius: .4rem; color: white; cursor: pointer; padding: .5rem .7rem; } button:disabled { opacity: .45; }
    .layout { display: grid; gap: .8rem; grid-template-columns: minmax(240px, .8fr) minmax(360px, 1.5fr); }
    .slot-list { background: #0c1627; border: 1px solid #2e4260; border-radius: .7rem; display: grid; gap: .3rem; max-height: 68vh; overflow: auto; padding: .5rem; }
    .slot-list button { align-items: start; background: transparent; display: flex; flex-direction: column; text-align: left; }
    .slot-list button[aria-selected='true'] { background: #17385f; border-color: #5790d5; } .slot-list span, small { color: #91a5c3; font-size: .7rem; }
    .detail, .apply { background: #0e192b; border: 1px solid #324868; border-radius: .7rem; display: grid; gap: .65rem; padding: .9rem; }
    dl { display: grid; gap: .35rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; } dl div { background: #111f34; border-radius: .4rem; padding: .45rem; }
    dt { color: #91a5c3; font-size: .7rem; } dd { margin: .15rem 0 0; }
    .chips { display: flex; flex-wrap: wrap; gap: .3rem; list-style: none; margin: 0; padding: 0; } .chips li { background: #263b5b; border-radius: 999px; font-size: .7rem; padding: .25rem .45rem; }
    .candidates { display: grid; gap: .35rem; } .candidates label { align-items: flex-start; background: #111f34; border: 1px solid #344d70; border-radius: .4rem; display: flex; gap: .5rem; padding: .5rem; }
    .candidates label span { display: grid; } .candidates .incompatible { opacity: .65; } .error { background: #411d27; color: #ffc3c9; padding: .6rem; }
    .apply label { display: grid; gap: .3rem; } .apply input[type='password'] { background: #111f34; border: 1px solid #405b80; border-radius: .35rem; color: white; padding: .5rem; }
    .apply .confirm { align-items: center; display: flex; } @media (max-width: 800px) { .layout { grid-template-columns: 1fr; } }
  `],
})
export class RoleSlotEditorComponent {
  readonly state = inject(OrganizationTopologyStateService);
  private readonly api = inject(OrganizationApiClient);
  readonly slots = signal<readonly OrganizationRoleSlot[]>([]);
  readonly selectedSlot = signal<OrganizationRoleSlot | null>(null);
  readonly candidates = signal<readonly OrganizationAssignmentCandidate[]>([]);
  readonly loading = signal(false);
  readonly error = signal('');
  selectedAgentId = '';
  adminGrant = '';
  confirmed = false;

  constructor() {
    effect(() => {
      if (this.state.selectedOrganizationId() && this.state.hubUrl()) this.loadSlots();
    });
  }

  loadSlots(): void {
    const hubUrl = this.state.hubUrl();
    const organizationId = this.state.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.loading()) return;
    this.loading.set(true); this.error.set('');
    this.api.roleSlots(hubUrl, organizationId).pipe(finalize(() => this.loading.set(false))).subscribe({
      next: slots => {
        this.slots.set(slots);
        if (slots.length) this.selectSlot(slots[0]);
      },
      error: () => this.error.set('Role Slots konnten nicht geladen werden.'),
    });
  }

  selectSlot(slot: OrganizationRoleSlot): void {
    this.selectedSlot.set(slot);
    this.selectedAgentId = '';
    this.candidates.set(slot.assignments ?? []);
    const hubUrl = this.state.hubUrl();
    const organizationId = this.state.selectedOrganizationId();
    if (!hubUrl || !organizationId) return;
    this.loading.set(true);
    this.api.assignmentCandidates(hubUrl, organizationId, slot.id).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: candidates => this.candidates.set(candidates),
      error: () => this.error.set('Assignment-Kandidaten konnten nicht geprüft werden.'),
    });
  }

  previewAssignment(): void {
    const slot = this.selectedSlot();
    if (!slot || !this.selectedAgentId) return;
    this.confirmed = false;
    this.adminGrant = this.state.selectedOrganizationAdminGrant();
    this.state.previewOperations([{ op: 'assign', role_slot_id: slot.id, agent_id: this.selectedAgentId }]);
  }
}
