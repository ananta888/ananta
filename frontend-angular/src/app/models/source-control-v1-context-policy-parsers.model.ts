/** Strict parsers for Source Control v1 Context Policy responses and documents. */

import type {
  ContextPolicyDiagnostic,
  ContextPolicyDiagnosticSeverity,
  ContextPolicyLintResult,
  ContextPolicyPreview,
  ContextPolicyState,
  ContextPolicySummary,
  ContextPolicySummaryPage,
  ContextPolicyVersion,
  ContextPolicyVersionPage,
  SourceControlAccessDecisionKind,
  SourceControlJsonObject,
} from './source-control-v1-api-types.model';
import {
  ACCESS_DECISIONS,
  POLICY_DIAGNOSTIC_SEVERITIES,
  POLICY_STATES,
  array,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
  exactKeys,
  fail,
  integer,
  nullableCursor,
  opaqueIdArray,
  record,
  safeJsonObject,
} from './source-control-v1-api-validation.model';

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
