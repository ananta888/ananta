import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CaseFlowAgentBindingCatalogReadModel,
  CaseFlowAgentBindingCatalogService,
} from '../agent-canvas/caseflow-agent-binding-catalog.service';
import { CaseFlowAgentCanvasComponent } from '../agent-canvas/caseflow-agent-canvas.component';
import { CaseFlowAgentEdgeInspectorComponent } from '../agent-canvas/caseflow-agent-edge-inspector.component';
import { CaseFlowEdgeTraceApiService } from '../agent-canvas/caseflow-edge-trace-api.service';
import { CaseFlowAgentNodeInspectorComponent } from '../agent-canvas/caseflow-agent-node-inspector.component';
import { CaseFlowAgentNodeRuntimeInspectorComponent } from '../agent-canvas/caseflow-agent-node-runtime-inspector.component';
import {
  CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG,
  CASEFLOW_EDGE_TRACE_READER,
} from '../agent-canvas/caseflow-agent-runtime-session.facade';
import {
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import { VpModelTrainingOptionsService } from '../../visual-process/vp-model-training-options.service';
import { VisualProcessEditorComponent } from '../../visual-process/visual-process-editor.component';
import { CaseFlowStudioComponent } from './caseflow-studio.component';

beforeAll(async () => {
  await ɵresolveComponentResources(async resource => {
    const name = resource.split('/').at(-1) || resource;
    const candidates = [
      new URL(resource, import.meta.url),
      resolve(process.cwd(), 'src/app/features/caseflow/agent-canvas', name),
      resolve(process.cwd(), 'src/app/features/visual-process', name),
    ];
    for (const candidate of candidates) {
      try {
        return await readFile(candidate, 'utf8');
      } catch {
        // Try the owning feature directory; Angular reports relative URLs only.
      }
    }
    throw new Error(`Component resource not found: ${resource}`);
  });
});

function graph(): VpGraph {
  return {
    id: 'shared-graph',
    name: 'Shared Graph',
    description: '',
    version: '1',
    definition_revision: 1,
    base_graph_hash: 'a'.repeat(64),
    tags: [],
    steps: [{
      id: 'builder', label: 'Builder', kind: 'task', role: 'builder', gate: false,
      policy_hints: [], position: { x: 0, y: 0 }, io: { inputs: [], outputs: [] },
    }],
    edges: [],
  };
}

describe('CaseFlowStudioComponent', () => {
  let queryParams: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  let api: Record<string, ReturnType<typeof vi.fn>>;
  let router: { navigate: ReturnType<typeof vi.fn> };
  let bindingCatalog: { load: ReturnType<typeof vi.fn> };
  let statusResponses: Subject<Record<string, unknown>>[];
  let traceResponses: Subject<Record<string, unknown>>[];
  let traceReader: { read: ReturnType<typeof vi.fn> };
  let inspectorTraceApi: { read: ReturnType<typeof vi.fn> };

  afterEach(() => vi.restoreAllMocks());

  beforeEach(async () => {
    queryParams = new BehaviorSubject(convertToParamMap({
      graph: 'shared-graph', scenario_id: 'shared-scenario', view: 'agents',
    }));
    api = {
      listSavedGraphs: vi.fn(() => of([
        { id: 'shared-graph', name: 'Shared Graph', description: '', tags: [], created_at: 1, updated_at: 1 },
      ])),
      loadSavedGraph: vi.fn(() => of(graph())),
      saveGraph: vi.fn(() => of({
        id: 'shared-graph', version: '2', definition_revision: 2,
        base_graph_hash: 'b'.repeat(64), saved: true,
      })),
      listPresets: vi.fn(() => of([])),
      getPreset: vi.fn(() => of({})),
      listSkillProfiles: vi.fn(() => of([])),
      listTaskKinds: vi.fn(() => of([])),
      listNodeDefinitions: vi.fn(() => of({
        schema: 'ananta.visual_process.node_definition_registry.v1', definitions: [],
      })),
      listModelProfiles: vi.fn(() => of({ profiles: [], fallback_groups: {}, status: 'loaded' })),
      validate: vi.fn(() => of({ valid: true, error_count: 0, warning_count: 0, issues: [] })),
      dryRun: vi.fn(() => of({})),
    };
    router = { navigate: vi.fn().mockResolvedValue(true) };
    bindingCatalog = {
      load: vi.fn(() => of(catalogReadModel(['context-alpha', 'context-beta']))),
    };
    statusResponses = [];
    traceResponses = [];
    api['getWorkflowStatus'] = vi.fn(() => {
      const response = new Subject<Record<string, unknown>>();
      statusResponses.push(response);
      return response.asObservable();
    });
    traceReader = {
      read: vi.fn(() => {
        const response = new Subject<Record<string, unknown>>();
        traceResponses.push(response);
        return response.asObservable();
      }),
    };
    inspectorTraceApi = { read: vi.fn(() => of(runtimeTraceReadModel())) };

    await TestBed.configureTestingModule({
      imports: [CaseFlowStudioComponent],
      providers: [
        { provide: ActivatedRoute, useValue: { queryParamMap: queryParams } },
        { provide: Router, useValue: router },
        { provide: VisualProcessApiService, useValue: api },
        { provide: CASEFLOW_EDGE_TRACE_READER, useValue: traceReader },
        { provide: CaseFlowEdgeTraceApiService, useValue: inspectorTraceApi },
        {
          provide: CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG,
          useValue: { poll_interval_ms: 60_000, max_initial_not_found_polls: 5 },
        },
        {
          provide: CaseFlowAgentBindingCatalogService,
          useValue: bindingCatalog,
        },
        {
          provide: VpModelTrainingOptionsService,
          useValue: {
            load: vi.fn(() => of({
              hubAvailable: true, datasets: [], trainingProfiles: [], baseModels: [],
            })),
          },
        },
      ],
    }).compileComponents();
  });

  it('switches local accessible tabs in both directions without reloading or losing the draft', async () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const initial = fixture.nativeElement as HTMLElement;

    expect(initial.querySelector('app-caseflow-agent-canvas')).not.toBeNull();
    expect(initial.querySelector('[role="tablist"]')).not.toBeNull();
    expect(initial.querySelector('[data-studio-view="agents"]')?.getAttribute('tabindex')).toBe('0');
    expect(initial.querySelector('[data-studio-view="process"]')?.getAttribute('tabindex')).toBe('-1');
    expect(initial.innerHTML).not.toContain('/process-designer');

    const initialCanvas = fixture.debugElement
      .query(By.directive(CaseFlowAgentCanvasComponent))
      .componentInstance as CaseFlowAgentCanvasComponent;
    initialCanvas.selectNode(initialCanvas.projection!.nodes[0]);
    expect(component.workspace.selectedId()).toBe('builder');

    component.workspace.editorState.mutate('unsaved', draft => { draft.name = 'Draft bleibt'; });
    component.workspace.updateDraft({ title: 'Szenario-Draft bleibt' });
    component.workspace.editorState.validation.set({
      valid: true, error_count: 0, warning_count: 0, issues: [],
    });
    const agentsTab = initial.querySelector<HTMLElement>('[data-studio-view="agents"]')!;
    agentsTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await Promise.resolve();
    fixture.detectChanges();

    expect(component.workspace.view()).toBe('process');
    expect((fixture.nativeElement as HTMLElement).querySelector('app-visual-process-editor')).not.toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-show-agent-runtime]')).not.toBeNull();
    const hostedEditor = fixture.debugElement
      .query(By.directive(VisualProcessEditorComponent))
      .componentInstance as VisualProcessEditorComponent;
    expect(hostedEditor.graphStateHosted).toBe(true);
    expect(hostedEditor.runtimeMode).toBe('external-readonly');
    expect(hostedEditor.graph).toBe(component.workspace.graph);
    expect(hostedEditor.selectedId()).toBe('builder');
    expect(component.workspace.editorState.validation()).toMatchObject({ valid: true });
    expect(component.workspace.graph()).toMatchObject({ id: 'shared-graph', name: 'Draft bleibt' });
    expect(component.workspace.draft().title).toBe('Szenario-Draft bleibt');
    expect(component.workspace.dirty()).toBe(true);
    expect(api['loadSavedGraph']).toHaveBeenCalledTimes(1);

    hostedEditor.undo();
    expect(component.workspace.graph().name).toBe('Shared Graph');
    hostedEditor.redo();
    expect(component.workspace.graph().name).toBe('Draft bleibt');

    queryParams.next(convertToParamMap({
      graph: 'shared-graph', scenario_id: 'shared-scenario', view: 'agents',
    }));
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('app-caseflow-agent-canvas')).not.toBeNull();
    const agentCanvas = fixture.debugElement
      .query(By.directive(CaseFlowAgentCanvasComponent))
      .componentInstance as CaseFlowAgentCanvasComponent;
    expect(agentCanvas.graph).toBe(component.workspace.graph());
    expect(agentCanvas.graph.name).toBe('Draft bleibt');
    expect(agentCanvas.selectedId).toBeNull();
    expect(agentCanvas.selectedNodeId).toBe('builder');
    expect(agentCanvas.selectedEdgeIdentity).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-step-id="builder"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(component.workspace.graph()).toMatchObject({ id: 'shared-graph', name: 'Draft bleibt' });
    expect(component.workspace.scenarioId()).toBe('shared-scenario');
    expect(api['loadSavedGraph']).toHaveBeenCalledTimes(1);
  });

  it('owns one graph-bound runtime session across graph edits and Agent/Process view switches', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    const component = fixture.componentInstance;
    const attach = vi.spyOn(component.runtime, 'attach');
    const detach = vi.spyOn(component.runtime, 'detach');

    fixture.detectChanges();
    expect(attach).toHaveBeenCalledOnce();
    expect(attach).toHaveBeenCalledWith({
      graph_id: 'shared-graph', workflow_id: 'shared-graph',
    });
    expect(statusResponses).toHaveLength(1);

    component.workspace.editorState.mutate('local edit', draft => { draft.name = 'Local edit'; });
    fixture.detectChanges();
    component.selectView('process');
    fixture.detectChanges();
    component.selectView('agents');
    fixture.detectChanges();

    expect(attach).toHaveBeenCalledOnce();
    expect(detach).not.toHaveBeenCalled();
    expect(statusResponses).toHaveLength(1);

    fixture.destroy();
    expect(detach).toHaveBeenCalledOnce();
  });

  it('offers an exact retry after the bounded no-run state and hides it after invalid evidence', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.runtime.state.set('no_run_timeout');
    fixture.detectChanges();
    expect(component.runtimeRefreshAvailable()).toBe(true);
    expect((fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-refresh-caseflow-runtime]')?.disabled).toBe(false);

    statusResponses[0].next({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'shared-graph',
      status: 'running',
      revision: 1,
      updated_at: 1,
      steps: [],
    });
    fixture.detectChanges();

    expect(component.runtime.state()).toBe('error');
    expect(component.runtimeRefreshAvailable()).toBe(false);
    expect((fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-refresh-caseflow-runtime]')?.disabled).toBe(true);
  });

  it('keeps same-string node and edge identities mutually exclusive and reverses exact direction', () => {
    api['loadSavedGraph'].mockReturnValueOnce(of(runtimeGraph()));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const canvas = fixture.debugElement
      .query(By.directive(CaseFlowAgentCanvasComponent))
      .componentInstance as CaseFlowAgentCanvasComponent;

    canvas.selectNode(canvas.projection!.nodes.find(node => node.step_id === 'builder')!);
    fixture.detectChanges();

    expect(component.workspace.selectedId()).toBe('builder');
    expect(component.selection.selection()).toMatchObject({
      kind: 'node', graph_id: 'shared-graph', step_id: 'builder',
    });
    expect(fixture.debugElement.query(By.directive(CaseFlowAgentNodeInspectorComponent))).not.toBeNull();
    expect(fixture.debugElement.query(
      By.directive(CaseFlowAgentNodeRuntimeInspectorComponent),
    )).not.toBeNull();
    expect(fixture.debugElement.query(By.directive(CaseFlowAgentEdgeInspectorComponent))).toBeNull();

    canvas.selectEdge(canvas.projection!.edges.find(edge => edge.edge_id === 'builder')!);
    fixture.detectChanges();

    expect(component.workspace.selectedId()).toBeNull();
    expect(component.selection.selection()).toMatchObject({
      kind: 'edge',
      graph_id: 'shared-graph',
      edge: {
        edge_id: 'builder', source_step_id: 'builder', target_step_id: 'critic',
      },
      reverse_edge: {
        edge_id: 'critic-builder', source_step_id: 'critic', target_step_id: 'builder',
      },
    });
    expect(fixture.debugElement.query(By.directive(CaseFlowAgentNodeInspectorComponent))).toBeNull();
    expect(fixture.debugElement.query(
      By.directive(CaseFlowAgentNodeRuntimeInspectorComponent),
    )).toBeNull();
    const edgeInspector = fixture.debugElement
      .query(By.directive(CaseFlowAgentEdgeInspectorComponent))
      .componentInstance as CaseFlowAgentEdgeInspectorComponent;
    expect(edgeInspector.edge).toEqual({
      edge_id: 'builder', source_step_id: 'builder', target_step_id: 'critic',
    });
    expect(edgeInspector.reverseEdge).toEqual({
      edge_id: 'critic-builder', source_step_id: 'critic', target_step_id: 'builder',
    });

    edgeInspector.selectDirection(edgeInspector.reverseEdge!);
    fixture.detectChanges();

    expect(component.selection.selectedEdge()).toEqual({
      edge_id: 'critic-builder', source_step_id: 'critic', target_step_id: 'builder',
    });
    expect(component.selection.reverseEdge()).toEqual({
      edge_id: 'builder', source_step_id: 'builder', target_step_id: 'critic',
    });
  });

  it('fans one runtime/trace read model into Canvas and host inspectors without duplicate trace reads', () => {
    api['loadSavedGraph'].mockReturnValueOnce(of(runtimeGraph()));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    statusResponses[0].next(runtimeStatus());
    expect(traceReader.read).toHaveBeenCalledOnce();
    traceResponses[0].next(runtimeTraceReadModel());
    traceResponses[0].complete();
    fixture.detectChanges();

    const canvas = fixture.debugElement
      .query(By.directive(CaseFlowAgentCanvasComponent))
      .componentInstance as CaseFlowAgentCanvasComponent;
    expect(canvas.runtimeOverlay).toBe(component.runtime.runtimeOverlay());
    expect(canvas.edgeTraceReadModel).toBe(component.runtime.edgeTraceReadModel());

    canvas.selectNode(canvas.projection!.nodes.find(node => node.step_id === 'builder')!);
    fixture.detectChanges();
    const nodeRuntimeInspector = fixture.debugElement
      .query(By.directive(CaseFlowAgentNodeRuntimeInspectorComponent))
      .componentInstance as CaseFlowAgentNodeRuntimeInspectorComponent;
    expect(nodeRuntimeInspector.workflowId).toBe('shared-graph');
    expect(nodeRuntimeInspector.runId).toBe('run-shared');
    expect(nodeRuntimeInspector.runtimeOverlay).toBe(component.runtime.runtimeOverlay());
    expect(nodeRuntimeInspector.traceReadModel).toBe(component.runtime.edgeTraceReadModel());
    expect(inspectorTraceApi.read).not.toHaveBeenCalled();

    canvas.selectEdge(canvas.projection!.edges[0]);
    fixture.detectChanges();
    const edgeInspector = fixture.debugElement
      .query(By.directive(CaseFlowAgentEdgeInspectorComponent))
      .componentInstance as CaseFlowAgentEdgeInspectorComponent;
    expect(edgeInspector.workflowId).toBe('shared-graph');
    expect(edgeInspector.runId).toBe('run-shared');
    expect(edgeInspector.traceReadModel).toBe(component.runtime.edgeTraceReadModel());
    expect(traceReader.read).toHaveBeenCalledOnce();
    expect(inspectorTraceApi.read).not.toHaveBeenCalled();
  });

  it('suppresses stale same-ID runtime and trace evidence after an unsaved graph edit', () => {
    api['loadSavedGraph'].mockReturnValueOnce(of(runtimeGraph()));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    const component = fixture.componentInstance;
    const attach = vi.spyOn(component.runtime, 'attach');
    const detach = vi.spyOn(component.runtime, 'detach');
    fixture.detectChanges();

    statusResponses[0].next(runtimeStatus());
    traceResponses[0].next(runtimeTraceReadModel());
    traceResponses[0].complete();
    component.selectNode('builder');
    fixture.detectChanges();

    expect(component.runtime.runtimeOverlay()).not.toBeNull();
    expect(component.runtime.edgeTraceReadModel()).not.toBeNull();
    expect(attach).toHaveBeenCalledOnce();

    component.workspace.editorState.mutate('structural draft edit', draft => {
      draft.steps[0] = { ...draft.steps[0], label: 'Builder im lokalen Draft' };
      draft.base_graph_hash = 'b'.repeat(64);
    });
    fixture.detectChanges();

    const canvas = fixture.debugElement
      .query(By.directive(CaseFlowAgentCanvasComponent))
      .componentInstance as CaseFlowAgentCanvasComponent;
    const nodeRuntimeInspector = fixture.debugElement
      .query(By.directive(CaseFlowAgentNodeRuntimeInspectorComponent))
      .componentInstance as CaseFlowAgentNodeRuntimeInspectorComponent;
    expect(component.workspace.dirty()).toBe(true);
    expect(component.workspace.graph().steps[0].label).toBe('Builder im lokalen Draft');
    expect(component.runtime.runtimeOverlay()).not.toBeNull();
    expect(component.runtime.edgeTraceReadModel()).not.toBeNull();
    expect(canvas.runtimeOverlay).toBeNull();
    expect(canvas.edgeTraceReadModel).toBeNull();
    expect(nodeRuntimeInspector.runtimeOverlay).toBeNull();
    expect(nodeRuntimeInspector.traceReadModel).toBeNull();
    expect(nodeRuntimeInspector.traceReadModelReason).toBe('caseflow_runtime_draft_changed');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-runtime-evidence-suppressed]')?.textContent).toContain('lokale Draft');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-refresh-caseflow-runtime]')?.disabled).toBe(true);
    expect(attach).toHaveBeenCalledOnce();
    expect(detach).not.toHaveBeenCalled();

    component.workspace.editorState.markSaved();
    fixture.detectChanges();

    expect(component.workspace.dirty()).toBe(false);
    expect(component.runtimeEvidenceReason()).toBe('caseflow_runtime_snapshot_mismatch');
    expect(canvas.runtimeOverlay).toBeNull();
    expect(canvas.edgeTraceReadModel).toBeNull();
  });

  it('suppresses runtime evidence when equal snapshot strings are not canonical SHA-256 hashes', () => {
    api['loadSavedGraph'].mockReturnValueOnce(of({
      ...runtimeGraph(),
      base_graph_hash: 'garbage',
    }));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    statusResponses[0].next({ ...runtimeStatus(), snapshot_hash: 'garbage' });
    traceResponses[0].next(runtimeTraceReadModel());
    traceResponses[0].complete();
    fixture.detectChanges();

    expect(fixture.componentInstance.runtime.runtimeOverlay()).not.toBeNull();
    expect(fixture.componentInstance.runtimeEvidenceSuppressed()).toBe(true);
    expect(fixture.componentInstance.visibleRuntimeOverlay()).toBeNull();
    expect(fixture.componentInstance.visibleEdgeTraceReadModel()).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-refresh-caseflow-runtime]')?.disabled).toBe(true);
  });

  it('matches canonical plain and sha256-prefixed definition hashes', () => {
    api['loadSavedGraph'].mockReturnValueOnce(of(runtimeGraph()));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    statusResponses[0].next({
      ...runtimeStatus(),
      snapshot_hash: `sha256:${'A'.repeat(64)}`,
    });
    traceResponses[0].next(runtimeTraceReadModel());
    traceResponses[0].complete();
    fixture.detectChanges();

    expect(fixture.componentInstance.runtimeEvidenceSuppressed()).toBe(false);
    expect(fixture.componentInstance.visibleRuntimeOverlay()).not.toBeNull();
  });

  it('detaches on graph identity drift or clear and fences selection to the exact graph', () => {
    api['loadSavedGraph'].mockImplementation((graphId: string) => of(runtimeGraph(graphId)));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    const component = fixture.componentInstance;
    const attach = vi.spyOn(component.runtime, 'attach');
    const detach = vi.spyOn(component.runtime, 'detach');
    fixture.detectChanges();
    component.selectNode('builder');
    expect(component.selection.selectedNodeId()).toBe('builder');

    queryParams.next(convertToParamMap({ graph: 'graph-b', view: 'agents' }));
    fixture.detectChanges();

    expect(detach).toHaveBeenCalledOnce();
    expect(attach).toHaveBeenNthCalledWith(2, {
      graph_id: 'graph-b', workflow_id: 'graph-b',
    });
    expect(component.runtime.graphId()).toBe('graph-b');
    expect(component.selection.selection()).toBeNull();

    queryParams.next(convertToParamMap({ view: 'agents' }));
    fixture.detectChanges();

    expect(detach).toHaveBeenCalledTimes(2);
    expect(component.runtime.state()).toBe('detached');
    expect(component.runtime.graphId()).toBeNull();
    expect(component.selection.selection()).toBeNull();
  });

  it('routes inspector access revocation through the Studio runtime owner and clears evidence', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectNode('builder');
    fixture.detectChanges();
    const revoke = vi.spyOn(component.runtime, 'revokeAccess');
    const nodeRuntimeInspector = fixture.debugElement
      .query(By.directive(CaseFlowAgentNodeRuntimeInspectorComponent))
      .componentInstance as CaseFlowAgentNodeRuntimeInspectorComponent;

    nodeRuntimeInspector.accessRevoked.emit('caseflow_node_trace_forbidden');
    fixture.detectChanges();

    expect(revoke).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith('caseflow_node_trace_forbidden');
    expect(component.runtime.state()).toBe('access_revoked');
    expect(component.runtime.runtimeOverlay()).toBeNull();
    expect(component.runtime.edgeTraceReadModel()).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-caseflow-runtime-status]')?.textContent).toContain('Zugriff wurde entzogen');
  });

  it('keeps a hosted save alive after switching back to the agent view', () => {
    const result = new Subject<{
      id: string; version: string; definition_revision: number;
      base_graph_hash: string; saved: boolean;
    }>();
    api['saveGraph'].mockReturnValueOnce(result);
    queryParams.next(convertToParamMap({
      graph: 'shared-graph', scenario_id: 'shared-scenario', view: 'process',
    }));
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const editor = fixture.debugElement
      .query(By.directive(VisualProcessEditorComponent))
      .componentInstance as VisualProcessEditorComponent;
    component.workspace.editorState.mutate('save me', draft => { draft.name = 'Save me'; });

    editor.saveGraphToServer();
    expect(component.workspace.busy()).toBe(true);
    queryParams.next(convertToParamMap({
      graph: 'shared-graph', scenario_id: 'shared-scenario', view: 'agents',
    }));
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.directive(VisualProcessEditorComponent))).toBeNull();

    result.next({
      id: 'shared-graph', version: '2', definition_revision: 2,
      base_graph_hash: 'b'.repeat(64), saved: true,
    });
    fixture.detectChanges();

    expect(component.workspace.graph()).toMatchObject({
      id: 'shared-graph', name: 'Save me', definition_revision: 2,
      base_graph_hash: 'b'.repeat(64),
    });
    expect(component.workspace.graphDirty()).toBe(false);
    expect(component.workspace.message()).toContain('wurde gespeichert');
    expect(api['loadSavedGraph']).toHaveBeenCalledOnce();
  });

  it('clears canonical selection when a shared graph edit removes the selected agent', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const workspace = component.workspace;
    component.selectNode('builder');
    fixture.detectChanges();
    expect(workspace.selectedId()).toBe('builder');
    expect(component.selection.selectedNodeId()).toBe('builder');

    workspace.editorState.execute('AI patch', current => ({
      ...current,
      steps: [],
      edges: [],
    }));
    fixture.detectChanges();

    expect(workspace.selectedId()).toBeNull();
    expect(component.selection.selection()).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-step-id="builder"]')).toBeNull();
  });

  it('offers only authorized Hub context IDs for the Agent-tab Gauntlet action', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;
    const contextSelect = html.querySelector<HTMLSelectElement>(
      '[data-gauntlet-context-source]',
    )!;
    const applyButton = html.querySelector<HTMLButtonElement>(
      '[data-apply-gauntlet-preset]',
    )!;

    expect([...contextSelect.options].map(option => option.value)).toEqual([
      '', 'context-alpha', 'context-beta',
    ]);
    expect(contextSelect.tagName).toBe('SELECT');
    expect(applyButton.disabled).toBe(true);

    fixture.componentInstance.workspace.selectGauntletContextSource('context-alpha');
    fixture.detectChanges();
    expect(applyButton.disabled).toBe(false);

    fixture.componentInstance.workspace.selectGauntletContextSource('caller-injected');
    fixture.detectChanges();
    expect(fixture.componentInstance.workspace.gauntletContextSourceId()).toBe('');
    expect(applyButton.disabled).toBe(true);
  });

  it('confirms only dirty route exits and protects dirty browser unloads', () => {
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);

    expect(component.canLeaveCaseFlowStudio()).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    component.workspace.updateDraft({ title: 'Ungespeichert' });
    expect(component.canLeaveCaseFlowStudio()).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();

    const unload = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    component.preventUnsafeUnload(unload);
    expect(unload.defaultPrevented).toBe(true);
  });

  it.each([
    ['network error', () => throwError(() => ({ status: 503 }))],
    ['mismatched identity', () => of({ ...graph(), id: 'wrong-graph' })],
  ])('announces an initial %s outside the unloaded graph panel', (_label, response) => {
    api['loadSavedGraph'].mockReturnValueOnce(response());
    const fixture = TestBed.createComponent(CaseFlowStudioComponent);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;

    expect(html.querySelector('app-caseflow-agent-canvas')).toBeNull();
    expect(html.querySelector('[role="alert"]')?.textContent).toMatch(/geladen|Graph-ID/);
  });
});

function runtimeGraph(id = 'shared-graph'): VpGraph {
  return {
    ...graph(),
    id,
    name: `Runtime ${id}`,
    steps: [
      {
        id: 'builder', label: 'Builder', kind: 'task', role: 'builder', gate: false,
        policy_hints: [], position: { x: 0, y: 0 }, io: { inputs: [], outputs: [] },
      },
      {
        id: 'critic', label: 'Critic', kind: 'review', role: 'critic', gate: false,
        policy_hints: [], position: { x: 260, y: 0 }, io: { inputs: [], outputs: [] },
      },
    ],
    edges: [
      {
        id: 'builder', source: 'builder', target: 'critic',
        condition: { kind: 'always' },
      },
      {
        id: 'critic-builder', source: 'critic', target: 'builder',
        condition: { kind: 'always' },
      },
    ],
  };
}

function runtimeStatus(): Record<string, unknown> {
  return {
    schema: 'ananta.workflow_backend_status.v1',
    backend: 'hub',
    workflow_id: 'shared-graph',
    run_id: 'run-shared',
    process_id: 'shared-graph',
    snapshot_hash: 'a'.repeat(64),
    revision: 1,
    status: 'running',
    updated_at: 1,
    steps: [
      { step_id: 'builder', status: 'running' },
      { step_id: 'critic', status: 'pending' },
    ],
  };
}

function runtimeTraceReadModel(): Record<string, unknown> {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'shared-graph',
    run_id: 'run-shared',
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    edges: [],
    telemetry: {
      source_event_count: 0,
      processed_event_count: 0,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: 0,
      redaction_policy: 'user',
      messages_per_edge_limit: 64,
      telemetry_per_edge_limit: 128,
    },
  };
}

function catalogReadModel(
  contextIds: readonly string[],
): CaseFlowAgentBindingCatalogReadModel {
  const loaded = { state: 'ready', reason_code: 'catalog_loaded' } as const;
  return {
    state: 'ready',
    catalog: {
      skill_profile_ids: [],
      personality_resource_ids: { agent_profile: [], instruction_layer: [] },
      context_resource_ids: { context_profile: [], context_source: contextIds },
      model_profile_ids: [], model_role_ids: [], fallback_group_ids: [],
    },
    availability: {
      skill_profile: loaded,
      agent_profile: loaded,
      instruction_layer: loaded,
      context_profile: loaded,
      context_source: loaded,
      model_profile: loaded,
      model_role: loaded,
      fallback_group: loaded,
    },
  };
}
