import type { CaseFlowEdgeTraceReadModel } from './caseflow-edge-trace.models';
import type { CaseFlowAgentCanvasEdgeProjection } from './caseflow-agent-canvas.models';

export interface CaseFlowAgentEdgeActivityProjection {
  readonly available: boolean;
  readonly active_edge_ids: readonly string[];
}

/**
 * Projects only Hub-verified directional edge activity onto the current graph.
 * Step runtime is intentionally not consulted: current steps cannot prove an
 * edge correlation.
 */
export function projectCaseFlowAgentEdgeActivity(
  graphId: string,
  graphEdges: readonly CaseFlowAgentCanvasEdgeProjection[],
  readModel: CaseFlowEdgeTraceReadModel | null | undefined,
): CaseFlowAgentEdgeActivityProjection {
  if (!readModel
    || readModel.workflow_id !== graphId
    || readModel.catalog_verification_status !== 'verified') {
    return unavailableProjection();
  }

  const activeEdgeIds: string[] = [];
  for (const graphEdge of graphEdges) {
    const matches = readModel.edges.filter(edge =>
      edge.edge_id === graphEdge.edge_id
      && edge.source_step_id === graphEdge.source_step_id
      && edge.target_step_id === graphEdge.target_step_id);
    if (matches.length !== 1) continue;
    const [edge] = matches;
    if (edge.verification_status === 'verified' && edge.activity_status === 'active') {
      activeEdgeIds.push(graphEdge.edge_id);
    }
  }

  return Object.freeze({
    available: true,
    active_edge_ids: Object.freeze(activeEdgeIds),
  });
}

function unavailableProjection(): CaseFlowAgentEdgeActivityProjection {
  return Object.freeze({ available: false, active_edge_ids: Object.freeze([]) });
}
