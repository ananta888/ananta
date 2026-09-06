import { CommonModule } from '@angular/common';
import { Component, HostListener, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { OrganizationBundleWorkbenchComponent } from '../bundle-workbench/organization-bundle-workbench.component';
import { OrganizationGraphComponent } from '../components/organization-graph/organization-graph.component';
import { OrganizationHierarchyComponent } from '../components/organization-hierarchy/organization-hierarchy.component';
import { OrganizationInspectorComponent } from '../inspector/organization-inspector.component';
import { OrganizationPlanningHierarchyComponent } from '../planning-hierarchy/organization-planning-hierarchy.component';
import { PersonaProfilePanelComponent } from '../persona-media/persona-profile-panel.component';
import { OrganizationRoleActivationComponent } from '../role-activation/organization-role-activation.component';
import { RoleSlotEditorComponent } from '../role-slot-editor/role-slot-editor.component';
import { OrganizationRuntimeOverlayComponent } from '../runtime-overlay/organization-runtime-overlay.component';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationSetupComponent } from '../setup/organization-setup.component';
import { OrganizationTopologyEditorComponent } from '../topology-editor/organization-topology-editor.component';
import { OrganizationGraph3dComponent } from '../visualization/organization-graph-3d.component';
import { OrganizationVisualProfileService } from '../visualization/organization-visual-profile.service';

type OrganizationSection = 'topology' | 'setup' | 'edit' | 'roles' | 'activation' | 'planning' | 'bundles' | 'persona';

@Component({
  selector: 'app-organization-shell',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    OrganizationBundleWorkbenchComponent,
    OrganizationGraphComponent,
    OrganizationGraph3dComponent,
    OrganizationHierarchyComponent,
    OrganizationInspectorComponent,
    OrganizationPlanningHierarchyComponent,
    OrganizationRoleActivationComponent,
    OrganizationRuntimeOverlayComponent,
    OrganizationSetupComponent,
    OrganizationTopologyEditorComponent,
    RoleSlotEditorComponent,
    PersonaProfilePanelComponent,
  ],
  providers: [OrganizationTopologyStateService, OrganizationVisualProfileService],
  template: `
    <main class="organization-shell">
      <header class="page-header">
        <div>
          <p class="eyebrow">Hub Control Plane</p>
          <h1>Organisationen</h1>
          <p>Mehrere Teams hierarchisch und als Graph verwalten – mit zentraler Hub-Orchestrierung.</p>
        </div>
        <label class="organization-picker">
          Aktive Organisation
          <select
            [ngModel]="state.selectedOrganizationId()"
            (ngModelChange)="selectOrganization($event)"
            [disabled]="state.loading() || state.mutating()">
            @for (organization of state.organizations(); track organization.id) {
              <option [value]="organization.id">{{ organization.title }} · {{ organization.team_count }} Teams · {{ organization.lifecycle }}</option>
            }
          </select>
        </label>
      </header>

      @if (state.error()) {
        <section class="error" role="alert">
          <strong>{{ state.error() }}</strong><small>{{ state.errorReasonCode() }}</small>
          <button type="button" (click)="state.initialize()">Erneut versuchen</button>
        </section>
      }

      <nav class="sections" aria-label="Organisationsmanagement">
        @for (item of sectionItems; track item.id) {
          <button type="button" [class.active]="section() === item.id" (click)="openSection(item.id)">{{ item.label }}</button>
        }
      </nav>

      @if (section() === 'topology') {
        @if (state.selectedOrganizationId()) {
          <app-organization-runtime-overlay />
          <section class="topology-controls" aria-label="Topologiefilter">
            <div class="view-switch" role="tablist" aria-label="Darstellung">
              <button type="button" role="tab" [attr.aria-selected]="state.mode() === 'hierarchy'" (click)="state.setMode('hierarchy')">Hierarchie</button>
              <button type="button" role="tab" [attr.aria-selected]="state.mode() === 'graph'" (click)="state.setMode('graph')">2D</button>
              <button type="button" role="tab" [attr.aria-selected]="state.mode() === 'graph3d'" (click)="state.setMode('graph3d')">3D</button>
            </div>
            <label>Suche <input type="search" [ngModel]="state.search()" (ngModelChange)="state.setSearch($event)" maxlength="128" /></label>
            <fieldset>
              <legend>Kantennamespaces</legend>
              @for (namespace of edgeNamespaceOptions; track namespace) {
                <label><input type="checkbox" [checked]="state.edgeNamespaces().includes(namespace)" (change)="state.toggleEdgeNamespace(namespace)" /> {{ namespace }}</label>
              }
            </fieldset>
            @if (state.focusedSubgraphId()) { <button type="button" (click)="state.setFocus(null)">Subgraph-Fokus lösen</button> }
          </section>

          <div class="topology-layout" [class.without-inspector]="!state.inspectorOpen()">
            <section class="renderer">
              @if (state.mode() === 'hierarchy') { <app-organization-hierarchy /> }
              @else if (state.mode() === 'graph') { <app-organization-graph /> }
              @else { <app-organization-graph-3d /> }
            </section>
            @if (state.inspectorOpen()) { <app-organization-inspector /> }
            @else { <button type="button" class="open-inspector" (click)="state.inspectorOpen.set(true)">Inspector öffnen</button> }
          </div>
        } @else if (state.loaded()) {
          <section class="empty-state">
            <h2>Noch keine Organisation</h2>
            <p>Starte kompakt mit 5 bis 20 Rollenplätzen oder wähle Enterprise Scrum mit 5 bis 10 Teams.</p>
            <button type="button" (click)="openSection('setup')">Einrichtung öffnen</button>
          </section>
        }
      }

      @if (section() === 'setup') { <app-organization-setup /> }
      @if (section() === 'edit') { <app-organization-topology-editor /> }
      @if (section() === 'roles') { <app-role-slot-editor /> }
      @if (section() === 'activation') { <app-organization-role-activation /> }
      @if (section() === 'planning') { <app-organization-planning-hierarchy /> }
      @if (section() === 'bundles') { <app-organization-bundle-workbench /> }
      @if (section() === 'persona') { <app-persona-profile-panel /> }

      @if (state.loading() && !state.loaded()) { <p class="loading" role="status">Organisationsmanagement wird geladen …</p> }
    </main>
  `,
  styles: [`
    :host { display: block; min-height: 100%; }
    .organization-shell { color: #e5edfb; display: grid; gap: 1rem; padding: clamp(.8rem, 2vw, 1.5rem); }
    .page-header { align-items: end; display: flex; gap: 1.5rem; justify-content: space-between; }
    h1, h2, p { margin: 0; } h1 { font-size: clamp(1.7rem, 4vw, 2.5rem); }
    .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
    .page-header p:last-child { color: #9eb0cd; }
    .organization-picker { display: grid; gap: .3rem; min-width: min(420px, 46vw); }
    select, input { background: #101b2e; border: 1px solid #3c5275; border-radius: .4rem; color: #f3f7ff; padding: .55rem; }
    .sections { border-bottom: 1px solid #2d405f; display: flex; gap: .2rem; overflow-x: auto; }
    .sections button { background: transparent; border: 0; border-bottom: 3px solid transparent; color: #a9b9d4; cursor: pointer; padding: .65rem .8rem; white-space: nowrap; }
    .sections button.active { border-color: #65a1eb; color: white; } button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
    .error { align-items: center; background: #401d26; border-left: 4px solid #e56c77; display: flex; gap: .8rem; padding: .65rem; }
    .error small { color: #f2aeb5; } .error button, .empty-state button, .topology-controls > button, .open-inspector { background: #2a6ec5; border: 0; border-radius: .4rem; color: white; cursor: pointer; padding: .45rem .65rem; }
    .topology-controls { align-items: center; background: #0d1728; border: 1px solid #2c4161; border-radius: .6rem; display: flex; flex-wrap: wrap; gap: .7rem; padding: .55rem; }
    .view-switch { display: flex; } .view-switch button { background: #1e304c; border: 1px solid #405a80; color: #cbd8eb; margin-left: -1px; padding: .45rem .7rem; }
    .view-switch button:first-child { margin-left: 0; }
    .view-switch button:first-child { border-radius: .4rem 0 0 .4rem; } .view-switch button:last-child { border-radius: 0 .4rem .4rem 0; }
    .view-switch button[aria-selected='true'] { background: #286abb; color: white; }
    .topology-controls > label { align-items: center; display: flex; gap: .4rem; }
    fieldset { border: 0; display: flex; gap: .6rem; margin: 0; padding: 0; } legend { float: left; font-size: .72rem; margin-right: .5rem; } fieldset label { font-size: .74rem; }
    .topology-layout { align-items: start; display: grid; gap: .8rem; grid-template-columns: minmax(0, 1fr) minmax(250px, 330px); }
    .topology-layout.without-inspector { grid-template-columns: minmax(0, 1fr) auto; } .renderer { min-width: 0; }
    .open-inspector { writing-mode: vertical-rl; } .empty-state, .loading { background: #0d1829; border: 1px solid #304464; border-radius: .7rem; padding: 2rem; text-align: center; }
    @media (max-width: 900px) { .page-header { align-items: stretch; flex-direction: column; } .organization-picker { min-width: 0; } .topology-layout, .topology-layout.without-inspector { grid-template-columns: 1fr; } .open-inspector { writing-mode: horizontal-tb; } }
  `],
})
export class OrganizationShellComponent implements OnInit {
  readonly state = inject(OrganizationTopologyStateService);
  readonly section = signal<OrganizationSection>('topology');
  readonly sectionItems: readonly { id: OrganizationSection; label: string }[] = [
    { id: 'topology', label: 'Topologie' },
    { id: 'setup', label: 'Einrichten' },
    { id: 'edit', label: 'Ändern' },
    { id: 'roles', label: 'Rollen & Assignments' },
    { id: 'activation', label: 'Aktivierung & Übergaben' },
    { id: 'planning', label: 'Planung & Proposals' },
    { id: 'bundles', label: 'Import / Export' },
    { id: 'persona', label: 'Persona & Medien' },
  ];
  readonly edgeNamespaceOptions = ['hierarchy', 'organization', 'runtime'] as const;

  ngOnInit(): void {
    this.state.initialize();
  }

  openSection(section: OrganizationSection): void {
    this.section.set(section);
    if (section === 'planning' && this.state.selectedOrganizationId() && !this.state.planning()) this.state.loadPlanning();
  }

  selectOrganization(organizationId: string): void {
    if (organizationId) this.state.selectOrganization(organizationId);
  }

  canLeaveOrganizations(): boolean {
    return !this.state.instantiationPending();
  }

  @HostListener('window:beforeunload', ['$event'])
  preventUnsafeUnload(event: BeforeUnloadEvent): void {
    if (this.canLeaveOrganizations()) return;
    event.preventDefault();
    event.returnValue = '';
  }
}
