export const SOURCE_CONTROL_V1_RESPONSE_SCHEMA =
  'ananta.source-control.api-response.v1' as const;
export const SOURCE_CONTROL_V1_ERROR_SCHEMA =
  'ananta.source-control.error.v1' as const;

export type SourceControlJson =
  | null
  | boolean
  | number
  | string
  | readonly SourceControlJson[]
  | SourceControlJsonObject;

export interface SourceControlJsonObject {
  readonly [key: string]: SourceControlJson;
}

export type SourceControlMutation =
  | 'refresh'
  | 'disable'
  | 'reindex'
  | 'grant_revoke';

export type SourceControlNextAction =
  | 'refresh'
  | 'scan'
  | 'index'
  | 'activate'
  | 'grant'
  | 'disable'
  | 'rollback';

export interface SourceControlProjection {
  readonly schema: 'ananta.source-control.projection.v1';
  readonly connection_id: string;
  readonly etag: string;
  readonly connection: SourceControlProjectionConnection;
  readonly revision: SourceControlJsonObject | null;
  readonly admission: SourceControlJsonObject | null;
  readonly index: SourceControlJsonObject | null;
  readonly active_index: SourceControlJsonObject | null;
  readonly stale: boolean;
  readonly grants: readonly SourceControlJsonObject[];
  readonly health: SourceControlJsonObject;
  readonly next_actions: readonly SourceControlNextAction[];
}

export interface SourceControlProjectionConnection
  extends SourceControlJsonObject {
  readonly project_id: string;
}

export interface SourceControlProjectionPage {
  readonly items: readonly SourceControlProjection[];
  readonly next_cursor: string | null;
}

export interface SourceControlProjectionDetail {
  readonly projection: SourceControlProjection;
  readonly etag: string;
}

export interface SourceControlIndexRecord extends SourceControlJsonObject {
  readonly knowledge_index_id: string;
  readonly source_revision_id: string;
  readonly status: string;
}

export interface SourceControlActiveIndex extends SourceControlJsonObject {
  readonly connection_id: string;
  readonly source_revision_id: string;
  readonly knowledge_index_id: string;
  readonly generation: number;
}

export interface SourceControlRunPage {
  readonly items: readonly SourceControlIndexRecord[];
  readonly active: SourceControlActiveIndex | null;
  readonly next_cursor: string | null;
}

export interface SourceControlIndexComparison {
  readonly left: SourceControlIndexRecord;
  readonly right: SourceControlIndexRecord;
  readonly changes: SourceControlJsonObject;
}

export interface SourceControlLifecycleAcknowledgement {
  readonly operation: string;
  readonly resource_id: string;
  readonly result: SourceControlJsonObject;
}

export interface SourceControlBulkTarget {
  readonly resource_id: string;
  readonly expected_etag: string;
}

export interface SourceControlBulkPlanItem {
  readonly resource_id: string;
  readonly expected_etag: string;
  readonly current_etag: string;
  readonly allowed: boolean;
  readonly reason_code: string;
}

export interface SourceControlBulkPlan {
  readonly schema: 'ananta.source-control.bulk-plan.v1';
  readonly tenant_id: string;
  readonly project_id: string;
  readonly actor_id: string;
  readonly mutation: SourceControlMutation;
  readonly items: readonly SourceControlBulkPlanItem[];
  readonly plan_digest: string;
}

export interface SourceControlBulkResultItem extends SourceControlJsonObject {
  readonly resource_id: string;
  readonly status: string;
}

export interface SourceControlBulkResult {
  readonly plan_digest: string;
  readonly results: readonly SourceControlBulkResultItem[];
}

export type SourceControlJobEventType =
  | 'source_refresh'
  | 'source_scan'
  | 'source_admission'
  | 'index_queued'
  | 'index_started'
  | 'index_progress'
  | 'index_completed'
  | 'index_failed'
  | 'index_cancelled'
  | 'index_activated'
  | 'index_rolled_back';

export interface SourceControlJobEvent {
  readonly event_id: string;
  readonly sequence: number;
  readonly resource_id: string;
  readonly job_id: string;
  readonly event_type: SourceControlJobEventType;
  readonly status: string;
  readonly reason_code: string | null;
  readonly trace_id: string;
  readonly occurred_at: string;
}

export interface SourceControlJobEventPage {
  readonly events: readonly SourceControlJobEvent[];
  readonly next_sequence: number;
}

export type SourceControlAccessDecisionKind =
  | 'allow'
  | 'deny'
  | 'approval_required'
  | 'unavailable';

export interface SourceControlAccessDecision {
  readonly schema: 'ananta.source-control.access-decision.v1';
  readonly source_revision_id: string;
  readonly revision_digest: string;
  readonly destination_id: string;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly decision: SourceControlAccessDecisionKind;
  readonly reason_codes: readonly string[];
  readonly matched_rule_path: readonly string[];
  readonly default_applied: boolean;
  readonly approval_requirement: string | null;
  readonly policy_digest: string;
}

export interface SourceControlAccessMatrix {
  readonly items: readonly SourceControlAccessDecision[];
  readonly source_next_cursor: string | null;
  readonly destination_next_cursor: string | null;
}

export interface SourceControlConnection extends SourceControlJsonObject {
  readonly schema: 'ananta.source-control.source-connection.v1';
  readonly authority: string;
  readonly connection_id: string;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly owner_id: string;
  readonly connector_type: string;
  readonly connection_identity_digest: string;
  readonly display_name: string;
  readonly sensitivity: string;
  readonly state: string;
  readonly created_at: string;
}

export interface SourceControlConnectionValidation {
  readonly valid: boolean;
  readonly connection: SourceControlConnection;
}

export interface SourceControlConnectionCreation {
  readonly connection: SourceControlConnection;
  readonly version: number;
}

export interface SourceControlOperationReceipt {
  readonly operation: 'refresh' | 'scan' | 'run';
  readonly connection_id: string;
  readonly receipt: SourceControlJsonObject;
}

export interface SourceControlExplorationResult
  extends SourceControlJsonObject {
  readonly text_alternative: string;
  readonly artifact_status: string | SourceControlJsonObject;
}

export type ContextPolicyState =
  | 'draft'
  | 'active'
  | 'superseded'
  | 'revoked';

export interface ContextPolicySummary {
  readonly policy_id: string;
  readonly latest_version: number;
  readonly state: ContextPolicyState;
  readonly etag: string;
  readonly policy_digest: string;
}

export interface ContextPolicySummaryPage {
  readonly items: readonly ContextPolicySummary[];
  readonly next_cursor: string | null;
}

export interface ContextPolicyVersion {
  readonly policy_id: string;
  readonly version: number;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly state: ContextPolicyState;
  readonly document: SourceControlJsonObject;
  readonly policy_digest: string;
  readonly etag: string;
  readonly created_by: string;
  readonly created_at: string;
}

export interface ContextPolicyVersionDetail {
  readonly policy: ContextPolicyVersion;
  readonly etag: string;
}

export interface ContextPolicyVersionPage {
  readonly items: readonly ContextPolicyVersion[];
  readonly next_cursor: string | null;
}

export type ContextPolicyDiagnosticSeverity = 'error' | 'warning' | 'info';

export interface ContextPolicyDiagnostic {
  readonly severity: ContextPolicyDiagnosticSeverity;
  readonly reason_code: string;
  readonly rule_id: string | null;
}

export interface ContextPolicyLintResult {
  readonly diagnostics: readonly ContextPolicyDiagnostic[];
}

export interface ContextPolicyPreview {
  readonly decision: SourceControlAccessDecisionKind;
  readonly reason_codes: readonly string[];
  readonly matched_rule_path: readonly string[];
  readonly approval_requirement: string | null;
  readonly policy_digest: string;
}

export interface SourceControlErrorEnvelope {
  readonly schema: typeof SOURCE_CONTROL_V1_ERROR_SCHEMA;
  readonly error: {
    readonly code: string;
  };
}

export class SourceControlV1ContractError extends Error {
  readonly status = 422;

  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'SourceControlV1ContractError';
  }
}

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$/;
const CURSOR = /^[A-Za-z0-9_-]{1,512}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const NEXT_ACTIONS = new Set<SourceControlNextAction>([
  'refresh',
  'scan',
  'index',
  'activate',
  'grant',
  'disable',
  'rollback',
]);
const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$/;
const ETAG = /^(?:[0-9a-f]{64}|index:[1-9][0-9]*)$/;
const POLICY_STATES = new Set<ContextPolicyState>([
  'draft',
  'active',
  'superseded',
  'revoked',
]);
const POLICY_DIAGNOSTIC_SEVERITIES =
  new Set<ContextPolicyDiagnosticSeverity>(['error', 'warning', 'info']);
const MUTATIONS = new Set<SourceControlMutation>([
  'refresh',
  'disable',
  'reindex',
  'grant_revoke',
]);
const EVENT_TYPES = new Set<SourceControlJobEventType>([
  'source_refresh',
  'source_scan',
  'source_admission',
  'index_queued',
  'index_started',
  'index_progress',
  'index_completed',
  'index_failed',
  'index_cancelled',
  'index_activated',
  'index_rolled_back',
]);
const ACCESS_DECISIONS = new Set<SourceControlAccessDecisionKind>([
  'allow',
  'deny',
  'approval_required',
  'unavailable',
]);
const SENSITIVE_RESPONSE_KEYS = new Set([
  'absolute_path',
  'credential',
  'credentials',
  'file_content',
  'private_remote_url',
  'prompt',
  'raw_content',
  'secret',
  'token',
]);

export function assertSourceControlOpaqueId(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !OPAQUE_ID.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlCursor(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !CURSOR.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlSha256(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !SHA256.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlIdempotencyKey(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !IDEMPOTENCY_KEY.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function assertSourceControlEtag(
  value: unknown,
  path: string,
): asserts value is string {
  if (typeof value !== 'string' || !ETAG.test(value)) {
    fail(`${path}_invalid`);
  }
}

export function parseSourceControlEnvelope<T>(
  value: unknown,
  parseData: (data: unknown, path: string) => T,
): T {
  const envelope = record(value, 'response');
  exactKeys(envelope, ['schema', 'data'], 'response');
  literal(
    envelope['schema'],
    SOURCE_CONTROL_V1_RESPONSE_SCHEMA,
    'response.schema',
  );
  return parseData(envelope['data'], 'response.data');
}

export function parseSourceControlErrorEnvelope(
  value: unknown,
): SourceControlErrorEnvelope {
  const envelope = record(value, 'error_response');
  exactKeys(envelope, ['schema', 'error'], 'error_response');
  literal(
    envelope['schema'],
    SOURCE_CONTROL_V1_ERROR_SCHEMA,
    'error_response.schema',
  );
  const error = record(envelope['error'], 'error_response.error');
  exactKeys(error, ['code'], 'error_response.error');
  assertSourceControlOpaqueId(error['code'], 'error_response.error.code');
  return {
    schema: SOURCE_CONTROL_V1_ERROR_SCHEMA,
    error: { code: error['code'] },
  };
}

export function parseSourceControlProjectionPage(
  value: unknown,
  path = 'projection_page',
): SourceControlProjectionPage {
  const page = record(value, path);
  exactKeys(page, ['items', 'next_cursor'], path);
  return {
    items: array(page['items'], `${path}.items`).map((item, index) =>
      parseSourceControlProjection(item, `${path}.items[${index}]`),
    ),
    next_cursor: nullableCursor(page['next_cursor'], `${path}.next_cursor`),
  };
}

export function parseSourceControlProjection(
  value: unknown,
  path = 'projection',
): SourceControlProjection {
  const projection = record(value, path);
  exactKeys(
    projection,
    [
      'schema',
      'connection_id',
      'etag',
      'connection',
      'revision',
      'admission',
      'index',
      'active_index',
      'stale',
      'grants',
      'health',
      'next_actions',
    ],
    path,
  );
  literal(
    projection['schema'],
    'ananta.source-control.projection.v1',
    `${path}.schema`,
  );
  assertSourceControlOpaqueId(
    projection['connection_id'],
    `${path}.connection_id`,
  );
  assertSourceControlSha256(projection['etag'], `${path}.etag`);
  const nextActions = array(
    projection['next_actions'],
    `${path}.next_actions`,
  ).map((action, index) => {
    if (typeof action !== 'string' || !NEXT_ACTIONS.has(action as SourceControlNextAction)) {
      fail(`${path}.next_actions[${index}]_invalid`);
    }
    return action as SourceControlNextAction;
  });
  if (typeof projection['stale'] !== 'boolean') {
    fail(`${path}.stale_invalid`);
  }
  const connection = safeJsonObject(
    projection['connection'],
    `${path}.connection`,
  );
  assertSourceControlOpaqueId(
    connection['project_id'],
    `${path}.connection.project_id`,
  );
  if ('tenant_id' in connection) {
    fail(`${path}.connection.tenant_id_forbidden`);
  }
  return {
    schema: 'ananta.source-control.projection.v1',
    connection_id: projection['connection_id'],
    etag: projection['etag'],
    connection: connection as SourceControlProjectionConnection,
    revision: nullableJsonObject(projection['revision'], `${path}.revision`),
    admission: nullableJsonObject(
      projection['admission'],
      `${path}.admission`,
    ),
    index: nullableJsonObject(projection['index'], `${path}.index`),
    active_index: nullableJsonObject(
      projection['active_index'],
      `${path}.active_index`,
    ),
    stale: projection['stale'],
    grants: array(projection['grants'], `${path}.grants`).map((grant, index) =>
      safeJsonObject(grant, `${path}.grants[${index}]`),
    ),
    health: safeJsonObject(projection['health'], `${path}.health`),
    next_actions: nextActions,
  };
}

export function parseSourceControlRunPage(
  value: unknown,
  path = 'run_page',
): SourceControlRunPage {
  const page = record(value, path);
  exactKeys(page, ['items', 'active', 'next_cursor'], path);
  return {
    items: array(page['items'], `${path}.items`).map((item, index) =>
      indexRecord(item, `${path}.items[${index}]`),
    ),
    active:
      page['active'] === null
        ? null
        : activeIndex(page['active'], `${path}.active`),
    next_cursor: nullableCursor(page['next_cursor'], `${path}.next_cursor`),
  };
}

export function parseSourceControlIndexComparison(
  value: unknown,
  path = 'comparison',
): SourceControlIndexComparison {
  const comparison = record(value, path);
  exactKeys(comparison, ['left', 'right', 'changes'], path);
  return {
    left: indexRecord(comparison['left'], `${path}.left`),
    right: indexRecord(comparison['right'], `${path}.right`),
    changes: safeJsonObject(comparison['changes'], `${path}.changes`),
  };
}

export function parseSourceControlLifecycleAcknowledgement(
  value: unknown,
  path = 'lifecycle_ack',
): SourceControlLifecycleAcknowledgement {
  const acknowledgement = record(value, path);
  exactKeys(
    acknowledgement,
    ['operation', 'resource_id', 'result'],
    path,
  );
  assertSourceControlOpaqueId(
    acknowledgement['operation'],
    `${path}.operation`,
  );
  assertSourceControlOpaqueId(
    acknowledgement['resource_id'],
    `${path}.resource_id`,
  );
  return {
    operation: acknowledgement['operation'],
    resource_id: acknowledgement['resource_id'],
    result: safeJsonObject(acknowledgement['result'], `${path}.result`),
  };
}

export function parseSourceControlBulkPlan(
  value: unknown,
  path = 'bulk_plan',
): SourceControlBulkPlan {
  const plan = record(value, path);
  exactKeys(
    plan,
    [
      'schema',
      'tenant_id',
      'project_id',
      'actor_id',
      'mutation',
      'items',
      'plan_digest',
    ],
    path,
  );
  literal(
    plan['schema'],
    'ananta.source-control.bulk-plan.v1',
    `${path}.schema`,
  );
  assertSourceControlOpaqueId(plan['tenant_id'], `${path}.tenant_id`);
  assertSourceControlOpaqueId(plan['project_id'], `${path}.project_id`);
  assertSourceControlOpaqueId(plan['actor_id'], `${path}.actor_id`);
  if (
    typeof plan['mutation'] !== 'string' ||
    !MUTATIONS.has(plan['mutation'] as SourceControlMutation)
  ) {
    fail(`${path}.mutation_invalid`);
  }
  assertSourceControlSha256(plan['plan_digest'], `${path}.plan_digest`);
  return {
    schema: 'ananta.source-control.bulk-plan.v1',
    tenant_id: plan['tenant_id'],
    project_id: plan['project_id'],
    actor_id: plan['actor_id'],
    mutation: plan['mutation'] as SourceControlMutation,
    items: array(plan['items'], `${path}.items`).map((item, index) =>
      bulkPlanItem(item, `${path}.items[${index}]`),
    ),
    plan_digest: plan['plan_digest'],
  };
}

export function parseSourceControlBulkResult(
  value: unknown,
  path = 'bulk_result',
): SourceControlBulkResult {
  const result = record(value, path);
  exactKeys(result, ['plan_digest', 'results'], path);
  assertSourceControlSha256(result['plan_digest'], `${path}.plan_digest`);
  return {
    plan_digest: result['plan_digest'],
    results: array(result['results'], `${path}.results`).map((item, index) => {
      const resultItem = safeJsonObject(
        item,
        `${path}.results[${index}]`,
      );
      assertSourceControlOpaqueId(
        resultItem['resource_id'],
        `${path}.results[${index}].resource_id`,
      );
      assertSourceControlOpaqueId(
        resultItem['status'],
        `${path}.results[${index}].status`,
      );
      return resultItem as SourceControlBulkResultItem;
    }),
  };
}

export function parseSourceControlJobEventPage(
  value: unknown,
  path = 'event_page',
): SourceControlJobEventPage {
  const page = record(value, path);
  exactKeys(page, ['events', 'next_sequence'], path);
  integer(page['next_sequence'], `${path}.next_sequence`, 0);
  return {
    events: array(page['events'], `${path}.events`).map((event, index) =>
      jobEvent(event, `${path}.events[${index}]`),
    ),
    next_sequence: page['next_sequence'],
  };
}

export function parseSourceControlAccessDecision(
  value: unknown,
  path = 'access_decision',
): SourceControlAccessDecision {
  const decision = record(value, path);
  exactKeys(
    decision,
    [
      'schema',
      'source_revision_id',
      'revision_digest',
      'destination_id',
      'operation',
      'transformation',
      'purpose',
      'decision',
      'reason_codes',
      'matched_rule_path',
      'default_applied',
      'approval_requirement',
      'policy_digest',
    ],
    path,
  );
  literal(
    decision['schema'],
    'ananta.source-control.access-decision.v1',
    `${path}.schema`,
  );
  assertSourceControlOpaqueId(
    decision['source_revision_id'],
    `${path}.source_revision_id`,
  );
  assertSourceControlSha256(
    decision['revision_digest'],
    `${path}.revision_digest`,
  );
  assertSourceControlOpaqueId(
    decision['destination_id'],
    `${path}.destination_id`,
  );
  for (const key of ['operation', 'transformation', 'purpose'] as const) {
    assertSourceControlOpaqueId(decision[key], `${path}.${key}`);
  }
  if (
    typeof decision['decision'] !== 'string' ||
    !ACCESS_DECISIONS.has(
      decision['decision'] as SourceControlAccessDecisionKind,
    )
  ) {
    fail(`${path}.decision_invalid`);
  }
  if (typeof decision['default_applied'] !== 'boolean') {
    fail(`${path}.default_applied_invalid`);
  }
  const approvalRequirement = decision['approval_requirement'];
  if (approvalRequirement !== null) {
    assertSourceControlOpaqueId(
      approvalRequirement,
      `${path}.approval_requirement`,
    );
  }
  assertSourceControlSha256(
    decision['policy_digest'],
    `${path}.policy_digest`,
  );
  return {
    schema: 'ananta.source-control.access-decision.v1',
    source_revision_id: decision['source_revision_id'],
    revision_digest: decision['revision_digest'],
    destination_id: decision['destination_id'],
    operation: decision['operation'] as string,
    transformation: decision['transformation'] as string,
    purpose: decision['purpose'] as string,
    decision: decision['decision'] as SourceControlAccessDecisionKind,
    reason_codes: opaqueIdArray(
      decision['reason_codes'],
      `${path}.reason_codes`,
    ),
    matched_rule_path: opaqueIdArray(
      decision['matched_rule_path'],
      `${path}.matched_rule_path`,
    ),
    default_applied: decision['default_applied'],
    approval_requirement: approvalRequirement as string | null,
    policy_digest: decision['policy_digest'],
  };
}

export function parseSourceControlAccessMatrix(
  value: unknown,
  path = 'access_matrix',
): SourceControlAccessMatrix {
  const matrix = record(value, path);
  exactKeys(
    matrix,
    ['items', 'source_next_cursor', 'destination_next_cursor'],
    path,
  );
  return {
    items: array(matrix['items'], `${path}.items`).map((decision, index) =>
      parseSourceControlAccessDecision(
        decision,
        `${path}.items[${index}]`,
      ),
    ),
    source_next_cursor: nullableCursor(
      matrix['source_next_cursor'],
      `${path}.source_next_cursor`,
    ),
    destination_next_cursor: nullableCursor(
      matrix['destination_next_cursor'],
      `${path}.destination_next_cursor`,
    ),
  };
}

export function parseSourceControlConnectionValidation(
  value: unknown,
  path = 'connection_validation',
): SourceControlConnectionValidation {
  const validation = record(value, path);
  exactKeys(validation, ['valid', 'connection'], path);
  if (typeof validation['valid'] !== 'boolean') {
    fail(`${path}.valid_invalid`);
  }
  return {
    valid: validation['valid'],
    connection: sourceConnection(
      validation['connection'],
      `${path}.connection`,
    ),
  };
}

export function parseSourceControlConnectionCreation(
  value: unknown,
  path = 'connection_creation',
): SourceControlConnectionCreation {
  const creation = record(value, path);
  exactKeys(creation, ['connection', 'version'], path);
  integer(creation['version'], `${path}.version`, 1);
  return {
    connection: sourceConnection(
      creation['connection'],
      `${path}.connection`,
    ),
    version: creation['version'],
  };
}

export function parseSourceControlOperationReceipt(
  value: unknown,
  path = 'operation_receipt',
): SourceControlOperationReceipt {
  const result = record(value, path);
  exactKeys(result, ['operation', 'connection_id', 'receipt'], path);
  if (
    result['operation'] !== 'refresh' &&
    result['operation'] !== 'scan' &&
    result['operation'] !== 'run'
  ) {
    fail(`${path}.operation_invalid`);
  }
  assertSourceControlOpaqueId(
    result['connection_id'],
    `${path}.connection_id`,
  );
  return {
    operation: result['operation'],
    connection_id: result['connection_id'],
    receipt: safeJsonObject(result['receipt'], `${path}.receipt`),
  };
}

export function parseSourceControlExplorationResult(
  value: unknown,
  path = 'exploration_result',
): SourceControlExplorationResult {
  const result = safeJsonObject(value, path);
  const textAlternative = result['text_alternative'];
  if (
    typeof textAlternative !== 'string' ||
    textAlternative.trim().length === 0
  ) {
    fail(`${path}.text_alternative_invalid`);
  }
  const artifactStatus = result['artifact_status'];
  if (
    typeof artifactStatus !== 'string' &&
    (typeof artifactStatus !== 'object' ||
      artifactStatus === null ||
      Array.isArray(artifactStatus))
  ) {
    fail(`${path}.artifact_status_invalid`);
  }
  return result as SourceControlExplorationResult;
}

export function parseContextPolicySummaryPage(
  value: unknown,
  path = 'policy_summary_page',
): ContextPolicySummaryPage {
  const page = record(value, path);
  exactKeys(page, ['items', 'next_cursor'], path);
  return {
    items: array(page['items'], `${path}.items`).map((item, index) =>
      contextPolicySummary(item, `${path}.items[${index}]`),
    ),
    next_cursor: nullableCursor(page['next_cursor'], `${path}.next_cursor`),
  };
}

export function parseContextPolicyVersionPage(
  value: unknown,
  path = 'policy_version_page',
): ContextPolicyVersionPage {
  const page = record(value, path);
  exactKeys(page, ['items', 'next_cursor'], path);
  return {
    items: array(page['items'], `${path}.items`).map((item, index) =>
      parseContextPolicyVersion(item, `${path}.items[${index}]`),
    ),
    next_cursor: nullableCursor(page['next_cursor'], `${path}.next_cursor`),
  };
}

export function parseContextPolicyVersion(
  value: unknown,
  path = 'policy_version',
): ContextPolicyVersion {
  const version = record(value, path);
  exactKeys(
    version,
    [
      'policy_id',
      'version',
      'tenant_id',
      'project_id',
      'state',
      'document',
      'policy_digest',
      'etag',
      'created_by',
      'created_at',
    ],
    path,
  );
  for (const key of [
    'policy_id',
    'tenant_id',
    'project_id',
    'created_by',
  ] as const) {
    assertSourceControlOpaqueId(version[key], `${path}.${key}`);
  }
  integer(version['version'], `${path}.version`, 1);
  if (
    typeof version['state'] !== 'string' ||
    !POLICY_STATES.has(version['state'] as ContextPolicyState)
  ) {
    fail(`${path}.state_invalid`);
  }
  assertSourceControlSha256(
    version['policy_digest'],
    `${path}.policy_digest`,
  );
  assertSourceControlSha256(version['etag'], `${path}.etag`);
  if (
    typeof version['created_at'] !== 'string' ||
    version['created_at'].length < 1
  ) {
    fail(`${path}.created_at_invalid`);
  }
  return {
    policy_id: version['policy_id'] as string,
    version: version['version'],
    tenant_id: version['tenant_id'] as string,
    project_id: version['project_id'] as string,
    state: version['state'] as ContextPolicyState,
    document: safeJsonObject(version['document'], `${path}.document`),
    policy_digest: version['policy_digest'],
    etag: version['etag'],
    created_by: version['created_by'] as string,
    created_at: version['created_at'],
  };
}

export function parseContextPolicyLintResult(
  value: unknown,
  path = 'policy_lint',
): ContextPolicyLintResult {
  const lint = record(value, path);
  exactKeys(lint, ['diagnostics'], path);
  return {
    diagnostics: array(
      lint['diagnostics'],
      `${path}.diagnostics`,
    ).map((diagnostic, index) =>
      contextPolicyDiagnostic(
        diagnostic,
        `${path}.diagnostics[${index}]`,
      ),
    ),
  };
}

export function parseContextPolicyPreview(
  value: unknown,
  path = 'policy_preview',
): ContextPolicyPreview {
  const preview = record(value, path);
  exactKeys(
    preview,
    [
      'decision',
      'reason_codes',
      'matched_rule_path',
      'approval_requirement',
      'policy_digest',
    ],
    path,
  );
  if (
    typeof preview['decision'] !== 'string' ||
    !ACCESS_DECISIONS.has(
      preview['decision'] as SourceControlAccessDecisionKind,
    )
  ) {
    fail(`${path}.decision_invalid`);
  }
  if (preview['approval_requirement'] !== null) {
    assertSourceControlOpaqueId(
      preview['approval_requirement'],
      `${path}.approval_requirement`,
    );
  }
  assertSourceControlSha256(
    preview['policy_digest'],
    `${path}.policy_digest`,
  );
  return {
    decision: preview['decision'] as SourceControlAccessDecisionKind,
    reason_codes: opaqueIdArray(
      preview['reason_codes'],
      `${path}.reason_codes`,
    ),
    matched_rule_path: opaqueIdArray(
      preview['matched_rule_path'],
      `${path}.matched_rule_path`,
    ),
    approval_requirement: preview['approval_requirement'] as string | null,
    policy_digest: preview['policy_digest'],
  };
}

export function parseContextPolicyDocument(
  value: unknown,
  path = 'policy_document',
): SourceControlJsonObject {
  const document = safeJsonObject(value, path);
  const keys = Object.keys(document);
  const expected = new Set([
    'schema',
    'policy_id',
    'scope',
    'defaults',
    'rules',
    'precedence',
  ]);
  if (
    keys.length !== expected.size ||
    keys.some((key) => !expected.has(key))
  ) {
    fail(`${path}_properties_invalid`);
  }
  assertSourceControlOpaqueId(document['policy_id'], `${path}.policy_id`);
  assertSourceControlOpaqueId(document['scope'], `${path}.scope`);
  integer(document['precedence'], `${path}.precedence`, 0);
  if (!Array.isArray(document['rules'])) {
    fail(`${path}.rules_invalid`);
  }
  if (
    typeof document['defaults'] !== 'object' ||
    document['defaults'] === null ||
    Array.isArray(document['defaults'])
  ) {
    fail(`${path}.defaults_invalid`);
  }
  if (typeof document['schema'] !== 'string') {
    fail(`${path}.schema_invalid`);
  }
  return document;
}

function bulkPlanItem(
  value: unknown,
  path: string,
): SourceControlBulkPlanItem {
  const item = record(value, path);
  exactKeys(
    item,
    [
      'resource_id',
      'expected_etag',
      'current_etag',
      'allowed',
      'reason_code',
    ],
    path,
  );
  assertSourceControlOpaqueId(item['resource_id'], `${path}.resource_id`);
  assertSourceControlSha256(item['expected_etag'], `${path}.expected_etag`);
  assertSourceControlSha256(item['current_etag'], `${path}.current_etag`);
  if (typeof item['allowed'] !== 'boolean') {
    fail(`${path}.allowed_invalid`);
  }
  assertSourceControlOpaqueId(item['reason_code'], `${path}.reason_code`);
  return {
    resource_id: item['resource_id'],
    expected_etag: item['expected_etag'],
    current_etag: item['current_etag'],
    allowed: item['allowed'],
    reason_code: item['reason_code'],
  };
}

function indexRecord(
  value: unknown,
  path: string,
): SourceControlIndexRecord {
  const item = safeJsonObject(value, path);
  assertSourceControlOpaqueId(
    item['knowledge_index_id'],
    `${path}.knowledge_index_id`,
  );
  assertSourceControlOpaqueId(
    item['source_revision_id'],
    `${path}.source_revision_id`,
  );
  assertSourceControlOpaqueId(item['status'], `${path}.status`);
  return item as SourceControlIndexRecord;
}

function activeIndex(
  value: unknown,
  path: string,
): SourceControlActiveIndex {
  const active = safeJsonObject(value, path);
  for (const key of [
    'connection_id',
    'source_revision_id',
    'knowledge_index_id',
  ] as const) {
    assertSourceControlOpaqueId(active[key], `${path}.${key}`);
  }
  integer(active['generation'], `${path}.generation`, 1);
  return active as SourceControlActiveIndex;
}

function sourceConnection(
  value: unknown,
  path: string,
): SourceControlConnection {
  const connection = record(value, path);
  exactKeys(
    connection,
    [
      'schema',
      'authority',
      'connection_id',
      'tenant_id',
      'project_id',
      'owner_id',
      'connector_type',
      'connection_identity_digest',
      'display_name',
      'sensitivity',
      'state',
      'created_at',
    ],
    path,
  );
  literal(
    connection['schema'],
    'ananta.source-control.source-connection.v1',
    `${path}.schema`,
  );
  for (const key of [
    'authority',
    'connection_id',
    'tenant_id',
    'project_id',
    'owner_id',
    'connector_type',
    'sensitivity',
    'state',
  ] as const) {
    assertSourceControlOpaqueId(connection[key], `${path}.${key}`);
  }
  assertSourceControlSha256(
    connection['connection_identity_digest'],
    `${path}.connection_identity_digest`,
  );
  if (
    typeof connection['display_name'] !== 'string' ||
    connection['display_name'].trim().length === 0 ||
    typeof connection['created_at'] !== 'string' ||
    connection['created_at'].length === 0
  ) {
    fail(`${path}.display_invalid`);
  }
  return safeJsonObject(connection, path) as SourceControlConnection;
}

function contextPolicySummary(
  value: unknown,
  path: string,
): ContextPolicySummary {
  const summary = record(value, path);
  exactKeys(
    summary,
    ['policy_id', 'latest_version', 'state', 'etag', 'policy_digest'],
    path,
  );
  assertSourceControlOpaqueId(summary['policy_id'], `${path}.policy_id`);
  integer(summary['latest_version'], `${path}.latest_version`, 1);
  if (
    typeof summary['state'] !== 'string' ||
    !POLICY_STATES.has(summary['state'] as ContextPolicyState)
  ) {
    fail(`${path}.state_invalid`);
  }
  assertSourceControlSha256(summary['etag'], `${path}.etag`);
  assertSourceControlSha256(
    summary['policy_digest'],
    `${path}.policy_digest`,
  );
  return {
    policy_id: summary['policy_id'],
    latest_version: summary['latest_version'],
    state: summary['state'] as ContextPolicyState,
    etag: summary['etag'],
    policy_digest: summary['policy_digest'],
  };
}

function contextPolicyDiagnostic(
  value: unknown,
  path: string,
): ContextPolicyDiagnostic {
  const diagnostic = record(value, path);
  exactKeys(diagnostic, ['severity', 'reason_code', 'rule_id'], path);
  if (
    typeof diagnostic['severity'] !== 'string' ||
    !POLICY_DIAGNOSTIC_SEVERITIES.has(
      diagnostic['severity'] as ContextPolicyDiagnosticSeverity,
    )
  ) {
    fail(`${path}.severity_invalid`);
  }
  assertSourceControlOpaqueId(
    diagnostic['reason_code'],
    `${path}.reason_code`,
  );
  if (diagnostic['rule_id'] !== null) {
    assertSourceControlOpaqueId(
      diagnostic['rule_id'],
      `${path}.rule_id`,
    );
  }
  return {
    severity: diagnostic[
      'severity'
    ] as ContextPolicyDiagnosticSeverity,
    reason_code: diagnostic['reason_code'],
    rule_id: diagnostic['rule_id'] as string | null,
  };
}

function jobEvent(value: unknown, path: string): SourceControlJobEvent {
  const event = record(value, path);
  exactKeys(
    event,
    [
      'event_id',
      'sequence',
      'resource_id',
      'job_id',
      'event_type',
      'status',
      'reason_code',
      'trace_id',
      'occurred_at',
    ],
    path,
  );
  for (const key of [
    'event_id',
    'resource_id',
    'job_id',
    'status',
    'trace_id',
    'occurred_at',
  ] as const) {
    assertSourceControlOpaqueId(event[key], `${path}.${key}`);
  }
  integer(event['sequence'], `${path}.sequence`, 1);
  if (
    typeof event['event_type'] !== 'string' ||
    !EVENT_TYPES.has(event['event_type'] as SourceControlJobEventType)
  ) {
    fail(`${path}.event_type_invalid`);
  }
  if (event['reason_code'] !== null) {
    assertSourceControlOpaqueId(
      event['reason_code'],
      `${path}.reason_code`,
    );
  }
  return {
    event_id: event['event_id'] as string,
    sequence: event['sequence'] as number,
    resource_id: event['resource_id'] as string,
    job_id: event['job_id'] as string,
    event_type: event['event_type'] as SourceControlJobEventType,
    status: event['status'] as string,
    reason_code: event['reason_code'] as string | null,
    trace_id: event['trace_id'] as string,
    occurred_at: event['occurred_at'] as string,
  };
}

function safeJsonObject(
  value: unknown,
  path: string,
): SourceControlJsonObject {
  const object = record(value, path);
  const result: Record<string, SourceControlJson> = {};
  for (const [key, nestedValue] of Object.entries(object)) {
    if (SENSITIVE_RESPONSE_KEYS.has(key.toLowerCase())) {
      fail(`${path}.${key}_forbidden`);
    }
    result[key] = safeJson(nestedValue, `${path}.${key}`);
  }
  return result;
}

function safeJson(value: unknown, path: string): SourceControlJson {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value as null | boolean | string;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => safeJson(item, `${path}[${index}]`));
  }
  return safeJsonObject(value, path);
}

function nullableJsonObject(
  value: unknown,
  path: string,
): SourceControlJsonObject | null {
  return value === null ? null : safeJsonObject(value, path);
}

function opaqueIdArray(value: unknown, path: string): readonly string[] {
  return array(value, path).map((item, index) => {
    assertSourceControlOpaqueId(item, `${path}[${index}]`);
    return item;
  });
}

function nullableCursor(value: unknown, path: string): string | null {
  if (value === null) {
    return null;
  }
  assertSourceControlCursor(value, path);
  return value;
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    fail(`${path}_object_required`);
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    fail(`${path}_array_required`);
  }
  return value;
}

function exactKeys(
  value: Record<string, unknown>,
  expectedKeys: readonly string[],
  path: string,
): void {
  const expected = new Set(expectedKeys);
  const actual = Object.keys(value);
  if (
    actual.length !== expected.size ||
    actual.some((key) => !expected.has(key))
  ) {
    fail(`${path}_properties_invalid`);
  }
}

function literal(value: unknown, expected: string, path: string): void {
  if (value !== expected) {
    fail(`${path}_invalid`);
  }
}

function integer(
  value: unknown,
  path: string,
  minimum: number,
): asserts value is number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    fail(`${path}_invalid`);
  }
}

function fail(reasonCode: string): never {
  throw new SourceControlV1ContractError(reasonCode);
}
