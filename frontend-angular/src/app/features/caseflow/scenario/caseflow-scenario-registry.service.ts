import { Injectable, inject, signal } from '@angular/core';
import { Observable, catchError, forkJoin, map, of, switchMap, tap } from 'rxjs';
import {
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_UI_EXTENSION,
  CASEFLOW_UI_SCHEMA,
  CaseFlowBlockDefinition,
  CaseFlowScenarioDefinition,
  CaseFlowScenarioDraft,
  isCaseFlowScenarioDefinition,
} from './caseflow-scenario.models';

const BUILT_IN_SCENARIOS: readonly CaseFlowScenarioDefinition[] = [
  {
    schema: CASEFLOW_UI_SCHEMA,
    id: 'bewerbungen',
    title: 'Bewerbungen',
    description: 'Stellen entdecken, Bewerbungen als Cases steuern und Ergebnisse nachvollziehen.',
    icon: 'work',
    caseType: 'job_application',
    workflowGraphId: 'preset-job-application-intake',
    route: '/caseflow/jobs',
    tags: ['bewerbung', 'recruiting'],
    pages: [],
  },
  {
    schema: CASEFLOW_UI_SCHEMA,
    id: 'classroom',
    title: 'Classroom',
    description: 'Lerninhalte und AI-gestützte Unterrichtsabläufe in einem Anwendungsszenario.',
    icon: 'school',
    caseType: 'classroom',
    workflowGraphId: '',
    route: '/caseflow/classroom',
    tags: ['lernen', 'assistenz'],
    pages: [],
  },
];

@Injectable({ providedIn: 'root' })
export class CaseFlowScenarioRegistryService {
  private readonly visualProcessApi = inject(VisualProcessApiService);
  readonly scenarios = signal<CaseFlowScenarioDefinition[]>([...BUILT_IN_SCENARIOS]);

  listScenarios(): Observable<CaseFlowScenarioDefinition[]> {
    return this.visualProcessApi.listSavedGraphs().pipe(
      switchMap(graphs => {
        const publishedGraphs = graphs.filter(summary => summary.tags?.includes('caseflow'));
        if (!publishedGraphs.length) return of([]);
        return forkJoin(publishedGraphs.map(summary =>
          this.visualProcessApi.loadSavedGraph(summary.id).pipe(catchError(() => of(null))),
        ));
      }),
      map(graphs => {
        const published = graphs
          .map(graph => graph ? this.fromGraph(graph) : null)
          .filter((scenario): scenario is CaseFlowScenarioDefinition => Boolean(scenario));
        const builtInIds = new Set(BUILT_IN_SCENARIOS.map(item => item.id));
        return [
          ...BUILT_IN_SCENARIOS,
          ...published.filter(item => !builtInIds.has(item.id)),
        ];
      }),
      catchError(() => of([...BUILT_IN_SCENARIOS])),
      tap(scenarios => this.scenarios.set(scenarios)),
    );
  }

  getScenario(id: string): Observable<CaseFlowScenarioDefinition | null> {
    return this.listScenarios().pipe(
      map(items => items.find(item => item.id === id) ?? null),
    );
  }

  compileFromGraph(graph: VpGraph, draft?: Partial<CaseFlowScenarioDraft>): CaseFlowScenarioDefinition {
    const id = this.slug(draft?.id || graph.id);
    const outputs = graph.steps.flatMap(step => step.io?.outputs || []);
    const blocks: CaseFlowBlockDefinition[] = [
      {
        id: 'summary',
        kind: 'summary',
        title: 'Übersicht',
        description: graph.description || 'Status und Kontext dieses CaseFlows.',
      },
      { id: 'metrics', kind: 'metrics', title: 'Kennzahlen' },
      { id: 'cases', kind: 'case-list', title: 'Cases' },
      {
        id: 'workflow',
        kind: 'workflow',
        title: 'Ablauf',
        description: 'Der zugrunde liegende, vom Hub ausgeführte Prozess.',
      },
    ];
    if (graph.steps.some(step => step.gate)) {
      blocks.splice(3, 0, { id: 'actions', kind: 'actions', title: 'Freigaben und Aktionen' });
    }
    if (outputs.length) {
      blocks.splice(-1, 0, { id: 'artifacts', kind: 'artifacts', title: 'Ergebnisse' });
    }

    const generated: CaseFlowScenarioDefinition = {
      schema: CASEFLOW_UI_SCHEMA,
      id,
      title: String(draft?.title || graph.name || id),
      description: String(draft?.description || graph.description || 'Workflowbasiertes Anwendungsszenario'),
      icon: String(draft?.icon || 'account_tree'),
      caseType: this.slug(draft?.caseType || id).replace(/-/g, '_'),
      workflowGraphId: graph.id,
      tags: [...new Set([...(graph.tags || []), 'caseflow'])],
      pages: [{ id: 'overview', title: 'Übersicht', blocks }],
    };
    return this.mergeScenarioExtension(
      graph.extensions?.[CASEFLOW_UI_EXTENSION],
      generated,
    );
  }

  publish(graph: VpGraph, scenario: CaseFlowScenarioDefinition) {
    const graphWithScenario = this.withScenario(graph, scenario);
    const savedScenario = this.fromGraph(graphWithScenario)!;
    return this.visualProcessApi.saveGraph(graphWithScenario).pipe(
      tap(result => {
        if (result.id !== graphWithScenario.id) return;
        this.scenarios.update(items => [
          ...items.filter(item => item.id !== savedScenario.id),
          savedScenario,
        ]);
      }),
    );
  }

  /**
   * Adds the manifest immutably. Unknown extension, page and block fields are
   * retained so the Studio remains forward compatible with additive schemas.
   */
  withScenario(graph: VpGraph, scenario: CaseFlowScenarioDefinition): VpGraph {
    const safeScenario = isCaseFlowScenarioDefinition(scenario)
      ? scenario
      : this.compileFromGraph(graph);
    const mergedScenario = this.mergeScenarioExtension(
      graph.extensions?.[CASEFLOW_UI_EXTENSION],
      safeScenario,
    );
    return {
      ...structuredClone(graph),
      extensions: {
        ...structuredClone(graph.extensions || {}),
        [CASEFLOW_UI_EXTENSION]: mergedScenario,
      },
      tags: [...new Set([...(graph.tags || []), 'caseflow'])],
    };
  }

  fromGraph(graph: VpGraph): CaseFlowScenarioDefinition | null {
    const value = graph.extensions?.[CASEFLOW_UI_EXTENSION];
    return isCaseFlowScenarioDefinition(value) ? value : null;
  }

  private slug(value: string): string {
    return value
      .trim()
      .toLocaleLowerCase()
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'caseflow';
  }

  private mergeScenarioExtension(
    existingValue: unknown,
    scenario: CaseFlowScenarioDefinition,
  ): CaseFlowScenarioDefinition {
    const existing = isCaseFlowScenarioDefinition(existingValue)
      ? structuredClone(existingValue) as unknown as Record<string, unknown>
      : {};
    const existingPages = Array.isArray(existing['pages']) ? existing['pages'] : [];
    const generatedPageIds = new Set(scenario.pages.map(page => page.id));
    const pages: unknown[] = scenario.pages.map(page => {
      const existingPage = existingPages.find(candidate =>
        isRecord(candidate) && candidate['id'] === page.id);
      const pageRecord = isRecord(existingPage) ? existingPage : {};
      const existingBlocks = Array.isArray(pageRecord['blocks']) ? pageRecord['blocks'] : [];
      const generatedBlockIds = new Set(page.blocks.map(block => block.id));
      const blocks: unknown[] = page.blocks.map(block => {
        const existingBlock = existingBlocks.find(candidate =>
          isRecord(candidate) && candidate['id'] === block.id);
        return {
          ...(isRecord(existingBlock) ? existingBlock : {}),
          ...structuredClone(block),
        };
      });
      blocks.push(...structuredClone(existingBlocks.filter(candidate =>
        !isRecord(candidate)
        || typeof candidate['id'] !== 'string'
        || !generatedBlockIds.has(candidate['id']))));
      return { ...pageRecord, ...structuredClone(page), blocks };
    });
    pages.push(...structuredClone(existingPages.filter(candidate =>
      !isRecord(candidate)
      || typeof candidate['id'] !== 'string'
      || !generatedPageIds.has(candidate['id']))));

    const candidate = {
      ...existing,
      ...structuredClone(scenario),
      pages,
    };
    return isCaseFlowScenarioDefinition(candidate)
      ? candidate
      : structuredClone(scenario);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
