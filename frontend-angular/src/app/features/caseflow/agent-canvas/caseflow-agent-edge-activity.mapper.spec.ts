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
      runtimeEvidence(),
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
      runtimeEvidence(),
    );

    expect(result.active_edge_ids).toEqual([]);
  });

  it('fails closed for another graph, an unverified catalog, or conflicting duplicate identity', () => {
    const graphEdge = edge('edge-ab', 'agent-a', 'agent-b');
    const active = traceEdge('edge-ab', 'agent-a', 'agent-b', 'active', 'verified');

    expect(projectCaseFlowAgentEdgeActivity(
      'another-graph', [graphEdge], readModel([active]), runtimeEvidence(),
    )).toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a', [graphEdge], { ...readModel([active]), catalog_verification_status: 'unverified' }, runtimeEvidence(),
    )).toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a', [graphEdge], readModel([active, active]), runtimeEvidence(),
    ).active_edge_ids).toEqual([]);
  });

  it('fails closed without the exact expected top-level run identity', () => {
    const graphEdge = edge('edge-ab', 'agent-a', 'agent-b');
    const model = readModel([traceEdge(
      'edge-ab', 'agent-a', 'agent-b', 'active', 'verified',
    )]);

    expect(projectCaseFlowAgentEdgeActivity('graph-a', [graphEdge], model, null))
      .toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity('graph-a', [graphEdge], model, runtimeEvidence('running', 'run-other')))
      .toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity('graph-a', [graphEdge], model, runtimeEvidence()))
      .toEqual({ available: true, active_edge_ids: ['edge-ab'] });
  });

  it('fails closed for a foreign runtime workflow even when the run ID matches', () => {
    const graphEdge = edge('edge-ab', 'agent-a', 'agent-b');
    const model = readModel([
      traceEdge('edge-ab', 'agent-a', 'agent-b', 'active', 'verified'),
    ]);

    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a',
      [graphEdge],
      model,
      runtimeEvidence('running', 'run-a', 'graph-b'),
    )).toEqual({ available: false, active_edge_ids: [] });
    expect(projectCaseFlowAgentEdgeActivity(
      'graph-a',
      [graphEdge],
      model,
      runtimeEvidence('running', 'run-a', 'graph-a', 'graph-b'),
    )).toEqual({ available: false, active_edge_ids: [] });
  });

  it.each(['completed', 'failed', 'cancelled', 'skipped'])(
    'suppresses stale active trace evidence after exact run status %s',
    status => {
      const graphEdge = edge('edge-ab', 'agent-a', 'agent-b');

      expect(projectCaseFlowAgentEdgeActivity(
        'graph-a',
        [graphEdge],
        readModel([traceEdge('edge-ab', 'agent-a', 'agent-b', 'active', 'verified')]),
        runtimeEvidence(status),
      )).toEqual({ available: true, active_edge_ids: [] });
    },
  );
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

function runtimeEvidence(
  overallStatus = 'running',
  runId = 'run-a',
  workflowId = 'graph-a',
  processId: string | undefined = 'graph-a',
): Pick<
  import('../../visual-process/visual-process-api.service').VpRuntimeOverlay,
  'run_id' | 'workflow_id' | 'process_id' | 'overall_status'
> {
  return {
    run_id: runId,
    workflow_id: workflowId,
    process_id: processId,
    overall_status: overallStatus,
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
