import { CommonModule } from '@angular/common';
import { Component, effect, inject, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationRoleActivationFacade } from './organization-role-activation.facade';
import { OrganizationRoleActivationStep } from './organization-role-activation.models';

@Component({
  selector: 'app-organization-role-activation',
  standalone: true,
  imports: [CommonModule, FormsModule],
  providers: [OrganizationRoleActivationFacade],
  template: `
    <section class="activation" aria-labelledby="role-activation-heading">
      <header class="heading">
        <div>
          <p class="eyebrow">Hub-Routing verständlich gemacht</p>
          <h2 id="role-activation-heading">Aktivierung &amp; Übergaben</h2>
          <p>Die Definition beschreibt, welche Rolle nach welchen Vorgängern und Inputs infrage kommt. Nur der Hub darf daraus zur Laufzeit einen Task, ein Team und ein Assignment auswählen.</p>
        </div>
        <button type="button" (click)="facade.load()" [disabled]="facade.loading()">Neu laden</button>
      </header>

      <ol class="state-model" aria-label="Hub-Ablauf mit revisionsgebundenem Ist-Status, soweit vorhanden">
        <li><strong>1 · Slot deklariert</strong><span>Ein aktiver Rollen-Slot ist in der Organisationsinstanz vorhanden.</span></li>
        <li><strong>2 · Assignment gezählt</strong><span>Die Ansicht zählt aktive Assignment-Zeilen; Eignung und freie Kapazität bleiben unbekannt.</span></li>
        <li><strong>3 · Lokale Abhängigkeiten erfüllt</strong><span>Nur exakt revisionsgebundene Tasks und ihre lokalen <code>depends_on</code>-Tasks werden beobachtet; externe Handoffs bleiben separat.</span></li>
        <li><strong>4 · Hub entscheidet</strong><span>Nur der Hub darf Team, Slot und Assignment autoritativ auswählen.</span></li>
        <li><strong>5 · Worker führt aus</strong><span>Running WorkerJob plus aktive, passende Lease belegen die Ausführung.</span></li>
      </ol>

      @if (facade.error()) { <p class="error" role="alert">{{ facade.error() }}</p> }
      @if (facade.loading() && !facade.model()) { <p class="status" role="status">Aktivierungsmodell wird geladen …</p> }

      @if (facade.model(); as model) {
        <section class="binding" aria-label="Bindung des Aktivierungsmodells">
          <span><strong>Routing Owner:</strong> {{ model.router_owner }}</span>
          <span><strong>Definitionsrevision:</strong> <code>{{ model.definition_revision }}</code></span>
          <span><strong>Topology-Snapshot:</strong> {{ model.snapshot_revision ?? 'fehlt' }} · <code>{{ model.snapshot_hash || model.snapshot_reason_code }}</code></span>
          <span><strong>{{ model.summary.workflow_step_count }}</strong> Workflow-Schritte</span>
          <span><strong>{{ model.summary.unbound_step_count }}</strong> nicht gebundene Schritte</span>
          <span><strong>{{ model.summary.runtime_bound_step_count }}</strong> revisionsgebundene Ist-Schritte</span>
          <span><strong>{{ model.summary.worker_executing_step_count }}</strong> Worker-Ausführungen</span>
        </section>

        @if (model.stale) {
          <p class="snapshot-warning" role="alert">
            <strong>Snapshot nicht revisionsgleich:</strong>
            {{ model.snapshot_reason_code }}. Definition, aktive Slots und Assignments können aus unterschiedlichen sichtbaren Revisionen stammen.
          </p>
        }

        @if (model.runtime_observation.state === 'not_observed') {
          <p class="runtime-note" role="status">
            <strong>Ist-Zustand noch nicht schrittgenau beobachtbar:</strong>
            Für diese Definition wurden keine exakt revisionsgebundenen Workflow-Tasks gefunden. Lokale Task-Abhängigkeiten, Hub routed und Worker executing bleiben unbekannt statt aus Titeln oder Rollen geschätzt.
          </p>
        } @else if (model.runtime_observation.state === 'partial') {
          <p class="runtime-note" role="status">
            <strong>Ist-Zustand teilweise beobachtet:</strong>
            Nur {{ model.summary.runtime_bound_step_count }} von {{ model.summary.workflow_step_count }} Schritten besitzen eine exakte Workflow-, Revisions-, Gate- und Handoff-Bindung. Die übrigen bleiben unbekannt.
          </p>
        } @else {
          <p class="runtime-observed" role="status">
            <strong>Ist-Zustand revisionsgebunden:</strong> Task-, Hub-Routing-, WorkerJob- und Lease-Fakten werden getrennt dargestellt.
          </p>
        }

        <section class="filters" aria-label="Rollenfluss filtern">
          <label>Team
            <select [ngModel]="facade.teamFilter()" (ngModelChange)="facade.teamFilter.set($event)">
              <option value="">Alle</option>
              @for (team of facade.teams(); track team.team_unit_id) { <option [value]="team.team_unit_id">{{ team.team_name }}</option> }
            </select>
          </label>
          <label>Workflow
            <select [ngModel]="facade.workflowFilter()" (ngModelChange)="facade.workflowFilter.set($event)">
              <option value="">Alle</option>
              @for (workflow of facade.workflows(); track workflow) { <option [value]="workflow">{{ workflow }}</option> }
            </select>
          </label>
          <label>Rolle
            <select [ngModel]="facade.roleFilter()" (ngModelChange)="facade.roleFilter.set($event)">
              <option value="">Alle</option>
              @for (role of facade.roles(); track role) { <option [value]="role">{{ role }}</option> }
            </select>
          </label>
          <label>Task-Art
            <select [ngModel]="facade.taskKindFilter()" (ngModelChange)="facade.taskKindFilter.set($event)">
              <option value="">Alle</option>
              @for (kind of facade.taskKinds(); track kind) { <option [value]="kind">{{ kind }}</option> }
            </select>
          </label>
          <label>Rollenbindung
            <select [ngModel]="facade.bindingFilter()" (ngModelChange)="facade.bindingFilter.set($event)">
              <option value="">Alle</option>
              @for (bindingState of facade.bindingStates(); track bindingState) { <option [value]="bindingState">{{ bindingState }}</option> }
            </select>
          </label>
          <label>Aktivierungs-/Ist-Status
            <select [ngModel]="facade.runtimeFilter()" (ngModelChange)="facade.runtimeFilter.set($event)">
              <option value="">Alle</option>
              @for (runtimeState of facade.runtimeStates; track runtimeState.value) { <option [value]="runtimeState.value">{{ runtimeState.label }}</option> }
            </select>
          </label>
          <label>Suche
            <input type="search" maxlength="128" [ngModel]="facade.search()" (ngModelChange)="facade.search.set($event)" />
          </label>
          <button type="button" class="secondary" (click)="facade.resetFilters()">Filter zurücksetzen</button>
        </section>

        <div class="flow" role="list" aria-label="Workflow-Schritte und Rollenaktivierung">
          @for (item of facade.visibleSteps(); track item.step.step_ref) {
            <article role="listitem" [attr.data-binding]="item.step.role_binding.state">
              <header>
                <div>
                  <span class="team">{{ item.team.team_name }} · {{ item.team.workflow.workflow_ref }}</span>
                  <h3>{{ item.step.title }}</h3>
                </div>
                <span class="task-kind">{{ item.step.task_kind }}</span>
              </header>

              <dl class="facts">
                <div><dt>Deklarierte Owner-Rolle</dt><dd><code>{{ item.step.owner_role_ref }}</code></dd></div>
                <div><dt>Aktivierungsbedingung</dt><dd>{{ reactsTo(item.step) }}</dd></div>
                <div><dt>Deklarierte Zielbindung</dt><dd>{{ targetState(item.step) }}</dd></div>
                <div><dt>Assignment-Abdeckung</dt><dd>{{ coverage(item.step) }}</dd></div>
              </dl>

              <dl class="runtime-facts" [attr.data-runtime-binding]="item.step.activation.runtime.binding.state">
                <div><dt>Lokale Task-Abhängigkeiten</dt><dd>{{ runtimeLabel(item.step.activation.runtime.task_ready.state) }}</dd></div>
                <div><dt>Hub routed</dt><dd>{{ runtimeLabel(item.step.activation.runtime.hub_routed.state) }}</dd></div>
                <div><dt>Worker executing</dt><dd>{{ runtimeLabel(item.step.activation.runtime.worker_executing.state) }}</dd></div>
                <div><dt>Runtime-Fakten</dt><dd>{{ item.step.activation.runtime.worker_job_count }} Job(s) · {{ item.step.activation.runtime.active_lease_count }} aktive Lease(s)</dd></div>
              </dl>

              <div class="io">
                <section><h4>Deklarierte Inputs</h4><p>{{ list(item.step.inputs) }}</p></section>
                <span aria-hidden="true">→</span>
                <section><h4>Deklarierte Outputs</h4><p>{{ list(item.step.outputs) }}</p></section>
              </div>

              <details class="why-role">
                <summary>Warum diese Rolle?</summary>
                <dl>
                  <div><dt>Hub-Regel</dt><dd>{{ item.step.activation.rule }} · deklarierter Fluss</dd></div>
                  <div><dt>Zielentscheidung</dt><dd>{{ item.step.target_resolution.reason_code }}</dd></div>
                  <div><dt>Rollenbindung</dt><dd>{{ item.step.role_binding.reason_code }}</dd></div>
                  <div><dt>Gebundene Rollenplätze</dt><dd>{{ list(item.step.role_binding.bound_role_slot_ids, 'keine') }}</dd></div>
                  <div><dt>Kandidaten-Rollenplätze</dt><dd>{{ list(item.step.role_binding.candidate_role_slot_ids, 'keine') }}</dd></div>
                  <div><dt>Externe Inputs</dt><dd>{{ list(item.step.activation.external_inputs, 'keine') }}</dd></div>
                  <div><dt>Deklarierte teamübergreifende Quellen</dt><dd>{{ inputSources(item.step) }}</dd></div>
                  <div><dt>Exakte Task-Bindung</dt><dd>{{ item.step.activation.runtime.binding.reason_code }} · {{ list(item.step.activation.runtime.binding.task_ids, 'keine') }}</dd></div>
                  <div><dt>Lokaler Readiness-Fakt</dt><dd>{{ item.step.activation.runtime.task_ready.reason_code }}</dd></div>
                  <div><dt>Hub-Routing-Fakt</dt><dd>{{ item.step.activation.runtime.hub_routed.reason_code }}</dd></div>
                  <div><dt>Worker-/Lease-Fakt</dt><dd>{{ item.step.activation.runtime.worker_executing.reason_code }}</dd></div>
                </dl>
                <p>Eine konkrete Assignment-ID wird von diesem Read-Model nicht offengelegt; sichtbar ist nur die gezählte Abdeckung.</p>
              </details>

              <footer>
                <span>Vorgänger-Definition: {{ list(item.step.depends_on, 'Workflow-Start durch den Hub') }}</span>
                @if (item.step.gate.required) {
                  <span class="gate">Gate: {{ item.step.gate.approval_role_ref || 'Hub' }} · {{ list(item.step.gate.acceptance_checks) }}</span>
                }
                @if (item.step.handoff_ref) { <span>Handoff: {{ item.step.handoff_ref }}</span> }
                <span>Deklarierter Fehlerpfad: {{ item.step.failure_policy }}</span>
              </footer>
            </article>
          } @empty {
            <p class="status">Keine Workflow-Schritte entsprechen den Filtern.</p>
          }
        </div>

        <section class="edges" aria-labelledby="activation-edges-heading">
          <h3 id="activation-edges-heading">Deklarierte Übergänge</h3>
          <div class="edge-grid">
            @for (edge of facade.visibleEdges(); track edge.edge_id) {
              <article>
                <strong>{{ edgeTypeLabel(edge.type) }}</strong>
                <code>{{ edge.source.ref }}</code><span aria-hidden="true">→</span><code>{{ edge.target.ref }}</code>
                @if (edge.type === 'declares_handoff') {
                  <small>{{ edge.metadata.relation_key }} · {{ edge.metadata.handoff_ref }} · {{ edge.metadata.dependency_policy }} · Gate {{ edge.metadata.acceptance_gate_ref }} · Artefakte {{ list(edge.metadata.required_artifact_kinds) }}</small>
                }
              </article>
            } @empty { <p>Keine Übergänge deklariert.</p> }
          </div>
          @if (facade.edgesTruncated()) {
            <p class="edge-limit">Die Darstellung ist auf 200 zu den sichtbaren Schritten gehörende Kanten begrenzt.</p>
          }
        </section>

        <section class="proposal-lane" aria-labelledby="proposal-lane-heading">
          <h3 id="proposal-lane-heading">Unverbindliche Worker-Proposals</h3>
          <p>Möglicher Hub-gesteuerter Pfad: Worker-Hinweis → Prüfung → optionales Track-Amendment → mögliche Task-Materialisierung. Der Hub kann den Vorschlag auch ablehnen oder zurückstellen.</p>
          @if (state.planning(); as planning) {
            @if (planning.organization_id === model.organization_id) {
            <ul>
              @for (proposal of planning.proposals; track proposal.proposal_id) {
                <li><code>{{ proposal.proposal_id }}</code> · {{ proposal.status }} · Quelle {{ proposal.source_task_id }} · Hub-Auswahl {{ proposal.selected_role_slot_id || 'noch offen' }}</li>
              } @empty { <li>Keine Proposals für diese Organisation.</li> }
            </ul>
            } @else {
              <p>Proposal-Lineage gehört nicht zur angezeigten Organisation und wird nicht dargestellt.</p>
            }
          } @else {
            <p>Proposal-Lineage wird über „Planung &amp; Proposals“ geladen.</p>
          }
        </section>
      }
    </section>
  `,
  styles: [`
    .activation { display: grid; gap: 1rem; }
    .heading { align-items: end; display: flex; gap: 1rem; justify-content: space-between; }
    h2, h3, h4, p { margin: 0; }
    .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    button { background: #2c70c8; border: 0; border-radius: .4rem; color: #fff; cursor: pointer; padding: .5rem .7rem; }
    button:disabled { opacity: .45; } button.secondary { background: #263a59; }
    .state-model { display: grid; gap: .45rem; grid-template-columns: repeat(5, minmax(0, 1fr)); list-style: none; margin: 0; padding: 0; }
    .state-model li { background: #0e1a2d; border: 1px solid #304769; border-radius: .55rem; display: grid; gap: .3rem; padding: .7rem; }
    .state-model span { color: #9db0cf; font-size: .75rem; }
    .binding { background: #0b1525; border: 1px solid #2d4160; border-radius: .6rem; display: flex; flex-wrap: wrap; gap: .8rem 1.2rem; padding: .65rem; }
    code { color: #9dc3ff; overflow-wrap: anywhere; }
    .runtime-note { background: #302813; border-left: 4px solid #e2ab45; color: #ffe0a3; padding: .7rem; }
    .runtime-observed { background: #123428; border-left: 4px solid #41c995; color: #c6f8e5; padding: .7rem; }
    .snapshot-warning { background: #401d26; border-left: 4px solid #e56c77; color: #ffc4ca; padding: .7rem; }
    .error { background: #401d26; border-left: 4px solid #e56c77; color: #ffc4ca; padding: .7rem; }
    .status { color: #91a5c4; padding: 1rem; text-align: center; }
    .filters { align-items: end; background: #0d1728; border: 1px solid #2c4161; border-radius: .6rem; display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); padding: .65rem; }
    label { color: #aebfda; display: grid; font-size: .72rem; gap: .25rem; }
    select, input { background: #101b2e; border: 1px solid #3c5275; border-radius: .35rem; color: #f3f7ff; min-width: 0; padding: .45rem; }
    .flow { display: grid; gap: .7rem; }
    .flow > article { background: #0e1a2d; border: 1px solid #304769; border-left: 5px solid #4b8fd8; border-radius: .65rem; display: grid; gap: .7rem; padding: .8rem; }
    .flow > article[data-binding='unavailable'] { border-left-color: #e06c75; }
    .flow > article[data-binding='candidate_only'] { border-left-color: #e2ab45; }
    .flow article > header { align-items: start; display: flex; gap: .7rem; justify-content: space-between; }
    .team { color: #8fa9cd; font-size: .68rem; } .task-kind { border: 1px solid #56749e; border-radius: 999px; font-size: .7rem; padding: .18rem .45rem; }
    .facts { display: grid; gap: .4rem; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; }
    .facts div { background: #111f34; padding: .45rem; } dt { color: #91a5c4; font-size: .66rem; } dd { margin: .15rem 0 0; overflow-wrap: anywhere; }
    .runtime-facts { display: grid; gap: .4rem; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; }
    .runtime-facts div { background: #0b2130; border-top: 2px solid #4b8fd8; padding: .45rem; }
    .runtime-facts[data-runtime-binding='unknown'] div { border-top-color: #e2ab45; }
    .io { align-items: center; display: grid; gap: .6rem; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); }
    .io section { background: #0b1525; border-radius: .4rem; padding: .55rem; }
    .io h4 { color: #91a5c4; font-size: .7rem; }
    .why-role { background: #0b1525; border: 1px solid #2d4160; border-radius: .4rem; padding: .5rem; }
    .why-role summary { cursor: pointer; font-weight: 700; }
    .why-role dl { display: grid; gap: .35rem; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: .55rem 0; }
    .why-role div { background: #111f34; padding: .4rem; }
    .why-role p { color: #91a5c4; font-size: .7rem; }
    .flow footer { color: #a8bad4; display: flex; flex-wrap: wrap; font-size: .72rem; gap: .45rem 1rem; }
    .gate { color: #ffd991; }
    .edges, .proposal-lane { background: #0b1525; border: 1px solid #2d4160; border-radius: .65rem; display: grid; gap: .55rem; padding: .75rem; }
    .edge-grid { display: grid; gap: .4rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .edge-grid article { background: #101f34; display: grid; gap: .25rem; grid-template-columns: auto minmax(0, 1fr) auto minmax(0, 1fr); padding: .45rem; }
    .edge-grid small { color: #9fb2d0; grid-column: 1 / -1; }
    .edge-limit { color: #ffd991; font-size: .75rem; }
    .proposal-lane ul { margin: 0; padding-left: 1.25rem; }
    @media (max-width: 1000px) { .state-model { grid-template-columns: repeat(2, minmax(0, 1fr)); } .facts, .runtime-facts, .why-role dl { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 620px) { .heading { align-items: stretch; flex-direction: column; } .state-model, .facts, .runtime-facts, .why-role dl { grid-template-columns: 1fr; } .io { grid-template-columns: 1fr; } .io > span { transform: rotate(90deg); text-align: center; } }
  `],
})
export class OrganizationRoleActivationComponent {
  readonly facade = inject(OrganizationRoleActivationFacade);
  readonly state = inject(OrganizationTopologyStateService);

  constructor() {
    effect(() => {
      const organizationId = this.state.selectedOrganizationId();
      const planningOrganizationId = this.state.planning()?.organization_id ?? null;
      untracked(() => {
        if (organizationId && planningOrganizationId !== organizationId) this.state.loadPlanning();
      });
    });
  }

  reactsTo(step: OrganizationRoleActivationStep): string {
    return step.activation.reacts_to.map(source => (
      source.kind === 'hub_workflow_intake'
        ? 'Workflow-Start durch den Hub'
        : `nach Abschluss von ${source.source_ref} (${source.source_owner_role_ref || 'Owner unbekannt'})`
    )).join(' · ');
  }

  targetState(step: OrganizationRoleActivationStep): string {
    const target = step.target_resolution;
    if (target.state === 'bound') return `Definition bindet ${target.bound_team_unit_ids.length} Team(s)`;
    if (target.state === 'hub_selection_required') return `Hub-Auswahl aus ${target.candidate_team_unit_ids.length} Kandidaten nötig`;
    return `${target.state} · ${target.reason_code}`;
  }

  coverage(step: OrganizationRoleActivationStep): string {
    const coverage = step.role_binding.assignment_coverage;
    return `${coverage.active_count}/${coverage.desired_count} aktive Assignments · ${coverage.state}`;
  }

  runtimeLabel(state: 'observed_true' | 'observed_false' | 'unknown'): string {
    return ({
      observed_true: 'ja · beobachtet',
      observed_false: 'nein · beobachtet',
      unknown: 'unbekannt',
    } as const)[state];
  }

  inputSources(step: OrganizationRoleActivationStep): string {
    const sources = step.activation.declared_input_sources ?? [];
    return sources.length
      ? sources.map(source => (
        `${source.source_owner_role_ref} via ${source.handoff_ref} (${source.relation_key}): ${source.artifacts.join(', ')}`
      )).join(' · ')
      : 'keine im Organisations-Read-Model deklariert';
  }

  list(values: readonly string[], empty = 'keine'): string {
    return values.length ? values.join(', ') : empty;
  }

  edgeTypeLabel(type: 'unblocks' | 'produces_input' | 'requires_gate' | 'declares_handoff'): string {
    return ({
      unblocks: 'deklariert Freigabe',
      produces_input: 'deklariert Inputfluss',
      requires_gate: 'deklariert Gate',
      declares_handoff: 'deklariert Handoff',
    } as const)[type];
  }
}
