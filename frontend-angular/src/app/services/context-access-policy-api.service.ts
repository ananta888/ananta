import { Injectable, inject } from '@angular/core';
import { Observable, forkJoin, map, of, switchMap, throwError } from 'rxjs';
import {
  ContextAccessPolicy,
  ContextAccessRule,
  ContextPolicyRecord,
  ContextPolicyRouteCapability,
  ContextPolicyValidationResult,
  ModelScope,
  Sensitivity,
  SourceType,
} from '../models/context-access-policy.model';
import {
  SourceControlV1ApiClient,
} from './source-control-v1-api.client';
import { ContextPolicyVersion } from '../models/source-control-v1-api.model';

export const CONTEXT_POLICY_ROUTE_CAPABILITIES: readonly ContextPolicyRouteCapability[] = Object.freeze([
  {
    flow: 'list',
    routeAvailable: true,
    mutating: false,
    reason: 'GET /api/source-control/v1/context-policies ist Hub-autorisierter Source-of-Truth.',
  },
  {
    flow: 'detail',
    routeAvailable: true,
    mutating: false,
    reason: 'Die kanonische Versionsliste liefert IDs; die konkrete Version wird anschließend über v1 gelesen.',
  },
  {
    flow: 'create',
    routeAvailable: true,
    mutating: true,
    reason: 'POST /api/source-control/v1/context-policies/{policy_id}/drafts ist idempotent und versionsgebunden.',
  },
  {
    flow: 'validate',
    routeAvailable: false,
    mutating: false,
    reason: 'v1 lintet nur bereits persistierte Draft-Versionen; eine lokale Ersatzvalidierung ist nicht autoritativ.',
  },
  {
    flow: 'version',
    routeAvailable: true,
    mutating: false,
    reason: 'Persistierte Versionen werden cursorbasiert über v1 gelesen.',
  },
  {
    flow: 'draft',
    routeAvailable: true,
    mutating: true,
    reason: 'Die v1-Draft-Route nutzt expected_latest_version und Idempotency-Key.',
  },
  {
    flow: 'lint',
    routeAvailable: true,
    mutating: false,
    reason: 'Die v1-Lint-Route liefert strukturierte serverseitige Diagnosen.',
  },
  {
    flow: 'preview',
    routeAvailable: true,
    mutating: false,
    reason: 'Die v1-Preview bindet Policy-Version, SourceRevision und Destination-ID.',
  },
  {
    flow: 'activate',
    routeAvailable: true,
    mutating: true,
    reason: 'Die v1-Activate-Route verlangt ETag und Idempotency-Key.',
  },
  {
    flow: 'revoke',
    routeAvailable: true,
    mutating: true,
    reason: 'Die v1-Revoke-Route verlangt ETag und Idempotency-Key.',
  },
  {
    flow: 'rollback',
    routeAvailable: true,
    mutating: true,
    reason: 'Die v1-Rollback-Route ist versions- und ETag-gebunden.',
  },
  {
    flow: 'presets',
    routeAvailable: false,
    mutating: false,
    reason: 'Kein serverseitiger Preset-Katalog vorhanden.',
  },
  {
    flow: 'destinations',
    routeAvailable: false,
    mutating: false,
    reason: 'Kein serverseitiger Destination-Katalog vorhanden.',
  },
  {
    flow: 'grant',
    routeAvailable: false,
    mutating: true,
    reason: 'Keine Grant- oder Approval-Mutationsroute vorhanden.',
  },
] satisfies ContextPolicyRouteCapability[]);

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export class ContextPolicyContractError extends Error {
  readonly status = 422;

  constructor() {
    super('Malformed context-policy response');
    this.name = 'ContextPolicyContractError';
  }
}

const POLICY_SCOPES = new Set(['system_default', 'project', 'blueprint_role', 'task', 'merged']);
const VALIDATION_STATES = new Set([
  'todo',
  'in_progress',
  'blocked',
  'done',
  'draft',
  'active',
  'archived',
  'superseded',
  'revoked',
]);
const SOURCE_TYPES = new Set<string>(Object.values(SourceType));
const SENSITIVITIES = new Set<string>(Object.values(Sensitivity));
const MODEL_SCOPES = new Set<string>(Object.values(ModelScope));
const STRING_ARRAY_FIELDS = [
  'allowed_worker_kinds',
  'denied_worker_kinds',
  'allowed_runtime_kinds',
  'denied_runtime_kinds',
  'allowed_provider_locations',
  'denied_provider_locations',
  'reason_tags',
] as const;
const BOOLEAN_FIELDS = [
  'read_allowed',
  'write_allowed',
  'send_allowed',
  'cloud_allowed',
  'external_worker_allowed',
  'redaction_required',
  'summarization_allowed',
  'approval_required',
] as const;

function contractError(): never {
  throw new ContextPolicyContractError();
}

function parsePolicyPayload(value: unknown): Record<string, unknown> {
  if (typeof value !== 'string') {
    const parsed = recordOf(value);
    if (!Object.keys(parsed).length && value !== undefined && value !== null) contractError();
    return parsed;
  }
  try {
    const parsed = recordOf(JSON.parse(value));
    if (!Object.keys(parsed).length) contractError();
    return parsed;
  } catch {
    return contractError();
  }
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function requiredString(value: unknown): string {
  const normalized = stringValue(value);
  return normalized || contractError();
}

function optionalString(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  return requiredString(value);
}

function versionValue(value: unknown, minimum = 0): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum) contractError();
  return parsed;
}

function requireStringArray(value: unknown, allowedValues?: ReadonlySet<string>): void {
  if (!Array.isArray(value)) contractError();
  for (const item of value) {
    if (typeof item !== 'string' || !item.trim()) contractError();
    if (allowedValues && !allowedValues.has(item)) contractError();
  }
}

function validateRule(value: unknown): ContextAccessRule {
  const rule = recordOf(value);
  if (!Object.keys(rule).length) contractError();
  requiredString(rule['id']);
  requiredString(rule['description']);
  if (rule['source_match'] !== undefined) optionalString(rule['source_match']);
  if (rule['source_types'] !== undefined) requireStringArray(rule['source_types'], SOURCE_TYPES);
  if (rule['sensitivity'] !== undefined && !SENSITIVITIES.has(requiredString(rule['sensitivity']))) {
    contractError();
  }
  if (rule['allowed_model_scopes'] !== undefined) requireStringArray(rule['allowed_model_scopes'], MODEL_SCOPES);
  if (rule['denied_model_scopes'] !== undefined) requireStringArray(rule['denied_model_scopes'], MODEL_SCOPES);
  for (const field of STRING_ARRAY_FIELDS) {
    if (rule[field] !== undefined) requireStringArray(rule[field]);
  }
  for (const field of BOOLEAN_FIELDS) {
    if (rule[field] !== undefined && typeof rule[field] !== 'boolean') contractError();
  }
  return rule as unknown as ContextAccessRule;
}

export function normalizeContextPolicyRecord(value: unknown): ContextPolicyRecord {
  const record = recordOf(value);
  if (!Object.keys(record).length) contractError();
  const hasPolicyPayload = Object.prototype.hasOwnProperty.call(record, 'policy_json');
  const policyPayload = hasPolicyPayload ? parsePolicyPayload(record['policy_json']) : record;
  const recordPolicyId = optionalString(record['policy_id']);
  const payloadPolicyId = optionalString(policyPayload['policy_id']);
  if (recordPolicyId && payloadPolicyId && recordPolicyId !== payloadPolicyId) contractError();
  const policyId = requiredString(payloadPolicyId || recordPolicyId);
  const recordVersion = record['version'] === undefined ? undefined : versionValue(record['version'], 1);
  const payloadVersion = policyPayload['version'] === undefined ? undefined : versionValue(policyPayload['version'], 1);
  if (recordVersion !== undefined && payloadVersion !== undefined && recordVersion !== payloadVersion) contractError();
  const version = payloadVersion ?? recordVersion ?? contractError();
  const recordScope = optionalString(record['scope']);
  const payloadScope = optionalString(policyPayload['scope']);
  if (recordScope && payloadScope && recordScope !== payloadScope) contractError();
  const scope = requiredString(payloadScope || recordScope);
  if (!POLICY_SCOPES.has(scope)) contractError();
  const rawRules = Array.isArray(policyPayload['rules'])
    ? policyPayload['rules']
    : (Array.isArray(record['rules']) ? record['rules'] : (
      policyPayload['rules'] === undefined && record['rules'] === undefined ? [] : contractError()
    ));
  const defaultsValue = policyPayload['defaults'] ?? record['defaults'];
  if (defaultsValue !== undefined && (typeof defaultsValue !== 'object' || defaultsValue === null || Array.isArray(defaultsValue))) {
    contractError();
  }
  const precedenceValue = policyPayload['precedence'] ?? record['precedence'];
  const precedence = precedenceValue === undefined ? 0 : versionValue(precedenceValue, 0);
  const validationStateValue = optionalString(policyPayload['validation_state'] ?? record['validation_state']) || 'draft';
  if (!VALIDATION_STATES.has(validationStateValue)) contractError();
  const projectId = optionalString(record['project_id'] ?? policyPayload['project_id']);
  const createdAtValue = record['created_at'] ?? policyPayload['created_at'];
  const updatedAtValue = record['updated_at'] ?? policyPayload['updated_at'];
  if (
    createdAtValue !== undefined
    && createdAtValue !== null
    && typeof createdAtValue !== 'string'
    && (typeof createdAtValue !== 'number' || !Number.isFinite(createdAtValue))
  ) contractError();
  if (
    updatedAtValue !== undefined
    && updatedAtValue !== null
    && typeof updatedAtValue !== 'string'
    && (typeof updatedAtValue !== 'number' || !Number.isFinite(updatedAtValue))
  ) contractError();
  const createdAt = createdAtValue === undefined || createdAtValue === null ? undefined : String(createdAtValue);
  const updatedAt = updatedAtValue === undefined || updatedAtValue === null ? undefined : String(updatedAtValue);
  const policy: ContextAccessPolicy = {
    policy_id: policyId,
    version,
    scope,
    rules: rawRules.map(validateRule),
    defaults: recordOf(defaultsValue),
    precedence,
    created_at: createdAt,
    updated_at: updatedAt,
    validation_state: validationStateValue as ContextAccessPolicy['validation_state'],
  };
  return {
    policy_id: policyId,
    version,
    project_id: projectId,
    scope,
    created_at: policy.created_at,
    updated_at: policy.updated_at,
    policy,
    raw: record,
  };
}

function canonicalContextPolicyRecord(
  value: ContextPolicyVersion,
): ContextPolicyRecord {
  const validationState =
    value.state === 'superseded' || value.state === 'revoked'
      ? 'archived'
      : value.state;
  return normalizeContextPolicyRecord({
    policy_id: value.policy_id,
    version: value.version,
    project_id: value.project_id,
    scope: value.document['scope'],
    created_at: value.created_at,
    updated_at: value.created_at,
    policy_digest: value.policy_digest,
    etag: value.etag,
    policy_json: {
      ...value.document,
      version: value.version,
      created_at: value.created_at,
      updated_at: value.created_at,
      validation_state: validationState,
    },
  });
}

@Injectable({ providedIn: 'root' })
export class ContextAccessPolicyApiService {
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);

  listPolicies(baseUrl: string, projectId: string, token?: string): Observable<ContextPolicyRecord[]> {
    void baseUrl;
    void projectId;
    void token;
    return this.sourceControlApi.listContextPolicies({ limit: 200 }).pipe(
      switchMap((page) => {
        if (page.next_cursor !== null) {
          return throwError(
            () => new ContextPolicyContractError(),
          );
        }
        if (page.items.length === 0) return of([]);
        return forkJoin(
          page.items.map((item) =>
            this.sourceControlApi
              .getContextPolicyVersion(item.policy_id, item.latest_version)
              .pipe(map(({ policy }) => canonicalContextPolicyRecord(policy))),
          ),
        );
      }),
    );
  }

  getLatestPolicy(baseUrl: string, policyId: string, token?: string): Observable<ContextPolicyRecord> {
    void baseUrl;
    void token;
    return this.sourceControlApi
      .listContextPolicyVersions(policyId, { limit: 200 })
      .pipe(
        switchMap((page) => {
          if (page.next_cursor !== null || page.items.length === 0) {
            return throwError(
              () => new ContextPolicyContractError(),
            );
          }
          const latest = page.items.reduce((left, right) =>
            right.version > left.version ? right : left,
          );
          return of(canonicalContextPolicyRecord(latest));
        }),
      );
  }

  createPolicy(
    baseUrl: string,
    projectId: string,
    policy: ContextAccessPolicy,
    token?: string,
  ): Observable<ContextPolicyRecord> {
    void baseUrl;
    void projectId;
    void token;
    const document = {
      schema: 'ananta.context-access-policy.v1',
      policy_id: policy.policy_id,
      scope: policy.scope,
      defaults: policy.defaults,
      rules: policy.rules,
      precedence: policy.precedence,
    };
    return this.sourceControlApi.createContextPolicyDraft(
      policy.policy_id,
      {
        document,
        expected_latest_version:
          policy.version > 1 ? policy.version - 1 : null,
      },
      `ui:${crypto.randomUUID()}`,
    ).pipe(
      map(canonicalContextPolicyRecord),
    );
  }

  validatePolicy(
    baseUrl: string,
    policy: ContextAccessPolicy,
    token?: string,
  ): Observable<ContextPolicyValidationResult> {
    void baseUrl;
    void policy;
    void token;
    return throwError(
      () => new ContextPolicyContractError(),
    );
  }
}
