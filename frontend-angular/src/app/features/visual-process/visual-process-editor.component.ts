import {
  Component, OnInit, OnDestroy, OnChanges, Input, SimpleChanges, inject,
  signal, computed, HostListener, ViewChild, ElementRef, effect, InjectionToken,
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subscription, map } from 'rxjs';
import {
  VisualProcessApiService,
  CONTEXT_RECOVERY_STRATEGIES, ContextRecoveryStrategy,
  VpGraph, VpStep, VpEdge,
  SkillProfile, PresetSummary,
  TaskKindInfo, SavedGraphSummary,
  ModelProfileSummary, ModelRoutingConfig, FallbackGroupSummary,
} from './visual-process-api.service';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { VpImportExportService } from './vp-import-export.service';
import { VpStepInspectorComponent } from './vp-step-inspector.component';
import { VpWorkflowRunnerService } from './vp-workflow-runner.service';
import { VisualProcessCanvasComponent } from './visual-process-canvas.component';
import {
  VP_EDITOR_HISTORY_LIMIT,
  VpEditorStateFacade,
} from './vp-editor-state.facade';
import {
  VP_EDITOR_PERSISTENCE,
  VP_EDITOR_STATE,
  VpEditorStatePort,
} from './vp-editor-state.port';
import { VpModelTrainingOptionsService } from './vp-model-training-options.service';
import { CanvasHitTarget } from './vp-editor-context.models';
import { VpAssistantBridgeService } from './vp-assistant-bridge.service';
import { VpAssistantBubbleComponent } from './vp-assistant-bubble.component';
import { VP_NODE_REGISTRY_VERSION, VpNodeDefinitionRegistryService } from './vp-node-definition-registry.service';
import { VpNodePaletteComponent } from './vp-node-palette.component';
import { VpResourceOptionProvider } from './vp-resource-option-provider';
import { VpAssistantPatchPreview } from './vp-assistant-api.service';
import { VpWorkflowPatchPreviewComponent } from './vp-workflow-patch-preview.component';
import { validateVpPresetDirectLoad } from './vp-preset-load.policy';
import {
  DatasetSummary,
  TrainingBaseModel,
  TrainingGpuProfile,
} from '../model-training/model-training.models';

import {
  ENCODING_MODES, FALLBACK_KINDS, NODE_H, NODE_W, RAG_CHANNELS,
  autoLayoutGraph, edgeId, hintColor, nodeKindColor, stepId,
} from './vp-editor-config';

interface ResolvedVpEditorState {
  readonly state: VpEditorStatePort;
  readonly hosted: boolean;
}

const VP_EDITOR_COMPONENT_STATE = new InjectionToken<ResolvedVpEditorState>(
  'VP_EDITOR_COMPONENT_STATE',
);

function resolveVpEditorState(): ResolvedVpEditorState {
  const hosted = inject(VP_EDITOR_STATE, { optional: true, skipSelf: true });
  return hosted
    ? { state: hosted, hosted: true }
    : {
      state: new VpEditorStateFacade(inject(VP_EDITOR_HISTORY_LIMIT)),
      hosted: false,
    };
}

@Component({
  standalone: true,
  selector: 'app-visual-process-editor',
  imports: [FormsModule, VpStepInspectorComponent, VisualProcessCanvasComponent, VpNodePaletteComponent, VpAssistantBubbleComponent, VpWorkflowPatchPreviewComponent],
  providers: [
    VpCanvasInteractionService,
    VpImportExportService,
    VpWorkflowRunnerService,
    VpAssistantBridgeService,
    VpResourceOptionProvider,
    { provide: VP_EDITOR_COMPONENT_STATE, useFactory: resolveVpEditorState },
  ],
  templateUrl: './visual-process-editor.component.html',
  styleUrls: ['./visual-process-editor.component.scss'],
})
export class VisualProcessEditorComponent implements OnInit, OnDestroy, OnChanges {
  @Input() graphId = '';
  @Input() editorMode: 'compact-readonly'|'embedded-edit'|'full-editor' = 'full-editor';
  private api = inject(VisualProcessApiService);
  private interaction = inject(VpCanvasInteractionService);
  private importExport = inject(VpImportExportService);
  private workflowRunner = inject(VpWorkflowRunnerService);
  private readonly resolvedEditorState = inject(VP_EDITOR_COMPONENT_STATE);
  private readonly editorState = this.resolvedEditorState.state;
  readonly graphStateHosted = this.resolvedEditorState.hosted;
  private readonly hostedPersistence = inject(VP_EDITOR_PERSISTENCE, {
    optional: true,
    skipSelf: true,
  });
  private trainingOptions = inject(VpModelTrainingOptionsService);
  private nodeRegistry = inject(VpNodeDefinitionRegistryService);
  private resourceOptions = inject(VpResourceOptionProvider);
  readonly assistant = inject(VpAssistantBridgeService);
  private subs = new Subscription();
  private suppressNextAssistantSelection = false;
  private presetLoadGeneration = 0;
  private policyRefreshGeneration = 0;

  @ViewChild('bpmnFileInput') bpmnFileInputRef!: ElementRef<HTMLInputElement>;
  readonly NODE_W = NODE_W;
  readonly NODE_H = NODE_H;
  readonly artifactKinds = ['text','code','report','json','file','dataset','image','binary','vector','unknown'];
  readonly edgeKinds = ['always','on_success','on_failure','on_output','back_edge','expression'];
  readonly encodingModes = ENCODING_MODES;
  readonly ragChannels = RAG_CHANNELS;
  graph = this.editorState.graph;
  presets = signal<PresetSummary[]>([]);
  skillProfiles = signal<SkillProfile[]>([]);
  taskKindList = signal<TaskKindInfo[]>(FALLBACK_KINDS);
  nodeDefinitions = signal(this.nodeRegistry.definitions(FALLBACK_KINDS));
  registrySource = signal<'backend' | 'fallback' | 'degraded'>('fallback');
  registryStatus = signal(`Offline-Fallback ${VP_NODE_REGISTRY_VERSION}`);
  saveConflict = signal(false);
  savedGraphs = signal<SavedGraphSummary[]>([]);
  modelProfiles = signal<ModelProfileSummary[]>([]);
  fallbackGroups = signal<Record<string, FallbackGroupSummary>>({});
  trainingDatasets = signal<DatasetSummary[]>([]);
  trainingProfiles = signal<TrainingGpuProfile[]>([]);
  trainingBaseModels = signal<TrainingBaseModel[]>([]);
  validationResult = this.editorState.validation;
  dryRunResult = this.workflowRunner.dryRunResult;
  mermaidText = signal<string>('');
  mermaidTuiText = signal<string>('');
  statusMsg = this.workflowRunner.status;
  selectedId = this.editorState.selectedId;
  edgeMode = this.editorState.edgeMode;
  edgeSourceId = this.editorState.edgeSourceId;
  isDirty = this.editorState.dirty;
  activeWorkflowId = this.workflowRunner.activeWorkflowId;
  workflowStatus = this.workflowRunner.workflowStatus;
  runtimeOverlay = this.workflowRunner.runtimeOverlay;

  private _loadPresetMenu = false;
  private _loadSavedMenu = false;
  private _showGraphDetails = false;
  private _showNodePalette = false;
  mermaidTab: 'mermaid' | 'tui' = 'mermaid';

  private _showMermaidDialog = false;
  constructor() {
    effect(() => {
      const validation = this.workflowRunner.validationResult();
      if (!this.graphStateHosted || validation !== null) {
        this.editorState.validation.set(validation);
      }
    });
    effect(() => {
      this.dryRunResult();
      this.assistant.patchPreview();
      this.edgeMode();
      this.drawingEdge();
      this.syncAssistantPreviewSuppression();
    });
  }

  get loadPresetMenu(): boolean { return this._loadPresetMenu; }
  set loadPresetMenu(value: boolean) {
    this._loadPresetMenu = value;
    this.syncAssistantPreviewSuppression();
  }

  get loadSavedMenu(): boolean { return this._loadSavedMenu; }
  set loadSavedMenu(value: boolean) {
    this._loadSavedMenu = value;
    this.syncAssistantPreviewSuppression();
  }

  get showGraphDetails(): boolean { return this._showGraphDetails; }
  set showGraphDetails(value: boolean) {
    this._showGraphDetails = value;
    this.syncAssistantPreviewSuppression();
  }

  get showNodePalette(): boolean { return this._showNodePalette; }
  set showNodePalette(value: boolean) {
    this._showNodePalette = value;
    this.syncAssistantPreviewSuppression();
  }

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
  readonly canUndo = this.editorState.canUndo;
  readonly canRedo = this.editorState.canRedo;
  readonly undoLabel = this.editorState.undoLabel;
  readonly redoLabel = this.editorState.redoLabel;
  readonly applyInspectorMutation = (label: string, mutator: (graph: VpGraph) => void, coalesceKey?: string): void => {
    if (this.editorMode === 'compact-readonly') return;
    this.editorState.mutate(label, mutator, { coalesceKey });
  };

  graphTagsStr = computed(() => this.graph().tags.join(', '));

  gateStepId = computed<string | null>(() => {
    const overlay = this.runtimeOverlay();
    if (!overlay) return null;
    const graphSteps = this.graph().steps;
    const found = Object.values(overlay.steps).find(
      step => step.status === 'awaiting_approval'
        && graphSteps.find(graphStep => graphStep.id === step.step_id)?.gate,
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
      model_routing_summary: r.model_routing_summary ?? null,
      model_plan: r.per_step_model_plan ?? [],
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
    this.resourceOptions.register('skills', () => this.api.listSkillProfiles().pipe(map(items => items.map(item => ({ id: item.id, label: item.name, description: item.description })))));
    this.resourceOptions.register('models', () => this.api.listModelProfiles().pipe(map(result => (result.profiles ?? []).map(item => ({ id: item.profile_id, label: `${item.profile_id} · ${item.provider_id}` })) )));
    this.resourceOptions.register('fallback-groups', () => this.api.listModelProfiles().pipe(map(result => Object.keys(result.fallback_groups ?? {}).map(id => ({ id, label: id })))));
    this.resourceOptions.register('processes', () => this.api.listSavedGraphs().pipe(map(items => items.map(item => ({ id: item.id, label: item.name, description: item.description })) )));
    this.resourceOptions.register('training-datasets', () => this.trainingOptions.load().pipe(map(options => options.datasets.map(item => ({ id: item.id, label: item.name })) )));
    this.resourceOptions.register('training-profiles', () => this.trainingOptions.load().pipe(map(options => options.trainingProfiles.map(item => ({ id: item.id, label: item.label || item.id })) )));
    this.resourceOptions.register('training-base-models', () => this.trainingOptions.load().pipe(map(options => options.baseModels.map(item => ({ id: item.id, label: item.label || item.id })) )));
    this.resourceOptions.setStatic('rag-channels', this.ragChannels.map(value => ({ id: value, label: value })));
    this.resourceOptions.setStatic('encoding-modes', this.encodingModes.map(value => ({ id: value, label: value })));
    this.subs.add(this.api.listPresets().subscribe(p => this.presets.set(p)));
    this.subs.add(this.api.listSkillProfiles().subscribe(p => {
      this.skillProfiles.set(p);
      this.resourceOptions.setStatic('skills', p.map(item => ({ id: item.id, label: item.name, description: item.description })));
    }));
    this.subs.add(this.api.listTaskKinds().subscribe({
      next: k => {
        this.taskKindList.set(k);
      },
      error: () => { /* keep fallback */ },
    }));
    this.subs.add(this.api.listNodeDefinitions().subscribe({
      next: payload => {
        const definitions = this.nodeRegistry.fromContract(payload);
        const version = payload.registry_version
          || [...new Set(payload.definitions.map(definition => definition.registry_version).filter(Boolean))][0]
          || '';
        if (!definitions.length || payload.schema !== 'ananta.visual_process.node_definition_registry.v1') {
          this.useRegistryFallback('Hub-Registry ist leer oder inkompatibel; der generierte Offline-Vertrag bleibt aktiv.');
          return;
        }
        this.nodeDefinitions.set(definitions);
        if (version !== VP_NODE_REGISTRY_VERSION) {
          this.registrySource.set('degraded');
          this.registryStatus.set(`Hub-Version ${version || 'unbekannt'} weicht vom Build-Fallback ${VP_NODE_REGISTRY_VERSION} ab; Verträge werden nicht vermischt.`);
        } else {
          this.registrySource.set('backend');
          this.registryStatus.set(`Hub-Registry ${version}${payload.registry_hash ? ` · ${payload.registry_hash.slice(0, 12)}` : ''}`);
        }
      },
      error: () => this.useRegistryFallback('Hub-Registry nicht erreichbar; kompatibler generierter Offline-Vertrag aktiv.'),
    }));
    this.subs.add(this.api.listSavedGraphs().subscribe({
      next: g => {
        this.savedGraphs.set(g);
        this.resourceOptions.setStatic('processes', g.map(item => ({ id: item.id, label: item.name, description: item.description })));
      },
      error: () => { /* ignore if backend not running */ },
    }));
    this.subs.add(this.api.listModelProfiles().subscribe({
      next: result => {
        this.modelProfiles.set(result.profiles ?? []);
        this.fallbackGroups.set(result.fallback_groups ?? {});
        this.resourceOptions.setStatic('models', (result.profiles ?? []).map(item => ({ id: item.profile_id, label: `${item.profile_id} · ${item.provider_id}` })));
        this.resourceOptions.setStatic('fallback-groups', Object.keys(result.fallback_groups ?? {}).map(id => ({ id, label: id })));
      },
      error: () => {
        this.modelProfiles.set([]);
        this.fallbackGroups.set({});
      },
    }));
    this.subs.add(this.trainingOptions.load().subscribe(options => {
      this.trainingDatasets.set(options.datasets);
      this.trainingProfiles.set(options.trainingProfiles);
      this.trainingBaseModels.set(options.baseModels);
      this.resourceOptions.setStatic('training-datasets', options.datasets.map(item => ({ id: item.id, label: item.name })));
      this.resourceOptions.setStatic('training-profiles', options.trainingProfiles.map(item => ({ id: item.id, label: item.label || item.id })));
      this.resourceOptions.setStatic('training-base-models', options.baseModels.map(item => ({ id: item.id, label: item.label || item.id })));
    }));
    if (this.graphId && !this.graphStateHosted) this.loadSavedGraphById(this.graphId);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (
      changes['graphId']
      && !changes['graphId'].firstChange
      && this.graphId
      && !this.graphStateHosted
    ) {
      this.loadSavedGraphById(this.graphId);
    }
  }

  ngOnDestroy(): void {
    this.presetLoadGeneration += 1;
    this.policyRefreshGeneration += 1;
    this.subs.unsubscribe();
    this.workflowRunner.destroy();
    if (!this.graphStateHosted) this.editorState.destroy();
  }
  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    if (this.editorMode === 'compact-readonly') return;
    const target = e.target as HTMLElement | null;
    if (target && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) this.redo(); else this.undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'y') {
      e.preventDefault();
      this.redo();
      return;
    }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); this.deleteSelected(); }
    if (!e.ctrlKey && !e.metaKey && !e.altKey && (e.key === 'n' || e.key === 'N')) {
      e.preventDefault();
      if (!this.showNodePalette) this.toggleNodePalette();
    }
    if (!e.ctrlKey && !e.metaKey && !e.altKey && (e.key === 'e' || e.key === 'E')) { e.preventDefault(); this.toggleEdgeMode(); }
    if (e.key === 'Escape') { this.edgeMode.set(false); this.edgeSourceId.set(null); this.drawingEdge.set(false); }
  }
  onCanvasMouseDown(e: MouseEvent): void {
    if (e.altKey) this.onAssistantTargetPreview(null);
    this.interaction.onCanvasMouseDown(e, () => {
      this.selectedId.set(null);
      this.loadPresetMenu = false;
      this.loadSavedMenu = false;
    });
  }

  onMouseMove(e: MouseEvent): void {
    this.interaction.onMouseMove(e, (id, mutate) => this.mutateStep(id, mutate));
  }
  onCanvasNodeMouseDown(payload: { event: MouseEvent; stepId: string }): void { this.onNodeMouseDown(payload.event, payload.stepId); }

  onMouseUp(e: MouseEvent): void {
    if (this.interaction.onMouseUp(e)) {
      this.editorState.commitTransaction();
      this.suppressNextAssistantSelection = true;
      setTimeout(() => this.suppressNextAssistantSelection = false, 0);
    }
  }

  onWheel(e: WheelEvent): void {
    this.onAssistantTargetPreview(null);
    this.interaction.onWheel(e);
  }

  onNodeMouseDown(e: MouseEvent, id: string): void {
    const step = this.graph().steps.find(candidate => candidate.id === id);
    if (!step) return;
    this.onAssistantTargetPreview(null);
    if (!this.edgeMode()) this.editorState.beginTransaction(`Node „${step.label}“ verschieben`);
    if (!this.interaction.onNodeMouseDown(e, id, step, this.edgeMode())) this.editorState.cancelTransaction();
  }

  selectStep(id: string): void {
    if (this.edgeMode()) {
      const src = this.edgeSourceId();
      if (!src) {
        this.onAssistantTargetPreview(null);
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
  addStep(kind = 'patch_propose'): void {
    const id = stepId();
    const definition = this.nodeDefinitions().find(item => item.kind === kind)
      ?? this.nodeRegistry.find(kind, this.taskKindList());
    const newStep = this.nodeRegistry.createStep(
      definition,
      id,
      this.nodeRegistry.nextPosition(this.graph(), this.interaction.viewportAnchor()),
    );
    this.editorState.mutate(`Node „${definition.label}“ hinzufügen`, graph => { graph.steps.push(newStep); });
    this.selectedId.set(id);
    this.showNodePalette = false;
    this.onAssistantTargetSelected({ kind: 'node', entityId: id, graphId: this.graph().id, role: kind, stepId: id });
  }

  addEdge(source: string, target: string): void {
    const e: VpEdge = { id: edgeId(), source, target, condition: { kind: 'always' } };
    this.editorState.mutate('Kante hinzufügen', graph => { graph.edges.push(e); });
  }

  deleteSelected(): void {
    const id = this.selectedId();
    if (!id) return;
    this.editorState.execute('Auswahl löschen', graph => ({
      ...graph,
      steps: graph.steps.filter(step => step.id !== id),
      edges: graph.edges.filter(edge => edge.id !== id && edge.source !== id && edge.target !== id),
    }));
    this.selectedId.set(null);
  }

  toggleEdgeMode(): void {
    const next = !this.edgeMode();
    this.edgeMode.set(next);
    if (!next) { this.edgeSourceId.set(null); this.drawingEdge.set(false); this.statusMsg.set(''); }
    else this.statusMsg.set('Kante-Modus: klicke Quell-Knoten');
  }

  toggleNodePalette(): void {
    this.onAssistantTargetPreview(null);
    this.showNodePalette = !this.showNodePalette;
  }

  setGraphName(val: string): void {
    this.editorState.mutate('Graphname ändern', graph => { graph.name = val; }, { coalesceKey: 'graph:name' });
  }

  setGraphDescription(val: string): void {
    this.editorState.mutate('Graphbeschreibung ändern', graph => { graph.description = val; }, { coalesceKey: 'graph:description' });
  }

  setTags(val: string): void {
    const tags = val.split(',').map(t => t.trim()).filter(Boolean);
    this.editorState.mutate('Graph-Tags ändern', graph => { graph.tags = tags; }, { coalesceKey: 'graph:tags' });
  }

  graphRouting(): ModelRoutingConfig {
    const raw = this.graph().metadata?.['model_routing'];
    return (raw && typeof raw === 'object' ? raw : {}) as ModelRoutingConfig;
  }

  readonly recoveryStrategyOptions = CONTEXT_RECOVERY_STRATEGIES;

  fallbackGroupIds(): string[] {
    return Object.keys(this.fallbackGroups());
  }

  setGraphRoutingField(key: keyof ModelRoutingConfig, value: unknown): void {
    this.editorState.execute('Model-Routing ändern', graph => {
      const metadata = { ...(graph.metadata ?? {}) };
      const routing = { ...((metadata['model_routing'] as ModelRoutingConfig | undefined) ?? {}) };
      if (value === '' || value === null || value === undefined) delete (routing as Record<string, unknown>)[key as string];
      else (routing as Record<string, unknown>)[key as string] = value;
      if (Object.keys(routing).length) metadata['model_routing'] = routing;
      else delete metadata['model_routing'];
      return { ...graph, metadata };
    }, { coalesceKey: `graph:model-routing:${key}` });
  }

  graphRecoveryStrategyEnabled(strategy: ContextRecoveryStrategy): boolean {
    return (this.graphRouting().context_recovery_strategies ?? []).includes(strategy);
  }

  toggleGraphRecoveryStrategy(strategy: ContextRecoveryStrategy, enabled: boolean): void {
    const selected = new Set(this.graphRouting().context_recovery_strategies ?? []);
    if (enabled) selected.add(strategy);
    else selected.delete(strategy);
    if (
      (strategy === 'segment_planning' || strategy === 'propose_task_plan')
      && enabled
    ) {
      selected.add('require_approval');
    }
    if (strategy === 'require_approval' && !enabled) {
      selected.delete('segment_planning');
      selected.delete('propose_task_plan');
    }
    this.setGraphRoutingField(
      'context_recovery_strategies',
      CONTEXT_RECOVERY_STRATEGIES.filter(item => selected.has(item)),
    );
    if (
      (strategy === 'segment_planning' || strategy === 'propose_task_plan')
      && enabled
    ) {
      this.setGraphRoutingField('require_approval_for_generated_plan', true);
    }
  }

  validateModelRouting(): void {
    this.subs.add(this.api.validateModelRouting(this.graph()).subscribe({
      next: result => this.statusMsg.set(`Routing geprüft (${((result['validation'] as any)?.warning_count ?? 0)} Warnungen)`),
      error: () => this.statusMsg.set('Routing-Prüfung fehlgeschlagen'),
    }));
  }

  estimateModelCost(): void {
    this.subs.add(this.api.estimateModelCost(this.graph()).subscribe({
      next: result => this.statusMsg.set(`Kosten geschätzt: ${JSON.stringify(result['model_routing_summary'] ?? {})}`),
      error: () => this.statusMsg.set('Kostenschätzung fehlgeschlagen'),
    }));
  }
  loadPreset(id: string): void {
    if (this.graphStateHosted) {
      this.statusMsg.set('Graphwechsel werden vom CaseFlow Studio verwaltet.');
      return;
    }
    this.loadPresetMenu = false;
    const generation = ++this.presetLoadGeneration;
    this.policyRefreshGeneration += 1;
    const revision = this.editorState.revision();
    this.subs.add(this.api.getPreset(id).subscribe({
      next: response => {
        if (generation !== this.presetLoadGeneration
          || revision !== this.editorState.revision()) return;
        const result = validateVpPresetDirectLoad(id, response);
        if (result.ok === false) {
          const catalogBound = result.issues.some(issue =>
            issue.code === 'catalog_application_required');
          this.statusMsg.set(catalogBound
            ? 'Dieses Preset benötigt autorisierte Katalog-Bindings und muss im CaseFlow Studio angewendet werden.'
            : 'Preset-Antwort wurde verworfen; der aktuelle Graph bleibt unverändert.');
          return;
        }
        this.editorState.initialize(result.value);
        this.statusMsg.set(`Preset "${result.value.name}" geladen`);
        this.schedulePolicyHintsRefresh(true);
      },
      error: () => {
        if (generation !== this.presetLoadGeneration
          || revision !== this.editorState.revision()) return;
        this.statusMsg.set('Preset konnte nicht geladen werden');
      },
    }));
  }

  loadSavedGraphById(id: string): void {
    if (this.graphStateHosted) {
      this.statusMsg.set('Graphwechsel werden vom CaseFlow Studio verwaltet.');
      return;
    }
    this.loadSavedMenu = false;
    this.subs.add(this.api.loadSavedGraph(id).subscribe({
      next: g => {
        this.editorState.initialize(g);
        this.saveConflict.set(false);
        this.statusMsg.set(`"${g.name}" geladen`);
        this.schedulePolicyHintsRefresh(true);
      },
      error: () => this.statusMsg.set('Graph konnte nicht geladen werden'),
    }));
  }

  saveGraphToServer(): void {
    if (this.graphStateHosted) {
      if (this.hostedPersistence) this.hostedPersistence.saveCurrentGraph();
      else this.statusMsg.set('Studio-Persistenz ist nicht verfügbar.');
      return;
    }
    const request = this.editorState.captureSaveRequest();
    this.subs.add(this.api.saveGraph(request.graph).subscribe({
      next: r => {
        const acceptance = this.editorState.acceptSaveResult(r, request);
        if (acceptance.status === 'rejected_identity' || acceptance.status === 'rejected_stale') {
          this.statusMsg.set('Veraltete oder abweichende Speicherantwort wurde verworfen.');
          return;
        }
        this.saveConflict.set(false);
        this.statusMsg.set(acceptance.status === 'accepted_clean'
          ? `Gespeichert ✓ (${this.graph().name})`
          : `Gespeichert; spätere Änderungen bleiben offen (${this.graph().name})`);
        this.api.listSavedGraphs().subscribe(g => this.savedGraphs.set(g));
      },
      error: error => {
        this.saveConflict.set(error?.status === 409);
        this.statusMsg.set(error?.status === 409
          ? 'Speicherkonflikt: Der Hub-Graph ist neuer. Lokaler Draft und Undo/Redo bleiben erhalten.'
          : 'Speichern fehlgeschlagen');
      },
    }));
  }

  reloadAfterSaveConflict(): void {
    if (this.graphStateHosted) {
      this.statusMsg.set('Neu laden ist im Studio nur über den Workspace möglich.');
      return;
    }
    const id = this.graph().id;
    if (id) this.loadSavedGraphById(id);
  }

  forkAfterSaveConflict(): void {
    if (!this.saveConflict()) return;
    const uuid = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`;
    this.editorState.execute('Konflikt-Draft als Kopie vorbereiten', graph => {
      const fork = structuredClone(graph);
      fork.id = `vp-fork-${uuid}`;
      fork.name = `${graph.name} (Kopie)`;
      fork.definition_revision = 0;
      delete fork.base_graph_hash;
      return fork;
    });
    this.saveConflict.set(false);
    this.statusMsg.set('Lokaler Draft als neue Kopie vorbereitet; Änderungen und Undo bleiben erhalten.');
  }

  validateGraph(): void { this.workflowRunner.validate(this.graph()); }
  runDryRun(): void { this.workflowRunner.dryRun(this.graph()); }
  saveAsBlueprintFromDryRun(): void { this.workflowRunner.saveAsBlueprint(this.graph()); }
  refreshPolicyHints(preserveCleanBaseline = false): void {
    const generation = ++this.policyRefreshGeneration;
    this.requestPolicyHints(preserveCleanBaseline, generation);
  }

  private requestPolicyHints(
    preserveCleanBaseline: boolean,
    generation: number,
  ): void {
    const graphId = this.graph().id;
    const revision = this.editorState.revision();
    const wasClean = !this.isDirty();
    this.workflowRunner.refreshPolicyHints(this.graph(), perStep => {
      if (generation !== this.policyRefreshGeneration
        || graphId !== this.graph().id
        || revision !== this.editorState.revision()) return;
      this.editorState.mutate('Policy-Hinweise aktualisieren', graph => {
        for (const step of graph.steps) step.policy_hints = perStep[step.id] ?? step.policy_hints;
      }, { recordHistory: false });
      if (preserveCleanBaseline && wasClean) this.editorState.markSaved();
    });
  }

  private schedulePolicyHintsRefresh(preserveCleanBaseline: boolean): void {
    const generation = ++this.policyRefreshGeneration;
    const graphId = this.graph().id;
    const revision = this.editorState.revision();
    setTimeout(() => {
      if (generation !== this.policyRefreshGeneration
        || graphId !== this.graph().id
        || revision !== this.editorState.revision()) return;
      this.requestPolicyHints(preserveCleanBaseline, generation);
    }, 300);
  }
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
          this.editorState.replaceGraph(result.graph, { markDirty: true, validation: result.validation });
          const warns = result.warnings?.length ? ` (${result.warnings.join(', ')})` : '';
          this.statusMsg.set(`BPMN importiert: ${result.graph.steps.length} Schritte${warns}`);
          this.schedulePolicyHintsRefresh(true);
        },
        error: (err) => this.statusMsg.set(`BPMN-Import-Fehler: ${err?.error?.detail ?? 'ungültige Datei'}`),
      }));
    (event.target as HTMLInputElement).value = '';
  }
  openMermaid(): void {
    this.onAssistantTargetPreview(null);
    this._showMermaidDialog = true;
    this.mermaidTab = 'mermaid';
    this.subs.add(this.importExport.mermaid(this.graph()).subscribe({
      next: r => { this.mermaidText.set(r.mermaid); this.mermaidTuiText.set(r.tui ?? ''); },
      error: () => this.mermaidText.set('Fehler beim Laden'),
    }));
  }

  get showMermaidDialog(): boolean { return this._showMermaidDialog; }
  set showMermaidDialog(val: boolean) {
    this._showMermaidDialog = val;
    this.syncAssistantPreviewSuppression();
  }

  copyMermaid(): void {
    this.importExport.copyMermaid(this.mermaidText()).then(() => this.statusMsg.set('Mermaid kopiert ✓'));
  }

  downloadMermaid(): void {
    this.importExport.downloadMermaid(this.mermaidText(), this.graph().name);
  }

  autoLayout(): void {
    this.editorState.execute('Auto-Layout anwenden', autoLayoutGraph);
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

  modelPlanForStep(stepId: string): any {
    return (this.dryRunResult()?.per_step_model_plan ?? []).find(plan => plan.step_id === stepId) ?? null;
  }

  modelBadge(step: VpStep): string {
    const plan = this.modelPlanForStep(step.id);
    if (plan?.provider_id) return `${plan.provider_id}:${plan.selected_profile_id ?? plan.model ?? 'auto'}`;
    const routing = step.metadata?.['model_routing'] as ModelRoutingConfig | undefined;
    return routing?.preferred_profile_id ?? routing?.fallback_group_id ?? '';
  }

  stepLabel(id: string): string {
    return this.graph().steps.find(s => s.id === id)?.label ?? id;
  }

  undo(): void {
    if (this.editorState.undo()) this.statusMsg.set(`Rückgängig${this.redoLabel() ? `: ${this.redoLabel()}` : ''}`);
  }

  redo(): void {
    if (this.editorState.redo()) this.statusMsg.set(`Wiederholt${this.undoLabel() ? `: ${this.undoLabel()}` : ''}`);
  }

  onAssistantTargetPreview(target: CanvasHitTarget | null): void {
    this.editorState.previewTarget(target);
    this.assistant.preview(this.assistantOptions(target));
  }

  onAssistantTargetFocused(target: CanvasHitTarget): void {
    this.editorState.focusTarget(target);
    this.assistant.preview(this.assistantOptions(target));
  }

  onAssistantTargetSelected(target: CanvasHitTarget): void {
    if (this.edgeMode() || this.suppressNextAssistantSelection) {
      this.suppressNextAssistantSelection = false;
      return;
    }
    this.editorState.focusTarget(target);
    this.assistant.select(this.assistantOptions(target));
  }

  askAssistant(question: string): void {
    this.editorState.freezeConversationTarget(this.assistant.target());
    const target = this.editorState.conversationTarget();
    if (target) void this.assistant.show({ ...this.assistantOptions(target), detailLevel: 'conversation' });
    if (!this.assistant.ask(question)) this.statusMsg.set('Die Frage ist leer oder eine Assistant-Anfrage läuft bereits.');
  }

  previewAssistantPatch(): void { this.assistant.previewWorkflowPatch(this.graph()); }

  refreshAssistantPatch(): void {
    this.assistant.refreshWorkflowPatch(
      () => this.graph(),
      this.validationResult()?.issues ?? [],
      this.runtimeOverlay(),
    );
  }

  acceptAssistantPatch(): void {
    this.assistant.acceptWorkflowPatch(() => this.graph(), preview => this.applyAssistantPatch(preview));
  }

  private applyAssistantPatch(preview: VpAssistantPatchPreview): boolean {
    if (this.editorMode === 'compact-readonly') return false;
    const current = this.graph();
    const candidate: VpGraph = {
      ...structuredClone(preview.preview_graph),
      version: current.version,
      graph_schema_version: current.graph_schema_version,
      node_registry_version: current.node_registry_version,
      definition_revision: current.definition_revision,
      base_graph_hash: current.base_graph_hash,
    };
    const applied = this.editorState.execute('Freigegebenen AI-Patch übernehmen', () => candidate);
    if (applied) this.editorState.validation.set(preview.validation);
    return applied;
  }

  private assistantOptions(target: CanvasHitTarget | null) {
    const step = target?.stepId ? this.graph().steps.find(item => item.id === target.stepId) : null;
    const definitionKind = target?.kind === 'palette_item' ? target.entityId : step?.kind;
    const definition = definitionKind
      ? this.nodeDefinitions().find(item => item.kind === definitionKind) ?? null
      : null;
    return {
      graph: this.graph(),
      target,
      definition,
      validationIssues: this.validationResult()?.issues ?? [],
      runtime: this.runtimeOverlay(),
      editorMode: this.editorMode,
    };
  }

  private syncAssistantPreviewSuppression(): void {
    this.assistant.setPreviewSuppressed(
      this._loadPresetMenu
      || this._loadSavedMenu
      || this._showGraphDetails
      || this._showNodePalette
      || this._showMermaidDialog
      || !!this.dryRunResult()
      || !!this.assistant.patchPreview()
      || this.edgeMode()
      || this.drawingEdge(),
    );
  }

  private useRegistryFallback(message: string): void {
    this.nodeDefinitions.set(this.nodeRegistry.definitions(FALLBACK_KINDS));
    this.registrySource.set('degraded');
    this.registryStatus.set(message);
  }

  private mutateStep(id: string, fn: (s: VpStep) => void): void {
    this.editorState.mutate('Node verschieben', graph => {
      const step = graph.steps.find(item => item.id === id);
      if (step) fn(step);
    }, { recordHistory: false });
  }
}
