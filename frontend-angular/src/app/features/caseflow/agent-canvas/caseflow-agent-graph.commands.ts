import type { VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_AGENT_BINDINGS_METADATA,
  CaseFlowAgentBindingsV1,
  CaseFlowAgentCanvasIssue,
  CaseFlowAgentCanvasResult,
  CaseFlowContextBindingRef,
  CaseFlowContextResourceType,
  CaseFlowModelRoutingBindings,
  CaseFlowPersonalityBindingRef,
  CaseFlowPersonalityResourceType,
  agentCanvasFailure,
  agentCanvasSuccess,
  isRecord,
  parseAgentBindings,
  parseAgentCanvasExtension,
  readModelRoutingBindings,
  serializeAgentBindings,
  serializeAgentCanvasExtension,
} from './caseflow-agent-canvas.models';
import {
  CaseFlowAgentRoleSelection,
  validateRoleSelection,
} from './caseflow-role-catalog';

export interface CaseFlowAgentBindingCatalog {
  skill_profile_ids: readonly string[];
  personality_resource_ids: Readonly<Record<CaseFlowPersonalityResourceType, readonly string[]>>;
  context_resource_ids: Readonly<Record<CaseFlowContextResourceType, readonly string[]>>;
  model_profile_ids: readonly string[];
  model_role_ids: readonly string[];
  fallback_group_ids: readonly string[];
}

export interface CaseFlowAgentBindingDraft {
  skill_profile_id?: string | null;
  personality_binding?: CaseFlowPersonalityBindingRef | null;
  context_bindings?: readonly CaseFlowContextBindingRef[] | null;
  model_routing?: {
    model_role?: string | null;
    preferred_profile_id?: string | null;
    fallback_group_id?: string | null;
  } | null;
}

/** Updates only VpStep.role and the versioned presentation icon. */
export function setAgentRoleAndIcon(
  graph: VpGraph,
  stepId: string,
  selection: CaseFlowAgentRoleSelection,
): CaseFlowAgentCanvasResult<VpGraph> {
  const stepIndex = graph.steps.findIndex(step => step.id === stepId);
  if (stepIndex < 0) return stepNotFound(stepId);

  const roleResult = validateRoleSelection(selection);
  if (!roleResult.ok) return agentCanvasFailure(...roleResult.issues);
  const extensionResult = parseAgentCanvasExtension(graph);
  if (!extensionResult.ok) return agentCanvasFailure(...extensionResult.issues);

  const currentPresentation = extensionResult.value.nodes?.[stepId] ?? {};
  const extension = {
    ...extensionResult.value,
    nodes: {
      ...(extensionResult.value.nodes ?? {}),
      [stepId]: {
        ...currentPresentation,
        icon: roleResult.value.icon,
      },
    },
  };
  const graphWithPresentation = serializeAgentCanvasExtension(graph, extension);
  if (!graphWithPresentation.ok) return graphWithPresentation;

  const steps = [...graph.steps];
  steps[stepIndex] = {
    ...graph.steps[stepIndex],
    role: roleResult.value.canonical_role,
  };
  return agentCanvasSuccess({ ...graphWithPresentation.value, steps });
}

/**
 * Applies only reference-bearing fields owned by the compact inspector. All
 * unrelated step fields and unknown forward-compatible metadata survive.
 */
export function setAgentBindings(
  graph: VpGraph,
  stepId: string,
  draft: CaseFlowAgentBindingDraft,
  catalog: CaseFlowAgentBindingCatalog,
): CaseFlowAgentCanvasResult<VpGraph> {
  const stepIndex = graph.steps.findIndex(step => step.id === stepId);
  if (stepIndex < 0) return stepNotFound(stepId);
  const step = graph.steps[stepIndex];

  const existingBindingsResult = parseAgentBindings(step);
  if (!existingBindingsResult.ok) return agentCanvasFailure(...existingBindingsResult.issues);
  const existingRoutingResult = readModelRoutingBindings(step);
  if (!existingRoutingResult.ok) return agentCanvasFailure(...existingRoutingResult.issues);

  const draftIssues = validateBindingDraft(draft, catalog);
  if (draftIssues.length) return agentCanvasFailure(...draftIssues);

  let updatedStep: VpStep = { ...step };
  if (Object.hasOwn(draft, 'skill_profile_id')) {
    if (draft.skill_profile_id === null) {
      delete updatedStep.agent_skill_profile_id;
    } else {
      updatedStep.agent_skill_profile_id = draft.skill_profile_id;
    }
  }

  if (Object.hasOwn(draft, 'personality_binding')
    || Object.hasOwn(draft, 'context_bindings')) {
    const bindings: CaseFlowAgentBindingsV1 = { ...existingBindingsResult.value };
    if (Object.hasOwn(draft, 'personality_binding')) {
      if (draft.personality_binding === null) {
        delete bindings.personality_binding;
      } else if (draft.personality_binding) {
        bindings.personality_binding = mergePersonalityBinding(
          bindings.personality_binding,
          draft.personality_binding,
        );
      }
    }
    if (Object.hasOwn(draft, 'context_bindings')) {
      if (draft.context_bindings === null) {
        delete bindings.context_bindings;
      } else {
        bindings.context_bindings = (draft.context_bindings ?? []).map(binding =>
          mergeContextBinding(bindings.context_bindings ?? [], binding));
      }
    }
    const serializedBindings = serializeAgentBindings(updatedStep, bindings);
    if (!serializedBindings.ok) return agentCanvasFailure(...serializedBindings.issues);
    updatedStep = serializedBindings.value;
  }

  if (Object.hasOwn(draft, 'model_routing')) {
    const metadata = { ...(updatedStep.metadata ?? {}) };
    const existingRouting = isRecord(metadata['model_routing'])
      ? { ...metadata['model_routing'] }
      : {};
    const routingDraft = draft.model_routing;
    for (const field of MODEL_ROUTING_REFERENCE_FIELDS) {
      if (routingDraft !== null && !Object.hasOwn(routingDraft ?? {}, field)) continue;
      const value = routingDraft?.[field];
      if (value === null || value === undefined) delete existingRouting[field];
      else existingRouting[field] = value;
    }
    if (Object.keys(existingRouting).length) metadata['model_routing'] = existingRouting;
    else delete metadata['model_routing'];
    updatedStep = { ...updatedStep, metadata };
  }

  const fullValidation = validateAgentStepBindings(updatedStep, catalog);
  if (fullValidation.length) return agentCanvasFailure(...fullValidation);

  const steps = [...graph.steps];
  steps[stepIndex] = updatedStep;
  return agentCanvasSuccess({ ...graph, steps });
}

/** Validates the complete effective step before a command may be persisted. */
export function validateAgentStepBindings(
  step: VpStep,
  catalog: CaseFlowAgentBindingCatalog,
): CaseFlowAgentCanvasIssue[] {
  const issues: CaseFlowAgentCanvasIssue[] = [];
  const bindingsResult = parseAgentBindings(step);
  if (!bindingsResult.ok) {
    issues.push(...bindingsResult.issues);
  } else {
    if (bindingsResult.value.personality_binding) {
      validatePersonalityBinding(
        bindingsResult.value.personality_binding,
        catalog,
        `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}/personality_binding`,
        issues,
      );
    }
    for (const [index, binding] of (bindingsResult.value.context_bindings ?? []).entries()) {
      validateContextBinding(
        binding,
        catalog,
        `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}/context_bindings/${index}`,
        issues,
      );
    }
    validateDuplicateContexts(
      bindingsResult.value.context_bindings ?? [],
      `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}/context_bindings`,
      issues,
    );
  }

  if (step.agent_skill_profile_id) {
    validateAllowedReference(
      step.agent_skill_profile_id,
      catalog.skill_profile_ids,
      `/steps/${step.id}/agent_skill_profile_id`,
      'skill profile',
      issues,
    );
  }

  const routingResult = readModelRoutingBindings(step);
  if (!routingResult.ok) {
    issues.push(...routingResult.issues);
  } else {
    validateModelRoutingBindings(
      routingResult.value,
      catalog,
      `/steps/${step.id}/metadata/model_routing`,
      issues,
    );
  }
  return issues;
}

export function validateBindingDraft(
  draft: CaseFlowAgentBindingDraft,
  catalog: CaseFlowAgentBindingCatalog,
): CaseFlowAgentCanvasIssue[] {
  const issues: CaseFlowAgentCanvasIssue[] = [];
  if (draft.skill_profile_id !== undefined && draft.skill_profile_id !== null) {
    validateAllowedReference(
      draft.skill_profile_id,
      catalog.skill_profile_ids,
      '/agent_skill_profile_id',
      'skill profile',
      issues,
    );
  }
  if (draft.personality_binding) {
    validatePersonalityBinding(draft.personality_binding, catalog, '/personality_binding', issues);
  }
  for (const [index, binding] of (draft.context_bindings ?? []).entries()) {
    validateContextBinding(binding, catalog, `/context_bindings/${index}`, issues);
  }
  if (draft.context_bindings) {
    validateDuplicateContexts(draft.context_bindings, '/context_bindings', issues);
  }
  if (draft.model_routing) {
    validateModelRoutingBindings(draft.model_routing, catalog, '/model_routing', issues);
  }
  return issues;
}

const MODEL_ROUTING_REFERENCE_FIELDS = [
  'model_role',
  'preferred_profile_id',
  'fallback_group_id',
] as const satisfies readonly (keyof CaseFlowModelRoutingBindings)[];

function validateModelRoutingBindings(
  routing: CaseFlowModelRoutingBindings,
  catalog: CaseFlowAgentBindingCatalog,
  path: string,
  issues: CaseFlowAgentCanvasIssue[],
): void {
  if (routing.model_role !== undefined && routing.model_role !== null) {
    validateAllowedReference(routing.model_role, catalog.model_role_ids, `${path}/model_role`, 'model role', issues);
  }
  if (routing.preferred_profile_id !== undefined && routing.preferred_profile_id !== null) {
    validateAllowedReference(
      routing.preferred_profile_id,
      catalog.model_profile_ids,
      `${path}/preferred_profile_id`,
      'model profile',
      issues,
    );
  }
  if (routing.fallback_group_id !== undefined && routing.fallback_group_id !== null) {
    validateAllowedReference(
      routing.fallback_group_id,
      catalog.fallback_group_ids,
      `${path}/fallback_group_id`,
      'fallback group',
      issues,
    );
  }
}

function validatePersonalityBinding(
  binding: CaseFlowPersonalityBindingRef,
  catalog: CaseFlowAgentBindingCatalog,
  path: string,
  issues: CaseFlowAgentCanvasIssue[],
): void {
  const allowed = catalog.personality_resource_ids[binding.resource_type];
  if (!allowed) {
    issues.push({
      code: 'agent_binding_reference_invalid',
      path: `${path}/resource_type`,
      message: `Personality resource type "${String(binding.resource_type)}" is not supported.`,
    });
    return;
  }
  validateAllowedReference(binding.resource_id, allowed, `${path}/resource_id`, binding.resource_type, issues);
}

function validateContextBinding(
  binding: CaseFlowContextBindingRef,
  catalog: CaseFlowAgentBindingCatalog,
  path: string,
  issues: CaseFlowAgentCanvasIssue[],
): void {
  const allowed = catalog.context_resource_ids[binding.resource_type];
  if (!allowed) {
    issues.push({
      code: 'agent_binding_reference_invalid',
      path: `${path}/resource_type`,
      message: `Context resource type "${String(binding.resource_type)}" is not supported.`,
    });
    return;
  }
  validateAllowedReference(binding.resource_id, allowed, `${path}/resource_id`, binding.resource_type, issues);
}

function validateAllowedReference(
  value: string,
  allowed: readonly string[],
  path: string,
  label: string,
  issues: CaseFlowAgentCanvasIssue[],
): void {
  if (typeof value !== 'string' || !value.trim()) {
    issues.push({
      code: 'agent_binding_reference_invalid',
      path,
      message: `The ${label} reference must be a non-empty string.`,
    });
    return;
  }
  if (!allowed.includes(value)) {
    issues.push({
      code: 'agent_binding_reference_not_allowed',
      path,
      message: `${label} reference "${value}" is not in the current Hub catalog.`,
    });
  }
}

function validateDuplicateContexts(
  bindings: readonly CaseFlowContextBindingRef[],
  path: string,
  issues: CaseFlowAgentCanvasIssue[],
): void {
  const seen = new Set<string>();
  for (const binding of bindings) {
    const key = `${binding.resource_type}:${binding.resource_id}`;
    if (!seen.has(key)) {
      seen.add(key);
      continue;
    }
    issues.push({
      code: 'agent_binding_reference_invalid',
      path,
      message: `Context binding "${key}" is duplicated.`,
    });
  }
}

function sanitizePersonalityBinding(
  binding: CaseFlowPersonalityBindingRef,
): CaseFlowPersonalityBindingRef {
  return {
    resource_type: binding.resource_type,
    resource_id: binding.resource_id,
  };
}

function sanitizeContextBinding(binding: CaseFlowContextBindingRef): CaseFlowContextBindingRef {
  return {
    resource_type: binding.resource_type,
    resource_id: binding.resource_id,
  };
}

function mergePersonalityBinding(
  existing: CaseFlowPersonalityBindingRef | undefined,
  updated: CaseFlowPersonalityBindingRef,
): CaseFlowPersonalityBindingRef {
  const known = sanitizePersonalityBinding(updated);
  return existing?.resource_type === known.resource_type
    && existing.resource_id === known.resource_id
    ? { ...existing, ...known }
    : known;
}

function mergeContextBinding(
  existing: readonly CaseFlowContextBindingRef[],
  updated: CaseFlowContextBindingRef,
): CaseFlowContextBindingRef {
  const known = sanitizeContextBinding(updated);
  const match = existing.find(binding =>
    binding.resource_type === known.resource_type
    && binding.resource_id === known.resource_id);
  return match ? { ...match, ...known } : known;
}

function stepNotFound(stepId: string): CaseFlowAgentCanvasResult<VpGraph> {
  return agentCanvasFailure({
    code: 'agent_step_not_found',
    path: `/steps/${stepId}`,
    message: `Agent step "${stepId}" does not exist.`,
  });
}
