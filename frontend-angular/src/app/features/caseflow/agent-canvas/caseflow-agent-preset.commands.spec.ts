import { describe, expect, it } from 'vitest';

import type {
  VpEdge,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_AGENT_BINDINGS_METADATA,
  CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
} from './caseflow-agent-canvas.models';
import type { CaseFlowAgentBindingCatalog } from './caseflow-agent-graph.commands';
import {
  BUILDER_CRITIC_GAUNTLET_EDGE_IDS,
  BUILDER_CRITIC_GAUNTLET_STEP_IDS,
  applyBuilderCriticGauntletPreset,
} from './caseflow-agent-preset.commands';

const AUTHORIZED_CONTEXT_FROM_FIXTURE_CATALOG = 'fixture-benchmark-evidence';
const AUTHORIZED_PERSONALITY_FROM_FIXTURE_CATALOG =
  'fixture-critic-instruction';

describe('Builder/Critic Gauntlet selective preset command', () => {
  it('adds normal catalog-role steps and edges with authorized Critic bindings', () => {
    const graph = targetGraph();
    const preset = gauntletPreset();
    const existingStep = graph.steps[0];
    const existingEdge = graph.edges[0];
    const existingExtensions = graph.extensions;
    const existingVendorExtension = graph.extensions?.['vendor.future'];
    const presetLead = preset.steps[0];
    const presetFeedback = preset.edges[3];
    const presetLeadPresentation = (
      preset.extensions?.['ananta.caseflow.agent-canvas'] as Record<string, any>
    )['nodes']['gauntlet-lead'];
    const presetBefore = JSON.stringify(preset);

    const result = applyBuilderCriticGauntletPreset(graph, preset, {
      selected_step_ids: BUILDER_CRITIC_GAUNTLET_STEP_IDS,
      selected_edge_ids: BUILDER_CRITIC_GAUNTLET_EDGE_IDS,
      critic_benchmark_context_binding: {
        resource_type: 'context_source',
        resource_id: AUTHORIZED_CONTEXT_FROM_FIXTURE_CATALOG,
      },
      critic_personality_binding: {
        resource_type: 'instruction_layer',
        resource_id: AUTHORIZED_PERSONALITY_FROM_FIXTURE_CATALOG,
      },
    }, catalog());
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    expect(result.value.steps.map(step => [step.id, step.role])).toEqual([
      ['existing-a', 'observer'],
      ['existing-b', 'observer'],
      ['gauntlet-lead', 'lead'],
      ['gauntlet-builder', 'developer'],
      ['gauntlet-critic', 'critic'],
    ]);
    expect(result.value.edges.slice(1).map(edge => [
      edge.id,
      edge.source,
      edge.target,
      edge.condition.kind,
    ])).toEqual([
      ['gauntlet-lead-builder', 'gauntlet-lead', 'gauntlet-builder', 'always'],
      ['gauntlet-lead-critic', 'gauntlet-lead', 'gauntlet-critic', 'always'],
      ['gauntlet-builder-critic', 'gauntlet-builder', 'gauntlet-critic', 'on_success'],
      [
        'gauntlet-critic-builder-feedback',
        'gauntlet-critic',
        'gauntlet-builder',
        'back_edge',
      ],
    ]);

    const critic = result.value.steps.find(step => step.id === 'gauntlet-critic');
    expect(critic?.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA]).toEqual({
      schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
      context_bindings: [{
        resource_type: 'context_source',
        resource_id: AUTHORIZED_CONTEXT_FROM_FIXTURE_CATALOG,
      }],
      personality_binding: {
        resource_type: 'instruction_layer',
        resource_id: AUTHORIZED_PERSONALITY_FROM_FIXTURE_CATALOG,
      },
    });
    expect(result.value.steps[0]).toBe(existingStep);
    expect(result.value.edges[0]).toBe(existingEdge);
    expect(result.value.extensions).not.toBe(existingExtensions);
    expect(result.value.extensions?.['vendor.future']).toBe(existingVendorExtension);
    expect((result.value.extensions?.['ananta.caseflow.agent-canvas'] as any)
      ['nodes']).toEqual({
      'gauntlet-lead': { icon: 'star', future_node_field: { id: 'lead' } },
      'gauntlet-builder': { icon: 'code', future_node_field: { id: 'builder' } },
      'gauntlet-critic': { icon: 'rule', future_node_field: { id: 'critic' } },
    });
    expect(result.value.steps[2]).not.toBe(presetLead);
    expect(result.value.edges[4]).not.toBe(presetFeedback);
    expect((result.value.extensions?.['ananta.caseflow.agent-canvas'] as any)
      ['nodes']['gauntlet-lead']).not.toBe(presetLeadPresentation);
    expect(JSON.stringify(preset)).toBe(presetBefore);

    result.value.steps[2].metadata!['future_step_field'] = { mutated: true };
    result.value.edges[4].condition.loop_policy!.max_iterations = 1;
    (result.value.extensions?.['ananta.caseflow.agent-canvas'] as any)
      ['nodes']['gauntlet-lead']['future_node_field']['id'] = 'mutated';
    expect(JSON.stringify(preset)).toBe(presetBefore);
  });

  it('fails closed when a selected Critic has no benchmark context binding', () => {
    const graph = targetGraph();
    const before = JSON.stringify(graph);

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-critic'],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_binding_required');
    expect(JSON.stringify(graph)).toBe(before);
  });

  it('fails closed when the supplied context is absent from the Hub catalog', () => {
    const graph = targetGraph();

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-critic'],
      selected_edge_ids: [],
      critic_benchmark_context_binding: {
        resource_type: 'context_source',
        resource_id: 'not-returned-by-fixture-catalog',
      },
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_binding_reference_not_allowed');
    expect(result.issues[0]?.path).toContain('context_bindings/0/resource_id');
  });

  it('rejects every preconfigured preset reference absent from the Hub catalog', () => {
    const preset = gauntletPreset();
    const builder = preset.steps.find(step => step.id === 'gauntlet-builder');
    if (!builder) throw new Error('Builder fixture is missing.');
    builder.agent_skill_profile_id = 'preset-must-not-authorize-itself';

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: ['gauntlet-builder'],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_binding_reference_not_allowed');
    expect(result.issues[0]?.path).toContain('agent_skill_profile_id');
  });

  it('adds a selected closed subset without adding the Critic or requiring bindings', () => {
    const graph = targetGraph();
    const existingSteps = [...graph.steps];
    const existingEdges = [...graph.edges];

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-lead', 'gauntlet-builder'],
      selected_edge_ids: ['gauntlet-lead-builder'],
    }, catalog());
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    expect(result.value.steps.map(step => step.id)).toEqual([
      'existing-a',
      'existing-b',
      'gauntlet-lead',
      'gauntlet-builder',
    ]);
    expect(result.value.edges.map(edge => edge.id)).toEqual([
      'existing-edge',
      'gauntlet-lead-builder',
    ]);
    expect(result.value.steps[0]).toBe(existingSteps[0]);
    expect(result.value.steps[1]).toBe(existingSteps[1]);
    expect(result.value.edges[0]).toBe(existingEdges[0]);
  });

  it('preserves existing canvas extension values and node presentations by identity', () => {
    const graph = targetGraph();
    const existingPresentation = {
      icon: 'visibility', future_node_field: { exact: ['keep', 8] },
    };
    const futureContract = { sequence: [2, 3, 5] };
    graph.extensions!['ananta.caseflow.agent-canvas'] = {
      schema: 'ananta.caseflow.agent-canvas/v1',
      future_contract: futureContract,
      nodes: { 'existing-a': existingPresentation },
    };

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-lead'],
      selected_edge_ids: [],
    }, catalog());
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    const canvas = result.value.extensions?.['ananta.caseflow.agent-canvas'] as any;
    expect(canvas['future_contract']).toBe(futureContract);
    expect(canvas['nodes']['existing-a']).toBe(existingPresentation);
    expect(canvas['nodes']['gauntlet-lead']).toEqual({
      icon: 'star', future_node_field: { id: 'lead' },
    });
  });

  it('does not overwrite orphaned target presentation data with the same selected ID', () => {
    const graph = targetGraph();
    const orphanedPresentation = { icon: 'visibility', retained: true };
    graph.extensions!['ananta.caseflow.agent-canvas'] = {
      schema: 'ananta.caseflow.agent-canvas/v1',
      nodes: { 'gauntlet-lead': orphanedPresentation },
    };

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-lead'],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_id_conflict');
    expect((graph.extensions?.['ananta.caseflow.agent-canvas'] as any)
      ['nodes']['gauntlet-lead']).toBe(orphanedPresentation);
  });

  it('rejects an edge unless both of its standard step endpoints are selected', () => {
    const result = applyBuilderCriticGauntletPreset(
      targetGraph(),
      gauntletPreset(),
      {
        selected_step_ids: ['gauntlet-lead'],
        selected_edge_ids: ['gauntlet-lead-builder'],
      },
      catalog(),
    );

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_selection_invalid');
  });

  it('rejects ID collisions instead of overwriting an existing graph object', () => {
    const graph = targetGraph();
    graph.steps.push({ ...step('gauntlet-lead', 'Existing Lead', 'observer') });
    const conflictingStep = graph.steps.at(-1);

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: ['gauntlet-lead'],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_id_conflict');
    expect(graph.steps.at(-1)).toBe(conflictingStep);
  });

  it('rejects a preset that removes the read-only binding requirement', () => {
    const preset = gauntletPreset();
    preset.metadata = { future_metadata: { preserved: true } };

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
  });

  it.each([
    ['duplicates the required slot', (slots: Record<string, unknown>[]) => [
      slots[0], { ...slots[0] },
    ]],
    ['injects a concrete resource into the logical slot', (
      slots: Record<string, unknown>[],
    ) => [{
      ...slots[0],
      resource_id: 'preset-must-not-select-a-context',
    }]],
    ['adds an unknown required slot', (slots: Record<string, unknown>[]) => [
      ...slots,
      {
        slot: 'runtime_catalog_bypass',
        step_id: 'gauntlet-builder',
        resource_type: 'skill_profile',
        required: true,
        access: 'execute',
      },
    ]],
    ['replaces the known slot with an unknown slot', () => [{
      slot: 'unknown_context',
      step_id: 'gauntlet-critic',
      resource_type: 'context_source',
      required: true,
      access: 'read_only',
    }]],
  ])('rejects binding slot drift that %s', (_label, mutateSlots) => {
    const preset = gauntletPreset();
    const marker = preset.metadata?.['ananta.caseflow.agent-preset'] as
      Record<string, unknown>;
    const slots = marker['binding_slots'] as Record<string, unknown>[];
    marker['binding_slots'] = mutateSlots(slots);

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]).toMatchObject({
      code: 'agent_preset_invalid',
      path: '/preset/metadata/ananta.caseflow.agent-preset',
    });
  });

  it('rejects a non-standard runtime-like edge kind', () => {
    const preset = gauntletPreset();
    preset.edges[3] = {
      ...preset.edges[3],
      condition: { kind: 'critic_runtime_feedback' },
    };

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
  });

  it.each([0, 4, 1.5, Number.POSITIVE_INFINITY])(
    'rejects an unbounded or altered feedback max_iterations value %s',
    maxIterations => {
      const preset = gauntletPreset();
      preset.edges[3].condition.loop_policy!.max_iterations = maxIterations;

      const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
        selected_step_ids: [],
        selected_edge_ids: [],
      }, catalog());

      expect(result.ok).toBe(false);
      expect(result.issues[0]?.code).toBe('agent_preset_invalid');
      expect(result.issues[0]?.path).toContain('loop_policy');
    },
  );

  it('rejects extra preset steps and edges injected through a runtime cast', () => {
    const preset = gauntletPreset();
    preset.steps.push(step('runtime-agent', 'Runtime agent', 'critic'));
    preset.edges.push(edge(
      'runtime-edge',
      'gauntlet-lead',
      'runtime-agent',
      'runtime_dispatch',
    ));

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [] as any,
      selected_edge_ids: [] as any,
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
  });

  it.each([
    ['null preset', null, '/preset'],
    ['non-array steps', { ...gauntletPreset(), steps: null }, '/preset/steps'],
    ['non-array edges', { ...gauntletPreset(), edges: {} }, '/preset/edges'],
  ])('fails closed without throwing for a %s', (_label, malformed, path) => {
    expect(() => applyMalformedPreset(malformed)).not.toThrow();

    const result = applyMalformedPreset(malformed);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.path).toBe(path);
  });

  it.each([
    ['null step', null],
    ['missing step I/O', { ...step('gauntlet-lead', 'Lead', 'lead'), io: undefined }],
    ['non-array inputs', {
      ...step('gauntlet-lead', 'Lead', 'lead'),
      io: { inputs: null, outputs: [] },
    }],
    ['malformed position', {
      ...step('gauntlet-lead', 'Lead', 'lead'),
      position: { x: Number.NaN, y: 0 },
    }],
    ['malformed policy hints', {
      ...step('gauntlet-lead', 'Lead', 'lead'),
      policy_hints: 'read_only',
    }],
  ])('rejects a malformed preset step shape: %s', (_label, malformedStep) => {
    const preset = gauntletPreset();
    preset.steps[0] = malformedStep as VpStep;

    const result = applyMalformedPreset(preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.path).toBe('/preset/steps/0');
  });

  it.each([
    ['null edge', null, '/preset/edges/0'],
    ['missing condition', {
      ...edge('gauntlet-lead-builder', 'gauntlet-lead', 'gauntlet-builder'),
      condition: undefined,
    }, '/preset/edges/0'],
    ['malformed loop policy', {
      ...edge(
        'gauntlet-critic-builder-feedback',
        'gauntlet-critic',
        'gauntlet-builder',
        'back_edge',
      ),
      condition: { kind: 'back_edge', loop_policy: { kind: 'fixed' } },
    }, '/preset/edges/0/condition/loop_policy'],
  ])('rejects a malformed preset edge shape: %s', (_label, malformedEdge, path) => {
    const preset = gauntletPreset();
    preset.edges[0] = malformedEdge as VpEdge;

    const result = applyMalformedPreset(preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.path).toBe(path);
  });

  it.each([
    ['step run state', (preset: VpGraph) => {
      preset.steps[0].run_state = 'running';
    }, '/preset/steps/0/run_state'],
    ['graph runtime overlay', (preset: VpGraph) => {
      (preset as unknown as Record<string, unknown>)['runtime_overlay'] = {
        current_step_ids: ['gauntlet-lead'],
      };
    }, '/preset/runtime_overlay'],
    ['nested execution result', (preset: VpGraph) => {
      preset.steps[1].metadata!['execution_result'] = { status: 'succeeded' };
    }, '/preset/steps/1/metadata/execution_result'],
    ['nested telemetry', (preset: VpGraph) => {
      preset.edges[0] = {
        ...preset.edges[0],
        telemetry: { latency_ms: 4 },
      } as VpEdge;
    }, '/preset/edges/0/telemetry'],
  ])('rejects non-definition runtime payloads: %s', (_label, mutate, path) => {
    const preset = gauntletPreset();
    mutate(preset);

    const result = applyMalformedPreset(preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.path).toBe(path);
  });

  it('accepts null legacy run_state while rejecting only concrete runtime truth', () => {
    const preset = gauntletPreset();
    preset.steps.forEach(stepValue => { stepValue.run_state = null as unknown as string; });

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(true);
  });

  it.each([
    ['null selection', null, '/selection'],
    ['non-array step IDs', {
      selected_step_ids: null,
      selected_edge_ids: [],
    }, '/selection/selected_step_ids'],
    ['non-array edge IDs', {
      selected_step_ids: [],
      selected_edge_ids: 'gauntlet-lead-builder',
    }, '/selection/selected_edge_ids'],
    ['unknown step ID', {
      selected_step_ids: ['runtime-agent'],
      selected_edge_ids: [],
    }, '/selection/selected_step_ids/0'],
    ['non-string edge ID', {
      selected_step_ids: [],
      selected_edge_ids: [42],
    }, '/selection/selected_edge_ids/0'],
  ])('fails closed without throwing for a %s', (_label, selection, path) => {
    expect(() => applyMalformedSelection(selection)).not.toThrow();

    const result = applyMalformedSelection(selection);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_selection_invalid');
    expect(result.issues[0]?.path).toBe(path);
  });

  it('fails closed for a malformed target graph cast before reading its arrays', () => {
    const graph = { ...targetGraph(), steps: null };

    const result = applyMalformedGraph(graph);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.path).toBe('/graph/steps');
  });

  it('rejects extra or non-catalog presentation entries', () => {
    const preset = gauntletPreset();
    const nodes = (preset.extensions?.['ananta.caseflow.agent-canvas'] as any)
      ['nodes'];
    nodes['runtime-agent'] = { icon: 'person' };

    const result = applyBuilderCriticGauntletPreset(targetGraph(), preset, {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
  });

  it('fails closed when selected unknown fields are not cloneable graph data', () => {
    const graph = targetGraph();
    const preset = gauntletPreset();
    (preset.steps[0] as any).future_callback = () => undefined;

    const result = applyBuilderCriticGauntletPreset(graph, preset, {
      selected_step_ids: ['gauntlet-lead'],
      selected_edge_ids: [],
    }, catalog());

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_preset_invalid');
    expect(result.issues[0]?.message).toContain('cloneable');
    expect(graph.steps.map(step => step.id)).toEqual(['existing-a', 'existing-b']);
  });

  it('returns the original graph for a valid empty selection', () => {
    const graph = targetGraph();

    const result = applyBuilderCriticGauntletPreset(graph, gauntletPreset(), {
      selected_step_ids: [],
      selected_edge_ids: [],
    }, catalog());
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    expect(result.value).toBe(graph);
  });
});

function catalog(): CaseFlowAgentBindingCatalog {
  return {
    skill_profile_ids: [],
    personality_resource_ids: {
      agent_profile: [],
      instruction_layer: [AUTHORIZED_PERSONALITY_FROM_FIXTURE_CATALOG],
    },
    context_resource_ids: {
      context_profile: [],
      context_source: [AUTHORIZED_CONTEXT_FROM_FIXTURE_CATALOG],
    },
    model_profile_ids: [],
    model_role_ids: [],
    fallback_group_ids: [],
  };
}

function applyMalformedPreset(preset: unknown) {
  return applyBuilderCriticGauntletPreset(
    targetGraph(),
    preset as VpGraph,
    { selected_step_ids: [], selected_edge_ids: [] },
    catalog(),
  );
}

function applyMalformedSelection(selection: unknown) {
  return applyBuilderCriticGauntletPreset(
    targetGraph(),
    gauntletPreset(),
    selection as Parameters<typeof applyBuilderCriticGauntletPreset>[2],
    catalog(),
  );
}

function applyMalformedGraph(graph: unknown) {
  return applyBuilderCriticGauntletPreset(
    graph as VpGraph,
    gauntletPreset(),
    { selected_step_ids: [], selected_edge_ids: [] },
    catalog(),
  );
}

function targetGraph(): VpGraph {
  const vendorExtension = { opaque: { retain_identity: true } };
  return {
    id: 'target-graph',
    name: 'Target',
    description: 'Existing graph',
    version: '1',
    tags: ['existing'],
    steps: [
      step('existing-a', 'Existing A', 'observer'),
      step('existing-b', 'Existing B', 'observer'),
    ],
    edges: [edge('existing-edge', 'existing-a', 'existing-b')],
    metadata: { future_graph_metadata: { exact: ['keep', 3] } },
    extensions: {
      'vendor.future': vendorExtension,
    },
  };
}

function gauntletPreset(): VpGraph {
  const preset: VpGraph = {
    id: 'preset-builder-critic-gauntlet',
    name: 'Builder/Critic Gauntlet',
    description: 'Fixture matching the Hub preset contract',
    version: '1',
    tags: ['gauntlet'],
    metadata: {
      'ananta.caseflow.agent-preset': {
        schema: 'ananta.caseflow.agent-preset/v1',
        binding_slots: [{
          slot: 'critic_benchmark_context',
          step_id: 'gauntlet-critic',
          resource_type: 'context_source',
          required: true,
          access: 'read_only',
        }],
        future_contract_field: { preserve: true },
      },
    },
    extensions: {
      'ananta.caseflow.agent-canvas': {
        schema: 'ananta.caseflow.agent-canvas/v1',
        future_extension: { untouched: true },
        nodes: {
          'gauntlet-lead': {
            icon: 'star', future_node_field: { id: 'lead' },
          },
          'gauntlet-builder': {
            icon: 'code', future_node_field: { id: 'builder' },
          },
          'gauntlet-critic': {
            icon: 'rule', future_node_field: { id: 'critic' },
          },
        },
      },
    },
    steps: [
      step('gauntlet-lead', 'Lead', 'lead'),
      step('gauntlet-builder', 'Builder', 'developer'),
      step('gauntlet-critic', 'Critic', 'critic'),
    ],
    edges: [
      edge('gauntlet-lead-builder', 'gauntlet-lead', 'gauntlet-builder'),
      edge('gauntlet-lead-critic', 'gauntlet-lead', 'gauntlet-critic'),
      edge(
        'gauntlet-builder-critic',
        'gauntlet-builder',
        'gauntlet-critic',
        'on_success',
      ),
      edge(
        'gauntlet-critic-builder-feedback',
        'gauntlet-critic',
        'gauntlet-builder',
        'back_edge',
      ),
    ],
  };
  const critic = preset.steps.find(step => step.id === 'gauntlet-critic');
  if (critic) critic.policy_hints = ['read_only'];
  return preset;
}

function step(id: string, label: string, role: string): VpStep {
  return {
    id,
    label,
    kind: role === 'lead'
      ? 'plan_only'
      : role === 'developer' ? 'patch_propose' : 'review',
    role,
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
    metadata: { future_step_field: { keep: id } },
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  kind = 'always',
): VpEdge {
  return {
    id,
    source,
    target,
    condition: kind === 'back_edge'
      ? { kind, loop_policy: { kind: 'fixed', max_iterations: 3 } }
      : { kind },
  };
}
