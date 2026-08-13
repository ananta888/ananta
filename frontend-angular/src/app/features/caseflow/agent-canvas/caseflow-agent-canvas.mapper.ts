import type { VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_AGENT_BINDINGS_METADATA,
  CaseFlowAgentCanvasIssue,
  CaseFlowAgentCanvasNodeProjection,
  CaseFlowAgentCanvasProjection,
  CaseFlowAgentCanvasResult,
  agentCanvasFailure,
  agentCanvasSuccess,
  parseAgentBindings,
  parseAgentCanvasExtension,
  readModelRoutingBindings,
} from './caseflow-agent-canvas.models';
import {
  isAllowedCaseFlowAgentIcon,
  isCaseFlowAgentRoleId,
  roleDefinitionFor,
} from './caseflow-role-catalog';

/**
 * Builds a read-only view. It never repairs or mutates malformed definitions;
 * callers must use a focused graph command and persist the returned VpGraph.
 */
export function projectAgentCanvas(
  graph: VpGraph,
): CaseFlowAgentCanvasResult<CaseFlowAgentCanvasProjection> {
  const extensionResult = parseAgentCanvasExtension(graph);
  if (!extensionResult.ok) return agentCanvasFailure(...extensionResult.issues);
  const extension = extensionResult.value;
  const agentSteps = graph.steps.filter(step => isAgentCapableStep(
    step,
    extension.nodes?.[step.id] !== undefined,
  ));
  const agentStepIds = new Set(agentSteps.map(step => step.id));
  const canvasEdges = graph.edges.filter(edge =>
    agentStepIds.has(edge.source) && agentStepIds.has(edge.target));

  const issues: CaseFlowAgentCanvasIssue[] = [];
  const nodes: CaseFlowAgentCanvasNodeProjection[] = [];
  for (const step of agentSteps) {
    const bindingsResult = parseAgentBindings(step);
    if (!bindingsResult.ok) {
      issues.push(...bindingsResult.issues);
      continue;
    }
    const routingResult = readModelRoutingBindings(step);
    if (!routingResult.ok) {
      issues.push(...routingResult.issues);
      continue;
    }

    const role = step.role?.trim() || 'custom';
    const rolePreset = isCaseFlowAgentRoleId(role) ? role : 'custom';
    const presentation = extension.nodes?.[step.id];
    const icon = presentation?.icon || roleDefinitionFor(rolePreset).default_icon;
    if (!isAllowedCaseFlowAgentIcon(icon)) {
      issues.push({
        code: 'agent_icon_not_allowed',
        path: `/extensions/ananta.caseflow.agent-canvas/nodes/${step.id}/icon`,
        message: `Icon "${icon}" for step "${step.id}" is not allowlisted.`,
      });
      continue;
    }

    nodes.push({
      step_id: step.id,
      label: step.label,
      role,
      role_preset: rolePreset,
      icon,
      position: { x: step.position.x, y: step.position.y },
      configuration: {
        ...(step.agent_skill_profile_id
          ? { skill_profile_id: step.agent_skill_profile_id }
          : {}),
        ...(bindingsResult.value.personality_binding
          ? { personality_binding: bindingsResult.value.personality_binding }
          : {}),
        context_bindings: bindingsResult.value.context_bindings ?? [],
        model_routing: routingResult.value,
        policy_hints: step.policy_hints,
        human_gate: step.gate,
      },
      incoming_edge_ids: canvasEdges
        .filter(edge => edge.target === step.id)
        .map(edge => edge.id),
      outgoing_edge_ids: canvasEdges
        .filter(edge => edge.source === step.id)
        .map(edge => edge.id),
    });
  }
  if (issues.length) return agentCanvasFailure(...issues);

  return agentCanvasSuccess({
    canonical_graph: graph,
    nodes,
    edges: canvasEdges.map(edge => ({
      edge_id: edge.id,
      source_step_id: edge.source,
      target_step_id: edge.target,
      ...(edge.label ? { label: edge.label } : {}),
      loop: edge.source === edge.target,
      feedback: edge.source !== edge.target && edge.condition?.kind === 'back_edge',
      reverse_edge_ids: canvasEdges
        .filter(candidate =>
          edge.source !== edge.target
          && candidate.id !== edge.id
          && candidate.source === edge.target
          && candidate.target === edge.source)
        .map(candidate => candidate.id),
    })),
  });
}

/**
 * Lossless Canvas -> Graph roundtrip: the projection is not a second editable
 * graph model, so serialization returns its canonical VpGraph unchanged.
 */
export function graphFromAgentCanvasProjection(
  projection: CaseFlowAgentCanvasProjection,
): VpGraph {
  return projection.canonical_graph;
}

export function isAgentCapableStep(step: VpStep, hasPresentation = false): boolean {
  return Boolean(
    step.role?.trim()
    || step.agent_skill_profile_id?.trim()
    || step.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA]
    || hasPresentation,
  );
}
