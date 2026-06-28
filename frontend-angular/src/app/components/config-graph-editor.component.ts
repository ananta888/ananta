import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';
import { ConfigGraphService } from '../services/config-graph.service';
import {
  ConfigGraph,
  ConfigGraphEdge,
  ConfigGraphNode,
  PatchOp,
  ValidationResult,
  VIEW_IDS,
  ViewId,
  nodeColor,
} from '../models/config-graph.model';
import { ConfigNodeDetailComponent, CreatableConfigType } from './config-node-detail.component';
import { ConfigGraphNodeDetailComponent } from './config-graph-node-detail.component';
import { ConfigEffectiveConfigComponent } from './config-effective-config.component';
import {
  COL_GAP, NODE_H, NODE_W,
  POLICY_PATH_SUGGESTIONS, ROW_GAP, VIEWS, VIEW_PRIMARY_TYPES,
  type ConnectedNode, type GraphStatusFilter, type LayoutNode, type ViewMeta,
} from './config-graph-editor.models';
import {
  allFieldsFor as buildAllFields,
  characterBadge as buildCharacterBadge,
  keyFieldsFor as buildKeyFields,
} from './config-node-detail.helpers';
@Component({
  standalone: true,
  selector: 'app-config-graph-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ConfigNodeDetailComponent, ConfigGraphNodeDetailComponent, ConfigEffectiveConfigComponent],
  templateUrl: './config-graph-editor.component.html',
  styleUrls: ['./config-graph-editor.component.scss'],
})
export class ConfigGraphEditorComponent implements OnInit, OnDestroy {
  private readonly svc = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);
  private readonly destroy$ = new Subject<void>();
  @ViewChild('svgEl') svgEl!: ElementRef<SVGSVGElement>;
  @ViewChild('configDetail') configDetail!: ConfigNodeDetailComponent;
  readonly views = VIEWS;
  readonly VIEW_IDS = VIEW_IDS;
  readonly nodeColor = nodeColor;
  graph: ConfigGraph | null = null;
  loading = true;
  activeView: ViewId = VIEW_IDS.configurationOverview;
  selectedNode: ConfigGraphNode | null = null;
  selectedConfigItem: ConfigGraphNode | null = null;
  displayMode: 'config' | 'graph' = 'config';
  editMode = false;
  graphFilterIds: string[] | null = null;
  graphSearchText = '';
  graphNodeType = '';
  graphStatus: GraphStatusFilter = 'all';
  pendingOps: PatchOp[] = [];
  lastValidation: ValidationResult | null = null;
  approvalToken = '';
  lastSourceDiffs: string[] = [];
  lastRollbackArtifact: Record<string, unknown> | null = null;
  configEditorActive = false;
  private layoutNodes: Map<string, LayoutNode> = new Map();
  svgWidth = 1200;
  svgHeight = 800;
  get activeViewMeta(): ViewMeta | null {
    return VIEWS.find(v => v.id === this.activeView) ?? null;
  }
  get availableNodeTypes(): string[] {
    if (!this.graph) return [];
    return Array.from(new Set(Object.values(this.graph.nodes).map(n => n.node_type))).sort();
  }
  get hasGraphFilters(): boolean {
    return Boolean(this.graphSearchText.trim() || this.graphNodeType || this.graphStatus !== 'all');
  }
  get visibleNodeIds(): string[] {
    if (!this.graph) return [];
    const all = (this.graph.views[this.activeView] ?? []).filter(id => id in this.graph!.nodes);
    const focusIds = this.graphFilterIds ? new Set(this.graphFilterIds) : null;
    return all.filter(id => {
      const node = this.graph!.nodes[id];
      return (!focusIds || focusIds.has(id)) && this.matchesGraphFilters(node);
    });
  }
  get visibleLayoutNodes(): LayoutNode[] {
    return this.visibleNodeIds.map(id => this.layoutNodes.get(id)!).filter(Boolean);
  }
  get visibleEdges(): ConfigGraphEdge[] {
    if (!this.graph) return [];
    const vis = new Set(this.visibleNodeIds);
    return this.graph.edges.filter(e => vis.has(e.source) && vis.has(e.target));
  }
  get configPanelItems(): ConfigGraphNode[] {
    if (!this.graph) return [];
    return this.visibleNodeIds.map(id => this.graph!.nodes[id]);
  }
  get creatableTypeForView(): 'agent_profile' | 'path_rule' | 'restricted_inference_model' | 'restricted_inference_task' | null {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    if (types.includes('agent_profile')) return 'agent_profile';
    if (types.includes('path_rule')) return 'path_rule';
    if (types.includes('restricted_inference_model')) return 'restricted_inference_model';
    if (types.includes('restricted_inference_task')) return 'restricted_inference_task';
    return null;
  }
  get connectedNodes(): ConnectedNode[] {
    if (!this.graph || !this.selectedConfigItem) return [];
    const nid = this.selectedConfigItem.id;
    const seen = new Set<string>();
    const result: ConnectedNode[] = [];
    for (const e of this.graph.edges) {
      if (e.source === nid && e.target in this.graph.nodes && !seen.has(e.target)) {
        seen.add(e.target);
        result.push({ node: this.graph.nodes[e.target], direction: 'out', edgeType: e.edge_type });
      }
      if (e.target === nid && e.source in this.graph.nodes && !seen.has(e.source)) {
        seen.add(e.source);
        result.push({ node: this.graph.nodes[e.source], direction: 'in', edgeType: e.edge_type });
      }
    }
    return result;
  }
  ngOnInit(): void { this.reload(); }
  ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }
  reload(): void {
    this.loading = true;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.configEditorActive = false;
    this.graphFilterIds = null;
    this.cdr.markForCheck();
    this.svc.getGraph().pipe(takeUntil(this.destroy$)).subscribe({
      next: g => { this.graph = g; this.computeLayout(); this.loading = false; this.cdr.markForCheck(); },
      error: () => { this.loading = false; this.cdr.markForCheck(); },
    });
  }
  setView(v: ViewId): void {
    this.activeView = v;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.configEditorActive = false;
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }
  setDisplayMode(m: 'config' | 'graph'): void {
    this.displayMode = m;
    if (m === 'config') { this.graphFilterIds = null; }
    else { this.computeLayout(); }
    this.cdr.markForCheck();
  }
  clearGraphFilter(): void {
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }
  onGraphFilterChanged(): void {
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }
  clearGraphSearchFilters(): void {
    this.graphSearchText = '';
    this.graphNodeType = '';
    this.graphStatus = 'all';
    this.onGraphFilterChanged();
  }
  selectConfigItem(node: ConfigGraphNode): void {
    this.selectedConfigItem = node;
    this.configEditorActive = false;
    this.cdr.markForCheck();
  }
  clearItemSelection(): void {
    this.selectedConfigItem = null;
    this.configEditorActive = false;
    this.cdr.markForCheck();
  }
  showInGraph(node: ConfigGraphNode): void {
    this.displayMode = 'graph';
    this.selectedNode = node;
    for (const v of VIEWS) {
      if ((this.graph?.views[v.id] ?? []).includes(node.id)) {
        this.activeView = v.id;
        break;
      }
    }
    const neighbors = this.getNeighborIds(node.id);
    this.graphFilterIds = [node.id, ...neighbors];
    this.computeLayout();
    this.cdr.markForCheck();
  }
  isPrimaryTypeInView(node: ConfigGraphNode): boolean {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    return types.includes(node.node_type);
  }
  private matchesGraphFilters(node: ConfigGraphNode): boolean {
    if (this.graphNodeType && node.node_type !== this.graphNodeType) return false;
    if (this.graphStatus === 'active' && !node.runtime_active) return false;
    if (this.graphStatus === 'inactive' && node.runtime_active) return false;
    if (this.graphStatus === 'diagnostics' && node.diagnostics.length === 0) return false;
    if (this.graphStatus === 'stale' && !node.stale) return false;
    const query = this.graphSearchText.trim().toLowerCase();
    if (!query) return true;
    const haystack = [
      node.id,
      node.node_type,
      node.label,
      node.source_file ?? '',
      node.runtime_source ?? '',
      ...Object.entries(node.data as Record<string, unknown>).flatMap(([k, v]) => [
        k,
        Array.isArray(v) ? v.join(' ') : typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? ''),
      ]),
    ].join(' ').toLowerCase();
    return haystack.includes(query);
  }
  readonly keyFieldsFor = buildKeyFields;
  readonly allFieldsFor = buildAllFields;
  readonly characterBadge = buildCharacterBadge;
  readonly policySuggestions = POLICY_PATH_SUGGESTIONS;
  prefillSuggestion(s: { glob: string; blocked: string; hint: string }): void {
    this.configDetail.prefillPathRule(s);
  }
  startNewEntry(): void {
    const entryType = this.creatableTypeForView;
    if (!entryType) return;
    this.configDetail.startCreate(entryType as CreatableConfigType);
  }
  queueConfigPatch(event: { op: PatchOp; node: ConfigGraphNode }): void {
    this.pendingOps.push(event.op);
    this.lastValidation = null;
    this.selectedConfigItem = event.node;
    this.cdr.markForCheck();
  }
  onConfigGraphChanged(graph: ConfigGraph): void {
    this.graph = graph;
    this.selectedConfigItem = null;
    this.configEditorActive = false;
    this.computeLayout();
    this.cdr.markForCheck();
  }
  queueRemoveNode(nodeId: string): void { this.pendingOps.push({ op: 'remove_node', target: nodeId, data: {} }); this.lastValidation = null; this.cdr.markForCheck(); }
  validatePatch(): void {
    if (!this.pendingOps.length) return;
    this.svc.validatePatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => { this.lastValidation = r; this.cdr.markForCheck(); },
    });
  }
  applyPatch(): void {
    if (!this.pendingOps.length || !this.lastValidation?.valid) return;
    this.svc.applyPatch(this.pendingOps, this.approvalToken).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => {
        this.graph = r.graph;
        this.pendingOps = [];
        this.lastValidation = null;
        this.approvalToken = '';
        this.selectedNode = null;
        this.lastSourceDiffs = (r.result.source_diffs ?? [])
          .map(item => String((item as Record<string, unknown>)['diff'] ?? ''))
          .filter(Boolean);
        this.lastRollbackArtifact = r.result.rollback_artifact ?? null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
    });
  }
  rollbackLastPatch(): void {
    if (!this.lastRollbackArtifact) return;
    this.svc.rollbackPatch(this.lastRollbackArtifact).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => {
        this.graph = r.graph;
        this.lastSourceDiffs = (r.result.source_diffs ?? [])
          .map(item => String((item as Record<string, unknown>)['diff'] ?? ''))
          .filter(Boolean);
        this.lastRollbackArtifact = r.result.rollback_artifact ?? null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
    });
  }
  discardPatch(): void {
    this.pendingOps = [];
    this.lastValidation = null;
    this.approvalToken = '';
    this.cdr.markForCheck();
  }
  private computeLayout(): void {
    if (!this.graph) return;
    this.layoutNodes.clear();
    const ids = this.visibleNodeIds;
    const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
    let maxX = 0, maxY = 0;
    ids.forEach((id, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const x = 24 + col * (NODE_W + COL_GAP), y = 24 + row * (NODE_H + ROW_GAP);
      maxX = Math.max(maxX, x + NODE_W + 24); maxY = Math.max(maxY, y + NODE_H + 24);
      this.layoutNodes.set(id, { id, x, y, w: NODE_W, h: NODE_H, node: this.graph!.nodes[id] });
    });
    this.svgWidth = Math.max(800, maxX);
    this.svgHeight = Math.max(600, maxY);
  }
  private getNeighborIds(nodeId: string): string[] {
    if (!this.graph) return [];
    const neighbors = new Set<string>();
    for (const e of this.graph.edges) {
      if (e.source === nodeId && e.target in this.graph.nodes) neighbors.add(e.target);
      if (e.target === nodeId && e.source in this.graph.nodes) neighbors.add(e.source);
    }
    return Array.from(neighbors);
  }
  edgeX1(e: ConfigGraphEdge): number { return (this.layoutNodes.get(e.source)?.x ?? 0) + NODE_W; }
  edgeY1(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.source); return ln ? ln.y + NODE_H / 2 : 0; }
  edgeX2(e: ConfigGraphEdge): number { return this.layoutNodes.get(e.target)?.x ?? 0; }
  edgeY2(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.target); return ln ? ln.y + NODE_H / 2 : 0; }
  selectNode(event: MouseEvent, node: ConfigGraphNode): void { event.stopPropagation(); this.selectedNode = node; this.cdr.markForCheck(); }
  onSvgClick(_: MouseEvent): void { this.selectedNode = null; this.cdr.markForCheck(); }
  truncate(text: string, max: number): string { return text.length > max ? text.slice(0, max - 1) + '…' : text; }
}
