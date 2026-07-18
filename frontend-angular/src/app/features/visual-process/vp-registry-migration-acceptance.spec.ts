import { ɵresolveComponentResources, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { VpGraph } from './visual-process-api.service';
import { FALLBACK_KINDS } from './vp-editor-config';
import {
  VpNodeDefinitionRegistryService,
  VpNodeFieldDefinition,
} from './vp-node-definition-registry.service';
import { VpResourceOptionProvider } from './vp-resource-option-provider';
import { VpStepInspectorComponent } from './vp-step-inspector.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

const ED008_KINDS = new Set([
  'domain_cluster', 'embed_api', 'embed_chunk', 'query_rewrite', 'rag_retrieve',
  'rerank', 'sign_rotation', 'turboquant_mse',
]);
const ED009_KINDS = new Set([
  'ml_intern_build_lora_dataset', 'ml_intern_train_lora', 'evolution_analyze',
  'evolution_validate', 'evolution_apply', 'evolve_prompt', 'evolve_project',
]);

function sample(field: VpNodeFieldDefinition): unknown {
  switch (field.type) {
    case 'number': return field.min ?? (field.default === 2 ? 3 : 2);
    case 'boolean': return !Boolean(field.default);
    case 'enum': return field.options?.find(option => option.value !== field.default)?.value ?? field.options?.[0]?.value;
    case 'multi-select': return field.options?.length ? [field.options.at(-1)!.value] : ['edited'];
    case 'resource-reference': return 'authorized-catalog-item';
    case 'secret-reference': return 'env://ACCEPTANCE_REFERENCE';
    case 'io-port': return [{ name: 'edited', kind: 'text', required: false }];
    case 'structured-list': return [{ name: 'edited' }];
    default: return 'edited-value';
  }
}

describe('registry migration acceptance (ED-007 through ED-009)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [VpStepInspectorComponent] }).compileComponents();
  });

  function inspector(kindId: string) {
    const kind = FALLBACK_KINDS.find(candidate => candidate.id === kindId)!;
    const registry = new VpNodeDefinitionRegistryService();
    const definition = registry.definition(kind);
    const step = registry.createStep(definition, `step-${kindId}`, { x: 0, y: 0 });
    const graph: VpGraph = {
      id: `graph-${kindId}`, name: kind.label, description: '', version: '1', tags: [], edges: [], steps: [step],
    };
    const fixture = TestBed.createComponent(VpStepInspectorComponent);
    const component = fixture.componentInstance;
    component.graph = signal(graph);
    component.selectedId = signal(step.id);
    component.taskKindList = signal(FALLBACK_KINDS);
    component.skillProfiles = signal([]);
    component.modelProfiles = signal([]);
    component.fallbackGroups = signal({});
    component.artifactKinds = ['text', 'json'];
    component.edgeKinds = [];
    component.encodingModes = [];
    component.ragChannels = [];
    component.nodeDefinitions = [definition];
    return { fixture, component, definition };
  }

  it('writes every offered field of all 37 canonical kinds through the EditorCommand port', () => {
    expect(FALLBACK_KINDS).toHaveLength(37);
    const visited = new Set<string>();
    for (const kind of FALLBACK_KINDS) {
      const { fixture, component, definition } = inspector(kind.id);
      const commands: string[] = [];
      component.graphMutator = (label, mutator) => {
        commands.push(label);
        component.graph.update(current => {
          const copy = structuredClone(current);
          mutator(copy);
          return copy;
        });
      };
      const fields = definition.fields.filter(field => !field.readOnly && !field.deprecated);
      expect(fields.length, kind.id).toBeGreaterThan(0);
      for (const field of fields) {
        const before = commands.length;
        const value = sample(field);
        component.setFieldValue(field, value);
        expect(commands.length, `${kind.id}:${field.path}`).toBe(before + 1);
        expect(component.fieldValue(field), `${kind.id}:${field.path}`).toEqual(value);
        visited.add(`${kind.id}:${field.path}`);
      }
      fixture.destroy();
    }
    const expected = FALLBACK_KINDS.reduce((count, kind) => {
      const definition = new VpNodeDefinitionRegistryService().definition(kind);
      return count + definition.fields.filter(field => !field.readOnly && !field.deprecated).length;
    }, 0);
    expect(visited.size).toBe(expected);
  });

  it('keeps retrieval, ML and training enum options isolated per field and kind', () => {
    const registry = new VpNodeDefinitionRegistryService();
    const affected = FALLBACK_KINDS.filter(kind => ED008_KINDS.has(kind.id) || ED009_KINDS.has(kind.id));
    const optionContracts = new Map<string, string>();
    for (const kind of affected) {
      for (const field of registry.definition(kind).fields.filter(candidate => candidate.options?.length)) {
        optionContracts.set(`${kind.id}:${field.path}`, JSON.stringify(field.options));
      }
    }
    expect(optionContracts.size).toBeGreaterThan(0);
    expect(optionContracts.get('embed_api:/metadata/provider')).not.toBe(
      optionContracts.get('ml_intern_train_lora:/metadata/backend'),
    );
    expect(optionContracts.get('ml_intern_build_lora_dataset:/metadata/format')).not.toBe(
      optionContracts.get('ml_intern_build_lora_dataset:/metadata/privacy'),
    );
    expect(optionContracts.get('ml_intern_train_lora:/metadata/method')).not.toBe(
      optionContracts.get('ml_intern_train_lora:/metadata/mode'),
    );
  });

  it('binds each catalog field only to its focused authorized option source', () => {
    const provider = TestBed.inject(VpResourceOptionProvider);
    const sources: Record<string, string> = {
      skills: 'skill-only', models: 'model-only', 'fallback-groups': 'fallback-only',
      'training-datasets': 'dataset-only', 'training-profiles': 'profile-only',
      'training-base-models': 'base-model-only', processes: 'process-only',
      'rag-channels': 'channel-only',
    };
    for (const [source, id] of Object.entries(sources)) {
      provider.setStatic(source, [{ id, label: id }]);
    }

    for (const kindId of ['embed_api', 'ml_intern_build_lora_dataset', 'ml_intern_train_lora']) {
      const { fixture, component, definition } = inspector(kindId);
      for (const field of definition.fields.filter(candidate => candidate.optionSource)) {
        const expected = sources[field.optionSource!];
        if (!expected) continue;
        expect(component.fieldOptions(field).map(option => option.value), `${kindId}:${field.path}`).toEqual([expected]);
      }
      fixture.destroy();
    }
  });
});
