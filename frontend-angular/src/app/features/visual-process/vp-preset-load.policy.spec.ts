import { describe, expect, it } from 'vitest';

import type { VpGraph } from './visual-process-api.service';
import {
  VP_CATALOG_BOUND_PRESET_METADATA_KEY,
  VP_CATALOG_BOUND_PRESET_SCHEMA_V1,
  validateVpPresetDirectLoad,
} from './vp-preset-load.policy';

describe('Visual Process direct preset load policy', () => {
  it('allows an ordinary preset without catalog application metadata', () => {
    const preset = graph('preset-plain');

    const result = validateVpPresetDirectLoad('preset-plain', preset);

    expect(result).toEqual({ ok: true, value: preset, issues: [] });
  });

  it('allows a valid marker that contains no required catalog slot', () => {
    const preset = catalogBoundGraph('preset-optional', [{
      slot: 'optional_context',
      step_id: 'step-a',
      resource_type: 'context_source',
      access: 'read_only',
      required: false,
    }]);

    const result = validateVpPresetDirectLoad('preset-optional', preset);

    expect(result.ok).toBe(true);
  });

  it('fails closed when a preset requires domain-authorized catalog application', () => {
    const preset = catalogBoundGraph('preset-bound', [{
      slot: 'required_context',
      step_id: 'step-a',
      resource_type: 'context_source',
      access: 'read_only',
      required: true,
    }]);

    const result = validateVpPresetDirectLoad('preset-bound', preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('catalog_application_required');
  });

  it.each([
    ['non-object marker', 'invalid'],
    ['missing binding slot array', { schema: VP_CATALOG_BOUND_PRESET_SCHEMA_V1 }],
    ['malformed binding slot', {
      schema: VP_CATALOG_BOUND_PRESET_SCHEMA_V1,
      binding_slots: [{ required: true }],
    }],
  ])('rejects a malformed catalog marker: %s', (_label, marker) => {
    const preset = graph('preset-malformed');
    preset.metadata = { [VP_CATALOG_BOUND_PRESET_METADATA_KEY]: marker };

    const result = validateVpPresetDirectLoad('preset-malformed', preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('preset_response_invalid');
  });

  it('rejects an unknown catalog marker schema', () => {
    const preset = catalogBoundGraph('preset-future', []);
    const marker = preset.metadata?.[VP_CATALOG_BOUND_PRESET_METADATA_KEY] as
      Record<string, unknown>;
    marker['schema'] = 'ananta.caseflow.agent-preset/v99';

    const result = validateVpPresetDirectLoad('preset-future', preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]).toMatchObject({
      code: 'preset_response_invalid',
      path: `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}/schema`,
    });
  });

  it('rejects a Hub response whose identity differs from the request', () => {
    const result = validateVpPresetDirectLoad(
      'preset-requested',
      graph('preset-returned'),
    );

    expect(result.ok).toBe(false);
    expect(result.issues[0]).toMatchObject({
      code: 'preset_identity_mismatch',
      path: '/preset/id',
    });
  });

  it('fails closed without throwing for a non-object response', () => {
    expect(() => validateVpPresetDirectLoad('preset-a', null)).not.toThrow();

    const result = validateVpPresetDirectLoad('preset-a', null);

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('preset_response_invalid');
  });

  it('rejects an incomplete object instead of casting it to a graph', () => {
    const result = validateVpPresetDirectLoad('preset-a', { id: 'preset-a' });

    expect(result.ok).toBe(false);
    expect(result.issues[0]).toMatchObject({
      code: 'preset_response_invalid',
      path: '/preset',
    });
  });

  it.each([
    ['runtime overlay', (preset: VpGraph) => {
      (preset as unknown as Record<string, unknown>)['runtime_overlay'] = {
        current_step_ids: ['step-a'],
      };
    }, '/preset/runtime_overlay'],
    ['step run state', (preset: VpGraph) => {
      preset.steps = [step('step-a')];
      preset.steps[0].run_state = 'running';
    }, '/preset/steps/0/run_state'],
    ['nested execution result', (preset: VpGraph) => {
      preset.metadata = { execution_result: { status: 'succeeded' } };
    }, '/preset/metadata/execution_result'],
    ['nested trace', (preset: VpGraph) => {
      preset.metadata = { projection: { trace: [{ event: 'started' }] } };
    }, '/preset/metadata/projection/trace'],
    ['nested telemetry', (preset: VpGraph) => {
      preset.extensions = { 'vendor.runtime': { telemetry: { latency_ms: 3 } } };
    }, '/preset/extensions/vendor.runtime/telemetry'],
    ['nested token usage', (preset: VpGraph) => {
      preset.metadata = { accounting: { token_usage: { input_tokens: 3 } } };
    }, '/preset/metadata/accounting/token_usage'],
  ])('rejects non-definition state from the direct preset path: %s', (
    _label,
    mutate,
    path,
  ) => {
    const preset = graph('preset-runtime');
    mutate(preset);

    const result = validateVpPresetDirectLoad('preset-runtime', preset);

    expect(result.ok).toBe(false);
    expect(result.issues[0]).toMatchObject({
      code: 'preset_response_invalid',
      path,
    });
  });

  it('allows a null legacy run_state because it carries no runtime truth', () => {
    const preset = graph('preset-null-state');
    preset.steps = [step('step-a')];
    preset.steps[0].run_state = null as unknown as string;

    const result = validateVpPresetDirectLoad('preset-null-state', preset);

    expect(result.ok).toBe(true);
  });
});

function graph(id: string): VpGraph {
  return {
    id,
    name: id,
    description: '',
    version: '1',
    tags: [],
    steps: [],
    edges: [],
  };
}

function catalogBoundGraph(
  id: string,
  bindingSlots: readonly Readonly<Record<string, unknown>>[],
): VpGraph {
  return {
    ...graph(id),
    metadata: {
      [VP_CATALOG_BOUND_PRESET_METADATA_KEY]: {
        schema: VP_CATALOG_BOUND_PRESET_SCHEMA_V1,
        binding_slots: bindingSlots,
      },
    },
  };
}

function step(id: string) {
  return {
    id,
    label: id,
    kind: 'review',
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  };
}
