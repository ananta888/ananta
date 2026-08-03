import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { ForceGraph3dRendererComponent } from '../../graph-rendering/components/force-graph-3d-renderer.component';
import { OrganizationEdgeKind } from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationGraphAccessibleListComponent } from './organization-graph-accessible-list.component';
import { OrganizationGraphLegendComponent } from './organization-graph-legend.component';
import { OrganizationGraphProjectionService } from './organization-graph-projection.service';
import { OrganizationVisualProfileService } from './organization-visual-profile.service';
import { OrganizationVisualSettingsComponent } from './organization-visual-settings.component';

const HARD_NODE_RENDER_LIMIT = 500;
const HARD_EDGE_RENDER_LIMIT = 2_000;

@Component({
  selector: 'app-organization-graph-3d',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ForceGraph3dRendererComponent,
    OrganizationGraphAccessibleListComponent,
    OrganizationGraphLegendComponent,
    OrganizationVisualSettingsComponent,
  ],
  providers: [OrganizationGraphProjectionService],
  template: `
    <section class="graph-3d-panel" aria-labelledby="organization-graph-3d-heading">
      <header>
        <div>
          <p class="eyebrow">Read-only 3D topology</p>
          <h2 id="organization-graph-3d-heading">3D-Organisationsgraph</h2>
        </div>
        <p class="counts">{{ projection().graph.nodes.length }} Knoten · {{ projection().graph.edges.length }} Kanten</p>
      </header>

      <p class="policy-notice">
        Größe, Farbe und Kantenstärke sind ausschließlich visuelle Hinweise. Sie verändern keine Rechte,
        Priorität, Aktivierung oder das Routing des Hubs.
      </p>

      <p class="assignment-status" role="status">
        <strong>Rollenbesetzung:</strong>
        {{ projection().assignmentStatus.underAssignedRoleCount }} von
        {{ projection().assignmentStatus.observedRoleCount }} beobachteten Rollenplätzen unter Zielbelegung.
        @if (projection().assignmentStatus.unknownRoleCount) {
          Für {{ projection().assignmentStatus.unknownRoleCount }} Rollenplätze fehlt ein autoritativer Belegungsstatus.
        }
      </p>

      @if (state.topology()?.truncated) {
        <section class="limit server-limit" role="status">
          <span>Die API-Antwort ist paginiert; diese Ansicht enthält noch nicht die vollständige Organisation.</span>
          @if (state.canLoadMore()) {
            <button type="button" (click)="state.loadMore()" [disabled]="state.loading()">Nächste Seite laden</button>
          }
        </section>
      }

      @if (renderLimitExceeded()) {
        <p class="limit" role="status">
          Die vollständigen Daten bleiben in der Liste verfügbar. 3D wird oberhalb von
          {{ nodeRenderLimit() }} Knoten oder {{ edgeRenderLimit() }} Kanten bewusst nicht erzeugt;
          bitte Filter oder einen Fokus-Subgraph verwenden.
        </p>
      }

      <app-organization-visual-settings
        [roleTargets]="projection().roleTargets"
        [edgeKinds]="edgeKinds()"
      />

      <app-organization-graph-legend [projection]="projection()" />

      <div class="canvas">
        <app-force-graph-3d-renderer
          [graph]="projection().graph"
          [nodeStyles]="projection().nodeStyles"
          [edgeStyles]="projection().edgeStyles"
          [selectedNodeId]="state.selectedNodeId()"
          [selectedEdgeId]="state.selectedEdgeId()"
          [nodeRenderLimit]="nodeRenderLimit()"
          [edgeRenderLimit]="edgeRenderLimit()"
          limitStrategy="reject"
          (nodeSelected)="state.selectNode($event)"
          (edgeSelected)="state.selectEdge($event)"
          (selectionCleared)="clearSelection()"
          (availabilityChange)="webglAvailable.set($event)"
        />
      </div>

      <app-organization-graph-accessible-list
        [graph]="projection().graph"
        [selectedNodeId]="state.selectedNodeId()"
        [selectedEdgeId]="state.selectedEdgeId()"
        [open]="!webglAvailable() || renderLimitExceeded()"
        (nodeSelected)="state.selectNode($event)"
        (edgeSelected)="state.selectEdge($event)"
      />
    </section>
  `,
  styles: [`
    .graph-3d-panel { display: grid; gap: .7rem; min-width: 0; }
    header { align-items: end; display: flex; gap: 1rem; justify-content: space-between; }
    h2, p { margin: 0; }
    .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .counts { color: #9eb2d0; font-size: .78rem; white-space: nowrap; }
    .policy-notice { background: #13233a; border-left: 4px solid #60a5fa; color: #bfd0e8; font-size: .78rem; padding: .55rem .7rem; }
    .assignment-status { background: #0d1728; border: 1px solid #304665; color: #bfd0e8; font-size: .78rem; padding: .5rem .7rem; }
    .canvas { background: #0f172a; border: 1px solid #2e4264; border-radius: .7rem; height: min(68vh, 780px); min-height: 460px; overflow: hidden; position: relative; }
    app-force-graph-3d-renderer { height: 100%; width: 100%; }
    .limit { background: #3a2d15; border-left: 4px solid #e4ab45; color: #ffdda0; padding: .5rem .7rem; }
    .server-limit { align-items: center; display: flex; gap: .7rem; justify-content: space-between; }
    button { background: #2a6ec5; border: 1px solid #77a9e9; border-radius: .4rem; color: white; cursor: pointer; padding: .4rem .6rem; }
    button:disabled { cursor: default; opacity: .45; }
    button:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
    @media (max-width: 700px) { header, .server-limit { align-items: stretch; flex-direction: column; } .canvas { min-height: 390px; } }
  `],
})
export class OrganizationGraph3dComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly profiles = inject(OrganizationVisualProfileService);
  private readonly projector = inject(OrganizationGraphProjectionService);

  readonly webglAvailable = signal(true);
  readonly projection = computed(() => this.projector.project(
    this.state.visibleNodes(),
    this.state.visibleEdges(),
    this.state.topology()?.runtime_overlay ?? null,
    this.profiles.profile(),
  ));
  readonly nodeRenderLimit = computed(() => effectiveLimit(
    this.state.topology()?.limits.max_render_nodes,
    HARD_NODE_RENDER_LIMIT,
  ));
  readonly edgeRenderLimit = computed(() => effectiveLimit(
    this.state.topology()?.limits.max_render_edges,
    HARD_EDGE_RENDER_LIMIT,
  ));
  readonly renderLimitExceeded = computed(() => (
    this.projection().graph.nodes.length > this.nodeRenderLimit()
    || this.projection().graph.edges.length > this.edgeRenderLimit()
  ));
  readonly edgeKinds = computed<readonly OrganizationEdgeKind[]>(() => [...new Set(
    this.projection().graph.edges.map(edge => edge.kind as OrganizationEdgeKind),
  )].sort());

  clearSelection(): void {
    this.state.selectNode(null);
  }
}

function effectiveLimit(serverLimit: number | undefined, hardLimit: number): number {
  const value = Number(serverLimit);
  return Number.isFinite(value) && value > 0
    ? Math.min(Math.floor(value), hardLimit)
    : hardLimit;
}
