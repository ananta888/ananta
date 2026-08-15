import {
  CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA,
  CaseFlowEdgeIdentity,
  CaseFlowEdgeTraceLimits,
  CaseFlowEdgeTraceMessage,
  CaseFlowEdgeTraceProjection,
  CaseFlowEdgeTraceProjectionTelemetry,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceScope,
  CaseFlowEdgeTraceTelemetryEntry,
  CaseFlowEdgeVerificationStatus,
  CaseFlowMessageTelemetryResolution,
  CaseFlowTokenUsage,
  CaseFlowTokenUsageKey,
} from './caseflow-edge-trace.models';

const MAX_EDGE_COUNT = 1024;
const MAX_MESSAGE_COUNT = 64;
const MAX_TELEMETRY_COUNT = 128;
const MAX_REFERENCE_COUNT = 128;
const MAX_IDENTIFIER_CHARS = 160;
const MAX_REFERENCE_CHARS = 256;
const MAX_MESSAGE_CHARS = 2048;

const TOKEN_USAGE_KEYS = new Set<CaseFlowTokenUsageKey>([
  'input_tokens',
  'output_tokens',
  'total_tokens',
  'prompt_tokens',
  'completion_tokens',
  'cached_tokens',
  'reasoning_tokens',
  'cache_read_input_tokens',
  'cache_creation_input_tokens',
]);

export class CaseFlowEdgeTraceContractError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'CaseFlowEdgeTraceContractError';
  }
}

/**
 * Converts an untrusted HTTP response into the small, allowlisted Hub read model.
 * Unknown response fields are deliberately not copied into the UI projection.
 */
export function decodeCaseFlowEdgeTraceReadModel(
  raw: unknown,
  expectedScope: Readonly<CaseFlowEdgeTraceScope>,
): CaseFlowEdgeTraceReadModel {
  const value = record(raw, 'caseflow_edge_trace_response_invalid');
  const workflowId = identity(value['workflow_id'], 'caseflow_workflow_id_invalid');
  const runId = identity(value['run_id'], 'caseflow_run_id_invalid');
  if (workflowId !== expectedScope.workflow_id || runId !== expectedScope.run_id) {
    fail('caseflow_edge_trace_scope_mismatch');
  }
  if (value['schema'] !== CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA) {
    fail('caseflow_edge_trace_schema_unsupported');
  }

  const rawEdges = array(value['edges'], MAX_EDGE_COUNT, 'caseflow_edge_trace_edges_invalid');
  const seenEdgeIds = new Set<string>();
  const edges = rawEdges.map((edge, index) => {
    const decoded = decodeEdge(edge, index);
    if (seenEdgeIds.has(decoded.edge_id)) fail('caseflow_edge_trace_duplicate_edge_id');
    seenEdgeIds.add(decoded.edge_id);
    return decoded;
  });

  return Object.freeze({
    schema: CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA,
    workflow_id: workflowId,
    run_id: runId,
    catalog_verification_status: verification(value['catalog_verification_status']),
    verification_status: verification(value['verification_status']),
    reason_code: text(value['reason_code'], 512, 'caseflow_edge_trace_reason_invalid'),
    source_revision: sourceRevision(value['source_revision']),
    edges: Object.freeze(edges),
    telemetry: decodeProjectionTelemetry(value['telemetry']),
  });
}

export function validateCaseFlowEdgeTraceScope(
  scope: Readonly<CaseFlowEdgeTraceScope>,
): CaseFlowEdgeTraceScope {
  return Object.freeze({
    workflow_id: identity(scope.workflow_id, 'caseflow_workflow_id_invalid'),
    run_id: identity(scope.run_id, 'caseflow_run_id_invalid'),
  });
}

/** Selects exactly one canonical directed edge; partial and ambiguous matches fail closed. */
export function selectExactCaseFlowEdge(
  readModel: Readonly<CaseFlowEdgeTraceReadModel>,
  selected: Readonly<CaseFlowEdgeIdentity>,
): CaseFlowEdgeTraceProjection | null {
  const edgeId = identity(selected.edge_id, 'caseflow_edge_id_invalid');
  const sourceStepId = identity(selected.source_step_id, 'caseflow_source_step_id_invalid');
  const targetStepId = identity(selected.target_step_id, 'caseflow_target_step_id_invalid');
  const matches = readModel.edges.filter(edge => edge.edge_id === edgeId
    && edge.source_step_id === sourceStepId
    && edge.target_step_id === targetStepId);
  return matches.length === 1 ? matches[0] : null;
}

/**
 * Resolves a message only when its existing correlation reference identifies
 * one telemetry entry after applying the message's existing event/trace refs.
 */
export function resolveCaseFlowMessageTelemetry(
  message: Readonly<CaseFlowEdgeTraceMessage>,
  telemetry: readonly CaseFlowEdgeTraceTelemetryEntry[],
): CaseFlowMessageTelemetryResolution {
  const correlationRef = message.correlation_ref;
  if (message.verification_status !== 'verified' || !correlationRef) {
    return Object.freeze({
      status: 'unverified',
      telemetry_index: null,
      correlation_ref: correlationRef,
    });
  }

  let candidates = telemetry
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => telemetryReferences(entry).includes(correlationRef));
  if (message.event_ref) {
    candidates = candidates.filter(({ entry }) => entry.event_ref === message.event_ref);
  }
  if (message.trace_ref) {
    candidates = candidates.filter(({ entry }) => entry.trace_ref === message.trace_ref);
  }
  if (candidates.length !== 1) {
    return Object.freeze({
      status: 'unverified',
      telemetry_index: null,
      correlation_ref: correlationRef,
    });
  }
  return Object.freeze({
    status: 'verified',
    telemetry_index: candidates[0].index,
    correlation_ref: correlationRef,
  });
}

/**
 * An absent stamp is tolerated so an older Hub stays readable, but a malformed
 * one is not: a garbled revision would be indistinguishable from a fresh one.
 */
function sourceRevision(raw: unknown): number | null {
  if (raw === undefined || raw === null) return null;
  if (typeof raw !== 'number' || !Number.isSafeInteger(raw) || raw < 0) {
    fail('caseflow_edge_trace_source_revision_invalid');
  }
  return raw as number;
}

function decodeEdge(raw: unknown, index: number): CaseFlowEdgeTraceProjection {
  const value = record(raw, `caseflow_edge_trace_edge_${index}_invalid`);
  const edgeKind = value['edge_kind'];
  if (edgeKind !== 'dependency' && edgeKind !== 'back_edge') {
    fail('caseflow_edge_kind_invalid');
  }
  const activity = value['activity_status'];
  if (activity !== 'active' && activity !== 'inactive' && activity !== 'unknown') {
    fail('caseflow_edge_activity_invalid');
  }
  const correlationBasis = value['correlation_basis'];
  if (correlationBasis !== 'explicit_edge_id'
    && correlationBasis !== 'explicit_direction'
    && correlationBasis !== 'unique_dependency_event_sequence'
    && correlationBasis !== 'unavailable') {
    fail('caseflow_edge_correlation_basis_invalid');
  }

  const edgeVerification = verification(value['verification_status']);
  if ((edgeVerification === 'verified') === (correlationBasis === 'unavailable')) {
    fail('caseflow_edge_correlation_verification_invalid');
  }

  const messages = array(value['messages'], MAX_MESSAGE_COUNT, 'caseflow_edge_messages_invalid')
    .map(decodeMessage);
  const telemetry = array(value['telemetry'], MAX_TELEMETRY_COUNT, 'caseflow_edge_telemetry_invalid')
    .map(decodeTelemetryEntry);
  return Object.freeze({
    edge_id: identity(value['edge_id'], 'caseflow_edge_id_invalid'),
    source_step_id: identity(value['source_step_id'], 'caseflow_source_step_id_invalid'),
    target_step_id: identity(value['target_step_id'], 'caseflow_target_step_id_invalid'),
    edge_kind: edgeKind,
    activity_status: activity,
    verification_status: edgeVerification,
    reason_code: text(value['reason_code'], 512, 'caseflow_edge_reason_invalid'),
    correlation_basis: correlationBasis,
    event_refs: decodeReferences(value['event_refs'], 'caseflow_edge_event_refs_invalid'),
    trace_refs: decodeReferences(value['trace_refs'], 'caseflow_edge_trace_refs_invalid'),
    messages: Object.freeze(messages),
    telemetry: Object.freeze(telemetry),
    limits: decodeLimits(value['limits']),
  });
}

function decodeMessage(raw: unknown): CaseFlowEdgeTraceMessage {
  const value = record(raw, 'caseflow_edge_message_invalid');
  const correlationRef = nullableReference(value['correlation_ref']);
  const status = verification(value['verification_status']);
  if ((status === 'verified') !== (correlationRef !== null)) {
    fail('caseflow_edge_message_verification_invalid');
  }
  if (typeof value['truncated'] !== 'boolean') fail('caseflow_edge_message_truncated_invalid');
  return Object.freeze({
    content: nonEmptyText(value['content'], MAX_MESSAGE_CHARS, 'caseflow_edge_message_content_invalid'),
    role: nullableNonEmptyText(value['role'], 64, 'caseflow_edge_message_role_invalid'),
    event_ref: nullableReference(value['event_ref']),
    trace_ref: nullableReference(value['trace_ref']),
    correlation_ref: correlationRef,
    occurred_at: nullablePositiveNumber(value['occurred_at'], 'caseflow_edge_message_time_invalid'),
    verification_status: status,
    truncated: value['truncated'],
  });
}

function decodeTelemetryEntry(raw: unknown): CaseFlowEdgeTraceTelemetryEntry {
  const value = record(raw, 'caseflow_edge_telemetry_entry_invalid');
  if (value['redaction_policy'] !== 'user') fail('caseflow_edge_redaction_policy_invalid');
  return Object.freeze({
    event_ref: nullableReference(value['event_ref']),
    trace_ref: nullableReference(value['trace_ref']),
    agent_run_ref: nullableReference(value['agent_run_ref']),
    correlation_ref: nullableReference(value['correlation_ref']),
    causation_ref: nullableReference(value['causation_ref']),
    event_type: nonEmptyText(value['event_type'], 128, 'caseflow_edge_event_type_invalid'),
    step_id: nullableIdentity(value['step_id'], 'caseflow_edge_step_id_invalid'),
    sequence: nullablePositiveInteger(value['sequence'], 'caseflow_edge_sequence_invalid'),
    occurred_at: nullablePositiveNumber(value['occurred_at'], 'caseflow_edge_time_invalid'),
    status: nullableNonEmptyText(value['status'], 64, 'caseflow_edge_status_invalid'),
    duration_ms: nullableNonNegativeNumber(value['duration_ms'], 'caseflow_edge_duration_invalid'),
    model: nullableNonEmptyText(value['model'], 160, 'caseflow_edge_model_invalid'),
    provider: nullableNonEmptyText(value['provider'], 160, 'caseflow_edge_provider_invalid'),
    token_usage: decodeTokenUsage(value['token_usage']),
    cost_micros: nullableNonNegativeInteger(value['cost_micros'], 'caseflow_edge_cost_invalid'),
    tool: nullableNonEmptyText(value['tool'], 160, 'caseflow_edge_tool_invalid'),
    error: nullableNonEmptyText(value['error'], 512, 'caseflow_edge_error_invalid'),
    redaction_policy: 'user',
  });
}

function decodeTokenUsage(raw: unknown): CaseFlowTokenUsage | null {
  if (raw === null) return null;
  const value = record(raw, 'caseflow_edge_token_usage_invalid');
  const result: Partial<Record<CaseFlowTokenUsageKey, number>> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!TOKEN_USAGE_KEYS.has(key as CaseFlowTokenUsageKey)) continue;
    if (typeof item !== 'number' || !Number.isFinite(item) || item < 0) {
      fail('caseflow_edge_token_usage_invalid');
    }
    result[key as CaseFlowTokenUsageKey] = item;
  }
  return Object.keys(result).length > 0 ? Object.freeze(result) : null;
}

function decodeLimits(raw: unknown): CaseFlowEdgeTraceLimits {
  const value = record(raw, 'caseflow_edge_limits_invalid');
  return Object.freeze({
    messages_truncated: nonNegativeInteger(value['messages_truncated'], 'caseflow_edge_limits_invalid'),
    telemetry_truncated: nonNegativeInteger(value['telemetry_truncated'], 'caseflow_edge_limits_invalid'),
    event_refs_truncated: nonNegativeInteger(value['event_refs_truncated'], 'caseflow_edge_limits_invalid'),
    trace_refs_truncated: nonNegativeInteger(value['trace_refs_truncated'], 'caseflow_edge_limits_invalid'),
  });
}

function decodeProjectionTelemetry(raw: unknown): CaseFlowEdgeTraceProjectionTelemetry {
  const value = record(raw, 'caseflow_edge_projection_telemetry_invalid');
  if (value['redaction_policy'] !== 'user') fail('caseflow_edge_redaction_policy_invalid');
  return Object.freeze({
    source_event_count: nonNegativeInteger(value['source_event_count'], 'caseflow_edge_projection_telemetry_invalid'),
    processed_event_count: nonNegativeInteger(value['processed_event_count'], 'caseflow_edge_projection_telemetry_invalid'),
    rejected_event_count: nonNegativeInteger(value['rejected_event_count'], 'caseflow_edge_projection_telemetry_invalid'),
    truncated_event_count: nonNegativeInteger(value['truncated_event_count'], 'caseflow_edge_projection_telemetry_invalid'),
    correlated_edge_count: nonNegativeInteger(value['correlated_edge_count'], 'caseflow_edge_projection_telemetry_invalid'),
    redaction_policy: 'user',
    messages_per_edge_limit: nonNegativeInteger(value['messages_per_edge_limit'], 'caseflow_edge_projection_telemetry_invalid'),
    telemetry_per_edge_limit: nonNegativeInteger(value['telemetry_per_edge_limit'], 'caseflow_edge_projection_telemetry_invalid'),
  });
}

function decodeReferences(raw: unknown, reason: string): readonly string[] {
  const values = array(raw, MAX_REFERENCE_COUNT, reason).map(item => reference(item, reason));
  if (new Set(values).size !== values.length) fail(reason);
  return Object.freeze(values);
}

function telemetryReferences(entry: Readonly<CaseFlowEdgeTraceTelemetryEntry>): readonly string[] {
  return [entry.event_ref, entry.trace_ref, entry.correlation_ref, entry.causation_ref]
    .filter((value): value is string => value !== null);
}

function verification(raw: unknown): CaseFlowEdgeVerificationStatus {
  if (raw !== 'verified' && raw !== 'unverified') fail('caseflow_edge_verification_invalid');
  return raw;
}

function identity(raw: unknown, reason: string): string {
  const result = nonEmptyText(raw, MAX_IDENTIFIER_CHARS, reason);
  if (result !== result.trim() || hasControlCharacter(result)) fail(reason);
  return result;
}

function nullableIdentity(raw: unknown, reason: string): string | null {
  return raw === null ? null : identity(raw, reason);
}

function reference(raw: unknown, reason: string): string {
  const result = nonEmptyText(raw, MAX_REFERENCE_CHARS, reason);
  if (result !== result.trim() || hasControlCharacter(result)) fail(reason);
  return result;
}

function nullableReference(raw: unknown): string | null {
  return raw === null ? null : reference(raw, 'caseflow_edge_reference_invalid');
}

function nullableNonEmptyText(raw: unknown, maximum: number, reason: string): string | null {
  return raw === null ? null : nonEmptyText(raw, maximum, reason);
}

function nonEmptyText(raw: unknown, maximum: number, reason: string): string {
  const result = text(raw, maximum, reason);
  if (!result) fail(reason);
  return result;
}

function text(raw: unknown, maximum: number, reason: string): string {
  if (typeof raw !== 'string' || Array.from(raw).length > maximum) fail(reason);
  return raw;
}

function nullablePositiveNumber(raw: unknown, reason: string): number | null {
  if (raw === null) return null;
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw <= 0) fail(reason);
  return raw;
}

function nullableNonNegativeNumber(raw: unknown, reason: string): number | null {
  if (raw === null) return null;
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0) fail(reason);
  return raw;
}

function nullablePositiveInteger(raw: unknown, reason: string): number | null {
  if (raw === null) return null;
  if (!Number.isSafeInteger(raw) || Number(raw) <= 0) fail(reason);
  return Number(raw);
}

function nullableNonNegativeInteger(raw: unknown, reason: string): number | null {
  if (raw === null) return null;
  return nonNegativeInteger(raw, reason);
}

function nonNegativeInteger(raw: unknown, reason: string): number {
  if (!Number.isSafeInteger(raw) || Number(raw) < 0) fail(reason);
  return Number(raw);
}

function array(raw: unknown, maximum: number, reason: string): unknown[] {
  if (!Array.isArray(raw) || raw.length > maximum) fail(reason);
  return raw;
}

function record(raw: unknown, reason: string): Record<string, unknown> {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) fail(reason);
  return raw as Record<string, unknown>;
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });
}

function fail(reason: string): never {
  throw new CaseFlowEdgeTraceContractError(reason);
}
