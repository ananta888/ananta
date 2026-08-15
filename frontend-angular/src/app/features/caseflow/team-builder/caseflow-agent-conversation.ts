/**
 * What one agent thought, and what it exchanged with each of the others.
 *
 * Both come out of the node runtime trace the Hub already projects: every
 * relation carries the messages that crossed it. Nothing is invented here —
 * a message is attributed to the agent that produced it, which is the source
 * end of the edge it crossed, and never guessed from anything else.
 */

import type {
  CaseFlowAgentNodeRelationProjection,
  CaseFlowAgentNodeRuntimeTraceProjection,
} from '../agent-canvas/caseflow-agent-node-runtime.mapper';
import type { CaseFlowEdgeTraceMessage } from '../agent-canvas/caseflow-edge-trace.models';

export type ConversationDirection = 'outgoing' | 'incoming';

export interface ConversationEntry {
  readonly key: string;
  readonly direction: ConversationDirection;
  readonly peer_step_id: string;
  readonly peer_label: string;
  readonly content: string;
  readonly role: string | null;
  readonly occurred_at: number | null;
  readonly verified: boolean;
  readonly truncated: boolean;
}

export interface ConversationExchange {
  readonly peer_step_id: string;
  readonly peer_label: string;
  readonly entries: readonly ConversationEntry[];
}

export interface AgentConversation {
  readonly available: boolean;
  readonly reason_code: string;
  /** What this agent produced, newest last, across every edge it feeds. */
  readonly thoughts: readonly ConversationEntry[];
  /** One tab per other agent, both directions, in the Hub's relation order. */
  readonly exchanges: readonly ConversationExchange[];
}

const EMPTY: AgentConversation = {
  available: false,
  reason_code: 'caseflow_conversation_unavailable',
  thoughts: [],
  exchanges: [],
};

export function projectAgentConversation(
  trace: Readonly<CaseFlowAgentNodeRuntimeTraceProjection> | null | undefined,
  stepId: string,
): AgentConversation {
  if (!trace) return EMPTY;
  if (!trace.available) return { ...EMPTY, reason_code: trace.reason_code };
  if (!stepId || trace.step_id !== stepId) {
    return { ...EMPTY, reason_code: 'caseflow_conversation_scope_mismatch' };
  }

  const exchanges: ConversationExchange[] = [];
  const thoughts: ConversationEntry[] = [];
  // Hub order is authoritative; peers keep the order the Hub related them in.
  const byPeer = new Map<string, ConversationEntry[]>();
  const labels = new Map<string, string>();

  for (const relation of trace.hub_ordered_relations) {
    const entries = relationEntries(relation, stepId);
    if (!labels.has(relation.peer_step_id)) {
      labels.set(relation.peer_step_id, relation.peer_label);
      byPeer.set(relation.peer_step_id, []);
    }
    byPeer.get(relation.peer_step_id)?.push(...entries);
    thoughts.push(...entries.filter(entry => entry.direction === 'outgoing'));
  }

  for (const [peerStepId, entries] of byPeer) {
    exchanges.push({
      peer_step_id: peerStepId,
      peer_label: labels.get(peerStepId) ?? peerStepId,
      entries,
    });
  }

  return {
    available: true,
    reason_code: trace.reason_code,
    thoughts,
    exchanges,
  };
}

function relationEntries(
  relation: Readonly<CaseFlowAgentNodeRelationProjection>,
  stepId: string,
): readonly ConversationEntry[] {
  // A message crossed this edge in the edge's own direction, so the producer
  // is the source end — never the selected node by assumption.
  const direction: ConversationDirection = relation.source_step_id === stepId ? 'outgoing' : 'incoming';
  return relation.messages.map((message, index) => entry(message, relation, direction, index));
}

function entry(
  message: Readonly<CaseFlowEdgeTraceMessage>,
  relation: Readonly<CaseFlowAgentNodeRelationProjection>,
  direction: ConversationDirection,
  index: number,
): ConversationEntry {
  return {
    key: `${relation.edge_id}:${index}`,
    direction,
    peer_step_id: relation.peer_step_id,
    peer_label: relation.peer_label,
    content: message.content,
    role: message.role,
    occurred_at: message.occurred_at,
    verified: message.verification_status === 'verified',
    truncated: message.truncated,
  };
}
