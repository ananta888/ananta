/**
 * Strict parsers for Source Control v1 projections, lifecycle, bulk, job
 * events, effective access and connection responses.
 */

import type {
  SourceControlAccessDecision,
  SourceControlAccessDecisionKind,
  SourceControlAccessMatrix,
  SourceControlActiveIndex,
  SourceControlBulkPlan,
  SourceControlBulkPlanItem,
  SourceControlBulkResult,
  SourceControlBulkResultItem,
  SourceControlConnection,
  SourceControlConnectionCreation,
  SourceControlConnectionValidation,
  SourceControlExplorationResult,
  SourceControlIndexComparison,
  SourceControlIndexRecord,
  SourceControlJobEvent,
  SourceControlJobEventPage,
  SourceControlJobEventType,
  SourceControlLifecycleAcknowledgement,
  SourceControlMutation,
  SourceControlNextAction,
  SourceControlOperationReceipt,
  SourceControlProjection,
  SourceControlProjectionConnection,
  SourceControlProjectionPage,
  SourceControlRunPage,
} from './source-control-v1-api-types.model';
import {
  ACCESS_DECISIONS,
  EVENT_TYPES,
  MUTATIONS,
  NEXT_ACTIONS,
  array,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
  exactKeys,
  fail,
  integer,
  literal,
  nullableCursor,
  nullableJsonObject,
  opaqueIdArray,
  record,
  safeJsonObject,
} from './source-control-v1-api-validation.model';

export function parseSourceControlProjectionPage(
  value: unknown,
  path = 'projection_page',
): SourceControlProjectionPage {
  const page = record(value, path);
  const expectedKeys = ['items', 'next_cursor'];
  if ('schema' in page) {
    expectedKeys.push('schema');
    literal(
      page['schema'],
      'ananta.source-control.projection-page.v1',
      `${path}.schema`,
    );
  }
  exactKeys(page, expectedKeys, path);
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
