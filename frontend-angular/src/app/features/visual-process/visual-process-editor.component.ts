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
import { VpStepInspectorComponent } from './vp-step-inspector.component';
import { VpWorkflowRunnerService } from './vp-workflow-runner.service';

import {
  ENCODING_MODES, FALLBACK_KINDS, NODE_H, NODE_W, RAG_CHANNELS,
  autoLayoutGraph, edgeId, emptyGraph, hintColor, nodeKindColor, stepId,
} from './vp-editor-config';

@Component({
  standalone: true,
  selector: 'app-visual-process-editor',
  imports: [FormsModule, VpStepInspectorComponent],
  providers: [VpCanvasInteractionService, VpImportExportService, VpWorkflowRunnerService],
  templateUrl: './visual-process-editor.component.html',
  styleUrls: ['./visual-process-editor.component.scss'],
})
export class VisualProcessEditorComponent implements OnInit, OnDestroy {
  private api = inject(VisualProcessApiService);
  private interaction = inject(VpCanvasInteractionService);
  private importExport = inject(VpImportExportService);
  private workflowRunner = inject(VpWorkflowRunnerService);
  private subs = new Subscription();

  @ViewChild('bpmnFileInput') bpmnFileInputRef!: ElementRef<HTMLInputElement>;
  readonly NODE_W = NODE_W;
  readonly artifactKinds = ['text','code','report','json','file','dataset','image','binary','vector','unknown'];
  readonly edgeKinds = ['always','on_success','on_failure','on_output','back_edge','expression'];
  readonly encodingModes = ENCODING_MODES;
  readonly ragChannels = RAG_CHANNELS;
  graph = signal<VpGraph>(emptyGraph());
  presets = signal<PresetSummary[]>([]);
  skillProfiles = signal<SkillProfile[]>([]);
  taskKindList = signal<TaskKindInfo[]>(FALLBACK_KINDS);
  savedGraphs = signal<SavedGraphSummary[]>([]);
  validationResult = this.workflowRunner.validationResult;
  dryRunResult = this.workflowRunner.dryRunResult;
  mermaidText = signal<string>('');
  mermaidTuiText = signal<string>('');
  statusMsg = this.workflowRunner.status;
  selectedId = signal<string | null>(null);
  edgeMode = signal<boolean>(false);
  edgeSourceId = signal<string | null>(null);
  isDirty = signal<boolean>(false);
  activeWorkflowId = this.workflowRunner.activeWorkflowId;
  workflowStatus = this.workflowRunner.workflowStatus;

  loadPresetMenu = false;
  loadSavedMenu = false;
  showGraphDetails = false;
  mermaidTab: 'mermaid' | 'tui' = 'mermaid';

  private _showMermaidDialog = false;

  readonly drawingEdge = this.interaction.drawingEdge;
  selectedStep = computed<VpStep | null>(() => {
    const id = this.selectedId();
    return this.graph().steps.find(s => s.id === id) ?? null;
  });

  selectedEdge = computed<VpEdge | null>(() => {
    const id = this.selectedId();
    return this.graph().edges.find(e => e.id === id) ?? null;
  });

  readonly canvasTransform = this.interaction.canvasTransform;

  graphTagsStr = computed(() => this.graph().tags.join(', '));

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
    this.workflowRunner.destroy();
  }
  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') return;
    if (e.key === 'Delete' || e.key === 'Backspace') this.deleteSelected();
    if (e.key === 'n' || e.key === 'N') this.addStep();
    if (e.key === 'e' || e.key === 'E') this.toggleEdgeMode();
    if (e.key === 'Escape') { this.edgeMode.set(false); this.edgeSourceId.set(null); this.drawingEdge.set(false); }
  }
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

  validateGraph(): void { this.workflowRunner.validate(this.graph()); }
  runDryRun(): void { this.workflowRunner.dryRun(this.graph()); }
  saveAsBlueprintFromDryRun(): void { this.workflowRunner.saveAsBlueprint(this.graph()); }
  refreshPolicyHints(): void { this.workflowRunner.refreshPolicyHints(this.graph); }
  startWorkflow(): void { this.workflowRunner.start(this.graph); }
  cancelWorkflow(): void { this.workflowRunner.cancel(); }
  approveGate(): void { this.workflowRunner.signalGate('approve', this.gateStepId()); }
  rejectGate(): void { this.workflowRunner.signalGate('reject', this.gateStepId()); }
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

  autoLayout(): void {
    this.graph.update(autoLayoutGraph);
    this.isDirty.set(true);
    this.statusMsg.set('Auto-Layout angewendet');
  }
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
