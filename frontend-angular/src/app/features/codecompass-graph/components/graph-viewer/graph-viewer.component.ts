import { Component, Input, OnChanges, OnInit, SimpleChanges, inject, signal, computed, ChangeDetectionStrategy } from '@angular/core';

import { Router } from '@angular/router';

import { GenericGraphModel, GraphNode } from '../../models/graph.model';
import { GraphLayoutMode } from '../../models/graph-layout-mode';
import { GraphStateService } from '../../services/graph-state.service';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { GraphToolbarComponent } from '../graph-toolbar/graph-toolbar.component';
import { GraphDetailPanelComponent } from '../graph-detail-panel/graph-detail-panel.component';
import { FileDiffPanelComponent } from '../file-diff-panel/file-diff-panel.component';
import { WikiArticlePanelComponent } from '../wiki-article-panel/wiki-article-panel.component';
import { SimpleGraphViewComponent } from '../simple-graph-view/simple-graph-view.component';
import { Graph2dViewComponent } from '../graph-2d-view/graph-2d-view.component';
import { Graph3dViewComponent } from '../graph-3d-view/graph-3d-view.component';
import { GraphVisualProfileFacade } from '../../services/graph-visual-profile.facade';
import { GraphVisualProjectionService } from '../../services/graph-visual-projection.service';
import { GraphColorService } from '../../services/graph-color.service';
import { GraphDomainLegendComponent } from '../graph-legend/graph-domain-legend.component';
import { GraphEdgeLegendComponent } from '../graph-legend/graph-edge-legend.component';
import { GraphVisualSettingsComponent } from '../graph-visual-settings/graph-visual-settings.component';
import {
  presentDomainLegend,
  presentEdgeWidthLegend,
  presentNodeSizeLegend,
  presentRelationLegend,
} from '../graph-legend/graph-legend.presentation';

@Component({
  standalone: true,
  selector: 'app-graph-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  providers: [GraphStateService, GraphVisualProfileFacade],
  imports: [
    GraphToolbarComponent,
    GraphDetailPanelComponent,
    FileDiffPanelComponent,
    WikiArticlePanelComponent,
    SimpleGraphViewComponent,
    Graph2dViewComponent,
    Graph3dViewComponent,
    GraphDomainLegendComponent,
    GraphEdgeLegendComponent,
    GraphVisualSettingsComponent,
],
  template: `
    <div class="gv-shell" data-testid="codecompass-graph-viewer">
      <app-graph-toolbar
        [activeMode]="state.viewMode()"
        [layoutMode]="layoutMode"
        [filter]="state.filter()"
        [nodeKinds]="state.nodeKindInventory()"
        [edgeTypes]="state.edgeTypeInventory()"
        [domainOptions]="domainFilterOptions()"
        [neighborhoodDepth]="state.focusHopDepth()"
        [neighborhoodAnchorLabel]="state.focusNodeLabel()"
        [webglAvailable]="webglAvailable"
        (viewModeChange)="setViewMode($event)"
        (layoutModeChange)="layoutMode = $event"
        (filterChange)="state.updateFilter($event)"
        (filterReset)="state.resetFilter()"
        (neighborhoodDepthChange)="state.setNeighborhoodDepth($event)"
      />

      @if (graphWindowInfo(); as window) {
        <div class="gv-window-info" data-testid="graph-window-info">
          <strong>Geladener Topologie-Ausschnitt:</strong>
          {{ window.loadedNodes }} von {{ window.totalNodes }} Knoten ·
          {{ window.loadedEdges }} interne Kanten von {{ window.totalEdges }} insgesamt.
          Filter und Verknüpfungstiefe wirken innerhalb dieses Ausschnitts.
        </div>
      }

      <div class="gv-visual-controls" aria-label="Graphvisualisierung">
        @if (profiles.activeProfile().legend.showDomains) {
          <app-graph-domain-legend
            [open]="domainLegendOpen()"
            [entries]="domainLegend()"
            [nodeSizeLegend]="nodeSizeLegend()"
            [showNodeSize]="state.viewMode() !== 'simple'"
            (openChange)="openDomainLegend($event)"
            (domainVisibilityChange)="state.setDomainVisible($event.id, $event.visible)"
            (domainHovered)="state.hoveredDomainId.set($event)"
            (clearHighlight)="state.clearHover()"
          />
        }
        @if (profiles.activeProfile().legend.showRelations) {
          <app-graph-edge-legend
            [open]="edgeLegendOpen()"
            [entries]="edgeLegend()"
            [widthLegend]="edgeWidthLegend()"
            [showEdgeWidth]="state.viewMode() !== 'simple'"
            (openChange)="openEdgeLegend($event)"
            (relationVisibilityChange)="state.setEdgeTypeVisible($event.id, $event.visible)"
            (relationHovered)="state.hoveredRawEdgeType.set($event)"
            (clearHighlight)="state.clearHover()"
          />
        }
        <app-graph-visual-settings
          [open]="settingsOpen()"
          [metricCapabilities]="metricCapabilities()"
          [domainOptions]="domainColorOptions()"
          (openChange)="openSettings($event)"
        />
      </div>

      <div class="gv-body">
        <div class="gv-renderer">
          @switch (state.viewMode()) {
            @case ('simple') {
              <app-simple-graph-view
                [graph]="state.filteredGraph()"
                [visualProjection]="visualProjection()"
                [highlightedNodeIds]="highlightedNodeIds()"
                [highlightedEdgeIds]="highlightedEdgeIds()"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="onNodeSelectedSimple($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
            @case ('2d') {
              <app-graph-2d-view
                [graph]="state.graph()"
                [visualProjection]="visualProjection()"
                [visibleNodeIds]="visibleNodeIds()"
                [visibleEdgeIds]="visibleEdgeIds()"
                [highlightedNodeIds]="highlightedNodeIds()"
                [highlightedEdgeIds]="highlightedEdgeIds()"
                [layoutMode]="layoutMode"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="state.selectNode($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
            @case ('3d') {
              <app-graph-3d-view
                [graph]="state.graph()"
                [visualProjection]="visualProjection()"
                [visibleNodeIds]="visibleNodeIds()"
                [visibleEdgeIds]="visibleEdgeIds()"
                [highlightedNodeIds]="highlightedNodeIds()"
                [highlightedEdgeIds]="highlightedEdgeIds()"
                [selectedNode]="state.selectedNode()"
                [selectedEdge]="state.selectedEdge()"
                (nodeSelected)="state.selectNode($event)"
                (edgeSelected)="state.selectEdge($event)"
              />
            }
          }
        </div>

        @if (diff3File()) {
          <div class="gv-diff3">
            <app-file-diff-panel
              [filePath]="diff3File()!"
              (closed)="diff3File.set(null)"
            />
          </div>
        } @else if (wikiNode()) {
          <div class="gv-diff3">
            <app-wiki-article-panel
              [nodeId]="wikiNode()!.id"
              [title]="wikiNode()!.label"
              [indexId]="wikiIndexId"
              (closed)="wikiNode.set(null)"
            />
          </div>
        } @else if (state.selectedNode() || state.selectedEdge()) {
          <div class="gv-detail">
            <app-graph-detail-panel
              [selectedNode]="state.selectedNode()"
              [selectedEdge]="state.selectedEdge()"
              [focusActive]="!!state.focusNodeId()"
              [focusHopDepth]="state.focusHopDepth()"
              (closed)="state.clearSelection()"
              (focusRequested)="state.setFocus(state.selectedNode()!.id, $event)"
              (focusCleared)="state.setFocus(null, 0)"
              (diff3Requested)="openDiff3()"
              (wikiArticleRequested)="openWikiArticle()"
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
    .gv-window-info { padding: .35rem .65rem; border-bottom: 1px solid #bae6fd; background: #f0f9ff; color: #0c4a6e; font-size: .75rem; line-height: 1.35; }
    .gv-visual-controls { display: flex; justify-content: flex-end; align-items: center; gap: .35rem; padding: .25rem .55rem; background: #f8fafc; border-bottom: 1px solid #e2e8f0; flex-shrink: 0; }
    .gv-body { display: flex; flex: 1; min-height: 0; overflow: hidden; }
    .gv-renderer { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
    .gv-detail { width: 320px; border-left: 1px solid #e2e8f0; overflow-y: auto; background: #fafafa; flex-shrink: 0; }
    .gv-diff3 { width: 480px; border-left: 1px solid #30363d; flex-shrink: 0; overflow: hidden; display: flex; flex-direction: column; }
    .gv-warnings { padding: .5rem .75rem; background: #fef9c3; border-top: 1px solid #fde68a; flex-shrink: 0; }
    .warning-msg { margin: 0; font-size: .8rem; color: #92400e; }
  `],
})
export class GraphViewerComponent implements OnChanges, OnInit {
  @Input() rawGraphData: unknown = null;
  @Input() wikiIndexId = '';

  readonly state = inject(GraphStateService);
  readonly profiles = inject(GraphVisualProfileFacade);
  private readonly adapter = inject(GraphAdapterService);
  private readonly router  = inject(Router);
  private readonly projection = inject(GraphVisualProjectionService);
  private readonly colors = inject(GraphColorService);

  readonly diff3File = signal<string | null>(null);
  readonly wikiNode  = signal<{id: string; label: string} | null>(null);
  readonly domainLegendOpen = signal(false);
  readonly edgeLegendOpen = signal(false);
  readonly settingsOpen = signal(false);

  readonly metricCapabilities = computed(() => this.state.graph()?.metadata.metricCapabilities ?? []);
  readonly baseVisualProjection = computed(() => {
    const graph = this.state.graph();
    return graph ? this.projection.project(graph, this.profiles.activeProfile()) : null;
  });
  readonly visibleNodeIds = computed<ReadonlySet<string>>(() =>
    new Set(this.state.filteredNodes().map(node => node.id)),
  );
  readonly visibleEdgeIds = computed<ReadonlySet<string>>(() =>
    new Set(this.state.filteredEdges().map(edge => edge.id)),
  );
  readonly visualProjection = computed(() => {
    const graph = this.state.graph();
    const base = this.baseVisualProjection();
    if (!graph || !base) return null;
    return this.projection.withVisibility(
      base,
      graph,
      this.visibleNodeIds(),
      this.visibleEdgeIds(),
    );
  });
  readonly domainLegend = computed(() => presentDomainLegend(
    this.visualProjection()?.domainLegend ?? [],
    this.state.filter().domains,
  ));
  readonly edgeLegend = computed(() => presentRelationLegend(
    this.visualProjection()?.relationLegend ?? [],
    this.state.filter().edgeTypes,
  ));
  readonly nodeSizeLegend = computed(() => presentNodeSizeLegend(
    this.profiles.activeProfile(),
    this.metricCapabilities(),
    Object.values(this.baseVisualProjection()?.nodeStyles ?? {}).map(style => style.baseSize),
  ));
  readonly edgeWidthLegend = computed(() => presentEdgeWidthLegend(
    this.profiles.activeProfile(),
    this.metricCapabilities(),
    this.representativeEdgeBreakdown(),
    Object.values(this.baseVisualProjection()?.edgeStyles ?? {}).map(style => style.baseThickness),
  ));
  readonly representativeEdgeBreakdown = computed(() => {
    const projection = this.baseVisualProjection();
    if (!projection) return [];
    const selectedId = this.state.selectedEdge()?.id;
    if (selectedId && projection.edgeStyles[selectedId]) return projection.edgeStyles[selectedId].breakdown;
    const styles = Object.values(projection.edgeStyles)
      .sort((left, right) => left.baseThickness - right.baseThickness || left.edgeId.localeCompare(right.edgeId));
    return styles[Math.floor(styles.length / 2)]?.breakdown ?? [];
  });
  readonly domainColorOptions = computed(() => this.domainLegend().map(entry => ({
    domainId: entry.domainId,
    label: entry.label,
    color: entry.color,
  })));
  readonly domainFilterOptions = computed(() => this.domainLegend().map(entry => ({
    domainId: entry.domainId,
    label: entry.label,
    nodeCount: entry.totalNodes,
  })));
  readonly graphWindowInfo = computed(() => {
    const graph = this.state.graph();
    if (!graph) return null;
    const totalNodes = this.metadataCount(graph.metadata['total_nodes']);
    const totalEdges = this.metadataCount(graph.metadata['total_edges']);
    const topologyView = graph.metadata['view'] === 'topology';
    if (!topologyView && totalNodes <= graph.nodes.length && totalEdges <= graph.edges.length) {
      return null;
    }
    return {
      loadedNodes: graph.nodes.length.toLocaleString('de-DE'),
      totalNodes: Math.max(totalNodes, graph.nodes.length).toLocaleString('de-DE'),
      loadedEdges: graph.edges.length.toLocaleString('de-DE'),
      totalEdges: Math.max(totalEdges, graph.edges.length).toLocaleString('de-DE'),
    };
  });
  readonly highlightedNodeIds = computed<ReadonlySet<string>>(() => {
    const domainId = this.state.hoveredDomainId();
    const graph = this.state.graph();
    if (!domainId || !graph) return new Set();
    return new Set(graph.nodes
      .filter(node => this.colors.resolveCanonicalDomain(node).canonicalId === domainId)
      .map(node => node.id));
  });
  readonly highlightedEdgeIds = computed<ReadonlySet<string>>(() => {
    const relation = this.state.hoveredRawEdgeType();
    const graph = this.state.graph();
    if (!relation || !graph) return new Set();
    return new Set(graph.edges
      .filter(edge => (edge.rawEdgeType ?? edge.edgeType) === relation)
      .map(edge => edge.id));
  });

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

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['rawGraphData']) return;
    if (this.rawGraphData) {
      this.graph = this.adapter.fromDomainArtifact(this.rawGraphData);
      this.state.setGraph(this.graph);
      this.profiles.load(`${this.graph.metadata.sourceKind}:${this.graph.metadata.sourceRef}:${this.graph.metadata.graphRevision ?? 'legacy'}`);
    } else {
      this.graph = null;
      this.state.setGraph({ nodes: [], edges: [], metadata: { sourceRef: '', sourceKind: '', nodeCount: 0, edgeCount: 0 }, warnings: [] });
    }
  }

  onNodeSelectedSimple(node: GraphNode): void {
    this.state.selectNode(node);
    if (node.file) {
      this.router.navigate(['/diff3'], { queryParams: { file: node.file } });
    }
  }

  openDiff3(): void {
    const file = this.state.selectedNode()?.file;
    if (file) { this.wikiNode.set(null); this.diff3File.set(file); }
  }

  openWikiArticle(): void {
    const node = this.state.selectedNode();
    if (node?.kind === 'wiki_article') {
      this.diff3File.set(null);
      this.wikiNode.set({ id: node.id, label: node.label });
    }
  }

  openDomainLegend(open: boolean): void {
    this.domainLegendOpen.set(open);
    if (open) {
      this.edgeLegendOpen.set(false);
      this.settingsOpen.set(false);
    }
  }

  openEdgeLegend(open: boolean): void {
    this.edgeLegendOpen.set(open);
    if (open) {
      this.domainLegendOpen.set(false);
      this.settingsOpen.set(false);
    }
  }

  openSettings(open: boolean): void {
    this.settingsOpen.set(open);
    if (open) {
      this.domainLegendOpen.set(false);
      this.edgeLegendOpen.set(false);
    }
  }

  private metadataCount(value: unknown): number {
    const count = Number(value);
    return Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0;
  }
}
