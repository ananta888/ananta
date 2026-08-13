import {
  DestroyRef,
  Injectable,
  OnDestroy,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, ParamMap, Router } from '@angular/router';
import { Subject, catchError, forkJoin, map, of, switchMap } from 'rxjs';

import {
  CaseFlowAgentBindingCatalogReadModel,
  CaseFlowAgentBindingCatalogService,
} from '../agent-canvas/caseflow-agent-binding-catalog.service';
import {
  BUILDER_CRITIC_GAUNTLET_EDGE_IDS,
  BUILDER_CRITIC_GAUNTLET_PRESET_ID,
  BUILDER_CRITIC_GAUNTLET_STEP_IDS,
  CaseFlowAgentPresetResult,
  applyBuilderCriticGauntletPreset,
} from '../agent-canvas/caseflow-agent-preset.commands';
import { projectAgentCanvas } from '../agent-canvas/caseflow-agent-canvas.mapper';
import {
  SavedGraphSummary,
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import { emptyGraph } from '../../visual-process/vp-editor-config';
import { VpEditorStateFacade } from '../../visual-process/vp-editor-state.facade';
import {
  VpEditorPersistencePort,
  VpEditorSaveAcceptance,
} from '../../visual-process/vp-editor-state.port';
import {
  CaseFlowScenarioDefinition,
  CaseFlowScenarioDraft,
} from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';

export type CaseFlowStudioView = 'agents' | 'process';

interface StudioRouteSelection {
  readonly graphId: string;
  readonly scenarioId: string;
  readonly view: CaseFlowStudioView;
}

interface GraphLoadRequest {
  readonly graphId: string;
  readonly scenarioId: string;
}

type GraphLoadOutcome =
  | { readonly request: GraphLoadRequest; readonly graph: VpGraph }
  | { readonly request: GraphLoadRequest; readonly error: true };

const EMPTY_DRAFT: Readonly<CaseFlowScenarioDraft> = Object.freeze({
  id: '',
  title: '',
  description: '',
  icon: 'account_tree',
  caseType: '',
});

/**
 * Studio coordinator. It owns navigation and persistence workflows while the
 * injected editor-state facade remains the single canonical graph aggregate.
 */
@Injectable()
export class CaseFlowStudioWorkspaceFacade
implements OnDestroy, VpEditorPersistencePort {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly api = inject(VisualProcessApiService);
  private readonly registry = inject(CaseFlowScenarioRegistryService);
  private readonly bindingCatalog = inject(CaseFlowAgentBindingCatalogService);
  private readonly destroyRef = inject(DestroyRef);
  readonly editorState = inject(VpEditorStateFacade);

  readonly graph = this.editorState.graph;
  readonly graphDirty = this.editorState.dirty;
  readonly graphs = signal<readonly SavedGraphSummary[]>([]);
  readonly loadingGraphs = signal(true);
  readonly loadingGraph = signal(false);
  readonly busy = signal(false);
  readonly presetBusy = signal(false);
  readonly view = signal<CaseFlowStudioView>('agents');
  readonly requestedGraphId = signal('');
  readonly scenarioId = signal('');
  readonly draft = signal<CaseFlowScenarioDraft>({ ...EMPTY_DRAFT });
  readonly preview = signal<CaseFlowScenarioDefinition | null>(null);
  readonly message = signal('');
  readonly hasError = signal(false);
  readonly saveConflict = signal(false);
  readonly catalogReadModel = signal<CaseFlowAgentBindingCatalogReadModel | null>(null);
  readonly gauntletContextSourceId = signal('');

  private readonly loadedGraphId = signal('');
  private readonly savedDraftFingerprint = signal(draftFingerprint(EMPTY_DRAFT));
  readonly selectedGraphId = computed(() => this.loadedGraphId() || this.requestedGraphId());
  readonly graphLoaded = computed(() => this.loadedGraphId().length > 0);
  readonly selectedGraphIsLocalOnly = computed(() =>
    this.graphLoaded()
    && !this.graphs().some(graph => graph.id === this.selectedGraphId()));
  readonly draftDirty = computed(() =>
    draftFingerprint(this.draft()) !== this.savedDraftFingerprint());
  readonly dirty = computed(() => this.graphDirty() || this.draftDirty());
  readonly selectedId = this.editorState.selectedId;
  readonly selectedAgentId = computed(() => {
    const selected = this.selectedId();
    return selected && this.graph().steps.some(step => step.id === selected)
      ? selected
      : '';
  });
  readonly agentProjection = computed(() => projectAgentCanvas(this.graph()));
  readonly hasAgentSteps = computed(() => {
    const projection = this.agentProjection();
    return projection.ok && projection.value.nodes.length > 0;
  });
  readonly gauntletContextSourceIds = computed<readonly string[]>(() => {
    const readModel = this.catalogReadModel();
    return readModel?.availability.context_source.state === 'ready'
      ? readModel.catalog.context_resource_ids.context_source
      : [];
  });
  readonly canApplyBuilderCriticGauntlet = computed(() => {
    const contextSourceId = this.gauntletContextSourceId();
    return this.graphLoaded()
      && !this.loadingGraph()
      && !this.busy()
      && !this.presetBusy()
      && this.requestedGraphId() === this.loadedGraphId()
      && contextSourceId.length > 0
      && this.gauntletContextSourceIds().includes(contextSourceId);
  });

  private readonly graphLoads = new Subject<GraphLoadRequest | null>();
  private connected = false;
  private presetRequestGeneration = 0;
  private catalogRequestGeneration = 0;
  private pendingGraphId = '';
  private lastRouteSelection: StudioRouteSelection = {
    graphId: '',
    scenarioId: '',
    view: 'agents',
  };

  connect(): void {
    if (this.connected) return;
    this.connected = true;

    this.graphLoads.pipe(
      switchMap(request => request ? this.api.loadSavedGraph(request.graphId).pipe(
        map(graph => ({ request, graph }) as GraphLoadOutcome),
        catchError(() => of({ request, error: true } as GraphLoadOutcome)),
      ) : of(null)),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(outcome => {
      if (outcome) this.acceptGraphLoad(outcome);
    });

    this.refreshGraphList();

    const catalogGeneration = ++this.catalogRequestGeneration;
    this.bindingCatalog.load().pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(readModel => {
      if (catalogGeneration !== this.catalogRequestGeneration) return;
      this.acceptCatalogReadModel(readModel);
    });

    this.route.queryParamMap.pipe(
      map(params => routeSelection(params)),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(selection => this.acceptRouteSelection(selection));
  }

  selectGraph(graphId: string): boolean {
    const currentId = this.loadedGraphId();
    if (this.presetBusy() && graphId !== currentId) {
      this.hasError.set(true);
      this.message.set('Workflowwechsel ist während der Preset-Prüfung gesperrt.');
      return false;
    }
    if (this.dirty() && currentId && graphId !== currentId) {
      this.rejectDirtyGraphSwitch();
      return false;
    }
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        graph: graphId || null,
        scenario_id: null,
        view: this.view(),
      },
      queryParamsHandling: 'merge',
    });
    return true;
  }

  selectView(view: CaseFlowStudioView): void {
    if (view === this.view()) return;
    this.view.set(view);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.canonicalQueryParams(view),
      queryParamsHandling: 'merge',
    });
  }

  updateDraft(patch: Partial<CaseFlowScenarioDraft>): void {
    this.draft.update(current => ({ ...current, ...patch }));
    this.preview.set(null);
    this.message.set('');
  }

  selectGauntletContextSource(contextSourceId: string): void {
    const normalized = typeof contextSourceId === 'string'
      ? contextSourceId
      : '';
    this.gauntletContextSourceId.set(
      this.gauntletContextSourceIds().includes(normalized) ? normalized : '',
    );
    this.message.set('');
  }

  applyBuilderCriticGauntlet(): void {
    if (this.presetBusy() || this.busy()) return;
    const graphId = this.loadedGraphId();
    const contextSourceId = this.gauntletContextSourceId();
    if (!graphId
      || this.graph().id !== graphId
      || this.loadingGraph()
      || this.requestedGraphId() !== graphId) {
      this.hasError.set(true);
      this.message.set('Warte, bis der ausgewählte Workflow vollständig geladen ist.');
      return;
    }
    if (!this.gauntletContextSourceIds().includes(contextSourceId)) {
      this.hasError.set(true);
      this.message.set('Wähle eine aktuell autorisierte Kontextquelle aus dem Hub-Katalog.');
      return;
    }

    const generation = ++this.presetRequestGeneration;
    const catalogGeneration = ++this.catalogRequestGeneration;
    const revision = this.editorState.revision();
    this.presetBusy.set(true);
    this.hasError.set(false);
    this.message.set('Builder/Critic-Preset und Berechtigungen werden geprüft …');

    forkJoin({
      preset: this.api.getPreset(BUILDER_CRITIC_GAUNTLET_PRESET_ID),
      catalog: this.bindingCatalog.load(),
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ preset, catalog }) => {
        if (!this.isCurrentPresetRequest(
          generation,
          graphId,
          revision,
          contextSourceId,
          catalogGeneration,
        )) {
          this.finishPresetRequest(generation);
          this.hasError.set(true);
          this.message.set('Preset-Anfrage wurde verworfen, weil sich der lokale Draft geändert hat.');
          return;
        }

        this.acceptCatalogReadModel(catalog);
        const authorizedContextIds = catalog.availability.context_source.state === 'ready'
          ? catalog.catalog.context_resource_ids.context_source
          : [];
        const presetId = readGraphIdentity(preset);
        if (presetId !== BUILDER_CRITIC_GAUNTLET_PRESET_ID
          || !authorizedContextIds.includes(contextSourceId)) {
          this.finishPresetRequest(generation);
          this.gauntletContextSourceId.set(
            authorizedContextIds.includes(contextSourceId) ? contextSourceId : '',
          );
          this.hasError.set(true);
          this.message.set(presetId !== BUILDER_CRITIC_GAUNTLET_PRESET_ID
            ? 'Der Hub lieferte ein abweichendes Preset; der Graph blieb unverändert.'
            : 'Die Kontextfreigabe ist nicht mehr aktuell; der Graph blieb unverändert.');
          return;
        }

        let result: CaseFlowAgentPresetResult | null = null;
        const changed = this.editorState.execute(
          'Builder/Critic Gauntlet anwenden',
          current => {
            result = applyBuilderCriticGauntletPreset(current, preset, {
              selected_step_ids: BUILDER_CRITIC_GAUNTLET_STEP_IDS,
              selected_edge_ids: BUILDER_CRITIC_GAUNTLET_EDGE_IDS,
              critic_benchmark_context_binding: {
                resource_type: 'context_source',
                resource_id: contextSourceId,
              },
            }, catalog.catalog);
            return result.ok ? result.value : current;
          },
        );
        this.finishPresetRequest(generation);
        if (!result || !result.ok || !changed) {
          this.hasError.set(true);
          this.message.set(result && !result.ok
            ? `Preset konnte nicht angewendet werden: ${result.issues[0]?.message ?? 'ungültige Auswahl'}`
            : 'Preset hat keine Änderung am aktuellen Graph erzeugt.');
          return;
        }
        this.preview.set(null);
        this.hasError.set(false);
        this.message.set('Builder/Critic Gauntlet wurde als ein rückgängig machbarer Schritt eingefügt.');
      },
      error: () => {
        if (generation !== this.presetRequestGeneration) return;
        this.finishPresetRequest(generation);
        this.catalogReadModel.set(null);
        this.gauntletContextSourceId.set('');
        this.hasError.set(true);
        this.message.set('Preset oder aktueller Berechtigungskatalog konnte nicht geladen werden.');
      },
    });
  }

  selectEntity(id: string | null): void {
    this.selectedId.set(id);
  }

  replaceGraphFromAgentView(graph: VpGraph): void {
    if (!this.sameGraphIdentity(graph)) return;
    this.editorState.execute('Agentenansicht aktualisieren', () => graph);
    this.preview.set(null);
    this.message.set('Ungespeicherte Agentenänderung im gemeinsamen Workflow-Draft.');
  }

  generatePreview(): void {
    const graph = this.currentGraphOrReportError();
    if (!graph) return;
    this.preview.set(this.registry.compileFromGraph(graph, this.draft()));
    this.hasError.set(false);
    this.message.set('UI-Struktur aus dem aktuellen ungespeicherten Workflow erzeugt.');
  }

  saveCurrentGraph(): void {
    const graph = this.currentGraphOrReportError();
    if (!graph || this.busy()) return;
    const request = this.editorState.captureSaveRequest();
    this.busy.set(true);
    this.hasError.set(false);
    this.api.saveGraph(request.graph).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: result => {
        this.busy.set(false);
        const acceptance = this.editorState.acceptSaveResult(result, request);
        if (!isAcceptedSave(acceptance)) {
          this.hasError.set(true);
          this.message.set('Veraltete oder abweichende Speicherantwort wurde verworfen.');
          return;
        }
        this.saveConflict.set(false);
        this.message.set(acceptance.status === 'accepted_clean'
          ? `Workflow „${this.graph().name}“ wurde gespeichert.`
          : `Workflow „${this.graph().name}“ wurde gespeichert; spätere Änderungen bleiben offen.`);
        this.refreshGraphList();
      },
      error: error => this.acceptSaveError(error),
    });
  }

  publish(): void {
    const current = this.currentGraphOrReportError();
    if (!current || this.busy() || this.presetBusy()) return;
    const scenario = this.registry.compileFromGraph(current, this.draft());
    const submittedDraftFingerprint = draftFingerprint(this.draft());
    const graphToPublish = this.registry.withScenario(current, scenario);
    this.editorState.replaceGraph(graphToPublish, { markDirty: true, resetHistory: false });
    const request = this.editorState.captureSaveRequest();

    this.preview.set(scenario);
    this.busy.set(true);
    this.hasError.set(false);
    this.registry.publish(request.graph, scenario).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: result => {
        const acceptance = this.editorState.acceptSaveResult(result, request);
        this.busy.set(false);
        if (!isAcceptedSave(acceptance)) {
          this.hasError.set(true);
          this.message.set('Veraltete oder abweichende Veröffentlichungsantwort wurde verworfen.');
          return;
        }
        this.scenarioId.set(scenario.id);
        this.savedDraftFingerprint.set(submittedDraftFingerprint);
        this.saveConflict.set(false);
        const clean = acceptance.status === 'accepted_clean' && !this.draftDirty();
        this.message.set(clean
          ? `CaseFlow „${scenario.title}“ wurde am Visual Process veröffentlicht.`
          : `CaseFlow „${scenario.title}“ wurde veröffentlicht; spätere Änderungen bleiben ungespeichert.`);
        this.syncCanonicalRoute(true);
        this.refreshGraphList();
      },
      error: error => this.acceptSaveError(error, 'CaseFlow konnte nicht veröffentlicht werden.'),
    });
  }

  forkAfterSaveConflict(): void {
    if (!this.saveConflict()) return;
    const uuid = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`;
    const forkId = `vp-fork-${uuid}`;
    const source = this.graph();
    let fork = structuredClone(source);
    fork.id = forkId;
    fork.name = `${source.name} (Kopie)`;
    fork.definition_revision = 0;
    delete fork.base_graph_hash;
    const scenario = this.registry.fromGraph(source);
    if (scenario) {
      fork = this.registry.withScenario(fork, {
        ...scenario,
        workflowGraphId: forkId,
      });
    }
    this.editorState.replaceGraph(fork, { markDirty: true });
    this.loadedGraphId.set(forkId);
    this.requestedGraphId.set(forkId);
    this.saveConflict.set(false);
    this.hasError.set(false);
    this.message.set('Lokaler Konflikt-Draft wurde als neue Kopie vorbereitet.');
    this.syncCanonicalRoute(true, true);
  }

  ngOnDestroy(): void {
    this.graphLoads.complete();
    this.editorState.destroy();
  }

  private acceptRouteSelection(selection: StudioRouteSelection): void {
    const currentId = this.loadedGraphId();
    if (this.presetBusy() && currentId && selection.graphId !== currentId) {
      this.hasError.set(true);
      this.message.set('Workflowwechsel ist während der Preset-Prüfung gesperrt.');
      this.syncCanonicalRoute(true, true);
      return;
    }
    if (
      this.dirty()
      && currentId
      && selection.graphId !== currentId
    ) {
      this.cancelGraphLoad();
      this.rejectDirtyGraphSwitch();
      this.syncCanonicalRoute(true, true);
      return;
    }

    this.lastRouteSelection = selection;
    this.view.set(selection.view);
    this.requestedGraphId.set(selection.graphId);

    if (!selection.graphId) {
      this.cancelGraphLoad();
      if (currentId) {
        this.editorState.initialize(emptyGraph());
        this.loadedGraphId.set('');
      }
      this.clearScenario();
      return;
    }
    if (selection.graphId === currentId) {
      if (this.pendingGraphId && this.pendingGraphId !== currentId) {
        this.cancelGraphLoad();
      }
      if (this.scenarioId() && selection.scenarioId !== this.scenarioId()) {
        this.syncCanonicalRoute(true);
      }
      return;
    }
    if (selection.graphId === this.pendingGraphId) return;

    this.pendingGraphId = selection.graphId;
    this.loadingGraph.set(true);
    this.hasError.set(false);
    this.graphLoads.next({
      graphId: selection.graphId,
      scenarioId: selection.scenarioId,
    });
  }

  private acceptGraphLoad(outcome: GraphLoadOutcome): void {
    if (this.pendingGraphId === outcome.request.graphId) this.pendingGraphId = '';
    if (this.requestedGraphId() !== outcome.request.graphId) return;
    this.loadingGraph.set(false);

    if ('error' in outcome) {
      this.hasError.set(true);
      this.message.set('Der aktuelle Workflow konnte nicht geladen werden.');
      this.restoreCanonicalLoadedRoute();
      return;
    }
    if (outcome.graph.id !== outcome.request.graphId) {
      this.hasError.set(true);
      this.message.set('Der Hub lieferte einen Workflow mit abweichender Graph-ID.');
      this.restoreCanonicalLoadedRoute();
      return;
    }
    if (
      this.dirty()
      && this.loadedGraphId()
      && outcome.request.graphId !== this.loadedGraphId()
    ) {
      this.rejectDirtyGraphSwitch();
      this.restoreCanonicalLoadedRoute();
      return;
    }

    this.editorState.initialize(outcome.graph);
    this.loadedGraphId.set(outcome.graph.id);
    const parsedExisting = this.registry.fromGraph(outcome.graph);
    const existing = parsedExisting?.workflowGraphId === outcome.graph.id
      ? parsedExisting
      : null;
    const scenario = existing ?? this.registry.compileFromGraph(
      outcome.graph,
      outcome.request.scenarioId ? { id: outcome.request.scenarioId } : undefined,
    );
    this.scenarioId.set(scenario.id);
    const draft = draftFromScenario(scenario);
    this.draft.set(draft);
    this.savedDraftFingerprint.set(draftFingerprint(draft));
    this.preview.set(existing);
    this.selectedId.set(null);
    this.saveConflict.set(false);
    this.hasError.set(false);
    this.message.set('');
    this.syncCanonicalRoute(true);
  }

  private rejectDirtyGraphSwitch(): void {
    this.hasError.set(true);
    this.message.set(
      'Workflowwechsel ist deaktiviert, solange der aktuelle Studio-Draft ungespeicherte Änderungen enthält.',
    );
  }

  private currentGraphOrReportError(): VpGraph | null {
    const graph = this.graph();
    if (this.loadedGraphId() && graph.id === this.loadedGraphId()) return graph;
    this.hasError.set(true);
    this.message.set('Wähle zuerst einen gespeicherten Workflow aus.');
    return null;
  }

  private sameGraphIdentity(candidate: VpGraph): boolean {
    if (candidate.id === this.loadedGraphId()) return true;
    this.hasError.set(true);
    this.message.set('Eine Änderung mit abweichender Graph-ID wurde verworfen.');
    return false;
  }

  private clearScenario(): void {
    this.scenarioId.set('');
    const draft = { ...EMPTY_DRAFT };
    this.draft.set(draft);
    this.savedDraftFingerprint.set(draftFingerprint(draft));
    this.preview.set(null);
    this.selectedId.set(null);
  }

  private cancelGraphLoad(): void {
    this.pendingGraphId = '';
    this.loadingGraph.set(false);
    this.graphLoads.next(null);
  }

  private restoreCanonicalLoadedRoute(): void {
    this.cancelGraphLoad();
    this.requestedGraphId.set(this.loadedGraphId());
    this.syncCanonicalRoute(true, true);
  }

  private acceptSaveError(error: unknown, fallback = 'Workflow konnte nicht gespeichert werden.'): void {
    this.busy.set(false);
    const status = readErrorStatus(error);
    this.saveConflict.set(status === 409);
    this.hasError.set(true);
    this.message.set(status === 409
      ? 'Speicherkonflikt: Der Hub-Graph ist neuer. Der lokale Draft bleibt erhalten.'
      : readErrorMessage(error) || fallback);
  }

  private isCurrentPresetRequest(
    generation: number,
    graphId: string,
    revision: number,
    contextSourceId: string,
    catalogGeneration: number,
  ): boolean {
    return generation === this.presetRequestGeneration
      && this.presetBusy()
      && this.loadedGraphId() === graphId
      && this.requestedGraphId() === graphId
      && !this.loadingGraph()
      && this.graph().id === graphId
      && this.editorState.revision() === revision
      && this.gauntletContextSourceId() === contextSourceId
      && this.catalogRequestGeneration === catalogGeneration;
  }

  private finishPresetRequest(generation: number): void {
    if (generation === this.presetRequestGeneration) this.presetBusy.set(false);
  }

  private acceptCatalogReadModel(readModel: CaseFlowAgentBindingCatalogReadModel): void {
    this.catalogReadModel.set(readModel);
    const selected = this.gauntletContextSourceId();
    const authorized = readModel.availability.context_source.state === 'ready'
      ? readModel.catalog.context_resource_ids.context_source
      : [];
    if (selected && !authorized.includes(selected)) {
      this.gauntletContextSourceId.set('');
    }
  }

  private refreshGraphList(): void {
    this.api.listSavedGraphs().pipe(
      catchError(() => of([] as SavedGraphSummary[])),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(graphs => {
      this.graphs.set(graphs);
      this.loadingGraphs.set(false);
    });
  }

  private syncCanonicalRoute(replaceUrl: boolean, force = false): void {
    const canonical = this.canonicalQueryParams(this.view());
    if (
      !force
      &&
      this.lastRouteSelection.graphId === canonical.graph
      && this.lastRouteSelection.scenarioId === canonical.scenario_id
      && this.lastRouteSelection.view === canonical.view
    ) return;
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: canonical,
      queryParamsHandling: 'merge',
      replaceUrl,
    });
  }

  private canonicalQueryParams(view: CaseFlowStudioView): {
    graph: string | null;
    scenario_id: string | null;
    view: CaseFlowStudioView;
  } {
    return {
      graph: this.loadedGraphId() || this.requestedGraphId() || null,
      scenario_id: this.scenarioId() || null,
      view,
    };
  }
}

function routeSelection(params: ParamMap): StudioRouteSelection {
  const view = params.get('view') === 'process' ? 'process' : 'agents';
  return {
    graphId: params.get('graph')?.trim() || '',
    scenarioId: params.get('scenario_id')?.trim() || '',
    view,
  };
}

function draftFromScenario(scenario: CaseFlowScenarioDefinition): CaseFlowScenarioDraft {
  return {
    id: scenario.id,
    title: scenario.title,
    description: scenario.description,
    icon: scenario.icon,
    caseType: scenario.caseType,
  };
}

function draftFingerprint(draft: Readonly<CaseFlowScenarioDraft>): string {
  return JSON.stringify(draft);
}

function isAcceptedSave(
  acceptance: VpEditorSaveAcceptance,
): acceptance is Extract<
  VpEditorSaveAcceptance,
  { status: 'accepted_clean' | 'accepted_dirty' }
> {
  return acceptance.status === 'accepted_clean' || acceptance.status === 'accepted_dirty';
}

function readErrorStatus(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const status = (error as Record<string, unknown>)['status'];
  return typeof status === 'number' ? status : 0;
}

function readErrorMessage(error: unknown): string {
  if (!error || typeof error !== 'object') return '';
  const payload = (error as Record<string, unknown>)['error'];
  if (!payload || typeof payload !== 'object') return '';
  const message = (payload as Record<string, unknown>)['message'];
  return typeof message === 'string' ? message : '';
}

function readGraphIdentity(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const id = (value as Record<string, unknown>)['id'];
  return typeof id === 'string' && id.length > 0 && id.trim() === id
    ? id
    : null;
}
