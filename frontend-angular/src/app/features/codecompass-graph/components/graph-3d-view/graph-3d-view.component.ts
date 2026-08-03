import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
} from '@angular/core';

import { ForceGraph3dRendererComponent } from '../../../graph-rendering/components/force-graph-3d-renderer.component';
import {
  RenderEdgeStyle,
  RenderGraph,
  RenderNodeStyle,
} from '../../../graph-rendering/models/render-graph.models';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';
import {
  EdgeVisualStyle,
  GraphVisualProjection,
  NodeVisualStyle,
} from '../../models/graph-visual-metrics.model';
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
      (selectionCleared)="selectionCleared.emit()"
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
  webglUnavailable = false;

  private readonly nodeMap = new Map<string, GraphNode>();
  private readonly edgeMap = new Map<string, GraphEdge>();

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['graph']) this.projectGraph();
    if (changes['graph'] || changes['visualProjection']) this.projectStyles();
  }

  selectNode(nodeId: string): void {
    const node = this.nodeMap.get(nodeId);
    if (node) this.nodeSelected.emit(node);
  }

  selectEdge(edgeId: string): void {
    const edge = this.edgeMap.get(edgeId);
    if (edge) this.edgeSelected.emit(edge);
  }

  private projectGraph(): void {
    this.nodeMap.clear();
    this.edgeMap.clear();
    if (!this.graph) {
      this.renderGraph = null;
      return;
    }
    this.graph.nodes.forEach(node => this.nodeMap.set(node.id, node));
    this.graph.edges.forEach(edge => this.edgeMap.set(edge.id, edge));
    this.renderGraph = {
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
    this.projectGraphTooltips();
  }

  private projectGraphTooltips(): void {
    if (!this.renderGraph) return;
    this.renderGraph = {
      nodes: this.renderGraph.nodes.map(node => ({
        ...node,
        tooltip: graphVisualTooltipText(node.label, this.nodeVisual(node.id)),
      })),
      edges: this.renderGraph.edges.map(edge => ({
        ...edge,
        tooltip: graphVisualTooltipText(edge.label, this.edgeVisual(edge.id)),
      })),
    };
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
