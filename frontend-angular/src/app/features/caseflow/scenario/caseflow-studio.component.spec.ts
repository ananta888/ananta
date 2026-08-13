import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { CaseFlowAgentBindingCatalogService } from '../agent-canvas/caseflow-agent-binding-catalog.service';
import { CaseFlowAgentCanvasComponent } from '../agent-canvas/caseflow-agent-canvas.component';
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

    await TestBed.configureTestingModule({
      imports: [CaseFlowStudioComponent],
      providers: [
        { provide: ActivatedRoute, useValue: { queryParamMap: queryParams } },
        { provide: Router, useValue: router },
        { provide: VisualProcessApiService, useValue: api },
        {
          provide: CaseFlowAgentBindingCatalogService,
          useValue: { load: () => of(null) },
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
    component.workspace.editorState.validation.set({
      valid: true, error_count: 0, warning_count: 0, issues: [],
    });
    const agentsTab = initial.querySelector<HTMLElement>('[data-studio-view="agents"]')!;
    agentsTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await Promise.resolve();
    fixture.detectChanges();

    expect(component.workspace.view()).toBe('process');
    expect((fixture.nativeElement as HTMLElement).querySelector('app-visual-process-editor')).not.toBeNull();
    const hostedEditor = fixture.debugElement
      .query(By.directive(VisualProcessEditorComponent))
      .componentInstance as VisualProcessEditorComponent;
    expect(hostedEditor.graphStateHosted).toBe(true);
    expect(hostedEditor.graph).toBe(component.workspace.graph);
    expect(hostedEditor.selectedId()).toBe('builder');
    expect(component.workspace.editorState.validation()).toMatchObject({ valid: true });
    expect(component.workspace.graph()).toMatchObject({ id: 'shared-graph', name: 'Draft bleibt' });
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
    expect(agentCanvas.selectedId).toBe('builder');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-step-id="builder"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(component.workspace.graph()).toMatchObject({ id: 'shared-graph', name: 'Draft bleibt' });
    expect(component.workspace.scenarioId()).toBe('shared-scenario');
    expect(api['loadSavedGraph']).toHaveBeenCalledTimes(1);
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
    const workspace = fixture.componentInstance.workspace;
    workspace.selectEntity('builder');
    fixture.detectChanges();
    expect(workspace.selectedId()).toBe('builder');

    workspace.editorState.execute('AI patch', current => ({
      ...current,
      steps: [],
      edges: [],
    }));
    fixture.detectChanges();

    expect(workspace.selectedId()).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-step-id="builder"]')).toBeNull();
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
