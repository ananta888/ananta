import type { CaseFlowEdgeTraceReadModel } from './caseflow-edge-trace.models';
import type { CaseFlowAgentCanvasEdgeProjection } from './caseflow-agent-canvas.models';
import type { VpRuntimeOverlay } from '../../visual-process/visual-process-api.service';

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
  runtime: Pick<
    VpRuntimeOverlay,
    'run_id' | 'workflow_id' | 'process_id' | 'overall_status'
  > | null | undefined,
): CaseFlowAgentEdgeActivityProjection {
  if (!readModel
    || readModel.workflow_id !== graphId
    || !runtime?.run_id
    || runtime.workflow_id !== graphId
    || (runtime.process_id !== undefined && runtime.process_id !== graphId)
    || readModel.run_id !== runtime.run_id
    || readModel.catalog_verification_status !== 'verified') {
    return unavailableProjection();
  }

  // Trace and status are independently refreshed Hub read models. A delayed
  // trace may still carry the last active edge after the exact run is already
  // terminal. Runtime state is therefore the final activity fence.
  if (!runtimeMayHaveActiveEdges(runtime.overall_status)) {
    return Object.freeze({ available: true, active_edge_ids: Object.freeze([]) });
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

function runtimeMayHaveActiveEdges(status: string): boolean {
  return new Set([
    'created',
    'queued',
    'pending',
    'waiting',
    'running',
    'in_progress',
    'cancel_requested',
    'paused',
    'waiting_for_approval',
    'waiting_for_review',
  ]).has(String(status || '').trim().toLowerCase());
}

function unavailableProjection(): CaseFlowAgentEdgeActivityProjection {
  return Object.freeze({ available: false, active_edge_ids: Object.freeze([]) });
}
