import { describe, expect, it } from 'vitest';
import type { VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import {
  CaseFlowAgentBindingCatalog,
  setAgentBindings,
  validateAgentStepBindings,
} from './caseflow-agent-graph.commands';
import {
  CASEFLOW_AGENT_BINDINGS_METADATA,
  CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
} from './caseflow-agent-canvas.models';

describe('CaseFlow focused agent binding commands', () => {
  it('stores only references in the existing skill, binding and model-routing seams', () => {
    const graph = fixtureGraph();
    const before = graph.steps[0];
    const result = setAgentBindings(graph, 'agent', {
      skill_profile_id: 'reviewer',
      personality_binding: {
        resource_type: 'instruction_layer',
        resource_id: 'critical-review-v1',
      },
      context_bindings: [{
        resource_type: 'context_source',
        resource_id: 'case-evidence',
      }],
      model_routing: {
        model_role: 'reviewer',
        preferred_profile_id: 'local-reviewer',
        fallback_group_id: 'reviewer-local-first',
      },
    }, catalog);
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    const after = result.value.steps[0];
    const bindings = after.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA] as Record<string, unknown>;
    const routing = after.metadata?.['model_routing'] as Record<string, unknown>;
    expect(after.agent_skill_profile_id).toBe('reviewer');
    expect(bindings).toEqual({
      schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
      future_binding_field: { preserved: true },
      personality_binding: {
        resource_type: 'instruction_layer',
        resource_id: 'critical-review-v1',
      },
      context_bindings: [{
        resource_type: 'context_source',
        resource_id: 'case-evidence',
      }],
    });
    expect(routing).toEqual({
      strategy: 'per_step',
      allow_cloud: false,
      future_routing_field: { preserved: true },
      model_role: 'reviewer',
      preferred_profile_id: 'local-reviewer',
      fallback_group_id: 'reviewer-local-first',
    });
    expect(JSON.stringify(bindings)).not.toContain('system_prompt');
    expect(JSON.stringify(bindings)).not.toContain('context_content');
    expect(JSON.stringify(routing)).not.toContain('provider');
    expect(after.metadata?.['allowed_tools']).toBe(before.metadata?.['allowed_tools']);
    expect(after.metadata?.['capabilities']).toBe(before.metadata?.['capabilities']);
  });

  it('fails closed before save for unknown skill, personality, context and routing references', () => {
    const graph = fixtureGraph();
    const before = JSON.stringify(graph);
    const result = setAgentBindings(graph, 'agent', {
      skill_profile_id: 'invented-skill',
      personality_binding: {
        resource_type: 'agent_profile',
        resource_id: 'invented-personality',
      },
      context_bindings: [{
        resource_type: 'context_profile',
        resource_id: 'invented-context',
      }],
      model_routing: {
        model_role: 'invented-role',
        preferred_profile_id: 'invented-model',
        fallback_group_id: 'invented-fallback',
      },
    }, catalog);

    expect(result.ok).toBe(false);
    expect(result.issues.map(issue => issue.code)).toEqual([
      'agent_binding_reference_not_allowed',
      'agent_binding_reference_not_allowed',
      'agent_binding_reference_not_allowed',
      'agent_binding_reference_not_allowed',
      'agent_binding_reference_not_allowed',
      'agent_binding_reference_not_allowed',
    ]);
    expect(JSON.stringify(graph)).toBe(before);
  });

  it('rejects malformed persisted bindings and duplicate context references', () => {
    const step = fixtureGraph().steps[0];
    step.metadata = {
      ...step.metadata,
      [CASEFLOW_AGENT_BINDINGS_METADATA]: {
        schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
        personality_binding: {
          resource_type: 'inline_system_prompt',
          resource_id: 'do-anything',
        },
      },
    };
    expect(validateAgentStepBindings(step, catalog)[0]?.code)
      .toBe('agent_binding_contract_invalid');

    const graph = fixtureGraph();
    const result = setAgentBindings(graph, 'agent', {
      context_bindings: [
        { resource_type: 'context_source', resource_id: 'case-evidence' },
        { resource_type: 'context_source', resource_id: 'case-evidence' },
      ],
    }, catalog);
    expect(result.ok).toBe(false);
    expect(result.issues[0]?.message).toContain('duplicated');

    const unsupportedDraft = setAgentBindings(graph, 'agent', {
      personality_binding: {
        resource_type: 'inline_system_prompt',
        resource_id: 'unsafe-inline-value',
      } as any,
      context_bindings: [{
        resource_type: 'whole_tenant',
        resource_id: 'all-data',
      } as any],
    }, catalog);
    expect(unsupportedDraft.ok).toBe(false);
    expect(unsupportedDraft.issues.map(issue => issue.code)).toEqual([
      'agent_binding_reference_invalid',
      'agent_binding_reference_invalid',
    ]);
  });

  it('preserves every non-inspector-owned step field across a graph update', () => {
    const graph = fixtureGraph();
    const before = graph.steps[0];
    const result = setAgentBindings(graph, 'agent', {
      personality_binding: {
        resource_type: 'agent_profile',
        resource_id: 'review-personality',
        future_ref_field: { keep: false },
      },
    }, catalog);
    if (!result.ok) throw new Error(JSON.stringify(result.issues));
    const after = result.value.steps[0];

    expect(after.id).toBe(before.id);
    expect(after.label).toBe(before.label);
    expect(after.kind).toBe(before.kind);
    expect(after.role).toBe(before.role);
    expect(after.io).toBe(before.io);
    expect(after.position).toBe(before.position);
    expect(after.policy_hints).toBe(before.policy_hints);
    expect(after.gate).toBe(before.gate);
    expect(after.run_state).toBe(before.run_state);
    expect((after as any).future_step_field).toEqual((before as any).future_step_field);
    expect(after.metadata?.['unmanaged_metadata']).toBe(before.metadata?.['unmanaged_metadata']);
    expect((after.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA] as Record<string, any>)
      ['future_binding_field']).toEqual({ preserved: true });
    expect((after.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA] as Record<string, any>)
      ['personality_binding']).toEqual({
      resource_type: 'agent_profile',
      resource_id: 'review-personality',
      future_personality_field: { preserved: true },
    });
  });

  it('removes only explicitly cleared references and keeps unrelated routing controls', () => {
    const graph = fixtureGraph();
    const result = setAgentBindings(graph, 'agent', {
      skill_profile_id: null,
      personality_binding: null,
      context_bindings: null,
      model_routing: null,
    }, catalog);
    if (!result.ok) throw new Error(JSON.stringify(result.issues));
    const after = result.value.steps[0];
    const bindings = after.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA] as Record<string, unknown>;
    const routing = after.metadata?.['model_routing'] as Record<string, unknown>;

    expect(after.agent_skill_profile_id).toBeUndefined();
    expect(bindings['personality_binding']).toBeUndefined();
    expect(bindings['context_bindings']).toBeUndefined();
    expect(bindings['future_binding_field']).toEqual({ preserved: true });
    expect(routing).toEqual({
      strategy: 'per_step',
      allow_cloud: false,
      future_routing_field: { preserved: true },
    });
  });

  it.each([
    'model_role',
    'preferred_profile_id',
    'fallback_group_id',
  ] as const)('clears only the selected %s routing reference', field => {
    const graph = fixtureGraph();
    const result = setAgentBindings(graph, 'agent', {
      model_routing: { [field]: null },
    }, catalog);
    if (!result.ok) throw new Error(JSON.stringify(result.issues));

    const before = graph.steps[0].metadata?.['model_routing'] as Record<string, unknown>;
    const after = result.value.steps[0].metadata?.['model_routing'] as Record<string, unknown>;
    expect(after[field]).toBeUndefined();
    for (const retained of ['model_role', 'preferred_profile_id', 'fallback_group_id']) {
      if (retained !== field) expect(after[retained]).toBe(before[retained]);
    }
    expect(after['future_routing_field']).toBe(before['future_routing_field']);
    expect(graph.steps[0].metadata?.['model_routing']).toBe(before);
  });
});

const catalog: CaseFlowAgentBindingCatalog = {
  skill_profile_ids: ['coder', 'reviewer'],
  personality_resource_ids: {
    agent_profile: ['review-personality'],
    instruction_layer: ['critical-review-v1'],
  },
  context_resource_ids: {
    context_profile: ['compact-case-context'],
    context_source: ['case-evidence'],
  },
  model_profile_ids: ['local-coder', 'local-reviewer'],
  model_role_ids: ['coder', 'reviewer'],
  fallback_group_ids: ['coder-local-first', 'reviewer-local-first'],
};

function fixtureGraph(): VpGraph {
  const step: VpStep & { future_step_field: unknown } = {
    id: 'agent',
    label: 'Agent',
    kind: 'coding',
    role: 'developer',
    agent_skill_profile_id: 'coder',
    io: { inputs: [{ name: 'prompt', kind: 'text', required: true }], outputs: [] },
    position: { x: 12, y: 34 },
    policy_hints: ['read_only'],
    gate: false,
    run_state: 'pending',
    metadata: {
      [CASEFLOW_AGENT_BINDINGS_METADATA]: {
        schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1,
        future_binding_field: { preserved: true },
        personality_binding: {
          resource_type: 'agent_profile',
          resource_id: 'review-personality',
          future_personality_field: { preserved: true },
        },
        context_bindings: [{
          resource_type: 'context_profile',
          resource_id: 'compact-case-context',
          future_context_field: { preserved: true },
        }],
      },
      model_routing: {
        strategy: 'per_step',
        allow_cloud: false,
        model_role: 'coder',
        preferred_profile_id: 'local-coder',
        fallback_group_id: 'coder-local-first',
        future_routing_field: { preserved: true },
      },
      allowed_tools: ['read_file'],
      capabilities: ['read_only'],
      unmanaged_metadata: { nested: [1, 2, 3] },
    },
    future_step_field: { exact: 'keep' },
  };
  return {
    id: 'bindings',
    name: 'Bindings',
    description: '',
    version: '1',
    tags: [],
    steps: [step],
    edges: [],
    metadata: { untouched: true },
    extensions: { 'vendor.extension': { untouched: true } },
  };
}
