import { describe, expect, it } from 'vitest';

import type {
  CaseFlowEdgeTraceMessage,
  CaseFlowEdgeTraceTelemetryEntry,
} from './caseflow-edge-trace.models';
import {
  CaseFlowEdgeTraceContractError,
  decodeCaseFlowEdgeTraceReadModel,
  resolveCaseFlowMessageTelemetry,
  selectExactCaseFlowEdge,
} from './caseflow-edge-trace.validator';

describe('CaseFlow edge trace contract', () => {
  it('preserves the deterministic Hub edge, message and telemetry order', () => {
    const raw = response();
    raw.edges = [
      edge('edge-b-a', 'agent-b', 'agent-a', 'message-b-a', 'event-b-a', 'trace-b-a'),
      edge('edge-a-b', 'agent-a', 'agent-b', 'message-a-b', 'event-a-b', 'trace-a-b'),
    ];

    const decoded = decodeCaseFlowEdgeTraceReadModel(raw, scope());

    expect(decoded.edges.map(item => item.edge_id)).toEqual(['edge-b-a', 'edge-a-b']);
    expect(decoded.edges.map(item => item.messages[0].content)).toEqual(['message-b-a', 'message-a-b']);
    expect(decoded.edges.map(item => item.telemetry[0].event_ref)).toEqual(['event-b-a', 'event-a-b']);
    expect(Object.isFrozen(decoded.edges)).toBe(true);
  });

  it('selects only the exact canonical edge identity and direction', () => {
    const raw = response();
    raw.edges = [
      edge('edge-a-b', 'agent-a', 'agent-b'),
      edge('edge-b-a', 'agent-b', 'agent-a'),
    ];
    const decoded = decodeCaseFlowEdgeTraceReadModel(raw, scope());

    expect(selectExactCaseFlowEdge(decoded, {
      edge_id: 'edge-a-b', source_step_id: 'agent-a', target_step_id: 'agent-b',
    })?.edge_id).toBe('edge-a-b');
    expect(selectExactCaseFlowEdge(decoded, {
      edge_id: 'edge-a-b', source_step_id: 'agent-b', target_step_id: 'agent-a',
    })).toBeNull();
    expect(selectExactCaseFlowEdge(decoded, {
      edge_id: 'edge-b-a', source_step_id: 'agent-a', target_step_id: 'agent-b',
    })).toBeNull();
  });

  it('rejects response scope, schema and duplicate canonical edge identities', () => {
    const wrongScope = response();
    wrongScope.run_id = 'run-other';
    expectContractError(() => decodeCaseFlowEdgeTraceReadModel(wrongScope, scope()), 'scope_mismatch');

    const wrongSchema = response();
    wrongSchema.schema = 'unversioned';
    expectContractError(() => decodeCaseFlowEdgeTraceReadModel(wrongSchema, scope()), 'schema_unsupported');

    const duplicate = response();
    duplicate.edges = [edge('edge-a-b', 'agent-a', 'agent-b'), edge('edge-a-b', 'agent-b', 'agent-a')];
    expectContractError(() => decodeCaseFlowEdgeTraceReadModel(duplicate, scope()), 'duplicate_edge_id');
  });

  it('copies only allowlisted telemetry and token fields without creating missing references', () => {
    const raw = response();
    const projected = edge('edge-a-b', 'agent-a', 'agent-b');
    const telemetry = projected.telemetry[0] as Record<string, unknown>;
    telemetry['secret'] = 'must-not-cross-the-contract';
    telemetry['token_usage'] = { input_tokens: 3, api_key: 'must-not-cross-the-contract' };
    telemetry['agent_run_ref'] = null;
    raw.edges = [projected];

    const decoded = decodeCaseFlowEdgeTraceReadModel(raw, scope());
    const item = decoded.edges[0].telemetry[0];

    expect(item.token_usage).toEqual({ input_tokens: 3 });
    expect(item.agent_run_ref).toBeNull();
    expect(item).not.toHaveProperty('secret');
    expect(JSON.stringify(decoded)).not.toContain('must-not-cross-the-contract');
  });

  it('links a message only to one telemetry entry with the exact existing references', () => {
    const message = traceMessage('event-a', 'trace-a');
    const first = telemetryEntry('event-a', 'trace-a');
    const unrelated = telemetryEntry('event-b', 'trace-b');

    expect(resolveCaseFlowMessageTelemetry(message, [unrelated, first])).toEqual({
      status: 'verified', telemetry_index: 1, correlation_ref: 'trace-a',
    });
    expect(resolveCaseFlowMessageTelemetry(message, [first, first])).toEqual({
      status: 'unverified', telemetry_index: null, correlation_ref: 'trace-a',
    });
    expect(resolveCaseFlowMessageTelemetry({
      ...message, event_ref: 'event-other',
    }, [first])).toEqual({
      status: 'unverified', telemetry_index: null, correlation_ref: 'trace-a',
    });
  });

  it('keeps missing correlation explicitly unverified instead of inventing an identifier', () => {
    const message: CaseFlowEdgeTraceMessage = {
      ...traceMessage('event-a', 'trace-a'),
      correlation_ref: null,
      verification_status: 'unverified',
    };

    expect(resolveCaseFlowMessageTelemetry(message, [telemetryEntry('event-a', 'trace-a')])).toEqual({
      status: 'unverified', telemetry_index: null, correlation_ref: null,
    });
  });
});

function scope(): { workflow_id: string; run_id: string } {
  return { workflow_id: 'workflow-a', run_id: 'run-a' };
}

function response(): Record<string, any> {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'workflow-a',
    run_id: 'run-a',
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    edges: [],
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

function edge(
  edgeId: string,
  source: string,
  target: string,
  content = 'message',
  eventRef = 'event-a',
  traceRef = 'trace-a',
): Record<string, any> {
  return {
    edge_id: edgeId,
    source_step_id: source,
    target_step_id: target,
    edge_kind: 'dependency',
    activity_status: 'active',
    verification_status: 'verified',
    reason_code: 'caseflow_edge_correlation_verified_active',
    correlation_basis: 'explicit_edge_id',
    event_refs: [eventRef],
    trace_refs: [traceRef],
    messages: [{
      content,
      role: 'assistant',
      event_ref: eventRef,
      trace_ref: traceRef,
      correlation_ref: traceRef,
      occurred_at: 12,
      verification_status: 'verified',
      truncated: false,
    }],
    telemetry: [telemetryEntry(eventRef, traceRef)],
    limits: {
      messages_truncated: 0,
      telemetry_truncated: 0,
      event_refs_truncated: 0,
      trace_refs_truncated: 0,
    },
  };
}

function traceMessage(eventRef: string, traceRef: string): CaseFlowEdgeTraceMessage {
  return {
    content: 'message',
    role: 'assistant',
    event_ref: eventRef,
    trace_ref: traceRef,
    correlation_ref: traceRef,
    occurred_at: 12,
    verification_status: 'verified',
    truncated: false,
  };
}

function telemetryEntry(eventRef: string, traceRef: string): CaseFlowEdgeTraceTelemetryEntry {
  return {
    event_ref: eventRef,
    trace_ref: traceRef,
    agent_run_ref: null,
    correlation_ref: null,
    causation_ref: null,
    event_type: 'workflow.edge.message.sent',
    step_id: 'agent-b',
    sequence: 1,
    occurred_at: 12,
    status: 'active',
    duration_ms: null,
    model: null,
    provider: null,
    token_usage: null,
    cost_micros: null,
    tool: null,
    error: null,
    redaction_policy: 'user',
  };
}

function expectContractError(callback: () => unknown, suffix: string): void {
  try {
    callback();
    throw new Error('expected contract validation to fail');
  } catch (error) {
    expect(error).toBeInstanceOf(CaseFlowEdgeTraceContractError);
    expect((error as CaseFlowEdgeTraceContractError).reasonCode).toContain(suffix);
  }
}
