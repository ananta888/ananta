import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import type {
  VpEdge,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import type {
  CaseFlowAgentBindingCatalogReadModel,
  CaseFlowBindingResourceKind,
} from './caseflow-agent-binding-catalog.service';
import {
  CASEFLOW_AGENT_BINDINGS_METADATA,
  CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
  CASEFLOW_AGENT_CANVAS_EXTENSION,
  CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
} from './caseflow-agent-canvas.models';
import { CaseFlowAgentNodeInspectorComponent } from './caseflow-agent-node-inspector.component';

const INSTRUCTION_A = '5d5ef0c1-6248-46f4-8733-97edcd6bf1c4';
const INSTRUCTION_B = 'bbf4b0cd-bcff-49d7-98dc-ebf5d34b0ee6';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('CaseFlowAgentNodeInspectorComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CaseFlowAgentNodeInspectorComponent],
    }).compileComponents();
  });

  it('edits role, icon and every authorized binding through one focused immutable graph emission', () => {
    const fixture = createFixture(agentGraph(), readyCatalog());
    const original = fixture.componentInstance.graph;
    const changes: VpGraph[] = [];
    fixture.componentInstance.graphChange.subscribe(graph => changes.push(graph));

    changeSelect(fixture, 'role', 'reviewer');
    changeSelect(fixture, 'icon', 'rule');
    changeSelect(fixture, 'skill-profile', 'skill-review');
    changeSelect(fixture, 'personality-type', 'instruction_layer');
    changeSelect(fixture, 'personality-id', INSTRUCTION_B);

    const oldContextRemove = fixture.nativeElement.querySelector(
      '[data-binding-id="source-a"] button',
    ) as HTMLButtonElement;
    oldContextRemove.click();
    fixture.detectChanges();
    changeSelect(fixture, 'context-type', 'context_source');
    changeSelect(fixture, 'context-id', 'source-b');
    const addContext = fixture.nativeElement.querySelector(
      '[data-action="add-context"]',
    ) as HTMLButtonElement;
    addContext.click();
    fixture.detectChanges();

    changeSelect(fixture, 'model-role', 'review');
    changeSelect(fixture, 'model-profile', 'model-review');
    changeSelect(fixture, 'fallback-group', 'safe-fallback');
    const save = fixture.nativeElement.querySelector('[data-action="save"]') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    save.click();

    expect(changes).toHaveLength(1);
    const updated = changes[0];
    expect(updated).not.toBe(original);
    expect(updated.steps).not.toBe(original.steps);
    expect(updated.edges).toBe(original.edges);
    expect(updated.metadata).toBe(original.metadata);
    expect(updated.steps[1]).toBe(original.steps[1]);

    const selected = updated.steps[0];
    expect(selected.role).toBe('reviewer');
    expect(selected.agent_skill_profile_id).toBe('skill-review');
    expect(selected.metadata?.['future-step-metadata']).toEqual({ exact: true });
    expect(selected.metadata?.['model_routing']).toEqual({
      future_routing_field: { preserved: true },
      model_role: 'review',
      preferred_profile_id: 'model-review',
      fallback_group_id: 'safe-fallback',
    });
    expect(selected.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA]).toEqual({
      schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
      future_binding_field: { preserved: true },
      personality_binding: {
        resource_type: 'instruction_layer',
        resource_id: INSTRUCTION_B,
      },
      context_bindings: [{ resource_type: 'context_source', resource_id: 'source-b' }],
    });
    expect((updated.extensions?.[CASEFLOW_AGENT_CANVAS_EXTENSION] as any).nodes.selected)
      .toEqual({ icon: 'rule', future_presentation: { preserved: true } });
    expect(updated.extensions?.['vendor.extension']).toBe(original.extensions?.['vendor.extension']);
  });

  it('shows both directions of bidirectional edges separately and preserves canonical loop IDs', () => {
    const fixture = createFixture(agentGraph(), readyCatalog());
    const parentRows = Array.from(
      fixture.nativeElement.querySelectorAll('[data-relations="parents"] li'),
    ) as HTMLElement[];
    const childRows = Array.from(
      fixture.nativeElement.querySelectorAll('[data-relations="children"] li'),
    ) as HTMLElement[];
    const loopRows = Array.from(
      fixture.nativeElement.querySelectorAll('[data-relations="loops"] li'),
    ) as HTMLElement[];

    expect(parentRows.map(row => row.dataset['edgeId'])).toEqual(['peer-selected']);
    expect(childRows.map(row => row.dataset['edgeId'])).toEqual(['selected-peer']);
    expect(loopRows.map(row => row.dataset['edgeId'])).toEqual(['selected-loop']);
    expect(parentRows[0].textContent).toContain('Peer');
    expect(parentRows[0].textContent).toContain('developer');
    expect(childRows[0].textContent).toContain('selected-peer');
    expect(loopRows[0].textContent).toContain('retry');
  });

  it('keeps native Advanced details initially closed and never changes graph data when toggled', () => {
    const fixture = createFixture(agentGraph(), readyCatalog());
    const graphBefore = JSON.stringify(fixture.componentInstance.graph);
    const changes: VpGraph[] = [];
    fixture.componentInstance.graphChange.subscribe(graph => changes.push(graph));
    const advanced = fixture.nativeElement.querySelector(
      '[data-section="advanced"]',
    ) as HTMLDetailsElement;
    const summary = advanced.querySelector('summary') as HTMLElement;

    expect(advanced.open).toBe(false);
    expect(summary.tabIndex).toBeGreaterThanOrEqual(0);
    advanced.open = true;
    advanced.dispatchEvent(new Event('toggle', { bubbles: true }));
    fixture.detectChanges();
    advanced.open = false;
    advanced.dispatchEvent(new Event('toggle', { bubbles: true }));
    fixture.detectChanges();

    expect(changes).toEqual([]);
    expect(JSON.stringify(fixture.componentInstance.graph)).toBe(graphBefore);
    expect(advanced.textContent).toContain('selected');
    expect(advanced.textContent).toContain('coding');
    expect(advanced.textContent).toContain('Aktiv');
  });

  it('requires an accessible custom role name before the focused command can run', () => {
    const fixture = createFixture(agentGraph(), readyCatalog());
    changeSelect(fixture, 'role', 'custom');
    const customRole = fixture.nativeElement.querySelector(
      '[data-field="custom-role"]',
    ) as HTMLInputElement;
    const save = fixture.nativeElement.querySelector('[data-action="save"]') as HTMLButtonElement;

    expect(customRole.labels?.[0]?.textContent).toContain('Eigener Rollenname');
    expect(save.disabled).toBe(true);
    expect(fixture.nativeElement.textContent).toContain(
      'A custom role requires a non-empty name.',
    );

    customRole.value = 'Evidence Curator';
    customRole.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
    expect(save.disabled).toBe(false);
  });

  it('fails closed for degraded and disabled resources, exposes text status and blocks stale IDs', () => {
    const baseCatalog = readyCatalog({
      skill_profile: { state: 'degraded', reason_code: 'catalog_request_forbidden' },
      instruction_layer: { state: 'degraded', reason_code: 'catalog_request_unauthorized' },
      context_source: { state: 'disabled', reason_code: 'catalog_not_configured' },
      model_profile: { state: 'degraded', reason_code: 'catalog_response_invalid' },
      model_role: { state: 'degraded', reason_code: 'catalog_response_invalid' },
      fallback_group: { state: 'degraded', reason_code: 'catalog_response_invalid' },
    });
    const catalog: CaseFlowAgentBindingCatalogReadModel = {
      ...baseCatalog,
      catalog: {
        ...baseCatalog.catalog,
        model_profile_ids: ['invented-profile'],
      },
    };
    const fixture = createFixture(agentGraph(), catalog);
    const changes: VpGraph[] = [];
    fixture.componentInstance.graphChange.subscribe(graph => changes.push(graph));

    const skill = fixture.nativeElement.querySelector('[data-field="skill-profile"]') as HTMLSelectElement;
    const save = fixture.nativeElement.querySelector('[data-action="save"]') as HTMLButtonElement;
    const statusText = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(skill.disabled).toBe(true);
    expect(save.disabled).toBe(true);
    expect(statusText).toContain('Katalog konnte nicht sicher geladen werden');
    expect(statusText).toContain('Katalog ist deaktiviert');
    expect(statusText).toContain('skill-main reference "skill-build" is not in the current Hub catalog'.replace('skill-main', 'skill profile'));
    expect(statusText).not.toContain('invented-profile');

    fixture.componentInstance.save();
    expect(changes).toEqual([]);
  });

  it('keeps save disabled until an authorized catalog exists and reports the missing trust boundary', () => {
    const fixture = createFixture(agentGraph(), null);
    const save = fixture.nativeElement.querySelector('[data-action="save"]') as HTMLButtonElement;

    expect(save.disabled).toBe(true);
    expect(fixture.nativeElement.textContent).toContain(
      'Der autorisierte Hub-Katalog ist noch nicht verfügbar.',
    );
    expect(fixture.nativeElement.textContent).not.toContain('SRC_');
    expect(fixture.nativeElement.textContent).not.toContain('RUN_');
  });
});

function createFixture(
  graph: VpGraph,
  catalog: CaseFlowAgentBindingCatalogReadModel | null,
): ComponentFixture<CaseFlowAgentNodeInspectorComponent> {
  const fixture = TestBed.createComponent(CaseFlowAgentNodeInspectorComponent);
  fixture.componentRef.setInput('graph', graph);
  fixture.componentRef.setInput('selectedStepId', 'selected');
  fixture.componentRef.setInput('catalogReadModel', catalog);
  fixture.detectChanges();
  return fixture;
}

function changeSelect(
  fixture: ComponentFixture<CaseFlowAgentNodeInspectorComponent>,
  field: string,
  value: string,
): void {
  const select = fixture.nativeElement.querySelector(
    `[data-field="${field}"]`,
  ) as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
  fixture.detectChanges();
}

function readyCatalog(
  availabilityOverrides: Partial<CaseFlowAgentBindingCatalogReadModel['availability']> = {},
  emptyCatalog = false,
): CaseFlowAgentBindingCatalogReadModel {
  const ready = { state: 'ready', reason_code: 'catalog_loaded' } as const;
  const disabled = { state: 'disabled', reason_code: 'catalog_not_implemented' } as const;
  const availability: Record<CaseFlowBindingResourceKind, any> = {
    skill_profile: ready,
    agent_profile: disabled,
    instruction_layer: ready,
    context_profile: disabled,
    context_source: ready,
    model_profile: ready,
    model_role: ready,
    fallback_group: ready,
    ...availabilityOverrides,
  };
  return {
    state: Object.values(availability).some(value => value.state === 'degraded')
      ? 'degraded'
      : 'ready',
    availability,
    catalog: {
      skill_profile_ids: emptyCatalog ? [] : ['skill-build', 'skill-review'],
      personality_resource_ids: {
        agent_profile: [],
        instruction_layer: emptyCatalog ? [] : [INSTRUCTION_A, INSTRUCTION_B],
      },
      context_resource_ids: {
        context_profile: [],
        context_source: emptyCatalog ? [] : ['source-a', 'source-b'],
      },
      model_profile_ids: emptyCatalog ? [] : ['model-build', 'model-review'],
      model_role_ids: emptyCatalog ? [] : ['code', 'review'],
      fallback_group_ids: emptyCatalog ? [] : ['default-fallback', 'safe-fallback'],
    },
  };
}

function agentGraph(): VpGraph {
  return {
    id: 'agent-inspector',
    name: 'Agent Inspector',
    description: 'Inspector fixture',
    version: '1',
    tags: ['fixture'],
    metadata: { exact: 'preserved' },
    steps: [
      step('selected', 'Selected', 'developer', {
        agent_skill_profile_id: 'skill-build',
        gate: true,
        policy_hints: ['read_only'],
        metadata: {
          'future-step-metadata': { exact: true },
          [CASEFLOW_AGENT_BINDINGS_METADATA]: {
            schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
            future_binding_field: { preserved: true },
            personality_binding: {
              resource_type: 'instruction_layer',
              resource_id: INSTRUCTION_A,
            },
            context_bindings: [{ resource_type: 'context_source', resource_id: 'source-a' }],
          },
          model_routing: {
            future_routing_field: { preserved: true },
            model_role: 'code',
            preferred_profile_id: 'model-build',
            fallback_group_id: 'default-fallback',
          },
        },
      }),
      step('peer', 'Peer', 'developer'),
    ],
    edges: [
      edge('selected-peer', 'selected', 'peer'),
      edge('peer-selected', 'peer', 'selected'),
      edge('selected-loop', 'selected', 'selected', 'retry'),
    ],
    extensions: {
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: {
        schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
        nodes: {
          selected: { icon: 'code', future_presentation: { preserved: true } },
        },
      },
      'vendor.extension': { exact: true },
    },
  };
}

function step(
  id: string,
  label: string,
  role: string,
  overrides: Partial<VpStep> = {},
): VpStep {
  return {
    id,
    label,
    role,
    kind: 'coding',
    io: { inputs: [], outputs: [] },
    position: { x: 20, y: 30 },
    policy_hints: [],
    gate: false,
    ...overrides,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  label?: string,
): VpEdge {
  return {
    id,
    source,
    target,
    condition: { kind: source === target ? 'back_edge' : 'always' },
    ...(label ? { label } : {}),
  };
}
