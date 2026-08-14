import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { vi, describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import { Subject, of, throwError } from 'rxjs';

import { VisualProcessEditorComponent } from './visual-process-editor.component';
import {
  GraphSaveResult,
  VisualProcessApiService,
  VpGraph,
  VpStep,
} from './visual-process-api.service';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { FALLBACK_KINDS, nodeKindColor } from './vp-editor-config';
import { VpImportExportService } from './vp-import-export.service';
import { VpWorkflowRunnerService } from './vp-workflow-runner.service';
import { VpModelTrainingOptionsService } from './vp-model-training-options.service';
import { VP_NODE_REGISTRY_VERSION } from './vp-node-definition-registry.service';
import { GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS } from './vp-node-definitions.generated';
import {
  VP_CATALOG_BOUND_PRESET_METADATA_KEY,
  VP_CATALOG_BOUND_PRESET_SCHEMA_V1,
} from './vp-preset-load.policy';

beforeAll(async () => {
  await ɵresolveComponentResources(resource =>
    readFile(new URL(resource, import.meta.url), 'utf8'),
  );
});

function emptyGraph(): VpGraph {
  return { id: '', name: '', description: '', version: '1.0.0', steps: [], edges: [], tags: [] };
}

function step(id: string, kind = 'patch_propose'): VpStep {
  return {
    id,
    kind,
    label: `Step ${id}`,
    role: '',
    enabled: true,
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  } as VpStep;
}

describe('VisualProcessEditorComponent (FSR-T015 acceptance)', () => {
  let api: {
    listPresets: ReturnType<typeof vi.fn>;
    listSkillProfiles: ReturnType<typeof vi.fn>;
    listTaskKinds: ReturnType<typeof vi.fn>;
    listNodeDefinitions: ReturnType<typeof vi.fn>;
    listSavedGraphs: ReturnType<typeof vi.fn>;
    listModelProfiles: ReturnType<typeof vi.fn>;
    getPreset: ReturnType<typeof vi.fn>;
    saveGraph: ReturnType<typeof vi.fn>;
    loadSavedGraph: ReturnType<typeof vi.fn>;
    policySummary: ReturnType<typeof vi.fn>;
    validate: ReturnType<typeof vi.fn>;
    dryRun: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    api = {
      listPresets: vi.fn().mockReturnValue(of([])),
      listSkillProfiles: vi.fn().mockReturnValue(of([])),
      listTaskKinds: vi.fn().mockReturnValue(of([])),
      listNodeDefinitions: vi.fn().mockReturnValue(of({ schema: 'ananta.visual_process.node_definition_registry.v1', definitions: [] })),
      listSavedGraphs: vi.fn().mockReturnValue(of([])),
      listModelProfiles: vi.fn().mockReturnValue(of({ profiles: [], fallback_groups: {}, status: 'ok' })),
      getPreset: vi.fn().mockReturnValue(of(emptyGraph())),
      saveGraph: vi.fn().mockReturnValue(of({ id: 'g', version: '1', definition_revision: 1, base_graph_hash: 'a'.repeat(64), saved: true })),
      loadSavedGraph: vi.fn().mockReturnValue(of(emptyGraph())),
      policySummary: vi.fn().mockReturnValue(of({ summary: {}, per_step: {} })),
      validate: vi.fn().mockReturnValue(of({ valid: true, error_count: 0, warning_count: 0, issues: [] })),
      dryRun: vi.fn().mockReturnValue(of({} as any)),
    };

    await TestBed.configureTestingModule({
      imports: [VisualProcessEditorComponent],
      providers: [
        { provide: VisualProcessApiService, useValue: api },
        VpWorkflowRunnerService,
        VpCanvasInteractionService,
        VpImportExportService,
        {
          provide: VpModelTrainingOptionsService,
          useValue: {
            load: vi.fn(() => of({ hubAvailable: true, datasets: [], trainingProfiles: [], baseModels: [] })),
          },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => vi.useRealTimers());

  it('mounts and initializes with an empty graph signal', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const graph = fixture.componentInstance.graph();
    expect(graph).toBeDefined();
    expect(Array.isArray(graph.steps)).toBe(true);
    expect(Array.isArray(graph.edges)).toBe(true);
  });

  it('keeps standalone graph loading backward compatible when no workspace port exists', () => {
    const saved = { ...emptyGraph(), id: 'standalone', name: 'Standalone' };
    api.loadSavedGraph.mockReturnValueOnce(of(saved));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.componentInstance.graphId = 'standalone';
    fixture.detectChanges();

    expect(fixture.componentInstance.graphStateHosted).toBe(false);
    expect(api.loadSavedGraph).toHaveBeenCalledOnce();
    expect(api.loadSavedGraph).toHaveBeenCalledWith('standalone');
    expect(fixture.componentInstance.graph()).toMatchObject({
      id: 'standalone', name: 'Standalone',
    });
  });

  it('keeps runtime commands owned by standalone editors and disables them for hosted projections', () => {
    const standalone = TestBed.createComponent(VisualProcessEditorComponent);
    standalone.detectChanges();
    expect(standalone.componentInstance.runtimeMode).toBe('owned');
    expect(standalone.nativeElement.textContent).toContain('Starten');

    const hosted = TestBed.createComponent(VisualProcessEditorComponent);
    hosted.componentInstance.runtimeMode = 'external-readonly';
    hosted.detectChanges();
    const runner = hosted.debugElement.injector.get(VpWorkflowRunnerService);
    const start = vi.spyOn(runner, 'start');
    const cancel = vi.spyOn(runner, 'cancel');
    const signalGate = vi.spyOn(runner, 'signalGate');

    hosted.componentInstance.startWorkflow();
    hosted.componentInstance.cancelWorkflow();
    hosted.componentInstance.approveGate();
    hosted.componentInstance.rejectGate();

    expect(hosted.nativeElement.textContent).not.toContain('▶ Starten');
    expect(hosted.nativeElement.textContent).not.toContain('⏹ Abbrechen');
    expect(hosted.nativeElement.textContent).not.toContain('Gate-Freigabe erforderlich');
    expect(start).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
    expect(signalGate).not.toHaveBeenCalled();
  });

  it('isolates editor commands and Assistant context across parallel instances', () => {
    const first = TestBed.createComponent(VisualProcessEditorComponent);
    const second = TestBed.createComponent(VisualProcessEditorComponent);
    first.detectChanges(); second.detectChanges();
    expect(first.componentInstance.assistant).not.toBe(second.componentInstance.assistant);
    first.componentInstance.addStep('review');
    first.componentInstance.assistant.target.set({
      kind: 'node', graphId: first.componentInstance.graph().id, entityId: 'first-only', stepId: 'first-only', role: 'review',
    });
    expect(first.componentInstance.graph().steps).toHaveLength(1);
    expect(second.componentInstance.graph().steps).toHaveLength(0);
    expect(second.componentInstance.assistant.target()).toBeNull();
  });

  it('centrally suppresses pending Assistant hover for panels, palettes and dialogs', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const suppression = vi.spyOn(component.assistant, 'setPreviewSuppressed');

    component.showGraphDetails = true;
    component.showGraphDetails = false;
    component.showNodePalette = true;
    component.showNodePalette = false;
    component.showMermaidDialog = true;

    expect(suppression).toHaveBeenCalledWith(true);
    expect(suppression).toHaveBeenCalledWith(false);
    expect(suppression).toHaveBeenLastCalledWith(true);
  });

  it('loads presets, skill profiles, task kinds and saved graphs on init', () => {
    TestBed.createComponent(VisualProcessEditorComponent).detectChanges();
    expect(api.listPresets).toHaveBeenCalledTimes(1);
    expect(api.listSkillProfiles).toHaveBeenCalledTimes(1);
    expect(api.listTaskKinds).toHaveBeenCalledTimes(1);
    expect(api.listNodeDefinitions).toHaveBeenCalledTimes(1);
    expect(api.listSavedGraphs).toHaveBeenCalledTimes(1);
  });

  it('loads an ordinary preset through the guarded standalone path', () => {
    const preset = {
      ...emptyGraph(), id: 'preset-plain', name: 'Plain preset',
    };
    api.getPreset.mockReturnValueOnce(of(preset));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();

    fixture.componentInstance.loadPreset('preset-plain');

    expect(fixture.componentInstance.graph()).toMatchObject({
      id: 'preset-plain', name: 'Plain preset',
    });
    expect(fixture.componentInstance.statusMsg()).toContain('geladen');
  });

  it('retires runtime evidence before either standalone graph replacement path', () => {
    const graphWithSharedStep = (id: string, name: string): VpGraph => ({
      ...emptyGraph(),
      id,
      name,
      steps: [step('shared-step')],
    });
    api.loadSavedGraph.mockReturnValueOnce(of(graphWithSharedStep('graph-b', 'Graph B')));
    api.getPreset.mockReturnValueOnce(of(graphWithSharedStep('preset-c', 'Preset C')));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const runner = fixture.debugElement.injector.get(VpWorkflowRunnerService);
    const seedRuntime = (graphId: string, runId: string): void => {
      (runner as any).activeWorkflowId.set(graphId);
      (runner as any).applyStatus({
        schema: 'ananta.workflow_backend_status.v1',
        workflow_id: graphId,
        run_id: runId,
        process_id: graphId,
        revision: 1,
        updated_at: 1,
        status: 'running',
        steps: [{ step_id: 'shared-step', status: 'running' }],
      });
    };

    seedRuntime('graph-a', 'run-a');
    component.loadSavedGraphById('graph-b');
    expect(component.graph().id).toBe('graph-b');
    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();

    seedRuntime('graph-b', 'run-b');
    component.loadPreset('preset-c');
    expect(component.graph().id).toBe('preset-c');
    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
  });

  it('suppresses snapshot-bound runtime evidence after a local graph mutation', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const runner = fixture.debugElement.injector.get(VpWorkflowRunnerService);
    const snapshotHash = 'a'.repeat(64);
    const persistedGraph: VpGraph = {
      ...emptyGraph(),
      id: 'graph-a',
      base_graph_hash: snapshotHash,
      steps: [step('shared-step')],
    };
    (component as unknown as {
      editorState: { initialize: (graph: VpGraph) => void };
    }).editorState.initialize(persistedGraph);
    (runner as any).activeWorkflowId.set('graph-a');
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'graph-a',
      run_id: 'run-a',
      process_id: 'graph-a',
      snapshot_hash: snapshotHash,
      revision: 1,
      updated_at: 1,
      status: 'running',
      steps: [{ step_id: 'shared-step', status: 'running' }],
    });

    expect(component.visibleRuntimeOverlay()?.run_id).toBe('run-a');

    component.addStep('review');
    fixture.detectChanges();

    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);
    expect(component.graph().steps).toHaveLength(2);
    expect(component.visibleRuntimeOverlay()).toBeNull();
    expect(runner.runtimeOverlay()?.run_id).toBe('run-a');
    expect((fixture.nativeElement as HTMLElement).querySelector('.vp-runtime-badge')).toBeNull();
  });

  it('shows a locally started dirty draft only until its editor revision changes', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const runner = fixture.debugElement.injector.get(VpWorkflowRunnerService);
    component.addStep('review');
    expect(component.isDirty()).toBe(true);
    expect(component.graph().base_graph_hash).toBeUndefined();
    (component as unknown as {
      editorState: { validation: { set: (value: unknown) => void } };
    }).editorState.validation.set({
      valid: true,
      error_count: 0,
      warning_count: 0,
      issues: [],
    });
    vi.spyOn(runner, 'start').mockImplementation(() => true);

    component.startWorkflow();
    (runner as any).activeWorkflowId.set(component.graph().id);
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: component.graph().id,
      run_id: 'draft-run',
      process_id: component.graph().id,
      revision: 1,
      updated_at: 1,
      status: 'running',
      steps: [{ step_id: component.graph().steps[0].id, status: 'running' }],
    });

    expect(component.visibleRuntimeOverlay()).toBeNull();

    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: component.graph().id,
      run_id: 'draft-run',
      process_id: component.graph().id,
      snapshot_hash: 'b'.repeat(64),
      revision: 2,
      updated_at: 2,
      status: 'running',
      steps: [{ step_id: component.graph().steps[0].id, status: 'running' }],
    });

    expect(component.visibleRuntimeOverlay()?.run_id).toBe('draft-run');

    component.setGraphDescription('lokale Änderung nach dem Start');
    fixture.detectChanges();

    expect(component.visibleRuntimeOverlay()).toBeNull();
    expect(runner.runtimeOverlay()?.run_id).toBe('draft-run');
    expect((fixture.nativeElement as HTMLElement).textContent)
      .toContain('Workflow-Evidenz gehört zu einem anderen Graph-Stand');
  });

  it.each([
    ['catalog-bound preset', () => ({
      ...emptyGraph(),
      id: 'preset-bound',
      name: 'Catalog bound',
      metadata: {
        [VP_CATALOG_BOUND_PRESET_METADATA_KEY]: {
          schema: VP_CATALOG_BOUND_PRESET_SCHEMA_V1,
          binding_slots: [{
            slot: 'critic_benchmark_context',
            step_id: 'gauntlet-critic',
            resource_type: 'context_source',
            access: 'read_only',
            required: true,
          }],
        },
      },
    }), 'CaseFlow Studio'],
    ['mismatched preset identity', () => ({
      ...emptyGraph(), id: 'wrong-preset', name: 'Wrong',
    }), 'verworfen'],
    ['runtime-bearing response', () => ({
      ...emptyGraph(),
      id: 'preset-bound',
      name: 'Runtime payload',
      runtime_overlay: { current_step_ids: ['runtime-step'] },
    }), 'verworfen'],
    ['malformed response', () => ({ id: 'preset-bound' }), 'verworfen'],
  ])('preserves graph and history for a rejected %s', (_label, response, message) => {
    api.getPreset.mockReturnValueOnce(of(response()));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.addStep('review');
    const before = structuredClone(component.graph());
    expect(component.canUndo()).toBe(true);

    component.loadPreset('preset-bound');

    expect(component.graph()).toEqual(before);
    expect(component.canUndo()).toBe(true);
    expect(component.statusMsg()).toContain(message);
    component.undo();
    expect(component.graph().steps).toHaveLength(0);
  });

  it('fences out-of-order preset responses behind the newest request generation', () => {
    const first = new Subject<VpGraph>();
    const second = new Subject<VpGraph>();
    api.getPreset
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second);
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.loadPreset('preset-first');
    component.loadPreset('preset-second');
    second.next({
      ...emptyGraph(), id: 'preset-second', name: 'Second',
    });
    component.addStep('review');
    const statusAfterNewerResponse = component.statusMsg();
    first.next({
      ...emptyGraph(), id: 'preset-first', name: 'First',
    });

    expect(component.graph()).toMatchObject({ id: 'preset-second', name: 'Second' });
    expect(component.graph().steps).toHaveLength(1);
    expect(component.canUndo()).toBe(true);
    expect(component.statusMsg()).toBe(statusAfterNewerResponse);
    component.undo();
    expect(component.graph()).toMatchObject({ id: 'preset-second', name: 'Second' });
    expect(component.graph().steps).toHaveLength(0);
  });

  it('preserves edits made while the current preset request is in flight', () => {
    const response = new Subject<VpGraph>();
    api.getPreset.mockReturnValueOnce(response);
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.loadPreset('preset-late');
    component.addStep('review');
    const graphAfterEdit = structuredClone(component.graph());
    const statusAfterEdit = component.statusMsg();
    response.next({
      ...emptyGraph(), id: 'preset-late', name: 'Late response',
    });

    expect(component.graph()).toEqual(graphAfterEdit);
    expect(component.canUndo()).toBe(true);
    expect(component.statusMsg()).toBe(statusAfterEdit);
  });

  it('fences an automatic policy response from an older loaded preset', () => {
    vi.useFakeTimers();
    const policyResponse = new Subject<{
      summary: Record<string, unknown>;
      per_step: Record<string, string[]>;
    }>();
    const preset = (id: string, name: string): VpGraph => ({
      ...emptyGraph(), id, name, steps: [step('shared-step', 'review')],
    });
    api.getPreset
      .mockReturnValueOnce(of(preset('preset-first', 'First')))
      .mockReturnValueOnce(of(preset('preset-second', 'Second')));
    api.policySummary.mockReturnValueOnce(policyResponse);
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.loadPreset('preset-first');
    vi.advanceTimersByTime(300);
    expect(api.policySummary).toHaveBeenCalledOnce();
    component.loadPreset('preset-second');
    component.addStep('review');
    const graphAfterNewerEdit = structuredClone(component.graph());

    policyResponse.next({
      summary: {}, per_step: { 'shared-step': ['stale-policy-hint'] },
    });

    expect(component.graph()).toEqual(graphAfterNewerEdit);
    expect(component.graph().id).toBe('preset-second');
    expect(component.graph().steps[0].policy_hints).toEqual([]);
    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);
  });

  it('lets a newer preset intent win when the old policy returns first', () => {
    vi.useFakeTimers();
    const oldPolicy = new Subject<{
      summary: Record<string, unknown>;
      per_step: Record<string, string[]>;
    }>();
    const newerPreset = new Subject<VpGraph>();
    const preset = (id: string, name: string): VpGraph => ({
      ...emptyGraph(), id, name, steps: [step('shared-step', 'review')],
    });
    api.getPreset
      .mockReturnValueOnce(of(preset('preset-first', 'First')))
      .mockReturnValueOnce(newerPreset);
    api.policySummary.mockReturnValueOnce(oldPolicy);
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.loadPreset('preset-first');
    vi.advanceTimersByTime(300);
    component.loadPreset('preset-second');
    oldPolicy.next({
      summary: {}, per_step: { 'shared-step': ['stale-policy-hint'] },
    });
    newerPreset.next(preset('preset-second', 'Second'));

    expect(component.graph()).toMatchObject({
      id: 'preset-second', name: 'Second',
    });
    expect(component.graph().steps[0].policy_hints).toEqual([]);
    expect(component.isDirty()).toBe(false);
    expect(component.canUndo()).toBe(false);
  });

  it('uses a matching Hub registry without mixing it with fallback definitions', () => {
    const hubDefinition = GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS[0];
    api.listNodeDefinitions.mockReturnValueOnce(of({
      schema: 'ananta.visual_process.node_definition_registry.v1', registry_version: VP_NODE_REGISTRY_VERSION,
      registry_hash: 'a'.repeat(64), definitions: [hubDefinition],
    }));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.registrySource()).toBe('backend');
    expect(fixture.componentInstance.nodeDefinitions().map(definition => definition.kind)).toEqual([hubDefinition.kind]);
  });

  it('explains registry version drift and network fallback as degraded states', () => {
    const hubDefinition = GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS[0];
    api.listNodeDefinitions.mockReturnValueOnce(of({
      schema: 'ananta.visual_process.node_definition_registry.v1', registry_version: 'future-registry',
      registry_hash: 'b'.repeat(64), definitions: [hubDefinition],
    }));
    const drift = TestBed.createComponent(VisualProcessEditorComponent);
    drift.detectChanges();
    expect(drift.componentInstance.registrySource()).toBe('degraded');
    expect(drift.componentInstance.registryStatus()).toContain('Verträge werden nicht vermischt');
    expect(drift.componentInstance.nodeDefinitions()).toHaveLength(1);

    api.listNodeDefinitions.mockReturnValueOnce(throwError(() => new Error('offline')));
    const offline = TestBed.createComponent(VisualProcessEditorComponent);
    offline.detectChanges();
    expect(offline.componentInstance.registrySource()).toBe('degraded');
    expect(offline.componentInstance.registryStatus()).toContain('Offline-Vertrag');
    expect(offline.componentInstance.nodeDefinitions()).toHaveLength(FALLBACK_KINDS.length);
  });

  it('adds a new step via addStep and updates the graph signal', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = component.graph().steps.length;
    component.addStep();
    const after = component.graph().steps.length;
    expect(after).toBe(before + 1);
    expect(component.graph().steps[after - 1].kind).toBeTruthy();
  });

  it('stores graph recovery strategies canonically and enforces approval for generated plans', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.setGraphRoutingField('context_recovery_strategies', ['stop']);
    component.setGraphRoutingField('require_approval_for_generated_plan', false);

    component.toggleGraphRecoveryStrategy('segment_planning', true);
    component.toggleGraphRecoveryStrategy('propose_task_plan', true);

    expect(component.graphRouting().context_recovery_strategies).toEqual([
      'segment_planning', 'propose_task_plan', 'require_approval', 'stop',
    ]);
    expect(component.graphRouting().require_approval_for_generated_plan).toBe(true);

    component.toggleGraphRecoveryStrategy('require_approval', false);
    expect(component.graphRouting().context_recovery_strategies).toEqual(['stop']);
  });

  it('has a fallback catalog entry for ML-Intern LoRA training', () => {
    const kind = FALLBACK_KINDS.find(item => item.id === 'ml_intern_train_lora');
    expect(kind).toBeDefined();
    expect(kind?.requires_approval).toBe(true);
    expect(kind?.risk_level).toBe('high');
    expect(nodeKindColor('ml_intern_train_lora')).toBeTruthy();
  });

  it('has a fallback catalog entry for ML-Intern LoRA dataset build', () => {
    const kind = FALLBACK_KINDS.find(item => item.id === 'ml_intern_build_lora_dataset');
    expect(kind).toBeDefined();
    expect(kind?.deterministic).toBe(true);
    expect(kind?.risk_level).toBe('medium');
    expect(nodeKindColor('ml_intern_build_lora_dataset')).toBeTruthy();
  });

it('routes validation calls through VpWorkflowRunnerService, not directly to api', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      validateGraph: () => void;
      workflowRunner: { validate: (...args: unknown[]) => void };
    };
    const validateSpy = vi.spyOn(component.workflowRunner, 'validate').mockImplementation(() => {});
    component.validateGraph();
    expect(validateSpy).toHaveBeenCalledTimes(1);
    expect(api.validate).not.toHaveBeenCalled();
  });

  it('routes dry-run calls through VpWorkflowRunnerService', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      runDryRun: () => void;
      workflowRunner: { dryRun: (...args: unknown[]) => void };
    };
    const dryRunSpy = vi.spyOn(component.workflowRunner, 'dryRun').mockImplementation(() => {});
    component.runDryRun();
    expect(dryRunSpy).toHaveBeenCalledTimes(1);
    expect(api.dryRun).not.toHaveBeenCalled();
  });

  it('handles listSavedGraphs failure without breaking init', () => {
    api.listSavedGraphs.mockReturnValueOnce(throwError(() => new Error('boom')));
    expect(() => TestBed.createComponent(VisualProcessEditorComponent).detectChanges()).not.toThrow();
  });

  it('renders the svg canvas element after init', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;
    const svg = html.querySelector('svg');
    expect(svg).not.toBeNull();
  });

  it('applies an approved patch as one undoable command while retaining the persisted revision identity', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = { ...component.graph(), definition_revision: 7, base_graph_hash: 'a'.repeat(64) };
    (component as unknown as { editorState: { initialize: (graph: VpGraph) => void } }).editorState.initialize(before);
    const previewGraph = { ...before, steps: [step('approved-step')], base_graph_hash: 'b'.repeat(64) };
    const applied = (component as unknown as { applyAssistantPatch: (preview: unknown) => boolean }).applyAssistantPatch({
      patch_hash: 'patch', base_graph_hash: before.base_graph_hash, preview_graph_hash: 'b'.repeat(64), preview_graph: previewGraph,
      validation: { valid: true, error_count: 0, warning_count: 0, issues: [] }, operation_count: 1,
      audit_id: 'audit', decision: 'accepted',
    });

    expect(applied).toBe(true);
    expect(component.graph().steps.map(item => item.id)).toEqual(['approved-step']);
    expect(component.graph().definition_revision).toBe(7);
    expect(component.graph().base_graph_hash).toBe('a'.repeat(64));
    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);
    component.undo();
    expect(component.graph().steps).toEqual([]);
    expect(component.graph().definition_revision).toBe(7);
  });

  it('keeps compact read-only mode free of editor and patch mutations', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.componentInstance.editorMode = 'compact-readonly';
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('.vpe-toolbar')).toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelector('.vpe-canvas-wrap')?.classList.contains('readonly')).toBe(true);
    const component = fixture.componentInstance;
    const applied = (component as unknown as { applyAssistantPatch: (preview: unknown) => boolean }).applyAssistantPatch({});
    expect(applied).toBe(false);
    expect(component.isDirty()).toBe(false);
  });

  it('does not report an older overlapping save response as a success', () => {
    const older = new Subject<GraphSaveResult>();
    const newer = new Subject<GraphSaveResult>();
    api.saveGraph.mockReturnValueOnce(older).mockReturnValueOnce(newer);
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.setGraphName('First');
    component.saveGraphToServer();
    component.setGraphName('Second');
    component.saveGraphToServer();

    newer.next({
      id: component.graph().id, version: '3', definition_revision: 3,
      base_graph_hash: 'c'.repeat(64), saved: true,
    });
    older.next({
      id: component.graph().id, version: '2', definition_revision: 2,
      base_graph_hash: 'b'.repeat(64), saved: true,
    });

    expect(component.graph()).toMatchObject({
      name: 'Second', definition_revision: 3, base_graph_hash: 'c'.repeat(64),
    });
    expect(component.statusMsg()).toContain('verworfen');
  });

  it('preserves draft, history and dirty state on 409 and offers reload or an undoable fork', () => {
    api.saveGraph.mockReturnValueOnce(throwError(() => ({ status: 409 })));
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const state = (component as unknown as { editorState: {
      initialize: (graph: VpGraph) => void;
      mutate: (label: string, mutation: (graph: VpGraph) => void) => void;
    } }).editorState;
    state.initialize({
      ...emptyGraph(),
      id: 'hub-graph',
      name: 'Hub Graph',
      definition_revision: 2,
      base_graph_hash: 'b'.repeat(64),
      steps: [step('shared-step')],
    });
    const runner = fixture.debugElement.injector.get(VpWorkflowRunnerService);
    (runner as any).activeWorkflowId.set('hub-graph');
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'hub-graph',
      run_id: 'run-before-fork',
      process_id: 'hub-graph',
      revision: 1,
      updated_at: 1,
      status: 'running',
      steps: [{ step_id: 'shared-step', status: 'running' }],
    });
    state.mutate('local edit', draft => { draft.name = 'Lokaler Draft'; });
    component.saveGraphToServer();
    fixture.detectChanges();

    expect(component.graph().name).toBe('Lokaler Draft');
    expect(component.graph().definition_revision).toBe(2);
    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);
    expect(component.saveConflict()).toBe(true);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Hub-Version neu laden');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Lokalen Draft als Kopie behalten');

    component.undo();
    expect(component.graph().name).toBe('Hub Graph');
    expect(component.canRedo()).toBe(true);
    expect(component.saveConflict()).toBe(true);
    component.redo();
    expect(component.graph().name).toBe('Lokaler Draft');
    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);

    component.forkAfterSaveConflict();
    expect(component.graph().id).toMatch(/^vp-fork-/);
    expect(component.graph().definition_revision).toBe(0);
    expect(component.graph().base_graph_hash).toBeUndefined();
    expect(component.isDirty()).toBe(true);
    expect(component.canUndo()).toBe(true);
    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
  });

  it('retires the accepted runtime before replacing a graph through BPMN import', () => {
    const imported = {
      ...emptyGraph(),
      id: 'imported-graph',
      name: 'Imported graph',
      steps: [step('shared-step')],
    };
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const runner = fixture.debugElement.injector.get(VpWorkflowRunnerService);
    const importExport = fixture.debugElement.injector.get(VpImportExportService);
    vi.spyOn(importExport, 'importBpmn').mockReturnValueOnce(of({
      graph: imported,
      validation: { valid: true, error_count: 0, warning_count: 0, issues: [] },
      warnings: [],
    }));
    (runner as any).activeWorkflowId.set('old-graph');
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'old-graph',
      run_id: 'run-before-import',
      process_id: 'old-graph',
      revision: 1,
      updated_at: 1,
      status: 'running',
      steps: [{ step_id: 'shared-step', status: 'running' }],
    });
    const target = { files: [new File(['<bpmn />'], 'graph.bpmn')], value: 'selected' };

    component.onBpmnFile({ target } as unknown as Event);

    expect(component.graph()).toEqual(imported);
    expect(target.value).toBe('');
    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
  });
});
