import { Injectable } from '@angular/core';

import {
  NodeDefinitionContract,
  NodeDefinitionFieldContract,
  NodeDefinitionRegistryResult,
  SkillProfile,
  TaskKindInfo,
  VpGraph,
  VpStep,
} from './visual-process-api.service';
import {
  GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS,
  GENERATED_VISUAL_PROCESS_NODE_REGISTRY_VERSION,
} from './vp-node-definitions.generated';

export type VpNodeFieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'boolean'
  | 'enum'
  | 'multi-select'
  | 'resource-reference'
  | 'secret-reference'
  | 'expression'
  | 'io-port'
  | 'structured-list';

export type VpOptionSource =
  | 'skills'
  | 'models'
  | 'fallback-groups'
  | 'training-datasets'
  | 'training-profiles'
  | 'training-base-models'
  | 'processes'
  | 'rag-channels'
  | 'encoding-modes';

export interface VpNodeFieldOption {
  label: string;
  value: string | number | boolean;
  disabled?: boolean;
}

export interface VpNodeFieldDefinition {
  path: string;
  label: string;
  type: VpNodeFieldType;
  description: string;
  default?: unknown;
  example?: unknown;
  effect?: string;
  required?: boolean;
  expert?: boolean;
  essential?: boolean;
  deprecated?: boolean;
  readOnly?: boolean;
  resourceType?: string;
  visibleWhen?: VpNodeFieldCondition;
  requiredWhen?: VpNodeFieldCondition;
  min?: number;
  max?: number;
  step?: number;
  options?: VpNodeFieldOption[];
  optionSource?: VpOptionSource;
}

export interface VpNodeFieldCondition {
  path: string;
  equals?: unknown;
  equalsAny?: unknown[];
  notEquals?: unknown;
  notEqualsAny?: unknown[];
  exists?: boolean;
}

export interface VpNodeDefinition {
  kind: string;
  label: string;
  category: string;
  purpose: string;
  keywords: string[];
  inputs: string[];
  outputs: string[];
  fields: VpNodeFieldDefinition[];
  metadataDefaults: Record<string, unknown>;
  capabilityFlags: {
    executable: boolean;
    deterministic: boolean;
    usesLlm: boolean;
    usesNetwork: boolean;
    requiresApproval: boolean;
    supportsModelRouting: boolean;
  };
  supported: boolean;
  schemaComplete: boolean;
  registryVersion: string;
  implementationState: string;
  implementationStatus: string;
  riskLevel: string;
  sideEffects: string[];
}

/** Version of the checked-in fallback generated from the Hub registry contract. */
export const VP_NODE_REGISTRY_VERSION = GENERATED_VISUAL_PROCESS_NODE_REGISTRY_VERSION;

const GENERATED_DEFINITION_BY_KIND = new Map(
  GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS.map(definition => [definition.kind, definition]),
);

const COMMON_FIELDS: VpNodeFieldDefinition[] = [
  { path: 'label', label: 'Label', type: 'text', description: 'Sichtbarer Name des Schritts.', required: true, example: 'Änderung prüfen' },
  { path: 'role', label: 'Rolle', type: 'text', description: 'Optionale fachliche Worker-Rolle.', example: 'developer' },
  { path: 'agent_skill_profile_id', label: 'Skill-Profil', type: 'resource-reference', optionSource: 'skills', description: 'Vom Hub verwaltetes Skill-Profil.' },
  { path: 'metadata.description', label: 'Beschreibung', type: 'textarea', description: 'Fachliche Erklärung für Leser und Reviews.' },
  { path: 'gate', label: 'Freigabe erforderlich', type: 'boolean', default: false, description: 'Hält den Lauf bis zu einer expliziten Freigabe an.' },
  { path: 'policy_hints', label: 'Policy-Hinweise', type: 'multi-select', default: [], options: ['read_only', 'requires_approval', 'high_risk'].map(value => ({ label: value, value })), description: 'Nicht-autoritative Hinweise; der Hub entscheidet die effektive Policy.' },
  { path: 'io.inputs', label: 'Inputs', type: 'io-port', default: [], description: 'Typisierte Eingangsartefakte.' },
  { path: 'io.outputs', label: 'Outputs', type: 'io-port', default: [], description: 'Typisierte Ausgangsartefakte.' },
];

function defaultMetadata(fields: VpNodeFieldDefinition[]): Record<string, unknown> {
  const metadata: Record<string, unknown> = {};
  for (const field of fields) {
    const prefix = field.path.startsWith('/metadata/') ? '/metadata/' : 'metadata.';
    if (!field.path.startsWith(prefix) || field.default === undefined) continue;
    metadata[field.path.slice(prefix.length)] = structuredClone(field.default);
  }
  return metadata;
}

@Injectable({ providedIn: 'root' })
export class VpNodeDefinitionRegistryService {
  definitions(taskKinds: readonly TaskKindInfo[]): VpNodeDefinition[] {
    return taskKinds.map(kind => this.definition(kind));
  }

  definition(kind: TaskKindInfo): VpNodeDefinition {
    const generated = GENERATED_DEFINITION_BY_KIND.get(kind.id);
    if (generated) return this.fromBackendDefinition(generated);
    const commonFields = COMMON_FIELDS.map(field => this.normalizeLocalField(field));
    return {
      kind: kind.id,
      label: kind.label || kind.id,
      category: kind.group || 'general',
      purpose: kind.description || `Ananta-Schritt des Typs ${kind.label || kind.id}.`,
      keywords: [kind.id, kind.label, kind.group, ...(kind.legacy_aliases ?? [])].filter(Boolean),
      inputs: [],
      outputs: [],
      fields: commonFields,
      metadataDefaults: defaultMetadata(commonFields),
      capabilityFlags: {
        executable: kind.implementation_state === 'wired_and_executable' || kind.dispatch_capable,
        deterministic: kind.deterministic === true,
        usesLlm: kind.uses_llm === true,
        usesNetwork: kind.uses_network === true,
        requiresApproval: kind.requires_approval === true,
        supportsModelRouting: kind.uses_llm === true,
      },
      supported: kind.implementation_state !== 'not_implemented',
      schemaComplete: false,
      registryVersion: VP_NODE_REGISTRY_VERSION,
      implementationState: kind.implementation_state ?? 'unknown',
      implementationStatus: kind.implementation_status ?? 'unknown',
      riskLevel: kind.risk_level ?? 'unknown',
      sideEffects: [...(kind.side_effects ?? [])],
    };
  }

  fromContract(payload: NodeDefinitionRegistryResult): VpNodeDefinition[] {
    return payload.definitions.map(definition => this.fromBackendDefinition(definition));
  }

  fromBackendDefinition(definition: NodeDefinitionContract): VpNodeDefinition {
    const runtime = definition.runtime ?? {};
    const execution = definition.execution ?? {};
    const capabilities = definition.capabilities ?? {};
    const fields = (definition.fields ?? []).map(field => this.fromBackendField(field));
    const legacyAliases = Array.isArray(runtime['legacy_aliases'])
      ? runtime['legacy_aliases'].map(String)
      : [];
    this.assertAcyclicFieldDependencies(definition.kind, fields);
    return {
      kind: definition.kind,
      label: definition.label || definition.kind,
      category: definition.category || 'general',
      purpose: definition.purpose || definition.help_text || `Ananta-Schritt ${definition.kind}.`,
      keywords: [
        definition.kind,
        definition.label,
        definition.category,
        definition.purpose,
        definition.help_text,
        ...legacyAliases,
      ].filter((value): value is string => typeof value === 'string' && value.length > 0),
      inputs: this.contractPorts(definition.inputs),
      outputs: this.contractPorts(definition.outputs),
      fields,
      metadataDefaults: structuredClone(definition.defaults?.metadata ?? defaultMetadata(fields)),
      capabilityFlags: {
        executable: execution['visual_process_executable'] === true,
        deterministic: runtime['deterministic'] === true,
        usesLlm: runtime['uses_llm'] === true,
        usesNetwork: runtime['uses_network'] === true,
        requiresApproval: capabilities['requires_approval'] === true || runtime['requires_approval'] === true,
        supportsModelRouting: capabilities['supports_model_routing'] === true,
      },
      supported: runtime['implementation_state'] !== 'not_implemented',
      schemaComplete: true,
      registryVersion: definition.registry_version || VP_NODE_REGISTRY_VERSION,
      implementationState: String(runtime['implementation_state'] ?? 'unknown'),
      implementationStatus: String(runtime['implementation_status'] ?? 'unknown'),
      riskLevel: String(runtime['risk_level'] ?? 'unknown'),
      sideEffects: Array.isArray(runtime['side_effects']) ? runtime['side_effects'].map(String) : [],
    };
  }

  find(kind: string, taskKinds: readonly TaskKindInfo[]): VpNodeDefinition {
    const info = taskKinds.find(candidate => candidate.id === kind) ?? {
      id: kind,
      label: kind,
      group: 'unsupported',
      dispatch_capable: false,
      description: 'Unbekannter Node-Kind. Daten bleiben verlustfrei lesbar.',
      implementation_state: 'not_implemented',
    };
    return this.definition(info);
  }

  createStep(definition: VpNodeDefinition, id: string, position: { x: number; y: number }): VpStep {
    return {
      id,
      label: definition.label,
      kind: definition.kind,
      role: '',
      io: { inputs: [], outputs: [] },
      position,
      policy_hints: definition.capabilityFlags.requiresApproval ? ['requires_approval'] : [],
      gate: definition.capabilityFlags.requiresApproval,
      metadata: structuredClone(definition.metadataDefaults),
    };
  }

  nextPosition(graph: VpGraph, anchor: { x: number; y: number } = { x: 40, y: 40 }): { x: number; y: number } {
    const occupied = new Set(graph.steps.map(step => `${Math.round((step.position.x - 40) / 200)}:${Math.round((step.position.y - 40) / 100)}`));
    const anchorColumn = Math.round((anchor.x - 40) / 200);
    const anchorRow = Math.round((anchor.y - 40) / 100);
    for (let index = 0; index < Math.max(1, graph.steps.length + 10); index++) {
      const column = anchorColumn + index % 4;
      const row = anchorRow + Math.floor(index / 4);
      if (!occupied.has(`${column}:${row}`)) return { x: 40 + column * 200, y: 40 + row * 100 };
    }
    return { x: 40 + anchorColumn * 200, y: 40 + (anchorRow + graph.steps.length) * 100 };
  }

  effectiveFields(definition: VpNodeDefinition, _skills: readonly SkillProfile[] = []): VpNodeFieldDefinition[] {
    return definition.fields;
  }

  private normalizeLocalField(field: VpNodeFieldDefinition): VpNodeFieldDefinition {
    const path = field.path.startsWith('/')
      ? field.path
      : `/${field.path.split('.').map(segment => segment.replaceAll('~', '~0').replaceAll('/', '~1')).join('/')}`;
    return { ...field, path };
  }

  private fromBackendField(field: NodeDefinitionFieldContract): VpNodeFieldDefinition {
    const constraints = field.constraints ?? {};
    let optionSource = ({
      skill_profile: 'skills',
      model_profile: 'models',
      training_dataset: 'training-datasets',
      training_profile: 'training-profiles',
      fallback_group: 'fallback-groups',
      process_reference: 'processes',
      rag_channel: 'rag-channels',
      worker_role: 'skills',
    } as Record<string, VpOptionSource>)[String(field.resource_type ?? '')];
    if (field.path === '/metadata/base_model') optionSource = 'training-base-models';
    const fieldType = ({
      multi_select: 'multi-select',
      resource_reference: 'resource-reference',
      secret_reference: 'secret-reference',
      structured_list: 'structured-list',
      io_port: 'io-port',
    } as Record<string, VpNodeFieldType>)[field.field_type] ?? (field.field_type as VpNodeFieldType);
    return {
      path: field.path,
      label: field.label,
      type: fieldType,
      description: field.help_text,
      effect: field.effect ?? (typeof constraints['effect'] === 'string' ? constraints['effect'] : field.help_text),
      default: field.default,
      required: field.required,
      essential: field.essential !== false,
      expert: field.essential === false,
      example: field.example,
      deprecated: field.deprecated === true,
      readOnly: field.read_only === true,
      resourceType: field.resource_type,
      visibleWhen: this.fromBackendCondition(field.visible_when),
      requiredWhen: this.fromBackendCondition(field.required_when),
      min: typeof constraints['minimum'] === 'number' ? constraints['minimum'] : undefined,
      max: typeof constraints['maximum'] === 'number' ? constraints['maximum'] : undefined,
      step: constraints['integer'] === true ? 1 : undefined,
      options: field.options?.map(option => ({ label: option.label, value: option.value as string | number | boolean })),
      optionSource,
    };
  }

  private fromBackendCondition(condition: NodeDefinitionFieldContract['visible_when']): VpNodeFieldCondition | undefined {
    if (!condition?.path) return undefined;
    return {
      path: condition.path,
      ...(Object.prototype.hasOwnProperty.call(condition, 'equals') ? { equals: condition.equals } : {}),
      ...(condition.equals_any ? { equalsAny: structuredClone(condition.equals_any) } : {}),
      ...(Object.prototype.hasOwnProperty.call(condition, 'not_equals') ? { notEquals: condition.not_equals } : {}),
      ...(condition.not_equals_any ? { notEqualsAny: structuredClone(condition.not_equals_any) } : {}),
      ...(condition.exists !== undefined ? { exists: condition.exists } : {}),
    };
  }

  private assertAcyclicFieldDependencies(kind: string, fields: readonly VpNodeFieldDefinition[]): void {
    const known = new Set(fields.map(field => field.path));
    const graph = new Map(fields.map(field => [
      field.path,
      [field.visibleWhen?.path, field.requiredWhen?.path].filter((path): path is string => !!path && known.has(path)),
    ]));
    const visiting = new Set<string>();
    const visited = new Set<string>();
    const visit = (path: string): void => {
      if (visiting.has(path)) throw new Error(`node_definition_field_dependency_cycle:${kind}:${path}`);
      if (visited.has(path)) return;
      visiting.add(path);
      for (const dependency of graph.get(path) ?? []) visit(dependency);
      visiting.delete(path);
      visited.add(path);
    };
    for (const path of graph.keys()) visit(path);
  }

  private contractPorts(ports: readonly unknown[]): string[] {
    return ports.map(port => {
      if (typeof port === 'string') return port;
      if (port && typeof port === 'object') {
        const value = port as Record<string, unknown>;
        return String(value['name'] ?? value['kind'] ?? value['label'] ?? 'artifact');
      }
      return String(port);
    });
  }
}
