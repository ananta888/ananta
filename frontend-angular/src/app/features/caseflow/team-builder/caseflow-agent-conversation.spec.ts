/**
 * Attribution is the whole point here.
 *
 * A thought shown under the wrong agent is worse than no thought at all, so
 * what is pinned is that a message is credited to the end of the edge that
 * produced it, that an unavailable trace produces nothing rather than
 * something plausible, and that the Hub's ordering survives untouched.
 */

import { describe, expect, it } from 'vitest';
import type {
  CaseFlowAgentNodeRelationProjection,
  CaseFlowAgentNodeRuntimeTraceProjection,
} from '../agent-canvas/caseflow-agent-node-runtime.mapper';
import { projectAgentConversation } from './caseflow-agent-conversation';

function message(content: string, overrides: Record<string, unknown> = {}) {
  return {
    content,
    role: 'assistant',
    event_ref: null,
    trace_ref: null,
    correlation_ref: null,
    occurred_at: 1,
    verification_status: 'verified',
    truncated: false,
    ...overrides,
  };
}

function relation(
  overrides: Partial<CaseFlowAgentNodeRelationProjection> = {},
): CaseFlowAgentNodeRelationProjection {
  return {
    edge_id: 'e-1',
    source_step_id: 'mara',
    target_step_id: 'fritz',
    kind: 'child',
    peer_step_id: 'fritz',
    peer_label: 'Fritz',
    activity_status: 'active',
    verification_status: 'verified',
    reason_code: 'ok',
    messages: [message('Ich schaue mir den Test an.')],
    telemetry: [],
    ...overrides,
  } as CaseFlowAgentNodeRelationProjection;
}

function trace(
  relations: readonly CaseFlowAgentNodeRelationProjection[],
  overrides: Partial<CaseFlowAgentNodeRuntimeTraceProjection> = {},
): CaseFlowAgentNodeRuntimeTraceProjection {
  return {
    available: true,
    reason_code: 'ok',
    workflow_id: 'g-1',
    run_id: 'r-1',
    step_id: 'mara',
    runtime: null,
    parents: [],
    children: [],
    loops: [],
    hub_ordered_relations: relations,
    current_activity: null,
    last_error: null,
    ...overrides,
  } as CaseFlowAgentNodeRuntimeTraceProjection;
}

describe('who said what', () => {
  it('credits a message to the agent at the source end of the edge it crossed', () => {
    const conversation = projectAgentConversation(trace([relation()]), 'mara');

    expect(conversation.thoughts.map(entry => entry.content)).toEqual(['Ich schaue mir den Test an.']);
    expect(conversation.thoughts[0].direction).toBe('outgoing');
  });

  it('does not credit an incoming message to the agent that received it', () => {
    const incoming = relation({
      source_step_id: 'fritz',
      target_step_id: 'mara',
      kind: 'parent',
      messages: [message('Bitte übernimm das.')],
    });

    const conversation = projectAgentConversation(trace([incoming]), 'mara');

    expect(conversation.thoughts).toEqual([]);
    expect(conversation.exchanges[0].entries[0].direction).toBe('incoming');
  });

  it('shows both directions in the exchange with one peer', () => {
    const out = relation({ edge_id: 'e-out', messages: [message('Fertig.')] });
    const back = relation({
      edge_id: 'e-back',
      source_step_id: 'fritz',
      target_step_id: 'mara',
      kind: 'parent',
      messages: [message('Danke.')],
    });

    const conversation = projectAgentConversation(trace([out, back]), 'mara');

    expect(conversation.exchanges).toHaveLength(1);
    expect(conversation.exchanges[0].entries.map(entry => [entry.direction, entry.content])).toEqual([
      ['outgoing', 'Fertig.'],
      ['incoming', 'Danke.'],
    ]);
  });

  it('keeps one tab per peer in the order the Hub related them', () => {
    const toZoe = relation({ edge_id: 'e-2', target_step_id: 'zoe', peer_step_id: 'zoe', peer_label: 'Zoe' });

    const conversation = projectAgentConversation(trace([toZoe, relation()]), 'mara');

    expect(conversation.exchanges.map(exchange => exchange.peer_label)).toEqual(['Zoe', 'Fritz']);
  });

  it('keeps the Hub order of messages rather than re-sorting by timestamp', () => {
    const relations = [
      relation({ edge_id: 'e-1', messages: [message('zuerst', { occurred_at: 99 })] }),
      relation({ edge_id: 'e-2', target_step_id: 'zoe', peer_step_id: 'zoe', messages: [message('danach', { occurred_at: 1 })] }),
    ];

    const conversation = projectAgentConversation(trace(relations), 'mara');

    expect(conversation.thoughts.map(entry => entry.content)).toEqual(['zuerst', 'danach']);
  });

  it('gives every entry a key unique across edges so a list can track them', () => {
    const relations = [
      relation({ edge_id: 'e-1', messages: [message('a'), message('b')] }),
      relation({ edge_id: 'e-2', target_step_id: 'zoe', peer_step_id: 'zoe', messages: [message('c')] }),
    ];

    const keys = projectAgentConversation(trace(relations), 'mara').thoughts.map(entry => entry.key);

    expect(new Set(keys).size).toBe(3);
  });

  it('carries whether a message was verified and whether it was cut short', () => {
    const relations = [
      relation({ messages: [message('gekürzt', { verification_status: 'unverified', truncated: true })] }),
    ];

    const entry = projectAgentConversation(trace(relations), 'mara').thoughts[0];

    expect(entry.verified).toBe(false);
    expect(entry.truncated).toBe(true);
  });
});

describe('when there is nothing to show', () => {
  it('shows nothing rather than something plausible without a trace', () => {
    expect(projectAgentConversation(null, 'mara').available).toBe(false);
    expect(projectAgentConversation(undefined, 'mara').thoughts).toEqual([]);
  });

  it('passes the Hub reason through when the trace is unavailable', () => {
    const unavailable = trace([], { available: false, reason_code: 'caseflow_node_trace_scope_unavailable' });

    const conversation = projectAgentConversation(unavailable, 'mara');

    expect(conversation.available).toBe(false);
    expect(conversation.reason_code).toBe('caseflow_node_trace_scope_unavailable');
  });

  it('refuses a trace projected for a different agent', () => {
    const conversation = projectAgentConversation(trace([relation()]), 'fritz');

    expect(conversation.available).toBe(false);
    expect(conversation.reason_code).toBe('caseflow_conversation_scope_mismatch');
  });

  it('reports an agent that has simply not spoken yet as available and empty', () => {
    const conversation = projectAgentConversation(trace([relation({ messages: [] })]), 'mara');

    expect(conversation.available).toBe(true);
    expect(conversation.thoughts).toEqual([]);
    expect(conversation.exchanges[0].entries).toEqual([]);
  });
});
