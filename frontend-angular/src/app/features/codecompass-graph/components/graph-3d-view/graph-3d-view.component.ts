import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
} from '@angular/core';

import { ForceGraph3dRendererComponent } from '../../../graph-rendering/components/force-graph-3d-renderer.component';
import {
  RenderEdgeStyle,
  RenderGraph,
  RenderNodeStyle,
} from '../../../graph-rendering/models/render-graph.models';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';
import { Graph3dLayoutMode } from '../../models/graph-3d-layout-mode';
import {
  EdgeVisualStyle,
  GraphVisualProjection,
  NodeVisualStyle,
} from '../../models/graph-visual-metrics.model';
import { Graph3dLayoutProjectionService } from '../../services/graph-3d-layout-projection.service';
import { graphVisualTooltipText } from '../graph-tooltip/graph-visual-tooltip';

@Component({
  standalone: true,
  selector: 'app-graph-3d-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ForceGraph3dRendererComponent],
  template: `
    <app-force-graph-3d-renderer
      [graph]="renderGraph"
      [nodeStyles]="renderNodeStyles"
      [edgeStyles]="renderEdgeStyles"
      [selectedNodeId]="selectedNode?.id ?? null"
      [focusedNodeId]="focusedNodeId"
      [selectedEdgeId]="selectedEdge?.id ?? null"
      [visibleNodeIds]="visibleNodeIds"
      [visibleEdgeIds]="visibleEdgeIds"
      [highlightedNodeIds]="highlightedNodeIds"
      [highlightedEdgeIds]="highlightedEdgeIds"
      [nodeRenderLimit]="nodeRenderLimit"
      [edgeRenderLimit]="edgeRenderLimit"
      limitStrategy="focus"
      (nodeSelected)="selectNode($event)"
      (edgeSelected)="selectEdge($event)"
      (selectionCleared)="clearFocusAndNotify()"
      (availabilityChange)="webglUnavailable = !$event"
    />
  `,
  styles: [`
    :host { display: flex; flex: 1; width: 100%; height: 100%; min-height: 0; position: relative; overflow: hidden; }
    app-force-graph-3d-renderer { flex: 1; min-height: 0; }
  `],
})
export class Graph3dViewComponent implements OnChanges {
  @Input() graph: GenericGraphModel | null = null;
  @Input() layoutMode: Graph3dLayoutMode = 'force';
  /**
   * Stable identity of the source/revision/scope whose local 3D interaction
   * state may be retained. A changed key is an explicit reset boundary.
   */
  @Input() interactionContextKey = '';
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;
  @Input() visualProjection: GraphVisualProjection | null = null;
  @Input() visibleNodeIds: ReadonlySet<string> | null = null;
  @Input() visibleEdgeIds: ReadonlySet<string> | null = null;
  @Input() highlightedNodeIds: ReadonlySet<string> = new Set();
  @Input() highlightedEdgeIds: ReadonlySet<string> = new Set();
  @Input() nodeRenderLimit: number | null = null;
  @Input() edgeRenderLimit: number | null = null;

  @Output() nodeSelected = new EventEmitter<GraphNode>();
  @Output() edgeSelected = new EventEmitter<GraphEdge>();
  @Output() selectionCleared = new EventEmitter<void>();

  renderGraph: RenderGraph | null = null;
  renderNodeStyles: Readonly<Record<string, Readonly<RenderNodeStyle>>> = {};
  renderEdgeStyles: Readonly<Record<string, Readonly<RenderEdgeStyle>>> = {};
  focusedNodeId: string | null = null;
  webglUnavailable = false;

  private readonly nodeMap = new Map<string, GraphNode>();
  private readonly edgeMap = new Map<string, GraphEdge>();
  private readonly layouts = inject(Graph3dLayoutProjectionService);
  private layoutSourceGraph: RenderGraph | null = null;

  ngOnChanges(changes: SimpleChanges): void {
    const interactionContextChanged = Boolean(
      changes['interactionContextKey']
      && changes['interactionContextKey'].previousValue
        !== changes['interactionContextKey'].currentValue,
    );
    if (interactionContextChanged) this.focusedNodeId = null;
    if (changes['graph']) {
      this.projectGraph();
    } else if (changes['layoutMode']) {
      this.projectLayout();
    }
    if (
      (changes['graph'] || changes['visibleNodeIds'])
      && this.focusedNodeId
      && this.visibleNodeIds
      && !this.visibleNodeIds.has(this.focusedNodeId)
    ) {
      this.focusedNodeId = null;
    }
    if (changes['graph'] || changes['visualProjection']) this.projectStyles();
    if (changes['visualProjection'] && !changes['graph']) this.projectGraphTooltips();
  }

  selectNode(nodeId: string): void {
    const node = this.nodeMap.get(nodeId);
    if (!node) return;
    this.focusedNodeId = this.focusedNodeId === nodeId ? null : nodeId;
    this.nodeSelected.emit(node);
  }

  selectEdge(edgeId: string): void {
    const edge = this.edgeMap.get(edgeId);
    if (edge) this.edgeSelected.emit(edge);
  }

  clearFocus(): void {
    this.focusedNodeId = null;
  }

  clearFocusAndNotify(): void {
    this.clearFocus();
    this.selectionCleared.emit();
  }

  private projectGraph(): void {
    const retainedFocus = this.focusedNodeId;
    this.nodeMap.clear();
    this.edgeMap.clear();
    if (!this.graph) {
      this.focusedNodeId = null;
      this.layoutSourceGraph = null;
      this.renderGraph = null;
      return;
    }
    this.graph.nodes.forEach(node => this.nodeMap.set(node.id, node));
    this.graph.edges.forEach(edge => this.edgeMap.set(edge.id, edge));
    this.focusedNodeId = retainedFocus && this.nodeMap.has(retainedFocus)
      ? retainedFocus
      : null;
    this.layoutSourceGraph = {
      nodes: this.graph.nodes.map(node => ({
        id: node.id,
        label: node.label,
        kind: node.rawNodeType ?? node.kind,
        tooltip: graphVisualTooltipText(node.label, this.nodeVisual(node.id)),
      })),
      edges: this.graph.edges.map(edge => ({
        id: edge.id,
        sourceId: edge.source,
        targetId: edge.target,
        kind: edge.rawEdgeType ?? edge.edgeType,
        label: edge.rawEdgeType ?? edge.edgeType,
        tooltip: graphVisualTooltipText(edge.rawEdgeType ?? edge.edgeType, this.edgeVisual(edge.id)),
      })),
    };
    this.projectLayout();
  }

  private projectLayout(): void {
    this.renderGraph = this.layoutSourceGraph
      ? this.layouts.project(this.layoutSourceGraph, this.layoutMode)
      : null;
  }

  private projectStyles(): void {
    if (!this.graph) {
      this.renderNodeStyles = {};
      this.renderEdgeStyles = {};
      return;
    }
    this.renderNodeStyles = Object.fromEntries(this.graph.nodes.map(node => {
      const visual = this.nodeVisual(node.id);
      return [node.id, {
        color: visual.baseColor,
        size: visual.baseSize,
        highlightFactors: visual.highlightFactors,
      } satisfies RenderNodeStyle];
    }));
    this.renderEdgeStyles = Object.fromEntries(this.graph.edges.map(edge => {
      const visual = this.edgeVisual(edge.id);
      return [edge.id, {
        color: visual.baseColor,
        width: visual.baseThickness,
        highlightFactors: visual.highlightFactors,
      } satisfies RenderEdgeStyle];
    }));
  }

  private projectGraphTooltips(): void {
    const project = (graph: RenderGraph): RenderGraph => ({
      nodes: graph.nodes.map(node => ({
        ...node,
        tooltip: graphVisualTooltipText(node.label, this.nodeVisual(node.id)),
      })),
      edges: graph.edges.map(edge => ({
        ...edge,
        tooltip: graphVisualTooltipText(edge.label, this.edgeVisual(edge.id)),
      })),
    });
    if (this.layoutSourceGraph) this.layoutSourceGraph = project(this.layoutSourceGraph);
    if (this.renderGraph) this.renderGraph = project(this.renderGraph);
  }

  private nodeVisual(id: string): Readonly<NodeVisualStyle> {
    return this.visualProjection?.nodeStyles[id] ?? {
      nodeId: id,
      baseColor: '#64748b',
      marker: 'circle',
      baseSize: 5,
      score: 0,
      scoreState: 'degraded_no_active_metric',
      availability: 'unavailable',
      breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    };
  }

  private edgeVisual(id: string): Readonly<EdgeVisualStyle> {
    return this.visualProjection?.edgeStyles[id] ?? {
      edgeId: id,
      baseColor: '#94a3b8',
      marker: 'triangle',
      baseThickness: 1,
      score: 0,
      scoreState: 'degraded_no_active_metric',
      availability: 'unavailable',
      breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    };
  }
}
