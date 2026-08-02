import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';

import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-inspector',
  standalone: true,
  imports: [CommonModule],
  template: `
    <aside class="inspector" aria-labelledby="organization-inspector-heading">
      <header>
        <h2 id="organization-inspector-heading">Inspector</h2>
        <button type="button" (click)="state.inspectorOpen.set(false)" aria-label="Inspector schließen">×</button>
      </header>
      @if (state.selectedNode(); as node) {
        <p class="kind">{{ node.kind }}</p>
        <h3>{{ node.label }}</h3>
        <dl>
          <div><dt>Stabiler Schlüssel</dt><dd><code>{{ node.stable_key }}</code></dd></div>
          <div><dt>ID</dt><dd><code>{{ node.id }}</code></dd></div>
          <div><dt>Tiefe</dt><dd>{{ node.depth }}</dd></div>
          <div><dt>Kinder</dt><dd>{{ node.child_count }}{{ node.has_more_children ? '+' : '' }}</dd></div>
        </dl>
        @if (node.capabilities?.length) {
          <h4>Fähigkeiten</h4>
          <ul class="chips">@for (capability of node.capabilities; track capability) { <li>{{ capability }}</li> }</ul>
        }
        @if (state.selectedRuntime(); as runtime) {
          <h4>Laufzeitstatus</h4>
          <dl>
            <div><dt>Status</dt><dd>{{ runtime.status.label }}</dd></div>
            <div><dt>Blocker</dt><dd>{{ runtime.status.blocker_count || 0 }}</dd></div>
            <div><dt>Gates</dt><dd>{{ runtime.status.gate_count || 0 }}</dd></div>
            <div><dt>Handoffs</dt><dd>{{ runtime.status.handoff_count || 0 }}</dd></div>
            @if (runtime.status.capacity_limit) {
              <div><dt>Kapazität</dt><dd>{{ runtime.status.capacity_used || 0 }} / {{ runtime.status.capacity_limit }}</dd></div>
            }
          </dl>
          @if (runtime.latest_artifacts?.length) {
            <h4>Letzte Artefakte</h4>
            <ul class="artifacts">
              @for (artifact of runtime.latest_artifacts; track artifact.artifact_id + artifact.version) {
                <li><strong>{{ artifact.label }}</strong><small>v{{ artifact.version }} · {{ artifact.digest }}</small></li>
              }
            </ul>
          }
        }
        <div class="actions">
          <button type="button" (click)="state.setFocus(node.id)">Als Subgraph fokussieren</button>
          @if (state.focusedSubgraphId()) { <button type="button" (click)="state.setFocus(null)">Fokus lösen</button> }
        </div>
      } @else if (state.selectedEdge(); as edge) {
        <p class="kind">{{ edge.namespace }} · {{ edge.kind }}</p>
        <h3>{{ edge.label || 'Topologiekante' }}</h3>
        <dl>
          <div><dt>ID</dt><dd><code>{{ edge.id }}</code></dd></div>
          <div><dt>Quelle</dt><dd><code>{{ edge.source_id }}</code></dd></div>
          <div><dt>Ziel</dt><dd><code>{{ edge.target_id }}</code></dd></div>
          <div><dt>Schreibbarkeit</dt><dd>{{ edge.read_only === false ? 'Draft-Mutation erlaubt' : 'Read-only' }}</dd></div>
        </dl>
        <div class="actions">
          <button type="button" (click)="state.selectNode(edge.source_id)">Quellknoten auswählen</button>
          <button type="button" (click)="state.selectNode(edge.target_id)">Zielknoten auswählen</button>
        </div>
      } @else {
        <p class="empty">Wähle in Hierarchie oder Graph einen Knoten oder eine Kante. Beide Ansichten verwenden denselben Topologiestand.</p>
      }

      @if (state.topology()?.diagnostics?.length) {
        <h3>Diagnosen</h3>
        <ul class="diagnostics">
          @for (diagnostic of state.topology()!.diagnostics; track diagnostic.reason_code + diagnostic.message) {
            <li [attr.data-severity]="diagnostic.severity">
              {{ diagnostic.message }}
              <small>{{ diagnostic.reason_code }}</small>
            </li>
          }
        </ul>
      }
    </aside>
  `,
  styles: [`
    .inspector { background: #0d1728; border: 1px solid #2e4161; border-radius: .7rem; display: grid; gap: .75rem; max-height: 76vh; overflow: auto; padding: .85rem; }
    header { align-items: center; display: flex; justify-content: space-between; } h2, h3, h4, p { margin: 0; }
    header button { background: transparent; border: 0; color: #d8e5fa; cursor: pointer; font-size: 1.35rem; }
    .kind { color: #7fa7e4; font-size: .7rem; letter-spacing: .08em; text-transform: uppercase; }
    dl { display: grid; gap: .35rem; margin: 0; } dl div { background: #101e34; border-radius: .35rem; padding: .4rem; }
    dt { color: #91a5c7; font-size: .68rem; } dd { margin: .1rem 0 0; overflow-wrap: anywhere; }
    .chips, .artifacts, .diagnostics { display: flex; flex-wrap: wrap; gap: .35rem; list-style: none; margin: 0; padding: 0; }
    .chips li { background: #203756; border-radius: 999px; font-size: .7rem; padding: .25rem .45rem; }
    .artifacts, .diagnostics { display: grid; } .artifacts li, .diagnostics li { background: #101e34; border-left: 3px solid #5878a6; padding: .45rem; }
    small { color: #8da1c1; display: block; overflow-wrap: anywhere; }
    .diagnostics li[data-severity='blocker'] { border-color: #e86874; } .diagnostics li[data-severity='warning'] { border-color: #dca349; }
    .actions { display: flex; flex-wrap: wrap; gap: .4rem; } .actions button { background: #233b5d; border: 1px solid #496789; border-radius: .35rem; color: white; padding: .4rem .55rem; }
    .empty { color: #91a5c7; }
  `],
})
export class OrganizationInspectorComponent {
  readonly state = inject(OrganizationTopologyStateService);
}
