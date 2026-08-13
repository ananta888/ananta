import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_UI_EXTENSION,
  CASEFLOW_UI_SCHEMA,
  isCaseFlowScenarioDefinition,
} from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';

function graph(): VpGraph {
  return {
    id: 'customer-onboarding',
    name: 'Customer Onboarding',
    description: 'Neue Kunden sicher aufnehmen.',
    version: '1',
    tags: ['crm'],
    steps: [
      {
        id: 'review',
        label: 'Prüfen',
        kind: 'task',
        io: {
          inputs: [],
          outputs: [{ name: 'report', kind: 'report', required: true }],
        },
        position: { x: 0, y: 0 },
        policy_hints: [],
        gate: true,
      },
    ],
    edges: [],
  };
}

describe('CaseFlowScenarioRegistryService', () => {
  const saveGraph = vi.fn(() => of({
    id: 'customer-onboarding',
    version: '1',
    definition_revision: 1,
    base_graph_hash: 'hash',
    saved: true,
  }));
  let registry: CaseFlowScenarioRegistryService;

  beforeEach(() => {
    saveGraph.mockClear();
    TestBed.configureTestingModule({
      providers: [
        CaseFlowScenarioRegistryService,
        {
          provide: VisualProcessApiService,
          useValue: {
            saveGraph,
            listSavedGraphs: () => of([]),
            loadSavedGraph: () => of(graph()),
          },
        },
      ],
    });
    registry = TestBed.inject(CaseFlowScenarioRegistryService);
  });

  it('compiles a workflow deterministically into allowlisted generic blocks', () => {
    const scenario = registry.compileFromGraph(graph());

    expect(scenario).toMatchObject({
      schema: CASEFLOW_UI_SCHEMA,
      id: 'customer-onboarding',
      title: 'Customer Onboarding',
      caseType: 'customer_onboarding',
      workflowGraphId: 'customer-onboarding',
    });
    expect(scenario.pages[0].blocks.map(block => block.kind)).toEqual([
      'summary',
      'metrics',
      'case-list',
      'actions',
      'artifacts',
      'workflow',
    ]);
    expect(isCaseFlowScenarioDefinition(scenario)).toBe(true);
  });

  it('publishes the manifest inside the graph extension without discarding existing extensions', () => {
    const source = { ...graph(), extensions: { 'vendor.feature': { enabled: true } } };
    const scenario = registry.compileFromGraph(source);

    registry.publish(source, scenario).subscribe();

    expect(saveGraph).toHaveBeenCalledOnce();
    const saved = saveGraph.mock.calls[0][0] as VpGraph;
    expect(saved.extensions?.['vendor.feature']).toEqual({ enabled: true });
    expect(saved.extensions?.[CASEFLOW_UI_EXTENSION]).toEqual(scenario);
    expect(saved.tags).toEqual(['crm', 'caseflow']);
    expect(registry.scenarios().at(-1)).toEqual(scenario);
  });

  it('does not register a scenario from a save response with a different graph identity', () => {
    saveGraph.mockReturnValueOnce(of({
      id: 'different-graph', version: '1', definition_revision: 1,
      base_graph_hash: 'hash', saved: true,
    }));
    const source = graph();
    const scenario = registry.compileFromGraph(source);

    registry.publish(source, scenario).subscribe();

    expect(registry.scenarios().some(item => item.id === scenario.id)).toBe(false);
  });

  it('preserves additive scenario fields immutably while replacing canonical draft values', () => {
    const source = graph();
    const existing = {
      ...registry.compileFromGraph(source),
      future_root: { mode: 'v2' },
      pages: [{
        id: 'overview', title: 'Alt', future_page: 17,
        blocks: [
          { id: 'summary', kind: 'summary', title: 'Alt', future_block: true },
          { id: 'custom-metrics', kind: 'metrics', title: 'Zusätzlich', untouched: 'block' },
        ],
      }, {
        id: 'details', title: 'Details', untouched: 'page',
        blocks: [{ id: 'details-summary', kind: 'summary', title: 'Details' }],
      }],
    };
    const graphWithExtension = {
      ...source,
      extensions: { [CASEFLOW_UI_EXTENSION]: existing },
    };
    const before = structuredClone(graphWithExtension);

    const scenario = registry.compileFromGraph(graphWithExtension, { title: 'Neu' });
    const published = registry.withScenario(graphWithExtension, scenario);
    const extension = published.extensions?.[CASEFLOW_UI_EXTENSION] as Record<string, unknown>;
    const page = (extension['pages'] as Record<string, unknown>[])[0];
    const block = (page['blocks'] as Record<string, unknown>[])[0];

    expect(extension['future_root']).toEqual({ mode: 'v2' });
    expect(page['future_page']).toBe(17);
    expect(block['future_block']).toBe(true);
    expect((page['blocks'] as Record<string, unknown>[])[6]).toMatchObject({
      id: 'custom-metrics', untouched: 'block',
    });
    expect((extension['pages'] as Record<string, unknown>[])[1]).toMatchObject({
      id: 'details', untouched: 'page',
    });
    expect(extension['title']).toBe('Neu');
    expect(graphWithExtension).toEqual(before);
    expect(published).not.toBe(graphWithExtension);
    expect(registry.withScenario(published, scenario)).toEqual(published);
  });

  it('rejects component kinds outside the renderer allowlist', () => {
    const scenario = registry.compileFromGraph(graph());
    const unsafe = {
      ...scenario,
      pages: [{ ...scenario.pages[0], blocks: [{ id: 'raw', title: 'Raw', kind: 'angular-component' }] }],
    };

    expect(isCaseFlowScenarioDefinition(unsafe)).toBe(false);
  });

  it('sanitizes a malformed stored extension before compiling or saving', () => {
    const source = {
      ...graph(),
      extensions: {
        [CASEFLOW_UI_EXTENSION]: {
          schema: CASEFLOW_UI_SCHEMA,
          id: 'broken', title: 'Broken', description: '', icon: 'warning',
          caseType: 'broken', workflowGraphId: 'wrong', tags: [],
          pages: [{
            id: 'overview', title: 'Broken',
            blocks: [7, { id: 'unsafe', title: 'Unsafe', kind: 'angular-component' }],
          }],
        },
      },
    } as unknown as VpGraph;

    const scenario = registry.compileFromGraph(source, { title: 'Safe' });
    expect(isCaseFlowScenarioDefinition(scenario)).toBe(true);
    expect(scenario.title).toBe('Safe');
    expect(scenario.pages[0].blocks.every(block => block.kind !== 'angular-component')).toBe(true);

    expect(() => registry.publish(source, scenario).subscribe()).not.toThrow();
    const saved = saveGraph.mock.calls.at(-1)?.[0] as VpGraph;
    expect(isCaseFlowScenarioDefinition(saved.extensions?.[CASEFLOW_UI_EXTENSION])).toBe(true);
  });
});
