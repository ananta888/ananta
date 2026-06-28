import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
  computed,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  ChTopologyReadModel,
  ChHubInstanceReadModel,
  ChWorkerInstanceReadModel,
  ChTestLayerReadModel,
  ChRoutingRuleReadModel,
  ChAgentRunReadModel,
  ChAgentStepReadModel,
  ChCliBackend,
} from '../models/codehug.models';

import {
  ChNodeKind,
  ChNodeRunState,
  ChCanvasNode,
  ChCanvasEdge,
  buildTopologyGraph,
} from './codehug-topology-layout';
import {
  badgeFill,
  edgePath as buildEdgePath,
  kindLabel,
  nodeFilter,
  nodeStyle,
  runStateLabel,
} from './codehug-canvas-presentation';

export type {
  ChNodeKind,
  ChNodeRunState,
  ChCanvasNode,
  ChCanvasEdge,
} from './codehug-topology-layout';

@Component({
  selector: 'ch-canvas',
  standalone: true,
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-canvas.component.html',
  styleUrls: ['./codehug-canvas.component.scss'],
})
export class CodeHugCanvasComponent implements AfterViewInit, OnChanges {
  @ViewChild('svgEl', { static: false }) svgRef!: ElementRef<SVGSVGElement>;

  @Input() topology: ChTopologyReadModel | null = null;
  @Input() activeRun: ChAgentRunReadModel | null = null;
  @Input() writeModeActive = false;

  @Output() refreshRequested = new EventEmitter<void>();
  @Output() layerToggled = new EventEmitter<{ layer: ChTestLayerReadModel; enabled: boolean }>();
  @Output() ruleChanged = new EventEmitter<{ rule: ChRoutingRuleReadModel; newBackend: ChCliBackend }>();

  readonly nodes = signal<ChCanvasNode[]>([]);
  readonly edges = signal<ChCanvasEdge[]>([]);
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedNode = computed(() => this.nodes().find(n => n.id === this.selectedNodeId()) ?? null);

  readonly panX = signal(40);
  readonly panY = signal(30);
  readonly zoom = signal(1);
  readonly isPanning = signal(false);

  readonly viewportTransform = computed(() =>
    `translate(${this.panX()},${this.panY()}) scale(${this.zoom()})`
  );
  readonly gridTransform = computed(() =>
    `translate(${this.panX() % 24},${this.panY() % 24})`
  );

  readonly activeRunStep = computed((): ChAgentStepReadModel | null => {
    const node = this.selectedNode();
    const run = this.activeRun;
    if (!node || !run) return null;
    const workerId = this.workerIdFromNodeId(node.id);
    if (!workerId) return null;
    return run.steps.find(s => s.workerId === workerId && s.status === 'running') ?? null;
  });

  readonly backends: ChCliBackend[] = ['sgpt', 'opencode', 'codex', 'claude_code', 'aider', 'mistral', 'deterministic'];

  private _draggingNodeId: string | null = null;
  private _didDrag = false;
  private _dragStartSvgX = 0;
  private _dragStartSvgY = 0;
  private _dragOrigNodeX = 0;
  private _dragOrigNodeY = 0;
  private _panStartX = 0;
  private _panStartY = 0;
  private _panOrigX = 0;
  private _panOrigY = 0;
  private _isPanDragging = false;

  ngAfterViewInit(): void {
    if (this.topology) this.rebuildCanvas();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['topology'] && this.topology) {
      this.rebuildCanvas();
    }
    if (changes['activeRun'] && this.activeRun) {
      this.applyRunState();
    }
  }

  private rebuildCanvas(): void {
    if (!this.topology) return;
    const { nodes, edges } = buildTopologyGraph(this.topology);
    this.nodes.set(nodes);
    this.edges.set(edges);
    if (this.activeRun) this.applyRunState();
  }

  private applyRunState(): void {
    const run = this.activeRun;
    if (!run) return;
    const runningSteps = run.steps.filter(s => s.status === 'running');
    const completedSteps = run.steps.filter(s => s.status === 'succeeded');
    const failedSteps = run.steps.filter(s => s.status === 'failed');

    this.nodes.update(nodes => nodes.map(n => {
      const workerId = this.workerIdFromNodeId(n.id);
      if (!workerId) return n;
      let runState: ChNodeRunState = 'idle';
      if (runningSteps.some(s => s.workerId === workerId)) runState = 'active';
      else if (failedSteps.some(s => s.workerId === workerId)) runState = 'failed';
      else if (completedSteps.some(s => s.workerId === workerId)) runState = 'completed';
      return { ...n, runState };
    }));

    this.nodes.update(nodes => nodes.map(n => {
      if (n.kind !== 'hub') return n;
      const state: ChNodeRunState = run.status === 'running' ? 'active' : run.status === 'succeeded' ? 'completed' : run.status === 'failed' ? 'failed' : 'idle';
      return { ...n, runState: state };
    }));
  }

  private workerIdFromNodeId(nodeId: string): string | null {
    if (nodeId.startsWith('worker::')) return nodeId.slice('worker::'.length);
    return null;
  }

  // ── Viewport / Interaction ────────────────────────────────────────────────

  zoomIn(): void { this.zoom.update(z => Math.min(2.5, z + 0.15)); }
  zoomOut(): void { this.zoom.update(z => Math.max(0.2, z - 0.15)); }
  resetView(): void { this.panX.set(40); this.panY.set(30); this.zoom.set(1); }
  fitToContent(): void {
    const ns = this.nodes();
    if (ns.length === 0) return;
    const maxX = Math.max(...ns.map(n => n.x + n.w));
    const maxY = Math.max(...ns.map(n => n.y + n.h));
    const svgEl = this.svgRef?.nativeElement;
    if (!svgEl) return;
    const { width, height } = svgEl.getBoundingClientRect();
    const zx = (width - 80) / maxX;
    const zy = (height - 80) / maxY;
    const z = Math.min(1, Math.min(zx, zy));
    this.zoom.set(z);
    this.panX.set(40);
    this.panY.set(40);
  }

  clearSelection(): void { this.selectedNodeId.set(null); }

  @HostListener('document:mousemove', ['$event'])
  onMousemove(e: MouseEvent): void {
    if (this._draggingNodeId) {
      const svgPt = this.toSvgCoords(e);
      const dx = (svgPt.x - this._dragStartSvgX) / this.zoom();
      const dy = (svgPt.y - this._dragStartSvgY) / this.zoom();
      const newX = this._dragOrigNodeX + dx;
      const newY = this._dragOrigNodeY + dy;
      this._didDrag = true;
      this.nodes.update(ns => ns.map(n =>
        n.id === this._draggingNodeId ? { ...n, x: Math.max(0, newX), y: Math.max(0, newY) } : n
      ));
    } else if (this._isPanDragging) {
      this.panX.set(this._panOrigX + (e.clientX - this._panStartX));
      this.panY.set(this._panOrigY + (e.clientY - this._panStartY));
    }
  }

  @HostListener('document:mouseup')
  onMouseup(): void {
    this._draggingNodeId = null;
    this._isPanDragging = false;
    this.isPanning.set(false);
    // _didDrag is cleared by onNodeClick after suppression check
  }

  onSvgMousedown(e: MouseEvent): void {
    if ((e.target as SVGElement).closest('.ch-cv-node')) return;
    this._isPanDragging = true;
    this._panStartX = e.clientX;
    this._panStartY = e.clientY;
    this._panOrigX = this.panX();
    this._panOrigY = this.panY();
    this.isPanning.set(true);
    e.preventDefault();
  }

  onNodeMousedown(e: MouseEvent, node: ChCanvasNode): void {
    e.stopPropagation();
    const svgPt = this.toSvgCoords(e);
    this._draggingNodeId = node.id;
    this._didDrag = false;
    this._dragStartSvgX = svgPt.x;
    this._dragStartSvgY = svgPt.y;
    this._dragOrigNodeX = node.x;
    this._dragOrigNodeY = node.y;
  }

  onNodeClick(node: ChCanvasNode): void {
    if (this._didDrag) { this._didDrag = false; return; }
    this.selectedNodeId.set(node.id === this.selectedNodeId() ? null : node.id);
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    const newZoom = Math.min(2.5, Math.max(0.2, this.zoom() + delta));
    this.zoom.set(newZoom);
  }

  nodeTransform(node: ChCanvasNode): string {
    return `translate(${node.x}, ${node.y})`;
  }

  readonly nodeFilter = nodeFilter;

  edgePath(edge: ChCanvasEdge): { d: string; labelX: number; labelY: number } | null {
    return buildEdgePath(edge, this.nodes());
  }

  readonly getStyle = nodeStyle;
  readonly badgeFill = badgeFill;
  readonly badgeText = () => '#fff';
  readonly kindLabel = kindLabel;
  readonly runStateLabel = runStateLabel;

  private toSvgCoords(e: MouseEvent): { x: number; y: number } {
    const svgEl = this.svgRef?.nativeElement;
    if (!svgEl) return { x: e.clientX, y: e.clientY };
    const rect = svgEl.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  asHub(p: unknown): ChHubInstanceReadModel { return p as ChHubInstanceReadModel; }
  asWorker(p: unknown): ChWorkerInstanceReadModel { return p as ChWorkerInstanceReadModel; }
  asLayer(p: unknown): ChTestLayerReadModel { return p as ChTestLayerReadModel; }
  asRule(p: unknown): ChRoutingRuleReadModel { return p as ChRoutingRuleReadModel; }

  hasKeys(obj: Record<string, unknown>): boolean { return Object.keys(obj).length > 0; }
  stringify(v: unknown): string {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }

  onLayerToggle(layer: ChTestLayerReadModel, enabled: boolean): void {
    this.layerToggled.emit({ layer, enabled });
    const updatedLayer: ChTestLayerReadModel = { ...layer, enabled };
    this.nodes.update(ns => ns.map(n =>
      n.id === `layer::${layer.id}`
        ? ({ ...n, payload: updatedLayer, sublabel: `order ${layer.order}${enabled ? '' : ' · deaktiviert'}`, badge: enabled ? 'on' : 'off' } satisfies ChCanvasNode)
        : n
    ));
  }

  onRuleChange(rule: ChRoutingRuleReadModel, newBackend: string): void {
    const backend = newBackend as ChCliBackend;
    this.ruleChanged.emit({ rule, newBackend: backend });
    const updatedRule: ChRoutingRuleReadModel = { ...rule, selectedBackend: backend };
    this.nodes.update(ns => ns.map(n =>
      n.id === `rule::${rule.id}`
        ? ({ ...n, payload: updatedRule, sublabel: `→ ${newBackend}` } satisfies ChCanvasNode)
        : n
    ));
  }
}
