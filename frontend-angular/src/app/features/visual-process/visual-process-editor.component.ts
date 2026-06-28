import {
  Component, OnInit, OnDestroy, inject,
  signal, computed, HostListener, ViewChild, ElementRef,
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  VisualProcessApiService,
  VpGraph, VpStep, VpEdge, ArtifactRef,
  ValidationResult, DryRunResult, SkillProfile, PresetSummary,
  TaskKindInfo, SavedGraphSummary, WorkflowStatus, StepExecutionPlan,
} from './visual-process-api.service';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { VpImportExportService } from './vp-import-export.service';

// ── constants ─────────────────────────────────────────────────────────────────
const NODE_W = 140;
const NODE_H = 52;
const FALLBACK_KINDS: TaskKindInfo[] = [
  { id: 'patch_propose',   label: 'Patch Vorschlagen',    group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'plan_only',       label: 'Planen (LLM)',          group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'review',          label: 'Review (LLM)',           group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'none',   uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'run_tests',       label: 'Tests Ausführen',        group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: ['shell_execution'] },
  { id: 'shell_execute',   label: 'Shell Ausführen',        group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'high',   uses_llm: false, uses_network: false, side_effects: ['shell_execution'] },
  { id: 'workspace_snapshot', label: 'Workspace Snapshot', group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'none',   uses_llm: false, uses_network: false, side_effects: ['read_workspace'] },
  { id: 'workspace_diff',    label: 'Workspace Diff',      group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: ['read_workspace', 'write_manifest'] },
  { id: 'fork',            label: 'Fork (Parallel)',        group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'join',            label: 'Join (Sync)',            group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'approval',        label: 'Approval Gate',          group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [], requires_approval: true },
  { id: 'codecompass_index_build',   label: 'CC: Index aufbauen', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'low',  uses_llm: false, uses_network: false, side_effects: ['write_index'] },
  { id: 'codecompass_vector_search', label: 'CC: Semantic Search', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'codecompass_fts_search',    label: 'CC: Full-Text Search', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'codecompass_graph_expand',  label: 'CC: Graph-Expansion', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'embed_api',       label: 'Embedding API',          group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: true,  side_effects: ['network_egress'] },
  { id: 'embed_chunk',     label: 'Chunk + Einbetten',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: true,  side_effects: ['read_workspace', 'network_egress'] },
  { id: 'turboquant_mse',  label: 'TurboQuant MSE (experimentell)', group: 'ml',   dispatch_capable: false, description: '', implementation_status: 'experimental',   implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'sign_rotation',   label: 'Sign-Rotation (TQ-011)', group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'rag_retrieve',    label: 'RAG Abruf',              group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'rerank',          label: 'Reranking',              group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'query_rewrite',   label: 'Query-Erweiterung',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'evolution_analyze',  label: 'Evolution: Analysieren', group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'medium',   uses_llm: true,  uses_network: false, side_effects: ['write_database'] },
  { id: 'evolution_validate', label: 'Evolution: Validieren',  group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'low',      uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'evolution_apply',    label: 'Evolution: Anwenden',    group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'high',     uses_llm: true,  uses_network: false, side_effects: ['write_files', 'write_database'], requires_approval: true },
  { id: 'evolve_prompt',   label: 'Prompt Evolver',         group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'medium',   uses_llm: true,  uses_network: false, side_effects: ['write_database'] },
  { id: 'evolve_project',  label: 'Projekt-Evolver',        group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'critical', uses_llm: true,  uses_network: false, side_effects: ['write_files', 'write_database'], requires_approval: true },
  { id: 'domain_cluster',  label: 'Domain-Clustering',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
];

const ENCODING_MODES = ['off', 'float32', 'float16', 'int8', 'symmetric4bit', 'turboquant_mse_experimental'];
const RAG_CHANNELS   = ['dense', 'lexical', 'symbol', 'codecompass_fts', 'codecompass_vector', 'codecompass_graph'];
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_MS = 10 * 60 * 1000;

function uid(): string { return Math.random().toString(36).slice(2, 10); }
function edgeId(): string { return `edge-${uid()}`; }
function stepId(): string { return `step-${uid()}`; }

function emptyGraph(): VpGraph {
  return { id: `vp-${uid()}`, name: 'Neuer Prozess', description: '', version: '1.0',
           steps: [], edges: [], tags: [], metadata: {} };
}

function hintColor(hints: string[]): string {
  if (hints.includes('high_risk') || hints.includes('mutates_production')) return '#ff6b6b';
  if (hints.includes('requires_approval')) return '#fdcb6e';
  if (hints.includes('evolution') || hints.includes('self_modifying')) return '#e84393';
  if (hints.includes('index_write')) return '#6c5ce7';
  if (hints.includes('retrieval')) return '#a29bfe';
  if (hints.includes('vector_operation') || hints.includes('quantization')) return '#00b894';
  if (hints.includes('ml_inference')) return '#00cec9';
  if (hints.includes('read_only')) return '#74b9ff';
  return '#636e72';
}

const RETRIEVAL_KINDS = new Set([
  'codecompass_index_build', 'codecompass_vector_search',
  'codecompass_fts_search', 'codecompass_graph_expand',
]);
const EVOLUTION_KINDS = new Set([
  'evolution_analyze', 'evolution_validate', 'evolution_apply',
  'evolve_prompt', 'evolve_project',
]);
const WORKSPACE_KINDS = new Set(['workspace_snapshot', 'workspace_diff']);

function nodeKindColor(kind: string): string {
  if (kind === 'fork' || kind === 'join' || kind === 'parallel') return '#00b894';
  if (kind === 'approval') return '#55efc4';
  if (RETRIEVAL_KINDS.has(kind)) return '#6c5ce7';
  if (EVOLUTION_KINDS.has(kind)) return '#e84393';
  if (WORKSPACE_KINDS.has(kind)) return '#b2bec3';
  if (kind === 'turboquant_mse' || kind === 'sign_rotation') return '#00b894';
  if (kind === 'embed_api' || kind === 'embed_chunk') return '#00cec9';
  return '';
}

@Component({
  standalone: true,
  selector: 'app-visual-process-editor',
  imports: [FormsModule],
  providers: [VpCanvasInteractionService, VpImportExportService],
  templateUrl: './visual-process-editor.component.html',
  styleUrls: ['./visual-process-editor.component.scss'],
})
export class VisualProcessEditorComponent implements OnInit, OnDestroy {
  private api = inject(VisualProcessApiService);
  private interaction = inject(VpCanvasInteractionService);
  private importExport = inject(VpImportExportService);
  private subs = new Subscription();

  @ViewChild('bpmnFileInput') bpmnFileInputRef!: ElementRef<HTMLInputElement>;

  // ── public constants for template ─────────────────────────────────────────
  readonly NODE_W = NODE_W;
  readonly artifactKinds = ['text','code','report','json','file','dataset','image','binary','vector','unknown'];
  readonly edgeKinds = ['always','on_success','on_failure','on_output','back_edge','expression'];
  readonly encodingModes = ENCODING_MODES;
  readonly ragChannels = RAG_CHANNELS;

  // ── state ──────────────────────────────────────────────────────────────────
  graph = signal<VpGraph>(emptyGraph());
  presets = signal<PresetSummary[]>([]);
  skillProfiles = signal<SkillProfile[]>([]);
  taskKindList = signal<TaskKindInfo[]>(FALLBACK_KINDS);
  savedGraphs = signal<SavedGraphSummary[]>([]);
  validationResult = signal<ValidationResult | null>(null);
  dryRunResult = signal<DryRunResult | null>(null);
  mermaidText = signal<string>('');
  mermaidTuiText = signal<string>('');
  statusMsg = signal<string>('');
  selectedId = signal<string | null>(null);
  edgeMode = signal<boolean>(false);
  edgeSourceId = signal<string | null>(null);
  isDirty = signal<boolean>(false);
  activeWorkflowId = signal<string | null>(null);
  workflowStatus = signal<WorkflowStatus | null>(null);

  loadPresetMenu = false;
  loadSavedMenu = false;
  showGraphDetails = false;
  mermaidTab: 'mermaid' | 'tui' = 'mermaid';

  private _showMermaidDialog = false;

  readonly drawingEdge = this.interaction.drawingEdge;

  // ── polling ────────────────────────────────────────────────────────────────
  private _pollHandle: ReturnType<typeof setInterval> | null = null;
  private _pollStartTime = 0;

  // ── computed ───────────────────────────────────────────────────────────────
  selectedStep = computed<VpStep | null>(() => {
    const id = this.selectedId();
    return this.graph().steps.find(s => s.id === id) ?? null;
  });

  selectedEdge = computed<VpEdge | null>(() => {
    const id = this.selectedId();
    return this.graph().edges.find(e => e.id === id) ?? null;
  });

  readonly canvasTransform = this.interaction.canvasTransform;

  kindGroups = computed(() => {
    const groups: Record<string, TaskKindInfo[]> = {};
    for (const k of this.taskKindList()) {
      (groups[k.group] ??= []).push(k);
    }
    const order = ['control_flow', 'worker', 'retrieval', 'ml'];
    return order.filter(g => groups[g]).map(g => ({ group: g, kinds: groups[g] }));
  });

  graphTagsStr = computed(() => this.graph().tags.join(', '));

  stepDescription = computed(() =>
    (this.selectedStep()?.metadata?.['description'] as string) ?? ''
  );

  gateStepId = computed<string | null>(() => {
    const status = this.workflowStatus();
    if (!status) return null;
    const steps = status['steps'] as any[] | undefined;
    if (!steps) return null;
    const graphSteps = this.graph().steps;
    const found = steps.find(
      s => s.run_state === 'awaiting_approval' && graphSteps.find(gs => gs.id === s.step_id)?.gate,
    );
    return found?.step_id ?? null;
  });

  expressionError = computed<string | null>(() => {
    const edge = this.selectedEdge();
    const result = this.validationResult();
    if (!edge || !result) return null;
    return result.issues.find(
      i => i.code === 'expression_syntax_error' && i.edge_id === edge.id,
    )?.message ?? null;
  });

  dryRunSummary = computed(() => {
    const r = this.dryRunResult();
    if (!r) return '';
    return JSON.stringify({
      valid: r.validation.valid,
      errors: r.validation.error_count,
      warnings: r.validation.warning_count,
      step_count: r.step_count,
      non_executable_count: r.non_executable_count ?? 0,
      policy: r.policy_summary,
    }, null, 2);
  });

  selectedStepKindInfo = computed<TaskKindInfo | null>(() => {
    const step = this.selectedStep();
    if (!step) return null;
    return this.taskKindList().find(k => k.id === step.kind) ?? null;
  });

  hasNonExecutableSteps = computed(() => {
    const plan = this.dryRunResult()?.step_execution_plan;
    if (plan) return plan.some(p => !p.executable);
    return false;
  });

  canStartWorkflow = computed(() => {
    if (!this.validationResult()?.valid) return false;
    if (this.activeWorkflowId()) return false;
    return true;
  });

  kindOptionSuffix(k: TaskKindInfo): string {
    const status = k.implementation_status;
    if (status === 'experimental') return ' [exp]';
    if (status === 'stub' || status === 'not_implemented') return ' [stub]';
    if (status === 'design_only') return ' [design]';
    const state = k.implementation_state;
    if (state === 'registered_only') return ' [reg]';
    if (!k.dispatch_capable && state === 'wired_and_executable') return ' (ML)';
    if (!k.dispatch_capable) return ' (ML)';
    return '';
  }

  // ── lifecycle ──────────────────────────────────────────────────────────────
  ngOnInit(): void {
    this.subs.add(this.api.listPresets().subscribe(p => this.presets.set(p)));
    this.subs.add(this.api.listSkillProfiles().subscribe(p => this.skillProfiles.set(p)));
    this.subs.add(this.api.listTaskKinds().subscribe({
      next: k => this.taskKindList.set(k),
      error: () => { /* keep fallback */ },
    }));
    this.subs.add(this.api.listSavedGraphs().subscribe({
      next: g => this.savedGraphs.set(g),
      error: () => { /* ignore if backend not running */ },
    }));
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
    this.stopPolling();
  }

  // ── keyboard ──────────────────────────────────────────────────────────────
  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') return;
    if (e.key === 'Delete' || e.key === 'Backspace') this.deleteSelected();
    if (e.key === 'n' || e.key === 'N') this.addStep();
    if (e.key === 'e' || e.key === 'E') this.toggleEdgeMode();
    if (e.key === 'Escape') { this.edgeMode.set(false); this.edgeSourceId.set(null); this.drawingEdge.set(false); }
  }

  // ── canvas interaction ─────────────────────────────────────────────────────
  onCanvasMouseDown(e: MouseEvent): void {
    this.interaction.onCanvasMouseDown(e, () => {
      this.selectedId.set(null);
      this.loadPresetMenu = false;
      this.loadSavedMenu = false;
    });
  }

  onMouseMove(e: MouseEvent): void {
    this.interaction.onMouseMove(e, (id, mutate) => this.mutateStep(id, mutate));
  }

  onMouseUp(e: MouseEvent): void { this.interaction.onMouseUp(e); }

  onWheel(e: WheelEvent): void { this.interaction.onWheel(e); }

  onNodeMouseDown(e: MouseEvent, id: string): void {
    const step = this.graph().steps.find(candidate => candidate.id === id);
    if (step) this.interaction.onNodeMouseDown(e, id, step, this.edgeMode());
  }

  selectStep(id: string): void {
    if (this.edgeMode()) {
      const src = this.edgeSourceId();
      if (!src) {
        this.edgeSourceId.set(id);
        this.drawingEdge.set(true);
        this.statusMsg.set('Klicke Zielknoten…');
      } else if (src !== id) {
        this.addEdge(src, id);
        this.edgeMode.set(false);
        this.edgeSourceId.set(null);
        this.drawingEdge.set(false);
        this.statusMsg.set('Kante hinzugefügt');
      }
      return;
    }
    this.selectedId.set(id);
  }

  selectEdge(id: string): void {
    if (this.edgeMode()) return;
    this.selectedId.set(id);
  }

  // ── graph mutations ────────────────────────────────────────────────────────
  addStep(): void {
    const id = stepId();
    const x = 60 + Math.random() * 300;
    const y = 80 + Math.random() * 200;
    const newStep: VpStep = {
      id, label: 'Neuer Schritt', kind: 'patch_propose', role: '',
      io: { inputs: [], outputs: [] },
      position: { x, y }, policy_hints: [], gate: false, metadata: {},
    };
    this.graph.update(g => ({ ...g, steps: [...g.steps, newStep] }));
    this.selectedId.set(id);
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  addEdge(source: string, target: string): void {
    const e: VpEdge = { id: edgeId(), source, target, condition: { kind: 'always' } };
    this.graph.update(g => ({ ...g, edges: [...g.edges, e] }));
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  deleteSelected(): void {
    const id = this.selectedId();
    if (!id) return;
    this.graph.update(g => ({
      ...g,
      steps: g.steps.filter(s => s.id !== id),
      edges: g.edges.filter(e => e.id !== id && e.source !== id && e.target !== id),
    }));
    this.selectedId.set(null);
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  toggleEdgeMode(): void {
    const next = !this.edgeMode();
    this.edgeMode.set(next);
    if (!next) { this.edgeSourceId.set(null); this.drawingEdge.set(false); this.statusMsg.set(''); }
    else this.statusMsg.set('Kante-Modus: klicke Quell-Knoten');
  }

  setGraphName(val: string): void {
    this.graph.update(g => ({ ...g, name: val }));
    this.isDirty.set(true);
  }

  setGraphDescription(val: string): void {
    this.graph.update(g => ({ ...g, description: val }));
    this.isDirty.set(true);
  }

  setTags(val: string): void {
    const tags = val.split(',').map(t => t.trim()).filter(Boolean);
    this.graph.update(g => ({ ...g, tags }));
    this.isDirty.set(true);
  }

  // ── step inspector helpers ─────────────────────────────────────────────────
  mutateSelectedStep(fn: (s: VpStep) => void): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, fn);
    this.isDirty.set(true);
  }

  onKindChange(kind: string): void {
    this.mutateSelectedStep(s => s.kind = kind);
    setTimeout(() => this.refreshPolicyHints(), 200);
  }

  setStepDescription(val: string): void {
    this.mutateSelectedStep(s => {
      s.metadata = { ...(s.metadata ?? {}), description: val };
    });
  }

  stepMeta(key: string): any {
    return this.selectedStep()?.metadata?.[key] ?? null;
  }

  setStepMeta(key: string, val: unknown): void {
    this.mutateSelectedStep(s => {
      s.metadata = { ...(s.metadata ?? {}), [key]: val };
    });
  }

  setStepLabel(val: string): void { this.mutateSelectedStep(s => s.label = val); }
  setStepRole(val: string): void { this.mutateSelectedStep(s => s.role = val); }
  setStepSkillProfile(val: string): void {
    this.mutateSelectedStep(s => s.agent_skill_profile_id = val);
  }
  setStepGate(val: boolean): void { this.mutateSelectedStep(s => s.gate = val); }

  isChannelSelected(ch: string): boolean {
    const channels = this.stepMeta('channels') as string[] | null;
    return channels ? channels.includes(ch) : ch === 'dense' || ch === 'lexical';
  }

  toggleChannel(ch: string, selected: boolean): void {
    const current = (this.stepMeta('channels') as string[] | null) ?? ['dense', 'lexical'];
    const next = selected ? [...new Set([...current, ch])] : current.filter(c => c !== ch);
    this.setStepMeta('channels', next);
  }

  // ── edge inspector helpers ─────────────────────────────────────────────────
  mutateEdge(fn: (e: VpEdge) => void): void {
    const edge = this.selectedEdge();
    if (!edge) return;
    this.graph.update(g => ({
      ...g,
      edges: g.edges.map(e => {
        if (e.id !== edge.id) return e;
        const copy = JSON.parse(JSON.stringify(e)) as VpEdge;
        fn(copy);
        return copy;
      }),
    }));
    this.isDirty.set(true);
  }

  setLoopPolicy(field: 'kind' | 'condition' | 'break_on_output' | 'max_iterations', value: unknown): void {
    this.mutateEdge(e => {
      if (!e.condition.loop_policy) {
        e.condition.loop_policy = { kind: 'fixed', max_iterations: 3 };
      }
      (e.condition.loop_policy as any)[field] = value;
    });
  }

  setEdgeLabel(val: string): void {
    this.mutateEdge(e => e.label = val || undefined);
  }
  setEdgeConditionKind(val: string): void {
    this.mutateEdge(e => e.condition.kind = val as any);
  }
  setEdgeExpression(val: string): void {
    this.mutateEdge(e => e.condition.expression = val);
  }
  setEdgeOutputName(val: string): void {
    this.mutateEdge(e => e.condition.output_name = val);
  }

  // ── io helpers ─────────────────────────────────────────────────────────────
  mutateIOInput(idx: number, field: string, val: unknown): void {
    this.mutateSelectedStep(s => { (s.io.inputs[idx] as any)[field] = val; });
  }

  mutateIOOutput(idx: number, field: string, val: unknown): void {
    this.mutateSelectedStep(s => { (s.io.outputs[idx] as any)[field] = val; });
  }

  addInput(): void {
    this.mutateSelectedStep(s => s.io.inputs.push({ name: 'input', kind: 'text', required: true }));
  }

  removeInput(idx: number): void {
    this.mutateSelectedStep(s => s.io.inputs.splice(idx, 1));
  }

  addOutput(): void {
    this.mutateSelectedStep(s => s.io.outputs.push({ name: 'output', kind: 'text', required: false }));
  }

  removeOutput(idx: number): void {
    this.mutateSelectedStep(s => s.io.outputs.splice(idx, 1));
  }

  applyProfile(profileId: string): void {
    const step = this.selectedStep();
    if (!step) { this.statusMsg.set('Wähle zuerst einen Schritt aus'); return; }
    const profile = this.skillProfiles().find(p => p.id === profileId);
    this.mutateStep(step.id, s => {
      s.agent_skill_profile_id = profileId;
      if (profile?.task_kinds?.[0]) s.kind = profile.task_kinds[0];
    });
    this.statusMsg.set(`Profil "${profileId}" angewendet`);
    this.isDirty.set(true);
    setTimeout(() => this.refreshPolicyHints(), 200);
  }

  // ── presets / saved ────────────────────────────────────────────────────────
  loadPreset(id: string): void {
    this.loadPresetMenu = false;
    this.subs.add(this.api.getPreset(id).subscribe({
      next: g => {
        this.graph.set(g);
        this.selectedId.set(null);
        this.validationResult.set(null);
        this.isDirty.set(false);
        this.statusMsg.set(`Preset "${g.name}" geladen`);
        setTimeout(() => this.refreshPolicyHints(), 300);
      },
      error: () => this.statusMsg.set('Preset konnte nicht geladen werden'),
    }));
  }

  loadSavedGraphById(id: string): void {
    this.loadSavedMenu = false;
    this.subs.add(this.api.loadSavedGraph(id).subscribe({
      next: g => {
        this.graph.set(g);
        this.selectedId.set(null);
        this.validationResult.set(null);
        this.isDirty.set(false);
        this.statusMsg.set(`"${g.name}" geladen`);
        setTimeout(() => this.refreshPolicyHints(), 300);
      },
      error: () => this.statusMsg.set('Graph konnte nicht geladen werden'),
    }));
  }

  saveGraphToServer(): void {
    this.subs.add(this.api.saveGraph(this.graph()).subscribe({
      next: r => {
        this.isDirty.set(false);
        this.statusMsg.set(`Gespeichert ✓ (${this.graph().name})`);
        this.api.listSavedGraphs().subscribe(g => this.savedGraphs.set(g));
      },
      error: () => this.statusMsg.set('Speichern fehlgeschlagen'),
    }));
  }

  // ── validation / dry-run ───────────────────────────────────────────────────
  validateGraph(): void {
    this.subs.add(this.api.validate(this.graph()).subscribe({
      next: r => { this.validationResult.set(r); this.statusMsg.set(r.valid ? 'Gültig ✓' : `${r.error_count} Fehler`); },
      error: () => this.statusMsg.set('Validierung fehlgeschlagen'),
    }));
  }

  runDryRun(): void {
    this.statusMsg.set('Dry-Run läuft…');
    this.subs.add(this.api.dryRun(this.graph()).subscribe({
      next: r => { this.dryRunResult.set(r); this.validationResult.set(r.validation); this.statusMsg.set('Dry-Run abgeschlossen'); },
      error: () => this.statusMsg.set('Dry-Run fehlgeschlagen'),
    }));
  }

  saveAsBlueprintFromDryRun(): void {
    this.subs.add(this.api.saveAsBlueprint(this.graph()).subscribe({
      next: r => this.statusMsg.set(`Blueprint gespeichert (id: ${r.blueprint_id})`),
      error: (err) => this.statusMsg.set(`Blueprint-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  // ── policy hints ───────────────────────────────────────────────────────────
  refreshPolicyHints(): void {
    this.subs.add(this.api.policySummary(this.graph()).subscribe({
      next: result => {
        this.graph.update(g => ({
          ...g,
          steps: g.steps.map(s => ({
            ...s,
            policy_hints: result.per_step[s.id] ?? s.policy_hints,
          })),
        }));
      },
      error: () => { /* keep existing hints */ },
    }));
  }

  // ── workflow execution ─────────────────────────────────────────────────────
  startWorkflow(): void {
    this.subs.add(this.api.startWorkflowFromGraph(this.graph()).subscribe({
      next: status => {
        this.activeWorkflowId.set(status.workflow_id);
        this.workflowStatus.set(status);
        this.statusMsg.set(`Workflow gestartet (id: ${status.workflow_id})`);
        this.startPolling();
      },
      error: (err) => this.statusMsg.set(`Fehler: ${err?.error?.detail ?? 'Workflow konnte nicht gestartet werden'}`),
    }));
  }

  cancelWorkflow(): void {
    const id = this.activeWorkflowId();
    if (!id) return;
    this.subs.add(this.api.cancelWorkflow(id).subscribe({
      next: () => { this.stopPolling(); this.statusMsg.set('Workflow abgebrochen'); },
      error: () => this.statusMsg.set('Abbrechen fehlgeschlagen'),
    }));
  }

  approveGate(): void {
    const wfId = this.activeWorkflowId();
    const gateId = this.gateStepId();
    if (!wfId || !gateId) return;
    this.subs.add(this.api.signalWorkflow(wfId, 'approve', { step_id: gateId }).subscribe({
      next: () => this.statusMsg.set('Gate genehmigt ✓'),
      error: (err) => this.statusMsg.set(`Gate-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  rejectGate(): void {
    const wfId = this.activeWorkflowId();
    const gateId = this.gateStepId();
    if (!wfId || !gateId) return;
    this.subs.add(this.api.signalWorkflow(wfId, 'reject', { step_id: gateId }).subscribe({
      next: () => this.statusMsg.set('Gate abgelehnt'),
      error: (err) => this.statusMsg.set(`Gate-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  private startPolling(): void {
    this.stopPolling();
    this._pollStartTime = Date.now();
    this._pollHandle = setInterval(() => {
      const id = this.activeWorkflowId();
      if (!id) { this.stopPolling(); return; }
      if (Date.now() - this._pollStartTime > POLL_MAX_MS) {
        this.stopPolling();
        this.statusMsg.set('Polling-Timeout (10 min) — Workflow-Status unbekannt');
        return;
      }
      this.api.getWorkflowStatus(id).subscribe({
        next: status => {
          this.workflowStatus.set(status);
          // Patch run_state back onto graph steps if backend provides them
          const steps = status['steps'] as any[] | undefined;
          if (steps?.length) {
            this.graph.update(g => ({
              ...g,
              steps: g.steps.map(s => {
                const found = steps.find((ws: any) => ws.step_id === s.id);
                return found ? { ...s, run_state: found.run_state } : s;
              }),
            }));
          }
          if (['done', 'failed', 'cancelled'].includes(status.status)) {
            this.stopPolling();
            this.activeWorkflowId.set(null);
            const msg = status.status === 'done' ? 'Workflow abgeschlossen ✓' : `Workflow ${status.status}`;
            this.statusMsg.set(msg);
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this._pollHandle !== null) {
      clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
  }

  // ── BPMN ───────────────────────────────────────────────────────────────────
  exportBpmn(): void {
    this.subs.add(this.importExport.exportBpmn(this.graph()).subscribe({
      next: result => {
        this.statusMsg.set(result.warnings?.length
          ? `BPMN exportiert (Warnungen: ${result.warnings.join(', ')})`
          : 'BPMN exportiert ✓');
      },
      error: (err) => this.statusMsg.set(`BPMN-Export-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  onBpmnFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    this.subs.add(this.importExport.importBpmn(file).subscribe({
        next: result => {
          this.graph.set(result.graph);
          this.validationResult.set(result.validation);
          this.isDirty.set(true);
          const warns = result.warnings?.length ? ` (${result.warnings.join(', ')})` : '';
          this.statusMsg.set(`BPMN importiert: ${result.graph.steps.length} Schritte${warns}`);
          setTimeout(() => this.refreshPolicyHints(), 300);
        },
        error: (err) => this.statusMsg.set(`BPMN-Import-Fehler: ${err?.error?.detail ?? 'ungültige Datei'}`),
      }));
    (event.target as HTMLInputElement).value = '';
  }

  // ── Mermaid ────────────────────────────────────────────────────────────────
  openMermaid(): void {
    this._showMermaidDialog = true;
    this.mermaidTab = 'mermaid';
    this.subs.add(this.importExport.mermaid(this.graph()).subscribe({
      next: r => { this.mermaidText.set(r.mermaid); this.mermaidTuiText.set(r.tui ?? ''); },
      error: () => this.mermaidText.set('Fehler beim Laden'),
    }));
  }

  get showMermaidDialog(): boolean { return this._showMermaidDialog; }
  set showMermaidDialog(val: boolean) { this._showMermaidDialog = val; }

  copyMermaid(): void {
    this.importExport.copyMermaid(this.mermaidText()).then(() => this.statusMsg.set('Mermaid kopiert ✓'));
  }

  downloadMermaid(): void {
    this.importExport.downloadMermaid(this.mermaidText(), this.graph().name);
  }

  // ── auto-layout ────────────────────────────────────────────────────────────
  autoLayout(): void {
    const g = this.graph();
    const forwardEdges = g.edges.filter(e => e.condition.kind !== 'back_edge');
    const inDegree: Record<string, number> = {};
    const adj: Record<string, string[]> = {};
    for (const s of g.steps) { inDegree[s.id] = 0; adj[s.id] = []; }
    for (const e of forwardEdges) {
      inDegree[e.target] = (inDegree[e.target] ?? 0) + 1;
      adj[e.source].push(e.target);
    }
    // Kahn's algorithm
    const queue = g.steps.filter(s => !inDegree[s.id]).map(s => s.id);
    const depthMap: Record<string, number> = {};
    let order: string[] = [];
    while (queue.length) {
      const node = queue.shift()!;
      order.push(node);
      for (const next of adj[node]) {
        inDegree[next]--;
        depthMap[next] = Math.max(depthMap[next] ?? 0, (depthMap[node] ?? 0) + 1);
        if (inDegree[next] === 0) queue.push(next);
      }
    }
    // Add any remaining (in cycles, shouldn't happen after validation)
    const placed = new Set(order);
    for (const s of g.steps) if (!placed.has(s.id)) order.push(s.id);

    // Group by column
    const cols: Record<number, string[]> = {};
    for (const id of order) {
      const col = depthMap[id] ?? 0;
      (cols[col] ??= []).push(id);
    }
    const colGap = NODE_W + 60;
    const rowGap = NODE_H + 40;
    const newPositions: Record<string, { x: number; y: number }> = {};
    for (const [colStr, ids] of Object.entries(cols)) {
      const col = Number(colStr);
      ids.forEach((id, row) => {
        newPositions[id] = { x: 40 + col * colGap, y: 40 + row * rowGap };
      });
    }
    this.graph.update(prev => ({
      ...prev,
      steps: prev.steps.map(s => ({
        ...s,
        position: newPositions[s.id] ?? s.position,
      })),
    }));
    this.isDirty.set(true);
    this.statusMsg.set('Auto-Layout angewendet');
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  formatDate(ts: number): string {
    return new Date(ts * 1000).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  edgePath(edge: VpEdge): string { return this.interaction.edgePath(edge, this.graph().steps); }

  edgeMidpoint(edge: VpEdge): { x: number; y: number } {
    return this.interaction.edgeMidpoint(edge, this.graph().steps);
  }

  liveEdgePath(): string {
    return this.interaction.liveEdgePath(this.graph().steps, this.edgeSourceId());
  }

  diamondPoints(): string {
    return this.interaction.diamondPoints();
  }

  nodeColor(step: VpStep): string {
    const kindOverride = nodeKindColor(step.kind);
    if (kindOverride) return kindOverride;
    return hintColor(step.policy_hints);
  }

  runStateColor(state: string): string {
    const m: Record<string, string> = { done: '#55efc4', running: '#fdcb6e', failed: '#ff7675', pending: '#636e72', skipped: '#b2bec3', awaiting_approval: '#e17055' };
    return m[state] ?? '#636e72';
  }

  stepLabel(id: string): string {
    return this.graph().steps.find(s => s.id === id)?.label ?? id;
  }

  private mutateStep(id: string, fn: (s: VpStep) => void): void {
    this.graph.update(g => ({
      ...g,
      steps: g.steps.map(s => {
        if (s.id !== id) return s;
        const copy = JSON.parse(JSON.stringify(s)) as VpStep;
        fn(copy);
        return copy;
      }),
    }));
  }
}
