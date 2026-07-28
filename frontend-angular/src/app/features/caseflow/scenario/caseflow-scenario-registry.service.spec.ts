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

  it('rejects component kinds outside the renderer allowlist', () => {
    const scenario = registry.compileFromGraph(graph());
    const unsafe = {
      ...scenario,
      pages: [{ ...scenario.pages[0], blocks: [{ id: 'raw', title: 'Raw', kind: 'angular-component' }] }],
    };

    expect(isCaseFlowScenarioDefinition(unsafe)).toBe(false);
  });
});
