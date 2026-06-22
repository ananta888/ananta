import { Component, Input, OnChanges, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

import { GenericGraphModel, GraphNode, GraphEdge } from '../../models/graph.model';
import { GraphViewMode } from '../../models/graph-view-mode';
import { GraphLayoutMode } from '../../models/graph-layout-mode';
import { GraphFilter } from '../../models/graph-filter.model';
import { GraphStateService } from '../../services/graph-state.service';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { GraphToolbarComponent } from '../graph-toolbar/graph-toolbar.component';
import { GraphDetailPanelComponent } from '../graph-detail-panel/graph-detail-panel.component';
import { SimpleGraphViewComponent } from '../simple-graph-view/simple-graph-view.component';
import { Graph2dViewComponent } from '../graph-2d-view/graph-2d-view.component';
import { Graph3dViewComponent } from '../graph-3d-view/graph-3d-view.component';

@Component({
  standalone: true,
  selector: 'app-graph-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    GraphToolbarComponent,
    GraphDetailPanelComponent,
    SimpleGraphViewComponent,
    Graph2dViewComponent,
    Graph3dViewComponent,
  ],
  template: `
    <div class="gv-shell">
      <app-graph-toolbar
        [activeMode]="state.viewMode()"
        [layoutMode]="layoutMode"
        [filter]="state.filter()"
        [webglAvailable]="webglAvailable"
        (viewModeChange)="setViewMode($event)"
        (layoutModeChange)="layoutMode = $event"
        (filterChange)="state.updateFilter($event)"
        (filterReset)="state.resetFilter()"
      />

      <div class="gv-body">
        <div class="gv-renderer">
          @switch (state.viewMode()) {
            @case ('simple') {
              <app-simple-graph-view
                [graph]="filteredGraph()"
                [layoutMode]="layoutMode"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="state.selectNode($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
            @case ('2d') {
              <app-graph-2d-view
                [graph]="filteredGraph()"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="state.selectNode($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
            @case ('3d') {
              <app-graph-3d-view
                [graph]="filteredGraph()"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="state.selectNode($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
          }
        </div>

        @if (state.selectedNode() || state.selectedEdge()) {
          <div class="gv-detail">
            <app-graph-detail-panel
              [selectedNode]="state.selectedNode()"
              [selectedEdge]="state.selectedEdge()"
              [focusActive]="!!state.focusNodeId()"
              [focusHopDepth]="state.focusHopDepth()"
              (closed)="state.clearSelection()"
              (focusRequested)="state.setFocus(state.selectedNode()!.id, $event)"
              (focusCleared)="state.setFocus(null)"
            />
          </div>
        }
      </div>

      @if (graph?.warnings?.length) {
        <div class="gv-warnings">
          @for (w of graph!.warnings; track w) {
            <p class="warning-msg">⚠ {{ w }}</p>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; flex: 1; min-height: 0; }
    .gv-shell { display: flex; flex-direction: column; flex: 1; min-height: 0; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; }
    .gv-body { display: flex; flex: 1; min-height: 0; overflow: hidden; }
    .gv-renderer { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
    .gv-detail { width: 320px; border-left: 1px solid #e2e8f0; overflow-y: auto; background: #fafafa; flex-shrink: 0; }
    .gv-warnings { padding: .5rem .75rem; background: #fef9c3; border-top: 1px solid #fde68a; flex-shrink: 0; }
    .warning-msg { margin: 0; font-size: .8rem; color: #92400e; }
  `],
})
export class GraphViewerComponent implements OnChanges, OnInit {
  @Input() rawGraphData: unknown = null;

  readonly state = inject(GraphStateService);
  private readonly adapter = inject(GraphAdapterService);

  graph: GenericGraphModel | null = null;
  webglAvailable = true;
  layoutMode: GraphLayoutMode = 'tier';

  ngOnInit(): void {
    try {
      const c = document.createElement('canvas');
      this.webglAvailable = !!(c.getContext('webgl') || c.getContext('experimental-webgl'));
    } catch {
      this.webglAvailable = false;
    }
    if (!this.webglAvailable && this.state.viewMode() === '3d') {
      this.state.setViewMode('2d');
    }
  }

  setViewMode(mode: import('../../models/graph-view-mode').GraphViewMode): void {
    if (mode === '3d' && !this.webglAvailable) return;
    this.state.setViewMode(mode);
  }

  ngOnChanges(): void {
    if (this.rawGraphData) {
      this.graph = this.adapter.fromDomainArtifact(this.rawGraphData);
      this.state.setGraph(this.graph);
    } else {
      this.graph = null;
      this.state.setGraph({ nodes: [], edges: [], metadata: { sourceRef: '', sourceKind: '', nodeCount: 0, edgeCount: 0 }, warnings: [] });
    }
  }

  filteredGraph(): GenericGraphModel | null {
    if (!this.graph) return null;
    return {
      ...this.graph,
      nodes: this.state.filteredNodes(),
      edges: this.state.filteredEdges(),
    };
  }
}
