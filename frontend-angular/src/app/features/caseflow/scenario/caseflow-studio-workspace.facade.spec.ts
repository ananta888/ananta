import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CaseFlowAgentBindingCatalogReadModel,
  CaseFlowAgentBindingCatalogService,
} from '../agent-canvas/caseflow-agent-binding-catalog.service';
import {
  BUILDER_CRITIC_GAUNTLET_PRESET_ID,
} from '../agent-canvas/caseflow-agent-preset.commands';
import {
  GraphSaveResult,
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import { VpEditorStateFacade } from '../../visual-process/vp-editor-state.facade';
import { CASEFLOW_UI_EXTENSION } from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';
import { CaseFlowStudioWorkspaceFacade } from './caseflow-studio-workspace.facade';

function graph(id: string, name = id): VpGraph {
  return {
    id,
    name,
    description: '',
    version: '1',
    definition_revision: 1,
    base_graph_hash: id.repeat(64).slice(0, 64),
    tags: [],
    steps: [{
      id: `${id}-agent`,
      label: 'Agent',
      kind: 'task',
      role: 'builder',
      gate: false,
      policy_hints: [],
      position: { x: 0, y: 0 },
      io: { inputs: [], outputs: [] },
    }],
    edges: [],
  };
}

describe('CaseFlowStudioWorkspaceFacade', () => {
  let queryParams: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  let loadRequests: Map<string, Subject<VpGraph>>;
  let saveResult: Subject<GraphSaveResult>;
  let api: {
    listSavedGraphs: ReturnType<typeof vi.fn>;
    loadSavedGraph: ReturnType<typeof vi.fn>;
    saveGraph: ReturnType<typeof vi.fn>;
    getPreset: ReturnType<typeof vi.fn>;
  };
  let bindingCatalog: { load: ReturnType<typeof vi.fn> };
  let router: { navigate: ReturnType<typeof vi.fn> };
  let workspace: CaseFlowStudioWorkspaceFacade;

  beforeEach(() => {
    queryParams = new BehaviorSubject(convertToParamMap({
      graph: 'graph-a',
      scenario_id: 'scenario-a',
      view: 'agents',
    }));
    loadRequests = new Map();
    saveResult = new Subject();
    api = {
      listSavedGraphs: vi.fn(() => of([
        { id: 'graph-a', name: 'A', description: '', tags: [], created_at: 1, updated_at: 1 },
        { id: 'graph-b', name: 'B', description: '', tags: [], created_at: 1, updated_at: 1 },
      ])),
      loadSavedGraph: vi.fn((id: string) => {
        const request = new Subject<VpGraph>();
        loadRequests.set(id, request);
        return request;
      }),
      saveGraph: vi.fn(() => saveResult),
      getPreset: vi.fn(() => of(gauntletPreset())),
    };
    bindingCatalog = { load: vi.fn(() => of(catalogReadModel(['context-a']))) };
    router = { navigate: vi.fn().mockResolvedValue(true) };

    TestBed.configureTestingModule({
      providers: [
        VpEditorStateFacade,
        CaseFlowScenarioRegistryService,
        CaseFlowStudioWorkspaceFacade,
        { provide: ActivatedRoute, useValue: { queryParamMap: queryParams } },
        { provide: Router, useValue: router },
        { provide: VisualProcessApiService, useValue: api },
        {
          provide: CaseFlowAgentBindingCatalogService,
          useValue: bindingCatalog,
        },
      ],
    });
    workspace = TestBed.inject(CaseFlowStudioWorkspaceFacade);
  });

  it('cancels stale graph loads and keeps graph/scenario identity across reactive view history', () => {
    workspace.connect();
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'process',
    }));

    loadRequests.get('graph-b')!.next(graph('graph-b', 'Graph B'));
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Late Graph A'));

    expect(workspace.graph().id).toBe('graph-b');
    expect(workspace.scenarioId()).toBe('scenario-b');
    expect(workspace.view()).toBe('process');
    expect(api.loadSavedGraph).toHaveBeenCalledTimes(2);

    workspace.editorState.mutate('local edit', draft => { draft.name = 'Ungespeichert'; });
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'agents',
    }));

    expect(workspace.view()).toBe('agents');
    expect(workspace.graph()).toMatchObject({ id: 'graph-b', name: 'Ungespeichert' });
    expect(workspace.dirty()).toBe(true);
    expect(api.loadSavedGraph).toHaveBeenCalledTimes(2);
  });

  it('rejects selector and browser-history graph switches while the draft is dirty', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a'));
    workspace.editorState.mutate('local edit', draft => { draft.description = 'lokal'; });
    router.navigate.mockClear();

    expect(workspace.selectGraph('graph-b')).toBe(false);
    expect(router.navigate).not.toHaveBeenCalled();

    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'process',
    }));

    expect(workspace.graph()).toMatchObject({ id: 'graph-a', description: 'lokal' });
    expect(workspace.message()).toContain('Workflowwechsel ist deaktiviert');
    expect(router.navigate).toHaveBeenCalledWith([], expect.objectContaining({
      queryParams: expect.objectContaining({ graph: 'graph-a', scenario_id: 'scenario-a' }),
      replaceUrl: true,
    }));
  });

  it('treats scenario-only edits as dirty and blocks selector and history graph switches', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a'));
    workspace.updateDraft({ title: 'Nur lokal geändert' });
    router.navigate.mockClear();

    expect(workspace.graphDirty()).toBe(false);
    expect(workspace.draftDirty()).toBe(true);
    expect(workspace.dirty()).toBe(true);
    expect(workspace.selectGraph('graph-b')).toBe(false);

    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'agents',
    }));
    expect(workspace.graph().id).toBe('graph-a');
    expect(workspace.draft().title).toBe('Nur lokal geändert');
    expect(router.navigate).toHaveBeenCalledWith([], expect.objectContaining({
      queryParams: expect.objectContaining({ graph: 'graph-a', scenario_id: 'scenario-a' }),
      replaceUrl: true,
    }));
  });

  it('keeps the canonical selection while agent-canvas changes use the shared history', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a'));
    workspace.selectEntity('graph-a-agent');
    const moved = structuredClone(workspace.graph());
    moved.steps[0].position.x = 120;

    workspace.replaceGraphFromAgentView(moved);

    expect(workspace.selectedId()).toBe('graph-a-agent');
    expect(workspace.graph().steps[0].position.x).toBe(120);
    expect(workspace.graphDirty()).toBe(true);
    expect(workspace.editorState.undo()).toBe(true);
    expect(workspace.graph().steps[0].position.x).toBe(0);
    expect(workspace.selectedId()).toBe('graph-a-agent');
  });

  it('rejects a Hub graph whose returned identity differs from the requested graph', () => {
    const initialGraphId = workspace.graph().id;
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-b'));

    expect(workspace.graph().id).toBe(initialGraphId);
    expect(workspace.selectedGraphId()).toBe('');
    expect(workspace.hasError()).toBe(true);
    expect(workspace.message()).toContain('abweichender Graph-ID');
  });

  it('does not expose the editor placeholder graph when no workflow is selected', () => {
    queryParams.next(convertToParamMap({ view: 'agents' }));
    workspace.connect();

    expect(workspace.selectedGraphId()).toBe('');
    expect(workspace.graphLoaded()).toBe(false);
    expect(api.loadSavedGraph).not.toHaveBeenCalled();
  });

  it('cancels an in-flight graph load when the graph query is cleared', () => {
    workspace.connect();
    expect(workspace.loadingGraph()).toBe(true);

    queryParams.next(convertToParamMap({ view: 'agents' }));
    expect(workspace.loadingGraph()).toBe(false);
    expect(workspace.selectedGraphId()).toBe('');
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Zu spät'));

    expect(workspace.graphLoaded()).toBe(false);
    expect(workspace.graph().name).not.toBe('Zu spät');
  });

  it('restores graph A in the canonical route when loading graph B fails', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    router.navigate.mockClear();
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'process',
    }));

    loadRequests.get('graph-b')!.error({ status: 503 });

    expect(workspace.graph()).toMatchObject({ id: 'graph-a', name: 'Graph A' });
    expect(workspace.selectedGraphId()).toBe('graph-a');
    expect(workspace.loadingGraph()).toBe(false);
    expect(router.navigate).toHaveBeenCalledWith([], expect.objectContaining({
      queryParams: expect.objectContaining({ graph: 'graph-a', scenario_id: 'scenario-a' }),
      replaceUrl: true,
    }));
  });

  it('restores graph A when graph B responds with a mismatched identity', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    router.navigate.mockClear();
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'agents',
    }));

    loadRequests.get('graph-b')!.next(graph('graph-c', 'Wrong'));

    expect(workspace.graph()).toMatchObject({ id: 'graph-a', name: 'Graph A' });
    expect(workspace.selectedGraphId()).toBe('graph-a');
    expect(workspace.message()).toContain('abweichender Graph-ID');
    expect(router.navigate).toHaveBeenCalledWith([], expect.objectContaining({
      queryParams: expect.objectContaining({ graph: 'graph-a', scenario_id: 'scenario-a' }),
      replaceUrl: true,
    }));
  });

  it('cancels graph B when browser history returns to the already loaded graph A', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'process',
    }));
    expect(workspace.loadingGraph()).toBe(true);

    queryParams.next(convertToParamMap({
      graph: 'graph-a', scenario_id: 'scenario-a', view: 'agents',
    }));

    expect(workspace.loadingGraph()).toBe(false);
    expect(workspace.view()).toBe('agents');
    loadRequests.get('graph-b')!.next(graph('graph-b', 'Zu spät'));
    expect(workspace.graph()).toMatchObject({ id: 'graph-a', name: 'Graph A' });
  });

  it('does not replace graph A when it becomes dirty while graph B is loading', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'agents',
    }));
    workspace.editorState.mutate('late local edit', draft => { draft.name = 'A bleibt'; });

    loadRequests.get('graph-b')!.next(graph('graph-b', 'Graph B'));

    expect(workspace.graph()).toMatchObject({ id: 'graph-a', name: 'A bleibt' });
    expect(workspace.selectedGraphId()).toBe('graph-a');
    expect(workspace.message()).toContain('Workflowwechsel ist deaktiviert');
  });

  it('previews and publishes the current in-memory graph without hiding later edits', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next({
      ...graph('graph-a', 'Hub-Name'),
      extensions: {
        [CASEFLOW_UI_EXTENSION]: {
          schema: 'ananta.caseflow.ui/v1',
          id: 'scenario-a',
          title: 'Alt', description: '', icon: 'account_tree', caseType: 'case',
          workflowGraphId: 'graph-a', tags: [],
          pages: [{
            id: 'overview', title: 'Übersicht', future_page: 'kept',
            blocks: [{ id: 'summary', kind: 'summary', title: 'Alt', future_block: 9 }],
          }],
          future_root: true,
        },
      },
    });
    workspace.editorState.mutate('rename graph', draft => { draft.name = 'Lokaler Graph'; });
    workspace.updateDraft({ title: 'Lokale Anwendung' });

    workspace.generatePreview();
    expect(workspace.preview()?.title).toBe('Lokale Anwendung');
    workspace.publish();

    const submitted = api.saveGraph.mock.calls[0][0] as VpGraph;
    expect(submitted.name).toBe('Lokaler Graph');
    const extension = submitted.extensions?.[CASEFLOW_UI_EXTENSION] as Record<string, unknown>;
    expect(extension['future_root']).toBe(true);
    workspace.editorState.mutate('later edit', draft => { draft.description = 'Später'; });
    workspace.updateDraft({ title: 'Noch späterer Titel' });
    saveResult.next({
      id: 'graph-a', version: '2', definition_revision: 2,
      base_graph_hash: 'b'.repeat(64), saved: true,
    });

    expect(workspace.graph()).toMatchObject({
      id: 'graph-a', name: 'Lokaler Graph', description: 'Später', definition_revision: 2,
    });
    expect(workspace.draft().title).toBe('Noch späterer Titel');
    expect(workspace.draftDirty()).toBe(true);
    expect(workspace.dirty()).toBe(true);
    expect(workspace.message()).toContain('spätere Änderungen bleiben ungespeichert');
  });

  it('offers a conflict-safe local fork with cleared persistence identity', () => {
    api.saveGraph.mockReturnValueOnce(throwError(() => ({ status: 409 })));
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    workspace.editorState.mutate('local', draft => { draft.description = 'Bleibt'; });

    workspace.saveCurrentGraph();
    expect(workspace.saveConflict()).toBe(true);
    expect(workspace.graph()).toMatchObject({ id: 'graph-a', description: 'Bleibt' });

    workspace.forkAfterSaveConflict();
    expect(workspace.graph().id).toMatch(/^vp-fork-/);
    expect(workspace.graph()).toMatchObject({
      name: 'Graph A (Kopie)', description: 'Bleibt', definition_revision: 0,
    });
    expect(workspace.graph().base_graph_hash).toBeUndefined();
    expect(workspace.selectedGraphId()).toBe(workspace.graph().id);
    expect(workspace.saveConflict()).toBe(false);
    expect(workspace.dirty()).toBe(true);
  });

  it('applies the fully authorized Gauntlet as exactly one undoable graph command', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next({
      ...graph('graph-a', 'Graph A'),
      metadata: { unrelated: { identity: 'preserved' } },
    });
    workspace.generatePreview();
    expect(workspace.preview()).not.toBeNull();
    workspace.selectGauntletContextSource('context-a');
    const execute = vi.spyOn(workspace.editorState, 'execute');

    workspace.applyBuilderCriticGauntlet();

    expect(api.getPreset).toHaveBeenCalledOnce();
    expect(api.getPreset).toHaveBeenCalledWith(BUILDER_CRITIC_GAUNTLET_PRESET_ID);
    expect(bindingCatalog.load).toHaveBeenCalledTimes(2);
    expect(execute).toHaveBeenCalledOnce();
    expect(workspace.graph()).toMatchObject({
      id: 'graph-a',
      name: 'Graph A',
      metadata: { unrelated: { identity: 'preserved' } },
    });
    expect(workspace.graph().steps.map(step => step.id)).toEqual([
      'graph-a-agent',
      'gauntlet-lead',
      'gauntlet-builder',
      'gauntlet-critic',
    ]);
    expect(workspace.graph().edges.map(edge => edge.id)).toEqual([
      'gauntlet-lead-builder',
      'gauntlet-lead-critic',
      'gauntlet-builder-critic',
      'gauntlet-critic-builder-feedback',
    ]);
    expect(workspace.graph().steps.find(step => step.id === 'gauntlet-critic')
      ?.metadata?.['ananta.caseflow.agent-bindings']).toMatchObject({
        context_bindings: [{
          resource_type: 'context_source', resource_id: 'context-a',
        }],
      });
    expect(workspace.preview()).toBeNull();
    expect(workspace.graphDirty()).toBe(true);
    expect(workspace.editorState.canUndo()).toBe(true);
    expect(workspace.editorState.undo()).toBe(true);
    expect(workspace.graph().steps.map(step => step.id)).toEqual(['graph-a-agent']);
    expect(workspace.editorState.canUndo()).toBe(false);
  });

  it('fences double clicks, publishing, graph switches and a stale local revision', () => {
    const presetResult = new Subject<VpGraph>();
    const freshCatalog = new Subject<CaseFlowAgentBindingCatalogReadModel>();
    api.getPreset.mockReturnValueOnce(presetResult);
    bindingCatalog.load
      .mockReturnValueOnce(of(catalogReadModel(['context-a'])))
      .mockReturnValueOnce(freshCatalog);
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    workspace.generatePreview();
    workspace.selectGauntletContextSource('context-a');

    workspace.applyBuilderCriticGauntlet();
    workspace.applyBuilderCriticGauntlet();
    workspace.publish();

    expect(workspace.presetBusy()).toBe(true);
    expect(api.getPreset).toHaveBeenCalledOnce();
    expect(api.saveGraph).not.toHaveBeenCalled();
    expect(workspace.selectGraph('graph-b')).toBe(false);
    expect(router.navigate).not.toHaveBeenCalledWith([], expect.objectContaining({
      queryParams: expect.objectContaining({ graph: 'graph-b' }),
    }));

    workspace.editorState.mutate('late edit', draft => {
      draft.description = 'must survive';
    });
    presetResult.next(gauntletPreset());
    presetResult.complete();
    freshCatalog.next(catalogReadModel(['context-a']));
    freshCatalog.complete();

    expect(workspace.presetBusy()).toBe(false);
    expect(workspace.graph()).toMatchObject({
      id: 'graph-a', description: 'must survive',
    });
    expect(workspace.graph().steps.map(step => step.id)).toEqual(['graph-a-agent']);
    expect(workspace.message()).toContain('lokale Draft geändert');
  });

  it('does not start a preset request while a newer routed graph is still loading', () => {
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    workspace.selectGauntletContextSource('context-a');
    queryParams.next(convertToParamMap({
      graph: 'graph-b', scenario_id: 'scenario-b', view: 'agents',
    }));

    expect(workspace.loadingGraph()).toBe(true);
    expect(workspace.requestedGraphId()).toBe('graph-b');
    expect(workspace.canApplyBuilderCriticGauntlet()).toBe(false);
    workspace.applyBuilderCriticGauntlet();

    expect(api.getPreset).not.toHaveBeenCalled();
    expect(bindingCatalog.load).toHaveBeenCalledOnce();
    expect(workspace.graph()).toMatchObject({ id: 'graph-a', name: 'Graph A' });
    expect(workspace.graph().steps.map(step => step.id)).toEqual(['graph-a-agent']);
    expect(workspace.presetBusy()).toBe(false);
  });

  it.each([
    ['revoked context', gauntletPreset(), catalogReadModel([])],
    ['degraded context catalog', gauntletPreset(), catalogReadModel(
      [], 'degraded', 'catalog_request_failed',
    )],
    ['wrong preset response', { ...gauntletPreset(), id: 'wrong-preset' }, catalogReadModel(['context-a'])],
    ['null preset response', null, catalogReadModel(['context-a'])],
    ['malformed preset response', {
      id: BUILDER_CRITIC_GAUNTLET_PRESET_ID,
    }, catalogReadModel(['context-a'])],
  ])('fails closed without history or preview mutation for a %s', (
    _label,
    preset,
    freshCatalog,
  ) => {
    bindingCatalog.load
      .mockReturnValueOnce(of(catalogReadModel(['context-a'])))
      .mockReturnValueOnce(of(freshCatalog));
    api.getPreset.mockReturnValueOnce(of(preset));
    workspace.connect();
    loadRequests.get('graph-a')!.next(graph('graph-a', 'Graph A'));
    workspace.generatePreview();
    const preview = workspace.preview();
    workspace.selectGauntletContextSource('context-a');

    workspace.applyBuilderCriticGauntlet();

    expect(workspace.graph().steps.map(step => step.id)).toEqual(['graph-a-agent']);
    expect(workspace.editorState.canUndo()).toBe(false);
    expect(workspace.preview()).toBe(preview);
    expect(workspace.hasError()).toBe(true);
    expect(workspace.presetBusy()).toBe(false);
  });
});

function catalogReadModel(
  contextIds: readonly string[],
  contextState: 'ready' | 'degraded' = 'ready',
  contextReason: 'catalog_loaded' | 'catalog_request_failed' = 'catalog_loaded',
): CaseFlowAgentBindingCatalogReadModel {
  const loaded = { state: 'ready', reason_code: 'catalog_loaded' } as const;
  return {
    state: contextState,
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
      context_source: { state: contextState, reason_code: contextReason },
      model_profile: loaded,
      model_role: loaded,
      fallback_group: loaded,
    },
  };
}

function gauntletPreset(): VpGraph {
  const presetStep = (
    id: string,
    label: string,
    role: string,
    kind: string,
  ) => ({
    id, label, role, kind,
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: role === 'critic' ? ['read_only'] : [],
    gate: false,
  });
  return {
    id: BUILDER_CRITIC_GAUNTLET_PRESET_ID,
    name: 'Builder/Critic Gauntlet',
    description: '', version: '1', tags: [],
    metadata: {
      'ananta.caseflow.agent-preset': {
        schema: 'ananta.caseflow.agent-preset/v1',
        binding_slots: [{
          slot: 'critic_benchmark_context', step_id: 'gauntlet-critic',
          resource_type: 'context_source', required: true, access: 'read_only',
        }],
      },
    },
    extensions: {
      'ananta.caseflow.agent-canvas': {
        schema: 'ananta.caseflow.agent-canvas/v1',
        nodes: {
          'gauntlet-lead': { icon: 'star' },
          'gauntlet-builder': { icon: 'code' },
          'gauntlet-critic': { icon: 'rule' },
        },
      },
    },
    steps: [
      presetStep('gauntlet-lead', 'Lead', 'lead', 'plan_only'),
      presetStep('gauntlet-builder', 'Builder', 'developer', 'patch_propose'),
      presetStep('gauntlet-critic', 'Critic', 'critic', 'review'),
    ],
    edges: [
      { id: 'gauntlet-lead-builder', source: 'gauntlet-lead', target: 'gauntlet-builder', condition: { kind: 'always' } },
      { id: 'gauntlet-lead-critic', source: 'gauntlet-lead', target: 'gauntlet-critic', condition: { kind: 'always' } },
      { id: 'gauntlet-builder-critic', source: 'gauntlet-builder', target: 'gauntlet-critic', condition: { kind: 'on_success' } },
      {
        id: 'gauntlet-critic-builder-feedback', source: 'gauntlet-critic', target: 'gauntlet-builder',
        condition: { kind: 'back_edge', loop_policy: { kind: 'fixed', max_iterations: 3 } },
      },
    ],
  };
}
