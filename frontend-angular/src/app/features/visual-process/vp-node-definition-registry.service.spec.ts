import { describe, expect, it } from 'vitest';

import { NodeDefinitionContract, TaskKindInfo, VpGraph } from './visual-process-api.service';
import { VpNodeDefinitionRegistryService } from './vp-node-definition-registry.service';
import { FALLBACK_KINDS } from './vp-editor-config';
import { GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS } from './vp-node-definitions.generated';

const kind: TaskKindInfo = {
  id: 'ml_intern_train_lora', label: 'LoRA Training', group: 'ml', description: 'Trainiert einen Adapter.',
  dispatch_capable: false, implementation_state: 'wired_and_executable', requires_approval: true,
};

describe('VpNodeDefinitionRegistryService', () => {
  it('creates typed definitions and registry defaults', () => {
    const service = new VpNodeDefinitionRegistryService();
    const definition = service.definition(kind);
    const step = service.createStep(definition, 'step-1', { x: 40, y: 40 });

    expect(definition.fields.some(field => field.path === '/metadata/dataset_id' && field.required)).toBe(true);
    expect(step.metadata?.['mode']).toBe('dry_run');
    expect(step.gate).toBe(true);
    expect(step.policy_hints).toContain('requires_approval');
  });

  it('consumes every generated Hub definition for the offline fallback', () => {
    const service = new VpNodeDefinitionRegistryService();
    const definitions = service.definitions(FALLBACK_KINDS);
    expect(definitions.map(item => item.kind)).toEqual(FALLBACK_KINDS.map(item => item.id));
    for (const generated of GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS) {
      const actual = definitions.find(item => item.kind === generated.kind)!;
      expect(actual.registryVersion, generated.kind).toBe(generated.registry_version);
      expect(actual.implementationState, generated.kind).toBe(generated.runtime['implementation_state']);
      expect(actual.sideEffects, generated.kind).toEqual(generated.runtime['side_effects']);
      expect(actual.fields.map(field => field.path), generated.kind).toEqual(generated.fields.map(field => field.path));
    }
  });

  it('keeps unknown kinds readable and explicitly unsupported', () => {
    const service = new VpNodeDefinitionRegistryService();
    const definition = service.find('future_kind', []);
    expect(definition.kind).toBe('future_kind');
    expect(definition.supported).toBe(false);
    expect(definition.fields.some(field => field.path === '/label')).toBe(true);
  });

  it('places new nodes deterministically without using randomness', () => {
    const service = new VpNodeDefinitionRegistryService();
    const graph: VpGraph = {
      id: 'g', name: 'G', description: '', version: '1', tags: [], edges: [],
      steps: [{ id: 'a', label: 'A', kind: 'task', position: { x: 40, y: 40 }, io: { inputs: [], outputs: [] }, policy_hints: [], gate: false }],
    };
    expect(service.nextPosition(graph)).toEqual({ x: 240, y: 40 });
    expect(service.nextPosition(graph)).toEqual({ x: 240, y: 40 });
    expect(service.nextPosition(graph, { x: 1040, y: 540 })).toEqual({ x: 1040, y: 540 });
  });

  it('retains declarative field help, expert and dependency metadata from the Hub contract', () => {
    const service = new VpNodeDefinitionRegistryService();
    const contract: NodeDefinitionContract = {
      contract_version: 'ananta.visual_process.node_definition.v1', registry_version: '1', kind: 'fixture',
      label: 'Fixture', category: 'test', purpose: 'Test',
      help_text: 'Suchbare Erklärung', runtime: { legacy_aliases: ['fixture_legacy'] },
      execution: { visual_process_executable: true },
      inputs: [], outputs: [], fields: [{
        path: '/metadata/endpoint', label: 'Endpoint', field_type: 'text', help_text: 'Kurzbeschreibung',
        example: 'https://example.invalid', effect: 'Steuert den Zielendpunkt.', essential: false,
        visible_when: { path: '/metadata/provider', equals_any: ['remote'] },
        required_when: { path: '/metadata/provider', equals: 'remote' }, read_only: true, deprecated: true,
      }],
    };
    const definition = service.fromBackendDefinition(contract);
    const field = definition.fields[0];
    expect(field).toMatchObject({
      example: 'https://example.invalid', effect: 'Steuert den Zielendpunkt.', essential: false, expert: true,
      visibleWhen: { path: '/metadata/provider', equalsAny: ['remote'] },
      requiredWhen: { path: '/metadata/provider', equals: 'remote' }, readOnly: true, deprecated: true,
    });
    expect(definition.keywords).toEqual(expect.arrayContaining(['fixture_legacy', 'Suchbare Erklärung']));
  });

  it('rejects cyclic field dependencies as a registry error', () => {
    const service = new VpNodeDefinitionRegistryService();
    const contract: NodeDefinitionContract = {
      contract_version: 'ananta.visual_process.node_definition.v1', registry_version: '1', kind: 'cycle',
      label: 'Cycle', category: 'test', purpose: 'Test', runtime: {}, execution: {}, inputs: [], outputs: [],
      fields: [
        { path: '/metadata/a', label: 'A', field_type: 'text', help_text: 'A', visible_when: { path: '/metadata/b', exists: true } },
        { path: '/metadata/b', label: 'B', field_type: 'text', help_text: 'B', required_when: { path: '/metadata/a', exists: true } },
      ],
    };
    expect(() => service.fromBackendDefinition(contract)).toThrow('node_definition_field_dependency_cycle:cycle');
  });
});
