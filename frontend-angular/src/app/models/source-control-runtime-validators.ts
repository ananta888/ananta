export const SOURCE_CONNECTION_V1_SCHEMA = 'ananta.source-control.source-connection.v1' as const;
export const SOURCE_REVISION_V1_SCHEMA = 'ananta.source-control.source-revision.v1' as const;
export const SOURCE_REF_MAPPING_V1_SCHEMA = 'ananta.source-control.source-ref-mapping.v1' as const;
export const DESTINATION_DESCRIPTOR_V1_SCHEMA = 'ananta.source-control.destination-descriptor.v1' as const;
export const SOURCE_ACCESS_GRANT_V1_SCHEMA = 'ananta.source-control.source-access-grant.v1' as const;
export const DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA = 'ananta.source-control.delegated-source-manifest-ref.v1' as const;

export type SourceConnectorType =
  | 'registered_workspace'
  | 'local_directory'
  | 'git'
  | 'github'
  | 'keycloak_docs'
  | 'wikimedia_dump'
  | 'web_doc'
  | 'local_dump'
  | 'open_notebook'
  | 'wiki';

export type SourceSensitivity =
  | 'public'
  | 'internal'
  | 'internal_high'
  | 'secret'
  | 'credential'
  | 'security_sensitive';

export interface SourceConnectionV1 {
  readonly schema: typeof SOURCE_CONNECTION_V1_SCHEMA;
  readonly authority: 'hub';
  readonly connection_id: string;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly owner_id: string;
  readonly connector_type: SourceConnectorType;
  readonly connection_identity_digest: string;
  readonly display_name: string;
  readonly sensitivity: SourceSensitivity;
  readonly state: 'draft' | 'active' | 'disabled' | 'tombstoned';
  readonly created_at: string;
}

export interface SourceRevisionV1 {
  readonly schema: typeof SOURCE_REVISION_V1_SCHEMA;
  readonly authority: 'hub';
  readonly source_revision_id: string;
  readonly connection_id: string;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly owner_id: string;
  readonly connector_type: SourceConnectorType;
  readonly sensitivity: SourceSensitivity;
  readonly revision_token: string;
  readonly revision_digest: string;
  readonly content_manifest_id: string;
  readonly content_manifest_digest: string;
  readonly admission_state: 'pending' | 'admitted' | 'blocked';
  readonly captured_at: string;
}

export interface SourceRefMappingV1 {
  readonly schema: typeof SOURCE_REF_MAPPING_V1_SCHEMA;
  readonly authority: 'hub';
  readonly source_ref_id: string;
  readonly connection_id: string;
  readonly source_revision_id: string;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly provenance_digest: string;
}

export interface DestinationDescriptorV1 {
  readonly schema: typeof DESTINATION_DESCRIPTOR_V1_SCHEMA;
  readonly authority: 'hub';
  readonly destination_id: string;
  readonly worker_id: string;
  readonly worker_kind: string;
  readonly runtime_id: string;
  readonly runtime_kind: string;
  readonly provider_id: string;
  readonly model_id: string;
  readonly model_class: string;
  readonly provider_location: 'local_container' | 'private_network' | 'tenant_region' | 'external_region';
  readonly data_residency: string;
}

export interface SourceAccessGrantV1 {
  readonly schema: typeof SOURCE_ACCESS_GRANT_V1_SCHEMA;
  readonly authority: 'hub';
  readonly grant_id: string;
  readonly version: number;
  readonly tenant_id: string;
  readonly project_id: string;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly operation:
    | 'inventory'
    | 'index'
    | 'retrieve'
    | 'analyze'
    | 'summarize'
    | 'chat_context'
    | 'tool_context'
    | 'export';
  readonly transformation: 'raw' | 'redacted' | 'summary';
  readonly purpose: string;
  readonly policy_version: string;
  readonly state: 'draft' | 'active' | 'superseded' | 'revoked';
  readonly issued_at: string;
  readonly expires_at: string;
}

export interface DelegatedSourceManifestRefV1 {
  readonly schema: typeof DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA;
  readonly authority: 'hub';
  readonly manifest_id: string;
  readonly manifest_digest: string;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly source_access_grant_id: string;
  readonly policy_version: string;
}

export type SourceControlRuntimeArtifact =
  | SourceConnectionV1
  | SourceRevisionV1
  | SourceRefMappingV1
  | DestinationDescriptorV1
  | SourceAccessGrantV1
  | DelegatedSourceManifestRefV1;

export type SourceControlRuntimeSchema = SourceControlRuntimeArtifact['schema'];

export interface SourceControlValidationIssue {
  readonly path: string;
  readonly code: 'type' | 'required' | 'additional_property' | 'const' | 'pattern' | 'enum' | 'format' | 'minimum';
}

export type SourceControlValidationResult<T extends SourceControlRuntimeArtifact = SourceControlRuntimeArtifact> =
  | Readonly<{ valid: true; value: T; issues: readonly [] }>
  | Readonly<{ valid: false; issues: readonly SourceControlValidationIssue[] }>;

type Predicate = (value: unknown) => boolean;
type Descriptor = Readonly<Record<string, Predicate>>;

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const CONNECTION_ID = /^conn_[0-9a-f]{64}$/;
const REVISION_ID = /^srev_[0-9a-f]{64}$/;
const SOURCE_REF_ID = /^sref_[0-9a-f]{64}$/;
const DESTINATION_ID = /^dst_[0-9a-f]{64}$/;
const GRANT_ID = /^grant_[0-9a-f]{64}$/;
const MANIFEST_ID = /^manifest_[0-9a-f]{64}$/;
const REVISION_TOKEN = /^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,255}$/;
const PURPOSE = /^[a-z][a-z0-9_.:-]{0,127}$/;
const RFC3339_DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|([+-])(\d{2}):(\d{2}))$/;

const CONNECTOR_TYPES = Object.freeze([
  'registered_workspace', 'local_directory', 'git', 'github', 'keycloak_docs',
  'wikimedia_dump', 'web_doc', 'local_dump', 'open_notebook', 'wiki',
]);
const SENSITIVITIES = Object.freeze([
  'public', 'internal', 'internal_high', 'secret', 'credential', 'security_sensitive',
]);

const stringPattern = (pattern: RegExp, minLength = 1, maxLength = Number.MAX_SAFE_INTEGER): Predicate =>
  (value) => typeof value === 'string'
    && value.length >= minLength
    && value.length <= maxLength
    && pattern.test(value);
const exact = (expected: string): Predicate => (value) => value === expected;
const oneOf = (values: readonly string[]): Predicate => (value) =>
  typeof value === 'string' && values.includes(value);
const dateTime: Predicate = (value) => {
  if (typeof value !== 'string') return false;
  const match = RFC3339_DATE_TIME.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[8] === 'Z' ? 0 : Number(match[10]);
  const offsetMinute = match[8] === 'Z' ? 0 : Number(match[11]);
  if (
    month < 1 || month > 12
    || day < 1 || day > 31
    || hour > 23
    || minute > 59
    || second > 59
    || offsetHour > 23
    || offsetMinute > 59
  ) return false;

  // Date.UTC treats years 0..99 as 1900..1999. setUTCFullYear avoids that
  // coercion and gives us an explicit calendar roundtrip for invalid dates.
  const calendar = new Date(0);
  calendar.setUTCHours(0, 0, 0, 0);
  calendar.setUTCFullYear(year, month - 1, day);
  if (
    calendar.getUTCFullYear() !== year
    || calendar.getUTCMonth() !== month - 1
    || calendar.getUTCDate() !== day
  ) return false;
  return Number.isFinite(Date.parse(value));
};
const positiveInteger: Predicate = (value) => Number.isSafeInteger(value) && Number(value) >= 1;

const DESCRIPTORS: Readonly<Record<SourceControlRuntimeSchema, Descriptor>> = Object.freeze({
  [SOURCE_CONNECTION_V1_SCHEMA]: Object.freeze({
    schema: exact(SOURCE_CONNECTION_V1_SCHEMA),
    authority: exact('hub'),
    connection_id: stringPattern(CONNECTION_ID),
    tenant_id: stringPattern(IDENTIFIER, 1, 128),
    project_id: stringPattern(IDENTIFIER, 1, 128),
    owner_id: stringPattern(IDENTIFIER, 1, 128),
    connector_type: oneOf(CONNECTOR_TYPES),
    connection_identity_digest: stringPattern(SHA256, 64, 64),
    display_name: stringPattern(/[\s\S]*/, 1, 200),
    sensitivity: oneOf(SENSITIVITIES),
    state: oneOf(['draft', 'active', 'disabled', 'tombstoned']),
    created_at: dateTime,
  }),
  [SOURCE_REVISION_V1_SCHEMA]: Object.freeze({
    schema: exact(SOURCE_REVISION_V1_SCHEMA),
    authority: exact('hub'),
    source_revision_id: stringPattern(REVISION_ID),
    connection_id: stringPattern(CONNECTION_ID),
    tenant_id: stringPattern(IDENTIFIER, 1, 128),
    project_id: stringPattern(IDENTIFIER, 1, 128),
    owner_id: stringPattern(IDENTIFIER, 1, 128),
    connector_type: oneOf(CONNECTOR_TYPES),
    sensitivity: oneOf(SENSITIVITIES),
    revision_token: stringPattern(REVISION_TOKEN, 1, 256),
    revision_digest: stringPattern(SHA256, 64, 64),
    content_manifest_id: stringPattern(MANIFEST_ID),
    content_manifest_digest: stringPattern(SHA256, 64, 64),
    admission_state: oneOf(['pending', 'admitted', 'blocked']),
    captured_at: dateTime,
  }),
  [SOURCE_REF_MAPPING_V1_SCHEMA]: Object.freeze({
    schema: exact(SOURCE_REF_MAPPING_V1_SCHEMA),
    authority: exact('hub'),
    source_ref_id: stringPattern(SOURCE_REF_ID),
    connection_id: stringPattern(CONNECTION_ID),
    source_revision_id: stringPattern(REVISION_ID),
    tenant_id: stringPattern(IDENTIFIER, 1, 128),
    project_id: stringPattern(IDENTIFIER, 1, 128),
    provenance_digest: stringPattern(SHA256, 64, 64),
  }),
  [DESTINATION_DESCRIPTOR_V1_SCHEMA]: Object.freeze({
    schema: exact(DESTINATION_DESCRIPTOR_V1_SCHEMA),
    authority: exact('hub'),
    destination_id: stringPattern(DESTINATION_ID),
    worker_id: stringPattern(IDENTIFIER, 1, 128),
    worker_kind: stringPattern(IDENTIFIER, 1, 128),
    runtime_id: stringPattern(IDENTIFIER, 1, 128),
    runtime_kind: stringPattern(IDENTIFIER, 1, 128),
    provider_id: stringPattern(IDENTIFIER, 1, 128),
    model_id: stringPattern(IDENTIFIER, 1, 128),
    model_class: stringPattern(IDENTIFIER, 1, 128),
    provider_location: oneOf(['local_container', 'private_network', 'tenant_region', 'external_region']),
    data_residency: stringPattern(IDENTIFIER, 1, 128),
  }),
  [SOURCE_ACCESS_GRANT_V1_SCHEMA]: Object.freeze({
    schema: exact(SOURCE_ACCESS_GRANT_V1_SCHEMA),
    authority: exact('hub'),
    grant_id: stringPattern(GRANT_ID),
    version: positiveInteger,
    tenant_id: stringPattern(IDENTIFIER, 1, 128),
    project_id: stringPattern(IDENTIFIER, 1, 128),
    source_revision_id: stringPattern(REVISION_ID),
    destination_id: stringPattern(DESTINATION_ID),
    operation: oneOf(['inventory', 'index', 'retrieve', 'analyze', 'summarize', 'chat_context', 'tool_context', 'export']),
    transformation: oneOf(['raw', 'redacted', 'summary']),
    purpose: stringPattern(PURPOSE, 1, 128),
    policy_version: stringPattern(IDENTIFIER, 1, 128),
    state: oneOf(['draft', 'active', 'superseded', 'revoked']),
    issued_at: dateTime,
    expires_at: dateTime,
  }),
  [DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA]: Object.freeze({
    schema: exact(DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA),
    authority: exact('hub'),
    manifest_id: stringPattern(MANIFEST_ID),
    manifest_digest: stringPattern(SHA256, 64, 64),
    source_revision_id: stringPattern(REVISION_ID),
    destination_id: stringPattern(DESTINATION_ID),
    source_access_grant_id: stringPattern(GRANT_ID),
    policy_version: stringPattern(IDENTIFIER, 1, 128),
  }),
});

export function validateSourceControlArtifact(
  value: unknown,
  expectedSchema?: SourceControlRuntimeSchema,
): SourceControlValidationResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return invalid([{ path: '$', code: 'type' }]);
  }
  const record = value as Record<string, unknown>;
  const schema = expectedSchema ?? (
    typeof record['schema'] === 'string' ? record['schema'] as SourceControlRuntimeSchema : undefined
  );
  const descriptor = schema ? DESCRIPTORS[schema] : undefined;
  if (!descriptor) return invalid([{ path: '$.schema', code: 'enum' }]);

  const issues: SourceControlValidationIssue[] = [];
  for (const key of Object.keys(descriptor)) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) {
      issues.push({ path: `$.${key}`, code: 'required' });
      continue;
    }
    if (!descriptor[key](record[key])) {
      issues.push({ path: `$.${key}`, code: issueCode(key, record[key]) });
    }
  }
  for (const key of Object.keys(record)) {
    if (!Object.prototype.hasOwnProperty.call(descriptor, key)) {
      issues.push({ path: `$.${key}`, code: 'additional_property' });
    }
  }
  if (issues.length) return invalid(issues);
  return Object.freeze({
    valid: true as const,
    value: value as SourceControlRuntimeArtifact,
    issues: Object.freeze([]) as readonly [],
  });
}

export function assertSourceControlArtifact<T extends SourceControlRuntimeArtifact>(
  value: unknown,
  expectedSchema: T['schema'],
): T {
  const result = validateSourceControlArtifact(value, expectedSchema);
  if (!result.valid) throw new SourceControlRuntimeValidationError(result.issues);
  return result.value as T;
}

export class SourceControlRuntimeValidationError extends Error {
  readonly status = 422;

  constructor(readonly issues: readonly SourceControlValidationIssue[]) {
    super('Malformed source-control artifact');
    this.name = 'SourceControlRuntimeValidationError';
  }
}

function issueCode(key: string, value: unknown): SourceControlValidationIssue['code'] {
  if (key === 'schema' || key === 'authority') return 'const';
  if (key === 'created_at' || key === 'captured_at' || key === 'issued_at' || key === 'expires_at') return 'format';
  if (key === 'version') return typeof value === 'number' ? 'minimum' : 'type';
  if (
    key === 'connector_type'
    || key === 'sensitivity'
    || key === 'state'
    || key === 'admission_state'
    || key === 'provider_location'
    || key === 'operation'
    || key === 'transformation'
  ) return 'enum';
  return typeof value === 'string' ? 'pattern' : 'type';
}

function invalid(issues: readonly SourceControlValidationIssue[]): SourceControlValidationResult {
  return Object.freeze({
    valid: false as const,
    issues: Object.freeze([...issues]),
  });
}
