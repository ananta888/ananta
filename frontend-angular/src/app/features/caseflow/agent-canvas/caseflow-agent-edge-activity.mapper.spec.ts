import { describe, expect, it } from 'vitest';

import type { CaseFlowEdgeTraceReadModel } from './caseflow-edge-trace.models';
import type { CaseFlowAgentCanvasEdgeProjection } from './caseflow-agent-canvas.models';
import { projectCaseFlowAgentEdgeActivity } from './caseflow-agent-edge-activity.mapper';

describe('projectCaseFlowAgentEdgeActivity', () => {
  it('activates only the exact verified direction reported by the Hub read model', () => {
    const result = projectCaseFlowAgentEdgeActivity(
      'graph-a',
      [edge('edge-ab', 'agent-a', 'agent-b'), edge('edge-ba', 'agent-b', 'agent-a')],
      readModel([
        traceEdge('edge-ab', 'agent-a', 'agent-b', 'active', 'verified'),
        traceEdge('edge-ba', 'agent-b', 'agent-a', 'inactive', 'verified'),
      ]),
    );

    expect(result).toEqual({ available: true, active_edge_ids: ['edge-ab'] });
  });

  it.each([
    ['unknown', 'verified'],
    ['active', 'unverified'],
  ] as const)('keeps %s/%s evidence static', (activity, verification) => {
    const result = projectCaseFlowAgentEdgeActivity(
      'graph-a',
      [edge('edge-ab', 'agent-a', 'agent-b')],
      readModel([traceEdge('edge-ab', 'agent-a', 'agent-b', activity, verification)]),
    );

    expect(result.active_edge_ids).toEqual([]);
  });

  it('fails closed for another graph, an unverified catalog, or conflicting duplicate identity', () => {
    const graphEdge = edge('edge-ab', 'agent-a', 'agent-b');
    const active = traceEdge('edge-ab', 'agent-a', 'agent-b', 'active', 'verified');

    expect(projectCaseFlowAgentEdgeActivity(
      'another-graph', [graphEdge], readModel([active]),
    )).toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a', [graphEdge], { ...readModel([active]), catalog_verification_status: 'unverified' },
    )).toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a', [graphEdge], readModel([active, active]),
    ).active_edge_ids).toEqual([]);
  });
});

function edge(
  edgeId: string,
  sourceStepId: string,
  targetStepId: string,
): CaseFlowAgentCanvasEdgeProjection {
  return {
    edge_id: edgeId,
    source_step_id: sourceStepId,
    target_step_id: targetStepId,
    label: '',
    reverse_edge_ids: [],
    loop: false,
    feedback: false,
  };
}

function traceEdge(
  edgeId: string,
  sourceStepId: string,
  targetStepId: string,
  activityStatus: 'active' | 'inactive' | 'unknown',
  verificationStatus: 'verified' | 'unverified',
): CaseFlowEdgeTraceReadModel['edges'][number] {
  return {
    edge_id: edgeId,
    source_step_id: sourceStepId,
    target_step_id: targetStepId,
    edge_kind: 'dependency',
    activity_status: activityStatus,
    verification_status: verificationStatus,
    reason_code: 'test',
    correlation_basis: verificationStatus === 'verified' ? 'explicit_edge_id' : 'unavailable',
    event_refs: [],
    trace_refs: [],
    messages: [],
    telemetry: [],
    limits: {
      messages_truncated: 0,
      telemetry_truncated: 0,
      event_refs_truncated: 0,
      trace_refs_truncated: 0,
    },
  };
}

function readModel(
  edges: CaseFlowEdgeTraceReadModel['edges'],
): CaseFlowEdgeTraceReadModel {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'graph-a',
    run_id: 'run-a',
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    edges,
    telemetry: {
      source_event_count: 0,
      processed_event_count: 0,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: 0,
      redaction_policy: 'user',
      messages_per_edge_limit: 64,
      telemetry_per_edge_limit: 128,
    },
  };
}
