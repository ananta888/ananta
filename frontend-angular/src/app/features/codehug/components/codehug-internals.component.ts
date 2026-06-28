import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { interval, Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged, switchMap } from 'rxjs/operators';
import { InternalsService, AnantaWorker, AutopilotStatus, VpPreset, VpSkillProfile, VpGraph } from '../services/internals.service';
import { DecimalPipe, SlicePipe } from '@angular/common';
import { GraphViewerComponent } from '../../codecompass-graph/components/graph-viewer/graph-viewer.component';
import { CodehugCanvasInteractionService } from '../services/codehug-canvas-interaction.service';

import {
  ARTIFACT_KINDS, BACKENDS, BLUEPRINTS, CAPABILITIES, COND_COLOR, CX, GAP_Y,
  NODE_H, NODE_STYLE, NODE_W, PLAYBOOKS, PRIORITY_COLOR, VP_KINDS,
  type ArtifactBinding, type ArtifactSlot, type CanvasEdge, type CanvasNode,
  type EdgeCondition, type NodeType, type Priority, type RoutingMode,
  type StepRouting,
} from './codehug-canvas-types';

@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [DecimalPipe, SlicePipe, GraphViewerComponent],
  providers: [CodehugCanvasInteractionService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-internals.component.html',
  styleUrls: ['./codehug-internals.component.scss'],
})
export class CodeHugInternalsComponent implements OnInit, OnDestroy {
  @ViewChild('svgEl') svgElRef!: ElementRef<SVGSVGElement>;

  private readonly svc = inject(InternalsService);
  private readonly canvas = inject(CodehugCanvasInteractionService);

  // ── Exposed statics for template ─────────────────────────────────────────
  readonly BLUEPRINTS = BLUEPRINTS;
  readonly PLAYBOOKS = PLAYBOOKS;
  readonly PRIORITY_COLOR = PRIORITY_COLOR;
  readonly COND_COLOR = COND_COLOR;
  readonly NODE_STYLE = NODE_STYLE;
  readonly BACKENDS = BACKENDS;
  readonly CAPABILITIES = CAPABILITIES;
  readonly VP_KINDS = VP_KINDS;
  readonly ARTIFACT_KINDS = ARTIFACT_KINDS;

  // ── VP API signals ────────────────────────────────────────────────────────
  readonly vpPresets = signal<VpPreset[]>([]);
  readonly skillProfiles = signal<VpSkillProfile[]>([]);
  readonly selectedPresetId = signal('');
  readonly workflowId = signal<string | null>(null);
  readonly workflowStatus = signal<Record<string, unknown> | null>(null);
  readonly workflowEvents = signal<Record<string, unknown>[]>([]);
  readonly dryRunResult = signal<string | null>(null);
  readonly detRunResult = signal<Record<string, unknown> | null>(null);
  readonly detRunning = signal(false);

  // ── Live data ─────────────────────────────────────────────────────────────
  readonly workers = signal<AnantaWorker[]>([]);
  readonly autopilot = signal<AutopilotStatus>({
    running: false, goal: '', team_id: '', started_at: null,
    tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
    last_error: null,
    effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
    circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
  });

  // ── Config ────────────────────────────────────────────────────────────────
  readonly selectedBlueprint = signal('scrum');
  readonly selectedPlaybook = signal('bug_fix');
  readonly selectedSecurity = signal('safe');
  readonly maxConcurrency = signal(1);
  readonly goalText = signal('');
  readonly goalResult = signal<string | null>(null);
  readonly goalOk = signal(false);

  // ── Canvas state ──────────────────────────────────────────────────────────
  readonly nodes = signal<CanvasNode[]>([]);
  readonly edges = signal<CanvasEdge[]>([]);
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedEdgeId = signal<string | null>(null);
  readonly viewTx = this.canvas.viewTx;
  readonly viewTy = this.canvas.viewTy;
  readonly viewScale = this.canvas.viewScale;
  readonly svgTransform = this.canvas.svgTransform;
  readonly selectedNode = computed(() => this.nodes().find(n => n.id === this.selectedNodeId()) ?? null);
  readonly selectedEdge = computed(() => this.edges().find(e => e.id === this.selectedEdgeId()) ?? null);
  readonly currentRoles = computed(() => BLUEPRINTS.find(b => b.id === this.selectedBlueprint())?.roles ?? []);

  // ── Tab / Quellgraph ──────────────────────────────────────────────────────
  readonly activeTab = signal<'vp' | 'graph'>('graph');
  readonly ccIndexes = signal<any[]>([]);
  readonly ccSelectedId = signal('');
  readonly ccGraphMode = signal<string>('self');
  readonly ccRawGraph = signal<any>(null);
  readonly ccLoading = signal(false);
  readonly ccError = signal('');
  readonly ccDomains = signal<{domain: string; display_name: string; file_count: number; kind: string; depth?: number; parent_domain?: string}[]>([]);
  readonly ccDomain = signal('agent.routes');
  readonly ccDetailLevel = signal(2);
  readonly ccGraphDepth = signal(0);
  readonly ccMaxNodes = signal(0);
  readonly ccMaxEdges = signal(0);
  readonly ccMeta = signal<Record<string, unknown> | null>(null);
  readonly ccSelectedIndex = computed(() => this.ccIndexes().find(i => i.id === this.ccGraphMode()) ?? null);

  // ── Wiki Graph Explorer ────────────────────────────────────────────────────
  readonly wgStatus = signal<any>(null);
  readonly wgSearchQuery = signal('');
  readonly wgSearchResults = signal<{slug: string; title: string}[]>([]);
  readonly wgSearchLoading = signal(false);
  readonly wgExpandedSlug = signal('');
  private _wgSearch$ = new Subject<string>();
  private _wgSearchSub: Subscription | null = null;

  // ── Wiki Domain Modes ──────────────────────────────────────────────────────
  readonly wgDomainStatus    = signal<any>(null);
  readonly wgHubDomains      = signal<any[]>([]);
  readonly wgCategoryDomains = signal<any[]>([]);
  readonly wgClusterDomains  = signal<any[]>([]);
  private _wgDomainPollTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Connect mode ──────────────────────────────────────────────────────────
  readonly connectMode = signal(false);
  readonly connectSource = signal<string | null>(null);

  // ── Drag state ────────────────────────────────────────────────────────────
  private _nodeSeq = 0;
  private _edgeSeq = 0;
  private _pollSub: Subscription | null = null;
  private _workflowPollSub: Subscription | null = null;

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.buildCanvas('scrum', 'bug_fix');
    this.svc.getWorkers().subscribe(w => this.workers.set(w));
    this.svc.getAutopilotStatus().subscribe(s => this.autopilot.set(s));
    this.svc.getVpPresets().subscribe(p => this.vpPresets.set(p));
    this.svc.getVpSkillProfiles().subscribe(sp => this.skillProfiles.set(sp));
    this.svc.getSelfGraphDomains().subscribe(domains => {
      this.ccDomains.set(domains);
      // pick a good default: prefer agent.routes → agent → first with files
      const best = domains.find(d => d.domain === 'agent.routes')
        ?? domains.find(d => d.domain === 'agent')
        ?? domains.find(d => d.file_count > 0)
        ?? domains[0];
      if (best) this.ccDomain.set(best.domain);
      this.loadSelfGraph();
    });
    this.svc.listKnowledgeIndexes().subscribe(items => this.ccIndexes.set(items));
    this._pollSub = interval(3000).pipe(switchMap(() => this.svc.getAutopilotStatus()))
      .subscribe(s => this.autopilot.set(s));
  }

  ngOnDestroy(): void {
    this._pollSub?.unsubscribe();
    this._workflowPollSub?.unsubscribe();
    this._wgSearchSub?.unsubscribe();
    if (this._wgDomainPollTimer !== null) {
      clearTimeout(this._wgDomainPollTimer);
    }
  }

  // ── Quellgraph ────────────────────────────────────────────────────────────

  loadCCGraph(id: string): void {
    if (!id) return;
    this.ccSelectedId.set(id);
    this.ccLoading.set(true);
    this.ccError.set('');
    this.ccRawGraph.set(null);
    this.svc.getCodeCompassGraph(id).subscribe({
      next: data => {
        this.ccLoading.set(false);
        if (data) { this.ccRawGraph.set(data); }
        else { this.ccError.set('Graph nicht verfügbar'); }
      },
      error: () => {
        this.ccLoading.set(false);
        this.ccError.set('Fehler beim Laden');
      },
    });
  }

  loadSelfGraph(): void {
    this.ccSelectedId.set('ananta');
    this.ccLoading.set(true);
    this.ccError.set('');
    this.ccRawGraph.set(null);
    this.svc.getSelfGraph(
      this.ccDomain(),
      this.ccDetailLevel(),
      this.ccGraphDepth(),
      this.ccMaxNodes(),
      this.ccMaxEdges(),
    ).subscribe({
      next: data => {
        if (this.ccGraphMode() !== 'self') return;
        this.ccLoading.set(false);
        if (data) {
          this.ccRawGraph.set(data);
          this.ccMeta.set((data as any)?.metadata ?? null);
        } else { this.ccError.set('Self-Graph nicht verfügbar'); }
      },
      error: () => {
        if (this.ccGraphMode() !== 'self') return;
        this.ccLoading.set(false);
        this.ccError.set('Fehler beim Laden des Self-Graphs');
      },
    });
  }

  domainOptionLabel(domain: {display_name: string; file_count: number; depth?: number}): string {
    const depth = Math.min(Math.max(domain.depth ?? 0, 0), 4);
    const indent = depth > 0 ? `${'--'.repeat(depth)} ` : '';
    return `${indent}${domain.display_name}${domain.file_count > 0 ? ` (${domain.file_count})` : ''}`;
  }

  indexLabel(idx: any): string {
    const scope: string = idx?.source_scope ?? '';
    const sourceId: string = idx?.index_metadata?.source_id ?? idx?.collection_id ?? idx?.id ?? '?';
    const scopePrefix: Record<string, string> = { wiki: 'Wiki', artifact: 'Artefakt' };
    const label = sourceId.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).replace(/Dewiki.*/, 'DE').trim();
    return `${scopePrefix[scope] ?? scope}: ${label}`;
  }

  onGraphSourceChange(value: string): void {
    this.ccGraphMode.set(value);
    this.ccMeta.set(null);
    this.ccRawGraph.set(null);
    this.ccLoading.set(false);
    this.ccError.set('');
    this.wgStatus.set(null);
    this.wgSearchResults.set([]);
    this.wgSearchQuery.set('');
    this.wgExpandedSlug.set('');
    this.wgDomainStatus.set(null);
    this.wgHubDomains.set([]);
    this.wgCategoryDomains.set([]);
    this.wgClusterDomains.set([]);
    if (this._wgDomainPollTimer !== null) {
      clearTimeout(this._wgDomainPollTimer);
      this._wgDomainPollTimer = null;
    }
    if (value === 'self') {
      this.loadSelfGraph();
    } else {
      this._initWikiGraphExplorer(value);
    }
  }

  private _initWikiGraphExplorer(indexId: string): void {
    this._wgSearchSub?.unsubscribe();
    this.svc.getWikiGraphStatus(indexId).subscribe(s => {
      this.wgStatus.set(s);
      if (s?.status === 'ready') {
        this.svc.getWikiDomainStatus(indexId).subscribe(ds => {
          this.wgDomainStatus.set(ds);
          this._loadReadyDomainLists(indexId, ds);
        });
      }
    });
    this._wgSearchSub = this._wgSearch$.pipe(
      debounceTime(300),
      distinctUntilChanged(),
    ).subscribe(q => {
      if (!q) { this.wgSearchResults.set([]); this.wgSearchLoading.set(false); return; }
      this.wgSearchLoading.set(true);
      this.svc.searchWikiArticles(indexId, q).subscribe(r => {
        this.wgSearchResults.set(r);
        this.wgSearchLoading.set(false);
      });
    });
  }

  private _loadReadyDomainLists(indexId: string, domainStatus: any): void {
    if (domainStatus?.hubs?.status === 'ready')
      this.svc.getWikiDomains(indexId, 'hubs').subscribe(d => this.wgHubDomains.set(d));
    if (domainStatus?.categories?.status === 'ready')
      this.svc.getWikiDomains(indexId, 'categories').subscribe(d => this.wgCategoryDomains.set(d));
    if (domainStatus?.clusters?.status === 'ready')
      this.svc.getWikiDomains(indexId, 'clusters').subscribe(d => this.wgClusterDomains.set(d));
  }

  wgSearch(q: string): void {
    this.wgSearchQuery.set(q);
    if (!q) {
      this.ccRawGraph.set(null);
      this.wgExpandedSlug.set('');
    }
    this._wgSearch$.next(q);
  }

  wgExpand(slug: string, _title?: string): void {
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    this.wgExpandedSlug.set(slug);
    this.wgSearchResults.set([]);
    this.wgSearchQuery.set('');
    this.ccLoading.set(true);
    this.ccError.set('');
    this.svc.expandWikiArticle(indexId, slug).subscribe({
      next: data => {
        if (this.ccGraphMode() !== indexId) return;
        this.ccLoading.set(false);
        if (data?.nodes?.length > 0) {
          this.ccRawGraph.set(data);
          this.ccMeta.set(data.metadata ?? null);
        } else {
          this.ccError.set('Keine Nachbarn gefunden');
        }
      },
      error: () => { if (this.ccGraphMode() !== indexId) return; this.ccLoading.set(false); this.ccError.set('Fehler beim Laden'); },
    });
  }

  wgSelectDomainItem(mode: string, domainId: string): void {
    if (!domainId) return;
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    if (mode === 'hubs') {
      // Hubs = individual article → expand neighborhood
      const hub = this.wgHubDomains().find(d => d.id === domainId);
      this.wgExpand(domainId, hub?.label);
    } else {
      this._loadDomainGraph(indexId, mode, domainId);
    }
  }

  private _loadDomainGraph(indexId: string, mode: string, domainId: string): void {
    this.ccLoading.set(true);
    this.ccError.set('');
    this.svc.getWikiDomainGraph(indexId, mode, domainId).subscribe({
      next: data => {
        if (this.ccGraphMode() !== indexId) return;
        this.ccLoading.set(false);
        if (data?.nodes?.length > 0) {
          this.ccRawGraph.set(data);
          this.ccMeta.set(data.metadata ?? null);
        } else {
          this.ccError.set('Keine Artikel in dieser Domäne');
        }
      },
      error: () => { if (this.ccGraphMode() !== indexId) return; this.ccLoading.set(false); this.ccError.set('Fehler beim Laden'); },
    });
  }

  wgBuild(force = false): void {
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    this.wgStatus.set({ status: 'building' });
    this.svc.triggerWikiGraphBuild(indexId, force).subscribe(() => {
      this._pollWgStatus(indexId);
    });
  }

  private _pollWgStatus(indexId: string): void {
    const tick = () => {
      this.svc.getWikiGraphStatus(indexId).subscribe(s => {
        this.wgStatus.set(s);
        if (s?.status === 'building') {
          setTimeout(tick, 5000);
        }
      });
    };
    setTimeout(tick, 3000);
  }

  // ── Wiki Domain Mode Methods ───────────────────────────────────────────────


  wgBuildDomainMode(mode: string, corpusPath?: string): void {
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    const currentStatus = this.wgDomainStatus() ?? {};
    this.wgDomainStatus.set({ ...currentStatus, [mode]: { status: 'building' } });
    this.svc.buildWikiDomains(indexId, mode, corpusPath).subscribe(() => {
      this._pollDomainStatus(indexId, mode);
    });
  }

  wgDomainModeStatusFor(mode: string): string {
    return this.wgDomainStatus()?.[mode]?.status ?? 'not_built';
  }

  private _pollDomainStatus(indexId: string, mode: string): void {
    if (this._wgDomainPollTimer !== null) {
      clearTimeout(this._wgDomainPollTimer);
      this._wgDomainPollTimer = null;
    }
    const tick = () => {
      this.svc.getWikiDomainStatus(indexId).subscribe(status => {
        this.wgDomainStatus.set(status);
        const modeStatus = status?.[mode];
        if (modeStatus?.status === 'building') {
          this._wgDomainPollTimer = setTimeout(tick, 5000);
        } else if (modeStatus?.status === 'ready') {
          this._wgDomainPollTimer = null;
          this.svc.getWikiDomains(indexId, mode).subscribe(domains => {
            if (mode === 'hubs')       this.wgHubDomains.set(domains);
            if (mode === 'categories') this.wgCategoryDomains.set(domains);
            if (mode === 'clusters')   this.wgClusterDomains.set(domains);
          });
        }
      });
    };
    this._wgDomainPollTimer = setTimeout(tick, 3000);
  }

  // ── Blueprint / Playbook / VP Preset ─────────────────────────────────────

  onBlueprintChange(id: string): void {
    this.selectedBlueprint.set(id);
    this.buildCanvas(id, this.selectedPlaybook());
  }

  onPlaybookChange(id: string): void {
    this.selectedPlaybook.set(id);
    this.buildCanvas(this.selectedBlueprint(), id);
  }

  onPresetChange(presetId: string): void {
    this.selectedPresetId.set(presetId);
    if (!presetId) return;
    this.svc.getVpPreset(presetId).subscribe(graph => {
      if (!graph) return;
      this.loadFromVpGraph(graph);
    });
  }

  loadFromVpGraph(graph: VpGraph): void {
    let maxX = 0;
    const nodes: CanvasNode[] = graph.steps.map(s => {
      const x = s.position.x + 40;
      const y = s.position.y + 40;
      if (x > maxX) maxX = x;
      const type = this.vpKindToNodeType(s.kind, s.gate);
      return {
        id: s.id, x, y, w: NODE_W, h: NODE_H,
        type, title: s.label,
        subtitle: s.io?.outputs?.map((o: any) => o.name).join(', ') || undefined,
        role: s.role ?? undefined,
        skillProfileId: s.agent_skill_profile_id ?? undefined,
        vpKind: s.kind,
        gate: s.gate,
        enabled: true,
        routing: { mode: 'auto' as RoutingMode },
        inputs: (s.io?.inputs ?? []).map((inp: any) => ({
          name: inp.name,
          kind: inp.kind ?? 'text',
          required: inp.required ?? true,
          description: inp.description ?? '',
          producedByStepId: inp.produced_by_step ?? undefined,
          producedByOutputName: inp.produced_by_output ?? undefined,
        })),
        outputs: (s.io?.outputs ?? []).map((out: any) => ({ name: out.name, kind: out.kind ?? 'text', required: out.required ?? false, description: out.description ?? '' })),
      };
    });
    const edges: CanvasEdge[] = graph.edges.map(e => ({
      id: e.id, from: e.source, to: e.target,
      condition: (e.condition.kind as EdgeCondition) ?? 'always',
      label: e.label ?? undefined,
      loopMaxIter: e.condition.loop_policy?.max_iterations ?? undefined,
      outputName: e.condition.output_name ?? undefined,
      bindings: Array.isArray(e.metadata?.['artifact_bindings']) ? (e.metadata['artifact_bindings'] as any[]).map(b => ({
        outputName: String(b.output_name ?? b.outputName ?? ''),
        inputName: String(b.input_name ?? b.inputName ?? ''),
      })).filter(b => b.outputName && b.inputName) : [],
    }));
    this.nodes.set(nodes);
    this.edges.set(edges);
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.workflowId.set(null);
    this.workflowStatus.set(null);
    this.workflowEvents.set([]);
    this.dryRunResult.set(null);
  }

  private vpKindToNodeType(kind: string, gate: boolean): NodeType {
    if (gate) return 'gate';
    if (kind === 'goal_plan' || kind === 'spec' || kind === 'breakdown') return 'planning';
    if (kind === 'run_tests' || kind === 'testing') return 'verification';
    if (kind === 'code_review') return 'review';
    return 'task';
  }

  private nodeKindFor(n: CanvasNode): string {
    if (n.vpKind) return n.vpKind;
    if (n.type === 'det') return 'run_tests';
    if (n.type === 'gate') return 'approval';
    if (n.type === 'review') return 'code_review';
    if (n.type === 'planning') return 'goal_plan';
    if (n.type === 'verification') return 'run_tests';
    if (n.type === 'fork') return 'fork';
    if (n.type === 'join') return 'join';
    if (n.type === 'start' || n.type === 'end') return 'llm_generate';
    return 'coding';
  }

  toVpGraph(): VpGraph {
    this.syncInputBindings();
    const name = this.goalText().trim() || 'Canvas Workflow';
    return {
      id: `vp-canvas-${Date.now()}`,
      name, description: name,
      tags: [this.selectedBlueprint(), this.selectedPlaybook()],
      metadata: {
        security_level: this.selectedSecurity(),
        blueprint: this.selectedBlueprint(),
        playbook: this.selectedPlaybook(),
      },
      steps: this.nodes().map(n => ({
        id: n.id, label: n.title,
        kind: this.nodeKindFor(n),
        role: n.role ?? null,
        agent_skill_profile_id: n.skillProfileId ?? null,
        gate: n.gate ?? false,
        position: { x: n.x, y: n.y },
        policy_hints: n.gate ? ['requires_approval'] : [],
        io: {
          inputs: (n.inputs ?? []).map(s => ({
            name: s.name,
            kind: s.kind,
            required: s.required,
            description: s.description,
            produced_by_step: s.producedByStepId ?? null,
            produced_by_output: s.producedByOutputName ?? null,
          })),
          outputs: (n.outputs ?? []).map(s => ({ name: s.name, kind: s.kind, required: s.required, description: s.description })),
        },
        metadata: {
          det_subtype: n.detSubtype ?? null,
          det_command: n.detCommand ?? null,
          det_expected: n.detExpectedResult ?? null,
          fail_action: n.failAction ?? null,
          routing: n.routing ?? null,
          node_type: n.type,
        },
      })),
      edges: this.edges().map(e => ({
        id: e.id, source: e.from, target: e.to,
        label: this.edgeBindingLabel(e) || (e.outputName ? `📦 ${e.outputName}` : (e.label ?? null)),
        metadata: {
          artifact_bindings: (e.bindings ?? []).map(b => ({
            output_name: b.outputName,
            input_name: b.inputName,
          })),
        },
        condition: {
          kind: e.condition,
          expression: null,
          output_name: e.condition === 'on_output' ? (e.outputName ?? null) : null,
          loop_policy: e.condition === 'back_edge'
            ? { kind: 'fixed', max_iterations: e.loopMaxIter ?? 3, condition: null, break_on_output: null }
            : null,
        },
      })),
    };
  }

  runDetStep(node: CanvasNode): void {
    if (!node.detCommand) return;
    this.detRunning.set(true);
    this.detRunResult.set(null);
    this.svc.runDetStep(
      node.detSubtype ?? 'script',
      node.detCommand,
      node.detExpectedResult ?? '',
    ).subscribe(result => {
      this.detRunResult.set(result);
      this.detRunning.set(false);
    });
  }

  dryRunWorkflow(): void {
    const graph = this.toVpGraph();
    this.svc.dryRunVpGraph(graph).subscribe(result => {
      const v = result?.validation;
      if (!v) { this.goalResult.set('Dry-run: keine Antwort'); this.goalOk.set(false); return; }
      if (v.valid) {
        this.goalOk.set(true);
        this.goalResult.set(`✓ Valide (${result.step_count} Schritte, ${result.edge_count} Kanten)`);
      } else {
        this.goalOk.set(false);
        this.goalResult.set(`✗ Fehler: ${v.errors?.join(', ') ?? 'unbekannt'}`);
      }
    });
  }

  startVpWorkflow(): void {
    const text = this.goalText().trim();
    if (!text) return;
    const graph = this.toVpGraph();
    this.svc.startVpWorkflow(graph, {
      requested_by: 'codehug_internals',
      workflow_type: 'visual_process',
    }).subscribe(result => {
      const wid = (result as any)?.workflow_id ?? (result as any)?.id ?? null;
      if (wid) {
        this.workflowId.set(wid);
        this.startWorkflowPolling(wid);
        this.goalOk.set(true);
        this.goalResult.set(`Workflow gestartet: ${wid}`);
      } else {
        this.goalOk.set(!!(result as any)?.status && (result as any)?.status !== 'error');
        this.goalResult.set((result as any)?.status ?? 'Gestartet');
      }
    });
  }

  buildCanvas(blueprintId: string, playbookId: string): void {
    const bp = BLUEPRINTS.find(b => b.id === blueprintId);
    const pb = PLAYBOOKS.find(p => p.id === playbookId);
    if (!bp || !pb) return;

    const roles = bp.roles;
    const nodes: CanvasNode[] = [];
    const edges: CanvasEdge[] = [];
    let y = 30;
    const addEdge = (from: string, to: string, cond: EdgeCondition = 'always') =>
      edges.push({ id: `e-${++this._edgeSeq}`, from, to, condition: cond });

    nodes.push({ id: 'start', x: CX - 60, y, w: 120, h: 40, type: 'start', title: 'Ziel', enabled: true, inputs: [], outputs: [] });
    y += 40 + GAP_Y;

    nodes.push({ id: 'plan', x: CX - 90, y, w: 180, h: 52, type: 'planning', title: 'Planung', subtitle: 'LMStudio · Gemma', enabled: true, inputs: [], outputs: [{ name: 'plan', kind: 'json' as const, required: false, description: '' }] });
    addEdge('start', 'plan');
    y += 52 + GAP_Y;

    let prev = 'plan';
    pb.tasks.forEach((task, i) => {
      const nid = `task-${++this._nodeSeq}`;
      nodes.push({
        id: nid, x: CX - NODE_W / 2, y, w: NODE_W, h: NODE_H, type: 'task',
        title: task.title, subtitle: task.description,
        role: roles[i % roles.length],
        priority: task.priority, enabled: true,
        routing: { mode: 'auto' as RoutingMode },
        inputs: [], outputs: [],
      });
      addEdge(prev, nid);
      prev = nid;
      y += NODE_H + GAP_Y;
    });

    nodes.push({ id: 'verif', x: CX - 90, y, w: 180, h: 52, type: 'verification', title: 'Verifikation', subtitle: 'Review · Tests', enabled: true, inputs: [], outputs: [{ name: 'verification_report', kind: 'report' as const, required: false, description: '' }] });
    addEdge(prev, 'verif');
    y += 52 + GAP_Y;

    nodes.push({ id: 'end', x: CX - 60, y, w: 120, h: 40, type: 'end', title: 'Fertig', enabled: true, inputs: [], outputs: [] });
    addEdge('verif', 'end');

    this.nodes.set(nodes);
    this.edges.set(edges);
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.connectMode.set(false);
    this.connectSource.set(null);
  }

  // ── Interactions ──────────────────────────────────────────────────────────

  onBgMouseDown(e: MouseEvent): void {
    const tag = (e.target as SVGElement).tagName;
    if (tag === 'svg' || (e.target as SVGElement).classList.contains('ch-bg-rect')) {
      this.canvas.onBackgroundMouseDown(e);
    }
  }

  onNodeMouseDown(e: MouseEvent, nodeId: string): void {
    if (this.connectMode()) return;
    const n = this.nodes().find(n => n.id === nodeId);
    if (n) this.canvas.onNodeMouseDown(e, n, this.svgElRef.nativeElement);
  }

  onNodeClick(e: MouseEvent, nodeId: string): void {
    if (this.canvas.consumeDrag()) return;
    e.stopPropagation();
    if (this.connectMode()) {
      const src = this.connectSource();
      if (!src) { this.connectSource.set(nodeId); return; }
      if (src !== nodeId) {
        this.edges.update(es => [...es, { id: `e-${++this._edgeSeq}`, from: src, to: nodeId, condition: 'always' }]);
      }
      this.connectSource.set(null); // stay in connect mode for next edge
      return;
    }
    this.selectedEdgeId.set(null);
    this.selectedNodeId.set(this.selectedNodeId() === nodeId ? null : nodeId);
  }

  onEdgeClick(e: MouseEvent, edgeId: string): void {
    e.stopPropagation();
    if (this.connectMode()) return;
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(this.selectedEdgeId() === edgeId ? null : edgeId);
  }

  @HostListener('document:mousemove', ['$event'])
  onMouseMove(e: MouseEvent): void {
    this.canvas.onMouseMove(e, this.svgElRef.nativeElement, (id, x, y) => {
      this.nodes.update(nodes => nodes.map(node => node.id === id ? { ...node, x, y } : node));
    });
  }

  @HostListener('document:keydown', ['$event'])
  onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Escape' && this.connectMode()) { this.cancelConnect(); }
  }

  @HostListener('document:mouseup')
  onMouseUp(): void { this.canvas.onMouseUp(); }

  onWheel(e: WheelEvent): void { this.canvas.onWheel(e, this.svgElRef.nativeElement); }

  zoomIn(): void { this.canvas.zoomIn(); }
  zoomOut(): void { this.canvas.zoomOut(); }
  resetView(): void { this.canvas.reset(); }
  toggleConnect(): void { this.connectMode.update(v => !v); if (!this.connectMode()) this.connectSource.set(null); }
  cancelConnect(): void { this.connectMode.set(false); this.connectSource.set(null); }

  // ── Node / Edge Operations ────────────────────────────────────────────────

  addFreeNode(): void {
    const { cx, cy } = this.viewCenter();
    const roles = this.currentRoles();
    const nid = `task-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - NODE_W / 2, y: cy - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'task', title: 'Neuer Schritt', subtitle: '',
      role: roles[ns.filter(n => n.type === 'task').length % roles.length] ?? '',
      priority: 'Medium', enabled: true,
      routing: { mode: 'auto' as RoutingMode },
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addGateNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `gate-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 120, y: cy - 30, w: 240, h: 58,
      type: 'gate', title: 'Verification Gate', subtitle: '',
      gateSubtype: 'auto-verify', failAction: 'block', enabled: true,
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addDetNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `det-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - NODE_W / 2, y: cy - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'det', title: 'Deterministischer Schritt', subtitle: '',
      detSubtype: 'script', detCommand: '', failAction: 'block',
      priority: 'Medium', enabled: true,
      routing: { mode: 'auto' },
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addReviewNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `rev-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 120, y: cy - 30, w: 240, h: 58,
      type: 'review', title: 'Review / Freigabe', subtitle: '',
      failAction: 'block', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addForkNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `fork-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 50, y: cy - 30, w: 100, h: 60,
      type: 'fork', title: 'Fork', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addJoinNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `join-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 60, y: cy - 22, w: 120, h: 44,
      type: 'join', title: 'Join', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  // ── I/O Artifact Slots ────────────────────────────────────────────────────

  addInput(nodeId: string): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId ? {
      ...n, inputs: [...n.inputs, { name: `input_${n.inputs.length + 1}`, kind: 'text' as const, required: true, description: '' }]
    } : n));
  }

  addArtifactBinding(edgeId: string, raw: string): void {
    if (!raw) return;
    const [outputName, inputName] = raw.split('=>');
    if (!outputName || !inputName) return;
    this.edges.update(es => es.map(e => {
      if (e.id !== edgeId) return e;
      const bindings = [...(e.bindings ?? [])];
      if (!bindings.some(b => b.outputName === outputName && b.inputName === inputName)) {
        bindings.push({ outputName, inputName });
      }
      return { ...e, bindings, outputName: e.outputName ?? outputName };
    }));
    this.syncInputBindings();
  }

  removeArtifactBinding(edgeId: string, binding: ArtifactBinding): void {
    this.edges.update(es => es.map(e => e.id === edgeId
      ? { ...e, bindings: (e.bindings ?? []).filter(b => b.outputName !== binding.outputName || b.inputName !== binding.inputName) }
      : e
    ));
    this.syncInputBindings();
  }

  availableBindingOptions(edge: CanvasEdge): { value: string; label: string }[] {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return [];
    const existing = new Set((edge.bindings ?? []).map(b => `${b.outputName}=>${b.inputName}`));
    const options: { value: string; label: string }[] = [];
    for (const out of src.outputs ?? []) {
      for (const inp of dst.inputs ?? []) {
        if (inp.kind !== out.kind && inp.kind !== 'text' && out.kind !== 'text') continue;
        const value = `${out.name}=>${inp.name}`;
        if (!existing.has(value)) {
          options.push({ value, label: `${out.name} → ${inp.name}` });
        }
      }
    }
    return options;
  }

  edgeBindingLabel(edge: CanvasEdge): string {
    const bindings = edge.bindings ?? [];
    if (!bindings.length) return '';
    if (bindings.length === 1) return `📦 ${bindings[0].outputName}`;
    return `📦 ${bindings.length} artifacts`;
  }

  addOutput(nodeId: string): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId ? {
      ...n, outputs: [...n.outputs, { name: `output_${n.outputs.length + 1}`, kind: 'text' as const, required: false, description: '' }]
    } : n));
  }

  patchSlot(nodeId: string, field: 'inputs' | 'outputs', index: number, patch: Partial<ArtifactSlot>): void {
    this.nodes.update(ns => ns.map(n => {
      if (n.id !== nodeId) return n;
      const slots = [...n[field]];
      slots[index] = { ...slots[index], ...patch };
      return { ...n, [field]: slots };
    }));
  }

  removeSlot(nodeId: string, field: 'inputs' | 'outputs', index: number): void {
    this.nodes.update(ns => ns.map(n => {
      if (n.id !== nodeId) return n;
      const slots = n[field].filter((_, i) => i !== index);
      return { ...n, [field]: slots };
    }));
  }

  // ── Geometry ──────────────────────────────────────────────────────────────

  forkPoints(node: CanvasNode): string {
    const cx = node.w / 2, cy = node.h / 2;
    return `${cx},0 ${node.w},${cy} ${cx},${node.h} 0,${cy}`;
  }

  setRoutingMode(nodeId: string, mode: RoutingMode): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId
      ? { ...n, routing: { mode, backend: 'ananta', capability: 'coder', workerName: '' } }
      : n
    ));
  }

  patchRouting(nodeId: string, patch: Partial<StepRouting>): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId
      ? { ...n, routing: { ...(n.routing ?? { mode: 'auto' as RoutingMode }), ...patch } }
      : n
    ));
  }

  isComplexNode(node: CanvasNode): boolean {
    return node.type === 'task' || node.type === 'det' || node.type === 'gate' || node.type === 'review' || node.type === 'fork' || node.type === 'join';
  }

  routingLabel(node: CanvasNode): string {
    const r = node.routing;
    if (!r || r.mode === 'auto') return '';
    if (r.mode === 'backend') return r.backend ?? '';
    if (r.mode === 'worker') return r.workerName?.split('-').pop() ?? '';
    if (r.mode === 'capability') return r.capability ?? '';
    return '';
  }

  routingBadgeW(node: CanvasNode): number {
    return this.routingLabel(node).length * 5.5 + 10;
  }

  private viewCenter(): { cx: number; cy: number } {
    const svg = this.svgElRef.nativeElement;
    return {
      cx: (svg.clientWidth / 2 - this.viewTx()) / this.viewScale(),
      cy: (svg.clientHeight / 2 - this.viewTy()) / this.viewScale(),
    };
  }

  insertOnEdge(e: MouseEvent, edgeId: string): void {
    e.stopPropagation();
    const edge = this.edges().find(ed => ed.id === edgeId);
    if (!edge) return;
    const mp = this.edgeMidpoint(edge);
    const roles = this.currentRoles();
    const taskCount = this.nodes().filter(n => n.type === 'task').length;
    const nid = `task-${++this._nodeSeq}`;
    const e1 = `e-${++this._edgeSeq}`;
    const e2 = `e-${++this._edgeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: mp.x - NODE_W / 2, y: mp.y - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'task', title: 'Eingefügter Schritt', subtitle: '',
      role: roles[taskCount % roles.length] ?? '', priority: 'Medium', enabled: true,
      routing: { mode: 'auto' as RoutingMode },
      inputs: [], outputs: [],
    }]);
    this.edges.update(es => [
      ...es.filter(ed => ed.id !== edgeId),
      { id: e1, from: edge.from, to: nid, condition: edge.condition },
      { id: e2, from: nid, to: edge.to, condition: 'always' as EdgeCondition },
    ]);
    this.selectedNodeId.set(nid);
    this.selectedEdgeId.set(null);
  }

  deleteNode(nodeId: string): void {
    this.nodes.update(ns => ns.filter(n => n.id !== nodeId));
    this.edges.update(es => es.filter(e => e.from !== nodeId && e.to !== nodeId));
    this.selectedNodeId.set(null);
  }

  patchNode(id: string, patch: Partial<CanvasNode>): void {
    this.nodes.update(ns => ns.map(n => n.id === id ? { ...n, ...patch } : n));
  }

  deleteEdge(edgeId: string): void {
    this.edges.update(es => es.filter(e => e.id !== edgeId));
    this.selectedEdgeId.set(null);
  }

  patchEdge(id: string, patch: Partial<CanvasEdge>): void {
    this.edges.update(es => es.map(e => e.id === id ? { ...e, ...patch } : e));
  }

  // ── Geometry ──────────────────────────────────────────────────────────────

  edgePath(edge: CanvasEdge): string {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return '';
    if (edge.condition === 'back_edge') {
      // Loop: goes left side of graph to target above source
      const sx = src.x, sy = src.y + src.h / 2;
      const tx = dst.x, ty = dst.y + dst.h / 2;
      const lx = Math.min(sx, tx) - 60;
      return `M ${sx} ${sy} C ${lx} ${sy}, ${lx} ${ty}, ${tx} ${ty}`;
    }
    const sx = src.x + src.w / 2, sy = src.y + src.h;
    const tx = dst.x + dst.w / 2, ty = dst.y;
    const dy = Math.max(28, Math.abs(ty - sy) * 0.38);
    return `M ${sx} ${sy} C ${sx} ${sy + dy}, ${tx} ${ty - dy}, ${tx} ${ty}`;
  }

  edgeMidpoint(edge: CanvasEdge): { x: number; y: number } {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return { x: 0, y: 0 };
    return { x: (src.x + src.w / 2 + dst.x + dst.w / 2) / 2, y: (src.y + src.h + dst.y) / 2 };
  }

  edgeMarkerSuffix(edge: CanvasEdge, selected: boolean): string {
    if (selected) return '-sel';
    if (edge.condition === 'on_success') return '-ok';
    if (edge.condition === 'on_failure') return '-fail';
    if (edge.condition === 'back_edge') return '-loop';
    return '';
  }

  nodeLabel(nodeId: string): string {
    return this.nodes().find(n => n.id === nodeId)?.title ?? nodeId;
  }

  // ── Live state ────────────────────────────────────────────────────────────

  nodeIsActive(node: CanvasNode): boolean {
    const activeStep = this.activeWorkflowStepId();
    if (activeStep) return node.id === activeStep;
    const ap = this.autopilot();
    if (!ap.running) return false;
    if (node.type === 'planning') return ap.dispatched_count === 0 && ap.tick_count > 0;
    if (node.type === 'task') return ap.dispatched_count > 0 && ap.completed_count < ap.dispatched_count;
    if (node.type === 'verification') return ap.completed_count > 0;
    return false;
  }

  workerIsActive(w: AnantaWorker): boolean { return this.autopilot().running && w.status === 'online'; }
  workerStatus(name: string): string { return this.workers().find(w => w.name === name)?.status ?? 'offline'; }

  activeWorkflowStepId(): string | null {
    const status = this.workflowStatus();
    const steps = Array.isArray(status?.['steps']) ? status?.['steps'] as Record<string, unknown>[] : [];
    const active = steps.find(s => ['running', 'waiting_for_approval'].includes(String(s['status'] ?? '')));
    return String(active?.['step_id'] ?? active?.['id'] ?? '') || null;
  }

  private startWorkflowPolling(workflowId: string): void {
    this._workflowPollSub?.unsubscribe();
    const load = () => {
      this.svc.getVpWorkflowStatus(workflowId).subscribe(status => this.workflowStatus.set(status));
      this.svc.getVpWorkflowEvents(workflowId).subscribe(events => this.workflowEvents.set(events));
    };
    load();
    this._workflowPollSub = interval(2000).subscribe(() => load());
  }

  // ── Goal Submit ───────────────────────────────────────────────────────────

  submitGoal(): void {
    const text = this.goalText().trim();
    if (!text) return;
    fetch('http://127.0.0.1:5000/goals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        description: text,
        security_level: this.selectedSecurity(),
        config_profile: this.selectedBlueprint(),
        playbook: this.selectedPlaybook(),
      }),
    }).then(r => {
      if (r.ok) {
        this.goalOk.set(true);
        this.goalResult.set('Ziel gesendet.');
        this.goalText.set('');
      } else {
        r.text().then(t => { this.goalOk.set(false); this.goalResult.set(`Fehler ${r.status}: ${t.slice(0, 80)}`); });
      }
    }).catch(err => { this.goalOk.set(false); this.goalResult.set(`Netzwerkfehler: ${err}`); });
  }

  // ── Private ───────────────────────────────────────────────────────────────

  private syncInputBindings(): void {
    const bindingByTarget = new Map<string, ArtifactBinding & { sourceId: string }>();
    for (const edge of this.edges()) {
      for (const binding of edge.bindings ?? []) {
        bindingByTarget.set(`${edge.to}:${binding.inputName}`, { ...binding, sourceId: edge.from });
      }
    }
    this.nodes.update(ns => ns.map(n => ({
      ...n,
      inputs: n.inputs.map(inp => {
        const binding = bindingByTarget.get(`${n.id}:${inp.name}`);
        return {
          ...inp,
          producedByStepId: binding?.sourceId,
          producedByOutputName: binding?.outputName,
        };
      }),
    })));
  }
}
