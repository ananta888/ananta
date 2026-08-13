import type {
  VpEdge,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import { validateVpGraphDefinition } from '../../visual-process/vp-graph-definition.policy';
import type {
  CaseFlowAgentCanvasIssue,
  CaseFlowAgentNodePresentationV1,
  CaseFlowContextBindingRef,
  CaseFlowPersonalityBindingRef,
} from './caseflow-agent-canvas.models';
import {
  isRecord,
  parseAgentCanvasExtension,
  serializeAgentCanvasExtension,
} from './caseflow-agent-canvas.models';
import {
  type CaseFlowAgentBindingCatalog,
  setAgentBindings,
  validateAgentStepBindings,
} from './caseflow-agent-graph.commands';

export const BUILDER_CRITIC_GAUNTLET_PRESET_ID =
  'preset-builder-critic-gauntlet';
export const BUILDER_CRITIC_GAUNTLET_STEP_IDS = [
  'gauntlet-lead',
  'gauntlet-builder',
  'gauntlet-critic',
] as const;
export const BUILDER_CRITIC_GAUNTLET_EDGE_IDS = [
  'gauntlet-lead-builder',
  'gauntlet-lead-critic',
  'gauntlet-builder-critic',
  'gauntlet-critic-builder-feedback',
] as const;

export type BuilderCriticGauntletStepId =
  typeof BUILDER_CRITIC_GAUNTLET_STEP_IDS[number];
export type BuilderCriticGauntletEdgeId =
  typeof BUILDER_CRITIC_GAUNTLET_EDGE_IDS[number];

export interface BuilderCriticGauntletSelection {
  readonly selected_step_ids: readonly BuilderCriticGauntletStepId[];
  readonly selected_edge_ids: readonly BuilderCriticGauntletEdgeId[];
  /** Supplied by the authenticated Hub catalog, never by the preset. */
  readonly critic_benchmark_context_binding?: CaseFlowContextBindingRef;
  readonly critic_personality_binding?: CaseFlowPersonalityBindingRef;
}

export type CaseFlowAgentPresetIssueCode =
  | 'agent_preset_invalid'
  | 'agent_preset_selection_invalid'
  | 'agent_preset_id_conflict'
  | 'agent_preset_binding_required';

export interface CaseFlowAgentPresetIssue {
  readonly code: CaseFlowAgentPresetIssueCode | CaseFlowAgentCanvasIssue['code'];
  readonly path: string;
  readonly message: string;
}

export type CaseFlowAgentPresetResult =
  | { readonly ok: true; readonly value: VpGraph; readonly issues: readonly [] }
  | { readonly ok: false; readonly issues: readonly CaseFlowAgentPresetIssue[] };

/**
 * Adds an explicitly selected, closed subset of the Builder/Critic preset.
 * Existing graph objects are never rewritten, and every concrete binding is
 * checked against the caller's current authoritative Hub catalog.
 */
export function applyBuilderCriticGauntletPreset(
  graph: VpGraph,
  preset: VpGraph,
  selection: BuilderCriticGauntletSelection,
  catalog: CaseFlowAgentBindingCatalog,
): CaseFlowAgentPresetResult {
  const presetIssue = validatePresetContract(preset as unknown);
  if (presetIssue) return failure(presetIssue);
  const selectionIssue = validateSelectionContract(selection as unknown);
  if (selectionIssue) return failure(selectionIssue);
  const targetIssue = validateTargetGraphContract(graph as unknown);
  if (targetIssue) return failure(targetIssue);
  const presetExtension = parseAgentCanvasExtension(preset);
  if (!presetExtension.ok) return failure(...presetExtension.issues);

  const stepIds = new Set<string>(selection.selected_step_ids);
  const edgeIds = new Set<string>(selection.selected_edge_ids);
  if (stepIds.size !== selection.selected_step_ids.length
    || edgeIds.size !== selection.selected_edge_ids.length) {
    return failure(issue(
      'agent_preset_selection_invalid',
      '/selection',
      'Preset step and edge selections must not contain duplicate IDs.',
    ));
  }

  const presetSteps = new Map(preset.steps.map(step => [step.id, step]));
  const presetEdges = new Map(preset.edges.map(edge => [edge.id, edge]));
  for (const stepId of stepIds) {
    if (!presetSteps.has(stepId)) {
      return failure(issue(
        'agent_preset_selection_invalid',
        `/selection/selected_step_ids/${stepId}`,
        `Step "${stepId}" is not part of the Builder/Critic preset.`,
      ));
    }
  }
  for (const edgeId of edgeIds) {
    const edge = presetEdges.get(edgeId);
    if (!edge) {
      return failure(issue(
        'agent_preset_selection_invalid',
        `/selection/selected_edge_ids/${edgeId}`,
        `Edge "${edgeId}" is not part of the Builder/Critic preset.`,
      ));
    }
    if (!stepIds.has(edge.source) || !stepIds.has(edge.target)) {
      return failure(issue(
        'agent_preset_selection_invalid',
        `/selection/selected_edge_ids/${edgeId}`,
        `Both endpoints of selected edge "${edgeId}" must also be selected.`,
      ));
    }
  }

  const targetStepIds = new Set(graph.steps.map(step => step.id));
  const targetEdgeIds = new Set(graph.edges.map(edge => edge.id));
  const stepConflict = selection.selected_step_ids.find(id => targetStepIds.has(id));
  if (stepConflict) {
    return failure(issue(
      'agent_preset_id_conflict',
      `/steps/${stepConflict}`,
      `Target graph already contains step ID "${stepConflict}".`,
    ));
  }
  const edgeConflict = selection.selected_edge_ids.find(id => targetEdgeIds.has(id));
  if (edgeConflict) {
    return failure(issue(
      'agent_preset_id_conflict',
      `/edges/${edgeConflict}`,
      `Target graph already contains edge ID "${edgeConflict}".`,
    ));
  }

  const selectedSteps = preset.steps.filter(step => stepIds.has(step.id));
  const selectedEdges = preset.edges.filter(edge => edgeIds.has(edge.id));
  for (const step of selectedSteps) {
    const bindingIssues = validateAgentStepBindings(step, catalog);
    if (bindingIssues.length) return failure(...bindingIssues);
  }

  if (!stepIds.has('gauntlet-critic')) {
    return appendSelection(
      graph,
      selectedSteps,
      selectedEdges,
      presetExtension.value.nodes ?? {},
    );
  }

  const contextBinding = selection.critic_benchmark_context_binding;
  if (!contextBinding) {
    return failure(issue(
      'agent_preset_binding_required',
      '/bindings/critic_benchmark_context',
      'The Critic requires an authorized read-only benchmark context source.',
    ));
  }
  if (contextBinding.resource_type !== 'context_source') {
    return failure(issue(
      'agent_preset_binding_required',
      '/bindings/critic_benchmark_context/resource_type',
      'The Critic benchmark binding must reference a context source.',
    ));
  }

  const candidate = appendSelection(
    graph,
    selectedSteps,
    selectedEdges,
    presetExtension.value.nodes ?? {},
  );
  if (!candidate.ok) return candidate;
  const bound = setAgentBindings(candidate.value, 'gauntlet-critic', {
    context_bindings: [contextBinding],
    ...(selection.critic_personality_binding
      ? { personality_binding: selection.critic_personality_binding }
      : {}),
  }, catalog);
  return bound.ok ? success(bound.value) : failure(...bound.issues);
}

function appendSelection(
  graph: VpGraph,
  selectedSteps: readonly VpStep[],
  selectedEdges: readonly VpEdge[],
  presetPresentations: Readonly<Record<string, CaseFlowAgentNodePresentationV1>>,
): CaseFlowAgentPresetResult {
  if (!selectedSteps.length && !selectedEdges.length) return success(graph);

  const targetExtension = parseAgentCanvasExtension(graph);
  if (!targetExtension.ok) return failure(...targetExtension.issues);
  let selectedPresentations: Record<string, CaseFlowAgentNodePresentationV1>;
  let clonedSteps: VpStep[];
  let clonedEdges: VpEdge[];
  try {
    selectedPresentations = Object.fromEntries(selectedSteps.flatMap(step => {
      const presentation = presetPresentations[step.id];
      return presentation === undefined
        ? []
        : [[step.id, structuredClone(presentation)]];
    }));
    clonedSteps = selectedSteps.map(step => structuredClone(step));
    clonedEdges = selectedEdges.map(edge => structuredClone(edge));
  } catch {
    return failure(issue(
      'agent_preset_invalid',
      '/preset',
      'The selected preset data is not safely cloneable graph data.',
    ));
  }
  const presentationConflict = Object.keys(selectedPresentations).find(stepId =>
    Object.hasOwn(targetExtension.value.nodes ?? {}, stepId));
  if (presentationConflict) {
    return failure(issue(
      'agent_preset_id_conflict',
      `/extensions/ananta.caseflow.agent-canvas/nodes/${presentationConflict}`,
      `Target graph already contains presentation data for step ID "${presentationConflict}".`,
    ));
  }

  const serialized = serializeAgentCanvasExtension(graph, {
    ...targetExtension.value,
    nodes: {
      ...(targetExtension.value.nodes ?? {}),
      ...selectedPresentations,
    },
  });
  if (!serialized.ok) return failure(...serialized.issues);
  return success({
    ...serialized.value,
    steps: [...graph.steps, ...clonedSteps],
    edges: [...graph.edges, ...clonedEdges],
  });
}

function validatePresetContract(presetValue: unknown): CaseFlowAgentPresetIssue | undefined {
  const definition = validateVpGraphDefinition(presetValue, { path: '/preset' });
  if (!definition.ok) return invalidPreset(
    definition.issues[0].path,
    definition.issues[0].message,
  );
  const preset = presetValue as VpGraph;

  if (preset.id !== BUILDER_CRITIC_GAUNTLET_PRESET_ID
    || hasDuplicateIds(preset.steps)
    || hasDuplicateIds(preset.edges)
    || !hasExactIds(preset.steps, BUILDER_CRITIC_GAUNTLET_STEP_IDS)
    || !hasExactIds(preset.edges, BUILDER_CRITIC_GAUNTLET_EDGE_IDS)) {
    return issue(
      'agent_preset_invalid',
      '/preset',
      'The Builder/Critic preset identity or graph IDs are invalid.',
    );
  }

  const expectedSteps: Readonly<Record<
    BuilderCriticGauntletStepId,
    Readonly<{ role: string; kind: string }>
  >> = {
    'gauntlet-lead': { role: 'lead', kind: 'plan_only' },
    'gauntlet-builder': { role: 'developer', kind: 'patch_propose' },
    'gauntlet-critic': { role: 'critic', kind: 'review' },
  };
  if (BUILDER_CRITIC_GAUNTLET_STEP_IDS.some(id => {
    const step = preset.steps.find(candidate => candidate.id === id);
    return step?.role !== expectedSteps[id].role
      || step.kind !== expectedSteps[id].kind;
  })) {
    return issue(
      'agent_preset_invalid',
      '/preset/steps',
      'The Builder/Critic preset must use its standard task kinds and catalog roles.',
    );
  }
  if (!preset.steps.find(step => step.id === 'gauntlet-critic')
    ?.policy_hints.includes('read_only')) {
    return issue(
      'agent_preset_invalid',
      '/preset/steps/gauntlet-critic/policy_hints',
      'The Critic preset must retain its read-only policy hint.',
    );
  }

  const expectedEdges: Readonly<Record<
    BuilderCriticGauntletEdgeId,
    Readonly<{ source: BuilderCriticGauntletStepId;
      target: BuilderCriticGauntletStepId; kind: string }>
  >> = {
    'gauntlet-lead-builder': {
      source: 'gauntlet-lead', target: 'gauntlet-builder', kind: 'always',
    },
    'gauntlet-lead-critic': {
      source: 'gauntlet-lead', target: 'gauntlet-critic', kind: 'always',
    },
    'gauntlet-builder-critic': {
      source: 'gauntlet-builder', target: 'gauntlet-critic', kind: 'on_success',
    },
    'gauntlet-critic-builder-feedback': {
      source: 'gauntlet-critic', target: 'gauntlet-builder', kind: 'back_edge',
    },
  };
  if (BUILDER_CRITIC_GAUNTLET_EDGE_IDS.some(id => {
    const edge = preset.edges.find(candidate => candidate.id === id);
    return edge?.source !== expectedEdges[id].source
      || edge.target !== expectedEdges[id].target
      || edge.condition?.kind !== expectedEdges[id].kind;
  })) {
    return issue(
      'agent_preset_invalid',
      '/preset/edges',
      'The Builder/Critic preset must use its standard fan-out and feedback edges.',
    );
  }
  const feedback = preset.edges.find(edge =>
    edge.id === 'gauntlet-critic-builder-feedback');
  const loopPolicy = feedback?.condition.loop_policy;
  if (!isRecord(loopPolicy)
    || loopPolicy['kind'] !== 'fixed'
    || loopPolicy['max_iterations'] !== 3) {
    return issue(
      'agent_preset_invalid',
      '/preset/edges/gauntlet-critic-builder-feedback/condition/loop_policy',
      'Critic feedback must use the fixed, three-iteration bounded loop policy.',
    );
  }

  const rawCanvas = preset.extensions?.['ananta.caseflow.agent-canvas'];
  const rawNodes = isRecord(rawCanvas) && isRecord(rawCanvas['nodes'])
    ? rawCanvas['nodes']
    : undefined;
  const expectedIcons: Readonly<Record<BuilderCriticGauntletStepId, string>> = {
    'gauntlet-lead': 'star',
    'gauntlet-builder': 'code',
    'gauntlet-critic': 'rule',
  };
  if (!rawNodes
    || !hasExactRecordKeys(rawNodes, BUILDER_CRITIC_GAUNTLET_STEP_IDS)
    || BUILDER_CRITIC_GAUNTLET_STEP_IDS.some(id =>
      !isRecord(rawNodes[id]) || rawNodes[id]['icon'] !== expectedIcons[id])) {
    return issue(
      'agent_preset_invalid',
      '/preset/extensions/ananta.caseflow.agent-canvas/nodes',
      'The preset must define exactly the Lead, Builder, and Critic presentations.',
    );
  }

  const marker = preset.metadata?.['ananta.caseflow.agent-preset'];
  const slots = isRecord(marker) ? marker['binding_slots'] : undefined;
  const requiredSlot = Array.isArray(slots) && slots.length === 1
    ? slots[0]
    : undefined;
  if (!isRecord(marker)
    || marker['schema'] !== 'ananta.caseflow.agent-preset/v1'
    || !isRecord(requiredSlot)
    || requiredSlot['slot'] !== 'critic_benchmark_context'
    || requiredSlot['step_id'] !== 'gauntlet-critic'
    || requiredSlot['resource_type'] !== 'context_source'
    || requiredSlot['required'] !== true
    || requiredSlot['access'] !== 'read_only'
    || !hasExactRecordKeys(requiredSlot, [
      'slot',
      'step_id',
      'resource_type',
      'required',
      'access',
    ])) {
    return issue(
      'agent_preset_invalid',
      '/preset/metadata/ananta.caseflow.agent-preset',
      'The Critic read-only benchmark-context requirement is missing or invalid.',
    );
  }
  return undefined;
}

function validateSelectionContract(
  selection: unknown,
): CaseFlowAgentPresetIssue | undefined {
  if (!isRecord(selection)) {
    return invalidSelection('/selection', 'The preset selection must be an object.');
  }

  const stepIds = selection['selected_step_ids'];
  const edgeIds = selection['selected_edge_ids'];
  if (!Array.isArray(stepIds)) {
    return invalidSelection(
      '/selection/selected_step_ids',
      'Selected preset step IDs must be an array.',
    );
  }
  if (!Array.isArray(edgeIds)) {
    return invalidSelection(
      '/selection/selected_edge_ids',
      'Selected preset edge IDs must be an array.',
    );
  }

  const invalidStepIndex = stepIds.findIndex(value =>
    typeof value !== 'string'
    || !(BUILDER_CRITIC_GAUNTLET_STEP_IDS as readonly string[]).includes(value));
  if (invalidStepIndex >= 0) {
    return invalidSelection(
      `/selection/selected_step_ids/${invalidStepIndex}`,
      'Every selected step ID must be an allowed Builder/Critic preset ID.',
    );
  }
  const invalidEdgeIndex = edgeIds.findIndex(value =>
    typeof value !== 'string'
    || !(BUILDER_CRITIC_GAUNTLET_EDGE_IDS as readonly string[]).includes(value));
  if (invalidEdgeIndex >= 0) {
    return invalidSelection(
      `/selection/selected_edge_ids/${invalidEdgeIndex}`,
      'Every selected edge ID must be an allowed Builder/Critic preset ID.',
    );
  }

  if (new Set(stepIds).size !== stepIds.length
    || new Set(edgeIds).size !== edgeIds.length) {
    return invalidSelection(
      '/selection',
      'Preset step and edge selections must not contain duplicate IDs.',
    );
  }

  const contextBinding = selection['critic_benchmark_context_binding'];
  if (contextBinding !== undefined
    && (!isResourceBinding(contextBinding, ['context_profile', 'context_source']))) {
    return invalidSelection(
      '/selection/critic_benchmark_context_binding',
      'The Critic context binding selection is malformed.',
    );
  }
  const personalityBinding = selection['critic_personality_binding'];
  if (personalityBinding !== undefined
    && (!isResourceBinding(personalityBinding, ['agent_profile', 'instruction_layer']))) {
    return invalidSelection(
      '/selection/critic_personality_binding',
      'The Critic personality binding selection is malformed.',
    );
  }
  return undefined;
}

function validateTargetGraphContract(
  graph: unknown,
): CaseFlowAgentPresetIssue | undefined {
  const definition = validateVpGraphDefinition(graph, {
    path: '/graph',
    reject_runtime_payload: false,
  });
  if (definition.ok) return undefined;
  return issue(
    'agent_preset_invalid',
    definition.issues[0].path,
    `The target graph is malformed: ${definition.issues[0].message}`,
  );
}

function isResourceBinding(
  value: unknown,
  allowedTypes: readonly string[],
): boolean {
  return isRecord(value)
    && typeof value['resource_type'] === 'string'
    && allowedTypes.includes(value['resource_type'])
    && typeof value['resource_id'] === 'string'
    && value['resource_id'].length > 0;
}

function invalidPreset(path: string, message: string): CaseFlowAgentPresetIssue {
  return issue('agent_preset_invalid', path, message);
}

function invalidSelection(path: string, message: string): CaseFlowAgentPresetIssue {
  return issue('agent_preset_selection_invalid', path, message);
}

function hasDuplicateIds(values: readonly { readonly id: string }[]): boolean {
  return new Set(values.map(value => value.id)).size !== values.length;
}

function hasExactIds(
  values: readonly { readonly id: string }[],
  expected: readonly string[],
): boolean {
  return values.length === expected.length
    && expected.every(id => values.some(value => value.id === id));
}

function hasExactRecordKeys(
  value: Readonly<Record<string, unknown>>,
  expected: readonly string[],
): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every(key => Object.hasOwn(value, key));
}

function issue(
  code: CaseFlowAgentPresetIssueCode,
  path: string,
  message: string,
): CaseFlowAgentPresetIssue {
  return { code, path, message };
}

function success(value: VpGraph): CaseFlowAgentPresetResult {
  return { ok: true, value, issues: [] };
}

function failure(
  ...issues: readonly CaseFlowAgentPresetIssue[]
): CaseFlowAgentPresetResult {
  return { ok: false, issues };
}
