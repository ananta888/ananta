import {
  AfterViewChecked,
  AfterViewInit,
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
import { interval, Subscription } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { InternalsService, AnantaWorker, AutopilotStatus, VpPreset, VpSkillProfile, VpGraph } from '../services/internals.service';
import { DecimalPipe } from '@angular/common';
import { CodehugWikiGraphComponent } from './codehug-wiki-graph.component';
import { CodehugCanvasInteractionService } from '../services/codehug-canvas-interaction.service';
import { CodehugNodeInspectorComponent } from './codehug-node-inspector.component';
import { CodehugWorkflowRunnerService } from '../services/codehug-workflow-runner.service';

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
  imports: [DecimalPipe, CodehugWikiGraphComponent, CodehugNodeInspectorComponent],
  providers: [CodehugCanvasInteractionService, CodehugWorkflowRunnerService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-internals.component.html',
  styleUrls: ['./codehug-internals.component.scss'],
})
export class CodeHugInternalsComponent implements OnInit, AfterViewInit, AfterViewChecked, OnDestroy {
  @ViewChild('svgEl') svgElRef!: ElementRef<SVGSVGElement>;
  @ViewChild(CodehugNodeInspectorComponent) nodeInspector?: CodehugNodeInspectorComponent;

  private readonly svc = inject(InternalsService);
  private readonly canvas = inject(CodehugCanvasInteractionService);
  private readonly workflowRunner = inject(CodehugWorkflowRunnerService);
  readonly BLUEPRINTS = BLUEPRINTS;
  readonly PLAYBOOKS = PLAYBOOKS;
  readonly PRIORITY_COLOR = PRIORITY_COLOR;
  readonly COND_COLOR = COND_COLOR;
  readonly NODE_STYLE = NODE_STYLE;
  readonly BACKENDS = BACKENDS;
  readonly CAPABILITIES = CAPABILITIES;
  readonly VP_KINDS = VP_KINDS;
  readonly ARTIFACT_KINDS = ARTIFACT_KINDS;
  readonly vpPresets = signal<VpPreset[]>([]);
  readonly skillProfiles = signal<VpSkillProfile[]>([]);
  readonly selectedPresetId = signal('');
  readonly workflowId = this.workflowRunner.workflowId;
  readonly workflowStatus = this.workflowRunner.workflowStatus;
  readonly workflowEvents = this.workflowRunner.workflowEvents;
  readonly dryRunResult = this.workflowRunner.dryRunResult;
  readonly detRunResult = this.workflowRunner.detRunResult;
  readonly detRunning = this.workflowRunner.detRunning;
  readonly workers = signal<AnantaWorker[]>([]);
  readonly autopilot = signal<AutopilotStatus>({
    running: false, goal: '', team_id: '', started_at: null,
    tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
    last_error: null,
    effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
    circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
  });
  readonly selectedBlueprint = signal('scrum');
  readonly selectedPlaybook = signal('bug_fix');
  readonly selectedSecurity = signal('safe');
  readonly maxConcurrency = signal(1);
  readonly goalText = signal('');
  readonly goalResult = this.workflowRunner.goalResult;
  readonly goalOk = this.workflowRunner.goalOk;
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

  readonly activeTab = signal<'vp' | 'graph'>('graph');
  readonly connectMode = signal(false);

  private _svgRegistered = false;

  readonly connectSource = signal<string | null>(null);
  private _nodeSeq = 0;
  private _edgeSeq = 0;
  private _pollSub: Subscription | null = null;

  ngOnInit(): void {
    this.buildCanvas('scrum', 'bug_fix');
    this.svc.getWorkers().subscribe(w => this.workers.set(w));
    this.svc.getAutopilotStatus().subscribe(s => this.autopilot.set(s));
    this.svc.getVpPresets().subscribe(p => this.vpPresets.set(p));
    this.svc.getVpSkillProfiles().subscribe(sp => this.skillProfiles.set(sp));
    this._pollSub = interval(3000).pipe(switchMap(() => this.svc.getAutopilotStatus()))
      .subscribe(s => this.autopilot.set(s));
  }

  ngAfterViewInit(): void {
    this._syncSvgRegistration();
  }

  ngAfterViewChecked(): void {
    this._syncSvgRegistration();
  }

  private _syncSvgRegistration(): void {
    const wantsVp = this.activeTab() === 'vp';
    if (wantsVp && this.svgElRef && !this._svgRegistered) {
      this.canvas.registerSvgElement(this.svgElRef.nativeElement);
      this._svgRegistered = true;
    } else if (!wantsVp && this._svgRegistered) {
      this.canvas.registerSvgElement(null);
      this._svgRegistered = false;
    }
  }

  ngOnDestroy(): void {
    this._pollSub?.unsubscribe();
    this.workflowRunner.destroy();
    this.canvas.registerSvgElement(null);
    this._svgRegistered = false;
  }

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
    this.nodeInspector?.syncInputBindings();
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
    this.workflowRunner.runDetStep(node);
  }

  dryRunWorkflow(): void { this.workflowRunner.dryRun(this.toVpGraph()); }

  startVpWorkflow(): void {
    if (this.goalText().trim()) this.workflowRunner.start(this.toVpGraph());
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

  onBgMouseDown(e: MouseEvent): void {
    const tag = (e.target as SVGElement).tagName;
    if (tag === 'svg' || (e.target as SVGElement).classList.contains('ch-bg-rect')) {
      this.canvas.onBackgroundMouseDown(e);
    }
  }

  onNodeMouseDown(e: MouseEvent, nodeId: string): void {
    if (this.connectMode() || !this.svgElRef) return;
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
    if (!this.svgElRef) return;
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

  onWheel(e: WheelEvent): void {
    if (!this.svgElRef) return;
    this.canvas.onWheel(e, this.svgElRef.nativeElement);
  }

  zoomIn(): void { this.canvas.zoomIn(); }
  zoomOut(): void { this.canvas.zoomOut(); }
  resetView(): void { this.canvas.reset(); }
  toggleConnect(): void { this.connectMode.update(v => !v); if (!this.connectMode()) this.connectSource.set(null); }
  cancelConnect(): void { this.connectMode.set(false); this.connectSource.set(null); }

  addFreeNode(): void { this.nodeInspector?.addNode('task'); }
  addGateNode(): void { this.nodeInspector?.addNode('gate'); }
  addDetNode(): void { this.nodeInspector?.addNode('det'); }
  addReviewNode(): void { this.nodeInspector?.addNode('review'); }
  addForkNode(): void { this.nodeInspector?.addNode('fork'); }
  addJoinNode(): void { this.nodeInspector?.addNode('join'); }
  insertOnEdge(event: MouseEvent, edgeId: string): void {
    event.stopPropagation();
    this.nodeInspector?.insertOnEdge(edgeId);
  }
  edgePath(edge: CanvasEdge): string { return this.nodeInspector?.edgePath(edge) ?? ''; }
  edgeMidpoint(edge: CanvasEdge): { x: number; y: number } {
    return this.nodeInspector?.edgeMidpoint(edge) ?? { x: 0, y: 0 };
  }
  edgeMarkerSuffix(edge: CanvasEdge, selected: boolean): string {
    return this.nodeInspector?.edgeMarkerSuffix(edge, selected) ?? '';
  }
  edgeBindingLabel(edge: CanvasEdge): string { return this.nodeInspector?.edgeBindingLabel(edge) ?? ''; }
  nodeLabel(id: string): string { return this.nodeInspector?.nodeLabel(id) ?? id; }
  forkPoints(node: CanvasNode): string { return this.nodeInspector?.forkPoints(node) ?? ''; }
  isComplexNode(node: CanvasNode): boolean { return this.nodeInspector?.isComplexNode(node) ?? false; }
  routingLabel(node: CanvasNode): string { return this.nodeInspector?.routingLabel(node) ?? ''; }
  routingBadgeW(node: CanvasNode): number { return this.nodeInspector?.routingBadgeW(node) ?? 0; }

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

  activeWorkflowStepId(): string | null { return this.workflowRunner.activeStepId(); }

  submitGoal(): void {
    const text = this.goalText().trim();
    if (!text) return;
    this.workflowRunner.submitClassicGoal(text, {
      securityLevel: this.selectedSecurity(),
      blueprint: this.selectedBlueprint(),
      playbook: this.selectedPlaybook(),
    });
    this.goalText.set('');
  }

}
