import { describe, expect, it } from 'vitest';

import type {
  VpEdge,
  VpGraph,
  VpRuntimeOverlay,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import type {
  CaseFlowEdgeTraceProjection,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceTelemetryEntry,
} from './caseflow-edge-trace.models';
import { projectCaseFlowAgentNodeRuntimeTrace } from './caseflow-agent-node-runtime.mapper';

describe('projectCaseFlowAgentNodeRuntimeTrace', () => {
  it('keeps parent, child and loop directions separate by exact canonical identity', () => {
    const graph = agentGraph();
    const readModel = traceReadModel([
      traceEdge('selected-peer', 'selected', 'peer', 'child-message', telemetry('selected', 20)),
      traceEdge('selected-loop', 'selected', 'selected', 'loop-message', telemetry('selected', 30)),
      traceEdge('peer-selected', 'peer', 'selected', 'parent-message', telemetry('selected', 10)),
    ]);

    const projection = projectCaseFlowAgentNodeRuntimeTrace(
      graph, 'selected', graph.id, 'run-a', runtimeOverlay(), readModel,
    );

    expect(projection.available).toBe(true);
    expect(projection.parents.map(relationIdentity)).toEqual([
      ['peer-selected', 'peer', 'selected'],
    ]);
    expect(projection.children.map(relationIdentity)).toEqual([
      ['selected-peer', 'selected', 'peer'],
    ]);
    expect(projection.loops.map(relationIdentity)).toEqual([
      ['selected-loop', 'selected', 'selected'],
    ]);
    expect(projection.parents[0].messages.map(message => message.content))
      .toEqual(['parent-message']);
    expect(projection.children[0].messages.map(message => message.content))
      .toEqual(['child-message']);
    expect(projection.hub_ordered_relations.map(relation => relation.edge_id)).toEqual([
      'selected-peer', 'selected-loop', 'peer-selected',
    ]);
  });

  it('allowlists graph/run-bound runtime metrics and summarizes only Hub trace evidence', () => {
    const graph = agentGraph();
    const runtime = runtimeOverlay();
    runtime.steps.selected = {
      ...runtime.steps.selected,
      status: 'running',
      started_at: 5,
      duration_ms: 15,
      selected_model_profile_id: 'model-profile-a',
      selected_provider_id: 'provider-a',
      selected_model: 'model-a',
      error: 'raw runtime error must not become the allowed trace error',
      gate: { secret: 'must-not-cross' },
    };
    const childFailure = traceEdge(
      'selected-peer',
      'selected',
      'peer',
      'result',
      telemetry('selected', 50, {
        status: 'failed',
        trace_ref: 'trace-error',
        event_ref: 'event-error',
        error: 'allowed redacted Hub error',
        token_usage: { input_tokens: 3 },
      }),
    );
    const peerOnly = telemetry('peer', 100, {
      status: 'failed',
      error: 'peer-only error must not become the selected agent error',
      token_usage: { input_tokens: 5, api_key: 9 } as unknown as
        CaseFlowEdgeTraceTelemetryEntry['token_usage'],
    });
    const readModel = traceReadModel([
      traceEdge(
        'peer-selected',
        'peer',
        'selected',
        'hello',
        telemetry('selected', 40, {
          status: 'running',
          trace_ref: 'trace-current',
          event_ref: 'event-current',
        }),
      ),
      { ...childFailure, telemetry: [...childFailure.telemetry, peerOnly] },
    ]);
    const graphBefore = JSON.stringify(graph);
    const runtimeBefore = JSON.stringify(runtime);
    const traceBefore = JSON.stringify(readModel);

    const projection = projectCaseFlowAgentNodeRuntimeTrace(
      graph, 'selected', graph.id, 'run-a', runtime, readModel,
    );

    expect(projection.runtime).toEqual({
      status: 'running',
      current: true,
      started_at: 5,
      finished_at: null,
      duration_ms: 15,
      selected_model_profile_id: 'model-profile-a',
      selected_provider_id: 'provider-a',
      selected_model: 'model-a',
    });
    expect(projection.current_activity).toEqual({
      edge_id: 'selected-peer',
      source_step_id: 'selected',
      target_step_id: 'peer',
      status: 'failed',
      occurred_at: 50,
      event_ref: 'event-error',
      trace_ref: 'trace-error',
    });
    expect(projection.last_error).toEqual({
      edge_id: 'selected-peer',
      source_step_id: 'selected',
      target_step_id: 'peer',
      status: 'failed',
      occurred_at: 50,
      event_ref: 'event-error',
      trace_ref: 'trace-error',
      error: 'allowed redacted Hub error',
    });
    expect(JSON.stringify(projection)).not.toContain('raw runtime error');
    expect(JSON.stringify(projection)).not.toContain('must-not-cross');
    expect(JSON.stringify(projection.last_error)).not.toContain('peer-only');
    expect(JSON.stringify(projection)).not.toContain('peer-only');
    expect(JSON.stringify(projection)).not.toContain('api_key');
    expect(JSON.stringify(graph)).toBe(graphBefore);
    expect(JSON.stringify(runtime)).toBe(runtimeBefore);
    expect(JSON.stringify(readModel)).toBe(traceBefore);
  });

  it('represents unavailable edge evidence and missing identifiers without inventing them', () => {
    const graph = agentGraph();
    const projection = projectCaseFlowAgentNodeRuntimeTrace(
      graph,
      'selected',
      graph.id,
      'run-a',
      runtimeOverlay(),
      traceReadModel([]),
    );

    expect(projection.available).toBe(true);
    expect(projection.parents[0]).toMatchObject({
      verification_status: 'unverified',
      activity_status: 'unknown',
      reason_code: 'caseflow_edge_projection_unavailable',
      messages: [],
      telemetry: [],
    });
    expect(projection.current_activity).toMatchObject({
      edge_id: null,
      event_ref: null,
      trace_ref: null,
      status: 'running',
    });
    expect(projection.last_error).toBeNull();
    expect(JSON.stringify(projection)).not.toContain('SRC_');
    expect(JSON.stringify(projection)).not.toContain('RUN_');
  });

  it('fails closed for mismatched graph, run, runtime step, and catalog scope', () => {
    const graph = agentGraph();
    const runtime = runtimeOverlay();
    const readModel = traceReadModel([]);
    const cases: Array<ReturnType<typeof projectCaseFlowAgentNodeRuntimeTrace>> = [
      projectCaseFlowAgentNodeRuntimeTrace(graph, 'selected', 'other-workflow', 'run-a', runtime, readModel),
      projectCaseFlowAgentNodeRuntimeTrace(graph, 'selected', graph.id, 'run-other', runtime, readModel),
      projectCaseFlowAgentNodeRuntimeTrace(
        graph,
        'selected',
        graph.id,
        'run-a',
        { ...runtime, steps: { selected: { ...runtime.steps.selected, step_id: 'peer' } } },
        readModel,
      ),
      projectCaseFlowAgentNodeRuntimeTrace(
        graph,
        'selected',
        graph.id,
        'run-a',
        { ...runtime, steps: { peer: runtime.steps.peer } },
        readModel,
      ),
      projectCaseFlowAgentNodeRuntimeTrace(
        graph,
        'selected',
        graph.id,
        'run-a',
        runtime,
        { ...readModel, catalog_verification_status: 'unverified' },
      ),
    ];

    expect(cases.every(projection => !projection.available)).toBe(true);
    for (const projection of cases) {
      expect(projection.runtime).toBeNull();
      expect(projection.hub_ordered_relations).toEqual([]);
    }
  });

  it('rejects duplicate exact Hub identities rather than merging their evidence', () => {
    const graph = agentGraph();
    const duplicate = traceEdge(
      'peer-selected', 'peer', 'selected', 'message', telemetry('selected', 10),
    );

    const projection = projectCaseFlowAgentNodeRuntimeTrace(
      graph,
      'selected',
      graph.id,
      'run-a',
      runtimeOverlay(),
      traceReadModel([duplicate, duplicate]),
    );

    expect(projection.available).toBe(false);
    expect(projection.reason_code).toBe('caseflow_node_trace_identity_ambiguous');
    expect(projection.parents).toEqual([]);
  });
});

function relationIdentity(
  relation: CaseFlowEdgeTraceProjection,
): readonly string[] {
  return [relation.edge_id, relation.source_step_id, relation.target_step_id];
}

function agentGraph(): VpGraph {
  return {
    id: 'graph-a',
    name: 'Agent graph',
    description: '',
    version: '1',
    tags: [],
    steps: [step('selected'), step('peer')],
    edges: [
      edge('selected-peer', 'selected', 'peer'),
      edge('peer-selected', 'peer', 'selected'),
      edge('selected-loop', 'selected', 'selected', 'back_edge'),
    ],
  };
}

function step(id: string): VpStep {
  return {
    id,
    label: id === 'selected' ? 'Selected agent' : 'Peer agent',
    role: 'developer',
    kind: 'coding',
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  kind = 'always',
): VpEdge {
  return { id, source, target, condition: { kind } };
}

function runtimeOverlay(): VpRuntimeOverlay {
  return {
    run_id: 'run-a',
    workflow_id: 'graph-a',
    process_id: 'graph-a',
    overall_status: 'running',
    current_step_ids: ['selected'],
    steps: {
      selected: { step_id: 'selected', status: 'running' },
      peer: { step_id: 'peer', status: 'pending' },
    },
    updated_at: 60,
  };
}

function traceReadModel(
  edges: readonly CaseFlowEdgeTraceProjection[],
): CaseFlowEdgeTraceReadModel {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'graph-a',
    run_id: 'run-a',
    catalog_verification_status: 'verified',
    verification_status: edges.every(edge => edge.verification_status === 'verified')
      ? 'verified'
      : 'unverified',
    reason_code: '',
    edges,
    telemetry: {
      source_event_count: edges.length,
      processed_event_count: edges.length,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: edges.length,
      redaction_policy: 'user',
      messages_per_edge_limit: 64,
      telemetry_per_edge_limit: 128,
    },
  };
}

function traceEdge(
  edgeId: string,
  sourceStepId: string,
  targetStepId: string,
  message: string,
  entry: CaseFlowEdgeTraceTelemetryEntry,
): CaseFlowEdgeTraceProjection {
  return {
    edge_id: edgeId,
    source_step_id: sourceStepId,
    target_step_id: targetStepId,
    edge_kind: sourceStepId === targetStepId ? 'back_edge' : 'dependency',
    activity_status: 'active',
    verification_status: 'verified',
    reason_code: 'caseflow_edge_correlation_verified_active',
    correlation_basis: 'explicit_edge_id',
    event_refs: entry.event_ref ? [entry.event_ref] : [],
    trace_refs: entry.trace_ref ? [entry.trace_ref] : [],
    messages: [{
      content: message,
      role: 'assistant',
      event_ref: entry.event_ref,
      trace_ref: entry.trace_ref,
      correlation_ref: entry.trace_ref,
      occurred_at: entry.occurred_at,
      verification_status: entry.trace_ref ? 'verified' : 'unverified',
      truncated: false,
    }],
    telemetry: [entry],
    limits: {
      messages_truncated: 0,
      telemetry_truncated: 0,
      event_refs_truncated: 0,
      trace_refs_truncated: 0,
    },
  };
}

function telemetry(
  stepId: string,
  occurredAt: number,
  overrides: Partial<CaseFlowEdgeTraceTelemetryEntry> = {},
): CaseFlowEdgeTraceTelemetryEntry {
  return {
    event_ref: `event-${occurredAt}`,
    trace_ref: `trace-${occurredAt}`,
    agent_run_ref: null,
    correlation_ref: null,
    causation_ref: null,
    event_type: 'workflow.step.updated',
    step_id: stepId,
    sequence: occurredAt,
    occurred_at: occurredAt,
    status: 'running',
    duration_ms: null,
    model: null,
    provider: null,
    token_usage: null,
    cost_micros: null,
    tool: null,
    error: null,
    redaction_policy: 'user',
    ...overrides,
  };
}
