import type {
  VpEdge,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import {
  type CaseFlowAgentCanvasResult,
  agentCanvasFailure,
  agentCanvasSuccess,
} from './caseflow-agent-canvas.models';

export interface CaseFlowAgentNeighborRelation {
  readonly edge_id: string;
  readonly peer_step_id: string;
  readonly peer_label: string;
  readonly peer_role: string;
}

export interface CaseFlowAgentLoopRelation {
  readonly edge_id: string;
  readonly label?: string;
}

export interface CaseFlowAgentNeighborhood {
  readonly step_id: string;
  readonly parents: readonly CaseFlowAgentNeighborRelation[];
  readonly children: readonly CaseFlowAgentNeighborRelation[];
  readonly loops: readonly CaseFlowAgentLoopRelation[];
}

/**
 * Derives the selected step's directed neighborhood from canonical VpEdges.
 * Self-loops deliberately have their own collection: calling a step its own
 * parent or child would erase the semantic distinction the inspector needs.
 */
export function selectCaseFlowAgentNeighborhood(
  graph: VpGraph,
  selectedStepId: string,
): CaseFlowAgentCanvasResult<CaseFlowAgentNeighborhood> {
  const selectedStep = graph.steps.find(step => step.id === selectedStepId);
  if (!selectedStep) {
    return missingStepFailure(selectedStepId, `/steps/${selectedStepId}`);
  }

  const stepsById = new Map(graph.steps.map(step => [step.id, step]));
  const parents: CaseFlowAgentNeighborRelation[] = [];
  const children: CaseFlowAgentNeighborRelation[] = [];
  const loops: CaseFlowAgentLoopRelation[] = [];

  for (const edge of graph.edges) {
    if (edge.source === selectedStepId && edge.target === selectedStepId) {
      loops.push(loopRelation(edge));
      continue;
    }

    if (edge.target === selectedStepId) {
      const parent = stepsById.get(edge.source);
      if (!parent) {
        return missingStepFailure(
          edge.source,
          `/edges/${edge.id}/source`,
          edge.id,
        );
      }
      parents.push(neighborRelation(edge, parent));
    }

    if (edge.source === selectedStepId) {
      const child = stepsById.get(edge.target);
      if (!child) {
        return missingStepFailure(
          edge.target,
          `/edges/${edge.id}/target`,
          edge.id,
        );
      }
      children.push(neighborRelation(edge, child));
    }
  }

  return agentCanvasSuccess({
    step_id: selectedStep.id,
    parents: parents.sort(compareNeighborRelations),
    children: children.sort(compareNeighborRelations),
    loops: loops.sort((left, right) => compareText(left.edge_id, right.edge_id)),
  });
}

function neighborRelation(
  edge: VpEdge,
  peer: VpStep,
): CaseFlowAgentNeighborRelation {
  return {
    edge_id: edge.id,
    peer_step_id: peer.id,
    peer_label: peer.label,
    peer_role: peer.role?.trim() || 'custom',
  };
}

function loopRelation(edge: VpEdge): CaseFlowAgentLoopRelation {
  return {
    edge_id: edge.id,
    ...(edge.label ? { label: edge.label } : {}),
  };
}

function compareNeighborRelations(
  left: CaseFlowAgentNeighborRelation,
  right: CaseFlowAgentNeighborRelation,
): number {
  return compareText(left.peer_step_id, right.peer_step_id)
    || compareText(left.edge_id, right.edge_id);
}

function compareText(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function missingStepFailure(
  stepId: string,
  path: string,
  edgeId?: string,
): CaseFlowAgentCanvasResult<never> {
  return agentCanvasFailure({
    code: 'agent_step_not_found',
    path,
    message: edgeId
      ? `Edge "${edgeId}" references unknown step "${stepId}".`
      : `Agent step "${stepId}" was not found.`,
  });
}
