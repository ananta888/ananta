import { Injectable, inject } from '@angular/core';
import { Observable, catchError, forkJoin, map, of, switchMap } from 'rxjs';
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

    return {
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
  }

  publish(graph: VpGraph, scenario: CaseFlowScenarioDefinition) {
    const extensions = { ...(graph.extensions || {}), [CASEFLOW_UI_EXTENSION]: scenario };
    const tags = [...new Set([...(graph.tags || []), 'caseflow'])];
    return this.visualProcessApi.saveGraph({ ...graph, extensions, tags });
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
}
