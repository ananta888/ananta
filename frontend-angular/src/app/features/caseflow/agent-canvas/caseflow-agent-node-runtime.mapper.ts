import type {
  VpGraph,
  VpRuntimeOverlay,
  VpRuntimeStepOverlay,
} from '../../visual-process/visual-process-api.service';
import {
  type CaseFlowAgentNeighborhood,
  selectCaseFlowAgentNeighborhood,
} from './caseflow-agent-neighborhood.selector';
import type {
  CaseFlowEdgeActivityStatus,
  CaseFlowEdgeIdentity,
  CaseFlowEdgeTraceMessage,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceTelemetryEntry,
  CaseFlowEdgeVerificationStatus,
} from './caseflow-edge-trace.models';

const TOKEN_USAGE_KEYS = [
  'input_tokens',
  'output_tokens',
  'total_tokens',
  'prompt_tokens',
  'completion_tokens',
  'cached_tokens',
  'reasoning_tokens',
  'cache_read_input_tokens',
  'cache_creation_input_tokens',
] as const;

export type CaseFlowAgentNodeRelationKind = 'parent' | 'child' | 'loop';

export type CaseFlowAgentNodeRuntimeStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'success'
  | 'error'
  | 'skipped'
  | 'cancelled'
  | 'unknown';

export interface CaseFlowAgentNodeRuntimeMetrics {
  readonly status: CaseFlowAgentNodeRuntimeStatus;
  readonly current: boolean;
  readonly started_at: number | null;
  readonly finished_at: number | null;
  readonly duration_ms: number | null;
  readonly selected_model_profile_id: string | null;
  readonly selected_provider_id: string | null;
  readonly selected_model: string | null;
}

export interface CaseFlowAgentNodeRelationProjection extends CaseFlowEdgeIdentity {
  readonly kind: CaseFlowAgentNodeRelationKind;
  readonly peer_step_id: string;
  readonly peer_label: string;
  readonly activity_status: CaseFlowEdgeActivityStatus;
  readonly verification_status: CaseFlowEdgeVerificationStatus;
  readonly reason_code: string;
  readonly messages: readonly CaseFlowEdgeTraceMessage[];
  readonly telemetry: readonly CaseFlowEdgeTraceTelemetryEntry[];
}

export interface CaseFlowAgentNodeActivitySummary {
  readonly edge_id: string | null;
  readonly source_step_id: string | null;
  readonly target_step_id: string | null;
  readonly status: string | null;
  readonly occurred_at: number | null;
  readonly event_ref: string | null;
  readonly trace_ref: string | null;
}

export interface CaseFlowAgentNodeErrorSummary extends CaseFlowAgentNodeActivitySummary {
  readonly error: string;
}

export interface CaseFlowAgentNodeRuntimeTraceProjection {
  readonly available: boolean;
  readonly reason_code: string;
  readonly workflow_id: string;
  readonly run_id: string;
  readonly step_id: string;
  readonly runtime: CaseFlowAgentNodeRuntimeMetrics | null;
  readonly parents: readonly CaseFlowAgentNodeRelationProjection[];
  readonly children: readonly CaseFlowAgentNodeRelationProjection[];
  readonly loops: readonly CaseFlowAgentNodeRelationProjection[];
  /** Exact related edges in the authoritative order received from the Hub. */
  readonly hub_ordered_relations: readonly CaseFlowAgentNodeRelationProjection[];
  readonly current_activity: CaseFlowAgentNodeActivitySummary | null;
  readonly last_error: CaseFlowAgentNodeErrorSummary | null;
}

interface RelationDescriptor extends CaseFlowEdgeIdentity {
  readonly kind: CaseFlowAgentNodeRelationKind;
  readonly peer_step_id: string;
  readonly peer_label: string;
}

/**
 * Pure, fail-closed projection for one node. It never derives edge activity or
 * trace identity from current_step_ids and never copies fields outside the
 * explicitly allowlisted runtime/trace contracts below.
 */
export function projectCaseFlowAgentNodeRuntimeTrace(
  graph: Readonly<VpGraph>,
  selectedStepId: string,
  workflowId: string,
  runId: string,
  runtimeOverlay: Readonly<VpRuntimeOverlay> | null | undefined,
  traceReadModel: Readonly<CaseFlowEdgeTraceReadModel> | null | undefined,
): CaseFlowAgentNodeRuntimeTraceProjection {
  const base = unavailableProjection(workflowId, runId, selectedStepId);
  if (!isCanonicalIdentity(selectedStepId)
    || !isCanonicalIdentity(workflowId)
    || !isCanonicalIdentity(runId)
    || graph.id !== workflowId) {
    return { ...base, reason_code: 'caseflow_node_scope_invalid' };
  }

  const selectedStep = graph.steps.find(step => step.id === selectedStepId);
  if (!selectedStep) return { ...base, reason_code: 'caseflow_node_not_found' };
  if (new Set(graph.steps.map(step => step.id)).size !== graph.steps.length) {
    return { ...base, reason_code: 'caseflow_node_graph_identity_ambiguous' };
  }

  const neighborhood = selectCaseFlowAgentNeighborhood(graph as VpGraph, selectedStepId);
  if (!neighborhood.ok) {
    return { ...base, reason_code: 'caseflow_node_neighborhood_invalid' };
  }
  const descriptors = relationDescriptors(graph, selectedStepId, neighborhood.value);
  if (!descriptors) return { ...base, reason_code: 'caseflow_node_relationship_invalid' };

  if (!traceReadModel
    || traceReadModel.workflow_id !== workflowId
    || traceReadModel.run_id !== runId
    || traceReadModel.catalog_verification_status !== 'verified') {
    return { ...base, reason_code: 'caseflow_node_trace_scope_unavailable' };
  }

  if (runtimeOverlay && !runtimeScopeMatches(graph, workflowId, runId, runtimeOverlay)) {
    return { ...base, reason_code: 'caseflow_node_runtime_scope_mismatch' };
  }

  const projected = projectRelations(descriptors, selectedStepId, traceReadModel);
  if (!projected) return { ...base, reason_code: 'caseflow_node_trace_identity_ambiguous' };

  const runtime = runtimeOverlay
    ? projectRuntimeMetrics(runtimeOverlay.steps[selectedStepId], runtimeOverlay.current_step_ids)
    : null;
  if (runtimeOverlay && !runtime) {
    return { ...base, reason_code: 'caseflow_node_runtime_step_unavailable' };
  }
  const summaries = summarizeActivity(
    selectedStepId,
    runtime,
    runtimeOverlay?.updated_at,
    projected.hubOrdered,
  );

  return Object.freeze({
    available: true,
    reason_code: '',
    workflow_id: workflowId,
    run_id: runId,
    step_id: selectedStepId,
    runtime,
    parents: projected.parents,
    children: projected.children,
    loops: projected.loops,
    hub_ordered_relations: projected.hubOrdered,
    current_activity: summaries.current,
    last_error: summaries.error,
  });
}

function relationDescriptors(
  graph: Readonly<VpGraph>,
  selectedStepId: string,
  neighborhood: Readonly<CaseFlowAgentNeighborhood>,
): readonly RelationDescriptor[] | null {
  const graphEdgesById = new Map<string, VpGraph['edges'][number]>();
  for (const edge of graph.edges) {
    if (graphEdgesById.has(edge.id)) return null;
    graphEdgesById.set(edge.id, edge);
  }

  const descriptors: RelationDescriptor[] = [];
  for (const relation of neighborhood.parents) {
    const edge = graphEdgesById.get(relation.edge_id);
    if (!edge || edge.source !== relation.peer_step_id || edge.target !== selectedStepId) return null;
    descriptors.push(Object.freeze({
      kind: 'parent',
      edge_id: edge.id,
      source_step_id: edge.source,
      target_step_id: edge.target,
      peer_step_id: relation.peer_step_id,
      peer_label: relation.peer_label,
    }));
  }
  for (const relation of neighborhood.children) {
    const edge = graphEdgesById.get(relation.edge_id);
    if (!edge || edge.source !== selectedStepId || edge.target !== relation.peer_step_id) return null;
    descriptors.push(Object.freeze({
      kind: 'child',
      edge_id: edge.id,
      source_step_id: edge.source,
      target_step_id: edge.target,
      peer_step_id: relation.peer_step_id,
      peer_label: relation.peer_label,
    }));
  }
  for (const relation of neighborhood.loops) {
    const edge = graphEdgesById.get(relation.edge_id);
    if (!edge || edge.source !== selectedStepId || edge.target !== selectedStepId) return null;
    descriptors.push(Object.freeze({
      kind: 'loop',
      edge_id: edge.id,
      source_step_id: edge.source,
      target_step_id: edge.target,
      peer_step_id: selectedStepId,
      peer_label: relation.label || 'Loop',
    }));
  }
  return Object.freeze(descriptors);
}

function runtimeScopeMatches(
  graph: Readonly<VpGraph>,
  workflowId: string,
  runId: string,
  runtime: Readonly<VpRuntimeOverlay>,
): boolean {
  if (runtime.workflow_id !== workflowId || runtime.run_id !== runId) return false;
  if (runtime.process_id !== undefined && runtime.process_id !== graph.id) return false;
  const graphStepIds = new Set(graph.steps.map(step => step.id));
  if (runtime.current_step_ids.some(stepId => !graphStepIds.has(stepId))) return false;
  return Object.entries(runtime.steps).every(([stepId, step]) =>
    graphStepIds.has(stepId) && step.step_id === stepId);
}

function projectRelations(
  descriptors: readonly RelationDescriptor[],
  selectedStepId: string,
  readModel: Readonly<CaseFlowEdgeTraceReadModel>,
): {
  readonly parents: readonly CaseFlowAgentNodeRelationProjection[];
  readonly children: readonly CaseFlowAgentNodeRelationProjection[];
  readonly loops: readonly CaseFlowAgentNodeRelationProjection[];
  readonly hubOrdered: readonly CaseFlowAgentNodeRelationProjection[];
} | null {
  const descriptorsByIdentity = new Map(
    descriptors.map(descriptor => [edgeIdentityKey(descriptor), descriptor]),
  );
  const exactByIdentity = new Map<string, CaseFlowAgentNodeRelationProjection>();
  const hubOrdered: CaseFlowAgentNodeRelationProjection[] = [];

  for (const edge of readModel.edges) {
    const key = edgeIdentityKey(edge);
    const descriptor = descriptorsByIdentity.get(key);
    if (!descriptor) continue;
    if (exactByIdentity.has(key)) return null;
    const relation = copyRelation(descriptor, selectedStepId, edge);
    exactByIdentity.set(key, relation);
    hubOrdered.push(relation);
  }

  const withMissingProjection = (descriptor: RelationDescriptor) =>
    exactByIdentity.get(edgeIdentityKey(descriptor)) ?? missingRelation(descriptor);
  return Object.freeze({
    parents: Object.freeze(
      descriptors.filter(item => item.kind === 'parent').map(withMissingProjection),
    ),
    children: Object.freeze(
      descriptors.filter(item => item.kind === 'child').map(withMissingProjection),
    ),
    loops: Object.freeze(
      descriptors.filter(item => item.kind === 'loop').map(withMissingProjection),
    ),
    hubOrdered: Object.freeze(hubOrdered),
  });
}

function copyRelation(
  descriptor: RelationDescriptor,
  selectedStepId: string,
  edge: Readonly<CaseFlowEdgeTraceReadModel['edges'][number]>,
): CaseFlowAgentNodeRelationProjection {
  return Object.freeze({
    ...descriptor,
    activity_status: edge.activity_status,
    verification_status: edge.verification_status,
    reason_code: boundedText(edge.reason_code, 512) ?? 'caseflow_edge_reason_unavailable',
    messages: Object.freeze(edge.messages.map(copyMessage)),
    telemetry: Object.freeze(
      edge.telemetry
        .filter(entry => entry.step_id === selectedStepId)
        .map(copyTelemetry),
    ),
  });
}

function missingRelation(descriptor: RelationDescriptor): CaseFlowAgentNodeRelationProjection {
  return Object.freeze({
    ...descriptor,
    activity_status: 'unknown',
    verification_status: 'unverified',
    reason_code: 'caseflow_edge_projection_unavailable',
    messages: Object.freeze([]),
    telemetry: Object.freeze([]),
  });
}

function copyMessage(message: Readonly<CaseFlowEdgeTraceMessage>): CaseFlowEdgeTraceMessage {
  return Object.freeze({
    content: boundedText(message.content, 2048) ?? '',
    role: boundedText(message.role, 64),
    event_ref: boundedText(message.event_ref, 256),
    trace_ref: boundedText(message.trace_ref, 256),
    correlation_ref: boundedText(message.correlation_ref, 256),
    occurred_at: nonNegativeNumber(message.occurred_at),
    verification_status: message.verification_status === 'verified' ? 'verified' : 'unverified',
    truncated: message.truncated === true,
  });
}

function copyTelemetry(
  entry: Readonly<CaseFlowEdgeTraceTelemetryEntry>,
): CaseFlowEdgeTraceTelemetryEntry {
  const usageEntries = entry.token_usage
    ? TOKEN_USAGE_KEYS.flatMap(key => {
      const value = entry.token_usage?.[key];
      return typeof value === 'number' && Number.isFinite(value) && value >= 0
        ? [[key, value] as const]
        : [];
    })
    : [];
  const usage = usageEntries.length
    ? Object.freeze(Object.fromEntries(usageEntries))
    : null;
  return Object.freeze({
    event_ref: boundedText(entry.event_ref, 256),
    trace_ref: boundedText(entry.trace_ref, 256),
    agent_run_ref: boundedText(entry.agent_run_ref, 256),
    correlation_ref: boundedText(entry.correlation_ref, 256),
    causation_ref: boundedText(entry.causation_ref, 256),
    event_type: boundedText(entry.event_type, 128) ?? 'Nicht verfügbar',
    step_id: boundedText(entry.step_id, 160),
    sequence: nonNegativeInteger(entry.sequence),
    occurred_at: nonNegativeNumber(entry.occurred_at),
    status: boundedText(entry.status, 64),
    duration_ms: nonNegativeNumber(entry.duration_ms),
    model: boundedText(entry.model, 160),
    provider: boundedText(entry.provider, 160),
    token_usage: usage,
    cost_micros: nonNegativeInteger(entry.cost_micros),
    tool: boundedText(entry.tool, 160),
    error: boundedText(entry.error, 512),
    redaction_policy: 'user',
  });
}

function projectRuntimeMetrics(
  step: Readonly<VpRuntimeStepOverlay> | undefined,
  currentStepIds: readonly string[],
): CaseFlowAgentNodeRuntimeMetrics | null {
  if (!step) return null;
  return Object.freeze({
    status: runtimeStatus(step.status),
    current: currentStepIds.includes(step.step_id),
    started_at: nonNegativeNumber(step.started_at),
    finished_at: nonNegativeNumber(step.finished_at),
    duration_ms: nonNegativeNumber(step.duration_ms),
    selected_model_profile_id: boundedText(step.selected_model_profile_id, 160),
    selected_provider_id: boundedText(step.selected_provider_id, 160),
    selected_model: boundedText(step.selected_model, 160),
  });
}

function summarizeActivity(
  selectedStepId: string,
  runtime: CaseFlowAgentNodeRuntimeMetrics | null,
  runtimeUpdatedAt: number | undefined,
  relations: readonly CaseFlowAgentNodeRelationProjection[],
): {
  readonly current: CaseFlowAgentNodeActivitySummary | null;
  readonly error: CaseFlowAgentNodeErrorSummary | null;
} {
  const entries = relations.flatMap(relation =>
    relation.telemetry.map(entry => ({ relation, entry })));
  const nodeEntries = entries.filter(item =>
    item.entry.step_id === selectedStepId || item.entry.step_id === null);
  const latest = latestByTime(nodeEntries);
  const lastError = latestByTime(nodeEntries.filter(item => item.entry.error !== null));

  const current = runtime || latest
    ? Object.freeze({
      ...(latest ? edgeIdentity(latest.relation) : missingEdgeIdentity()),
      status: latest?.entry.status ?? runtime?.status ?? null,
      occurred_at: latest?.entry.occurred_at ?? nonNegativeNumber(runtimeUpdatedAt),
      event_ref: latest?.entry.event_ref ?? null,
      trace_ref: latest?.entry.trace_ref ?? null,
    })
    : null;
  const error = lastError?.entry.error
    ? Object.freeze({
      ...edgeIdentity(lastError.relation),
      status: lastError.entry.status,
      occurred_at: lastError.entry.occurred_at,
      event_ref: lastError.entry.event_ref,
      trace_ref: lastError.entry.trace_ref,
      error: lastError.entry.error,
    })
    : null;
  return Object.freeze({ current, error });
}

function latestByTime<T extends { readonly entry: CaseFlowEdgeTraceTelemetryEntry }>(
  values: readonly T[],
): T | null {
  let latest: T | null = null;
  for (const value of values) {
    if (!latest
      || (value.entry.occurred_at ?? -1) >= (latest.entry.occurred_at ?? -1)) {
      latest = value;
    }
  }
  return latest;
}

function edgeIdentity(edge: Readonly<CaseFlowEdgeIdentity>): CaseFlowEdgeIdentity {
  return {
    edge_id: edge.edge_id,
    source_step_id: edge.source_step_id,
    target_step_id: edge.target_step_id,
  };
}

function missingEdgeIdentity(): Readonly<{
  edge_id: null;
  source_step_id: null;
  target_step_id: null;
}> {
  return { edge_id: null, source_step_id: null, target_step_id: null };
}

function edgeIdentityKey(edge: Readonly<CaseFlowEdgeIdentity>): string {
  return `${edge.edge_id}\u0000${edge.source_step_id}\u0000${edge.target_step_id}`;
}

function runtimeStatus(status: VpRuntimeStepOverlay['status']): CaseFlowAgentNodeRuntimeStatus {
  switch (status) {
    case 'pending':
    case 'running':
    case 'awaiting_approval':
    case 'skipped':
    case 'cancelled':
      return status;
    case 'succeeded': return 'success';
    case 'failed': return 'error';
    default: return 'unknown';
  }
}

function isCanonicalIdentity(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 160
    && value === value.trim()
    && !Array.from(value).some(character => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127;
    });
}

function boundedText(value: unknown, maximum: number): string | null {
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
    ? value
    : null;
}

function nonNegativeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function nonNegativeInteger(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

function unavailableProjection(
  workflowId: string,
  runId: string,
  stepId: string,
): CaseFlowAgentNodeRuntimeTraceProjection {
  return Object.freeze({
    available: false,
    reason_code: 'caseflow_node_projection_unavailable',
    workflow_id: workflowId,
    run_id: runId,
    step_id: stepId,
    runtime: null,
    parents: Object.freeze([]),
    children: Object.freeze([]),
    loops: Object.freeze([]),
    hub_ordered_relations: Object.freeze([]),
    current_activity: null,
    last_error: null,
  });
}
