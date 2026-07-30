export const MODEL_INTELLIGENCE_SCHEMAS = {
  model_identity: 'ananta.model-intelligence.model-identity.v1',
  capability_descriptor:
    'ananta.model-intelligence.capability-descriptor.v1',
  analysis_job: 'ananta.model-intelligence.analysis-job.v1',
  artifact_ref: 'ananta.model-intelligence.artifact-ref.v1',
  error_envelope: 'ananta.model-intelligence.error-envelope.v1',
} as const;

export type ModelIntelligenceContractKind =
  keyof typeof MODEL_INTELLIGENCE_SCHEMAS;
export type JsonScalar = string | number | boolean | null;
export type ExtensionMap = Readonly<Record<string, JsonScalar>>;
export type CapabilityState =
  | 'supported'
  | 'conditional'
  | 'unsupported'
  | 'unknown';
export type CapabilityEvidence = 'declared' | 'probed' | 'inferred';
export type CapabilityReasonCode =
  | 'adapter_unavailable'
  | 'capability_not_declared'
  | 'capability_probe_failed'
  | 'dependency_unavailable'
  | 'format_unsupported'
  | 'requires_compatible_model_task'
  | 'requires_sentence_transformers_mode'
  | 'runtime_unsupported';
export type ModelIntelligenceReasonCode =
  | 'contract_invalid'
  | 'model_identity_invalid'
  | 'capability_unsupported'
  | 'analysis_request_invalid'
  | 'analysis_cancelled'
  | 'analysis_deadline_exceeded'
  | 'artifact_not_found'
  | 'artifact_integrity_mismatch'
  | 'artifact_store_unavailable'
  | 'resource_limit_exceeded'
  | 'runtime_unavailable'
  | 'policy_denied'
  | 'internal_error';

export interface ModelIdentity {
  readonly extensions?: ExtensionMap;
  readonly schema: typeof MODEL_INTELLIGENCE_SCHEMAS.model_identity;
  readonly model_id: string;
  readonly source: string;
  readonly locator: string;
  readonly revision: string;
  readonly content_sha256: string;
}

export interface CapabilityDescriptor {
  readonly extensions?: ExtensionMap;
  readonly schema:
    typeof MODEL_INTELLIGENCE_SCHEMAS.capability_descriptor;
  readonly model_id: string;
  readonly capability_id: string;
  readonly state: CapabilityState;
  readonly evidence: CapabilityEvidence;
  readonly adapter_id: string;
  readonly adapter_version: string;
  readonly reason_code?: CapabilityReasonCode | null;
}

export interface AnalysisJob {
  readonly extensions?: ExtensionMap;
  readonly schema: typeof MODEL_INTELLIGENCE_SCHEMAS.analysis_job;
  readonly job_id: string;
  readonly hub_task_id: string;
  readonly tenant_id: string;
  readonly model_id: string;
  readonly analysis_kind: string;
  readonly profile_id: string;
  readonly request_sha256: string;
  readonly requested_artifact_kinds: readonly string[];
  readonly max_runtime_seconds: number;
  readonly max_output_bytes: number;
}

export interface ArtifactRef {
  readonly extensions?: ExtensionMap;
  readonly schema: typeof MODEL_INTELLIGENCE_SCHEMAS.artifact_ref;
  readonly artifact_id: string;
  readonly job_id: string;
  readonly kind: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly media_type: string;
}

export interface ErrorEnvelope {
  readonly extensions?: ExtensionMap;
  readonly schema: typeof MODEL_INTELLIGENCE_SCHEMAS.error_envelope;
  readonly reason_code: ModelIntelligenceReasonCode;
  readonly retryable: boolean;
  readonly details: Readonly<Record<string, JsonScalar>>;
}

export type ModelIntelligenceContract =
  | ModelIdentity
  | CapabilityDescriptor
  | AnalysisJob
  | ArtifactRef
  | ErrorEnvelope;

const MODEL_ID = /^model_[0-9a-f]{64}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const SOURCE = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const COORDINATE = /^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,511}$/;
const REVISION = /^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,127}$/;
const KIND =
  /^[a-z][a-z0-9-]{0,31}(?:\.[a-z][a-z0-9-]{0,31}){1,7}$/;
const VERSION = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$/;
const MEDIA_TYPE =
  /^[a-z0-9][a-z0-9.+-]{0,63}\/[a-z0-9][a-z0-9.+-]{0,63}$/;
const EXTENSION_KEY = /^x-[a-z0-9]+(?:[._-][a-z0-9]+){0,7}$/;
const SENSITIVE_KEY_PARTS = [
  'authorization',
  'cookie',
  'credential',
  'password',
  'private',
  'secret',
  'token',
] as const;
const SENSITIVE_VALUE =
  /(bearer\s+|password\s*=|api[_-]?key\s*=|token\s*=|-----BEGIN [A-Z ]*PRIVATE KEY-----)/i;
const CAPABILITY_STATES = new Set<CapabilityState>([
  'supported',
  'conditional',
  'unsupported',
  'unknown',
]);
const CAPABILITY_EVIDENCE = new Set<CapabilityEvidence>([
  'declared',
  'probed',
  'inferred',
]);
const CAPABILITY_REASONS = new Set<CapabilityReasonCode>([
  'adapter_unavailable',
  'capability_not_declared',
  'capability_probe_failed',
  'dependency_unavailable',
  'format_unsupported',
  'requires_compatible_model_task',
  'requires_sentence_transformers_mode',
  'runtime_unsupported',
]);
const ERROR_REASONS = new Set<ModelIntelligenceReasonCode>([
  'contract_invalid',
  'model_identity_invalid',
  'capability_unsupported',
  'analysis_request_invalid',
  'analysis_cancelled',
  'analysis_deadline_exceeded',
  'artifact_not_found',
  'artifact_integrity_mismatch',
  'artifact_store_unavailable',
  'resource_limit_exceeded',
  'runtime_unavailable',
  'policy_denied',
  'internal_error',
]);
const RETRYABLE_REASONS = new Set<ModelIntelligenceReasonCode>([
  'analysis_deadline_exceeded',
  'artifact_store_unavailable',
  'runtime_unavailable',
]);
const ERROR_DETAIL_KEYS = new Set([
  'adapter_id',
  'analysis_kind',
  'artifact_id',
  'capability_id',
  'field',
  'job_id',
  'limit_name',
  'model_id',
  'operation',
  'task_id',
]);

type JsonRecord = Record<string, unknown>;

export class ModelIntelligenceContractError extends Error {
  readonly reasonCode: ModelIntelligenceReasonCode = 'contract_invalid';

  constructor() {
    super('contract_invalid');
    this.name = 'ModelIntelligenceContractError';
  }
}

function record(value: unknown): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('object_required');
  }
  return value as JsonRecord;
}

function exactKeys(
  value: JsonRecord,
  required: readonly string[],
  optional: readonly string[] = [],
): void {
  const allowed = new Set([...required, ...optional]);
  if (
    required.some((key) => !(key in value))
    || Object.keys(value).some((key) => !allowed.has(key))
  ) {
    throw new Error('contract_fields_invalid');
  }
}

function stringMatching(
  value: unknown,
  pattern: RegExp,
): value is string {
  return typeof value === 'string' && pattern.test(value);
}

function integerInRange(
  value: unknown,
  minimum: number,
  maximum: number,
): value is number {
  return (
    typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= minimum
    && value <= maximum
  );
}

function isScalar(value: unknown): value is JsonScalar {
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  return (
    typeof value === 'string'
    && value.trim().length > 0
    && value.trim().length <= 256
    && !/[\u0000-\u001f]/.test(value)
    && !SENSITIVE_VALUE.test(value)
  );
}

function extensions(value: unknown): void {
  if (value === undefined) return;
  const extensionRecord = record(value);
  if (
    Object.keys(extensionRecord).length > 16
    || Object.entries(extensionRecord).some(
      ([key, item]) =>
        !EXTENSION_KEY.test(key)
        || SENSITIVE_KEY_PARTS.some((part) => key.includes(part))
        || !isScalar(item),
    )
  ) {
    throw new Error('extensions_invalid');
  }
}

function coordinate(value: unknown, pattern: RegExp): value is string {
  if (!stringMatching(value, pattern)) return false;
  return (
    !value.includes('\\')
    && !value.includes('://')
    && !value.includes('//')
    && value.split('/').every((segment) => segment !== '.' && segment !== '..')
  );
}

function modelIdentity(value: unknown): ModelIdentity {
  const payload = record(value);
  exactKeys(
    payload,
    [
      'schema',
      'model_id',
      'source',
      'locator',
      'revision',
      'content_sha256',
    ],
    ['extensions'],
  );
  extensions(payload['extensions']);
  if (
    payload['schema'] !== MODEL_INTELLIGENCE_SCHEMAS.model_identity
    || !stringMatching(payload['model_id'], MODEL_ID)
    || !stringMatching(payload['source'], SOURCE)
    || !coordinate(payload['locator'], COORDINATE)
    || !coordinate(payload['revision'], REVISION)
    || !stringMatching(payload['content_sha256'], SHA256)
  ) {
    throw new Error('model_identity_invalid');
  }
  return payload as unknown as ModelIdentity;
}

function capabilityDescriptor(value: unknown): CapabilityDescriptor {
  const payload = record(value);
  exactKeys(
    payload,
    [
      'schema',
      'model_id',
      'capability_id',
      'state',
      'evidence',
      'adapter_id',
      'adapter_version',
    ],
    ['reason_code', 'extensions'],
  );
  extensions(payload['extensions']);
  const state = payload['state'] as CapabilityState;
  const reason = payload['reason_code'] as CapabilityReasonCode | null | undefined;
  if (
    payload['schema']
      !== MODEL_INTELLIGENCE_SCHEMAS.capability_descriptor
    || !stringMatching(payload['model_id'], MODEL_ID)
    || !stringMatching(payload['capability_id'], KIND)
    || !CAPABILITY_STATES.has(state)
    || !CAPABILITY_EVIDENCE.has(payload['evidence'] as CapabilityEvidence)
    || !stringMatching(payload['adapter_id'], IDENTIFIER)
    || !stringMatching(payload['adapter_version'], VERSION)
    || (state === 'supported' && reason != null)
    || (state !== 'supported' && !CAPABILITY_REASONS.has(reason as CapabilityReasonCode))
  ) {
    throw new Error('capability_descriptor_invalid');
  }
  return payload as unknown as CapabilityDescriptor;
}

function analysisJob(value: unknown): AnalysisJob {
  const payload = record(value);
  exactKeys(
    payload,
    [
      'schema',
      'job_id',
      'hub_task_id',
      'tenant_id',
      'model_id',
      'analysis_kind',
      'profile_id',
      'request_sha256',
      'requested_artifact_kinds',
      'max_runtime_seconds',
      'max_output_bytes',
    ],
    ['extensions'],
  );
  extensions(payload['extensions']);
  const artifactKinds = payload['requested_artifact_kinds'];
  if (
    payload['schema'] !== MODEL_INTELLIGENCE_SCHEMAS.analysis_job
    || !stringMatching(payload['job_id'], IDENTIFIER)
    || !stringMatching(payload['hub_task_id'], IDENTIFIER)
    || !stringMatching(payload['tenant_id'], IDENTIFIER)
    || !stringMatching(payload['model_id'], MODEL_ID)
    || !stringMatching(payload['analysis_kind'], KIND)
    || !stringMatching(payload['profile_id'], KIND)
    || !stringMatching(payload['request_sha256'], SHA256)
    || !Array.isArray(artifactKinds)
    || artifactKinds.length < 1
    || artifactKinds.length > 16
    || artifactKinds.some((kind) => !stringMatching(kind, KIND))
    || new Set(artifactKinds).size !== artifactKinds.length
    || !integerInRange(payload['max_runtime_seconds'], 1, 86_400)
    || !integerInRange(payload['max_output_bytes'], 1, 1_073_741_824)
  ) {
    throw new Error('analysis_job_invalid');
  }
  return payload as unknown as AnalysisJob;
}

function artifactRef(value: unknown): ArtifactRef {
  const payload = record(value);
  exactKeys(
    payload,
    [
      'schema',
      'artifact_id',
      'job_id',
      'kind',
      'sha256',
      'size_bytes',
      'media_type',
    ],
    ['extensions'],
  );
  extensions(payload['extensions']);
  if (
    payload['schema'] !== MODEL_INTELLIGENCE_SCHEMAS.artifact_ref
    || !stringMatching(payload['artifact_id'], IDENTIFIER)
    || !stringMatching(payload['job_id'], IDENTIFIER)
    || !stringMatching(payload['kind'], KIND)
    || !stringMatching(payload['sha256'], SHA256)
    || !integerInRange(payload['size_bytes'], 0, 107_374_182_400)
    || !stringMatching(payload['media_type'], MEDIA_TYPE)
  ) {
    throw new Error('artifact_ref_invalid');
  }
  return payload as unknown as ArtifactRef;
}

function errorEnvelope(value: unknown): ErrorEnvelope {
  const payload = record(value);
  exactKeys(
    payload,
    ['schema', 'reason_code', 'retryable', 'details'],
    ['extensions'],
  );
  extensions(payload['extensions']);
  const reason = payload['reason_code'] as ModelIntelligenceReasonCode;
  const details = record(payload['details']);
  if (
    payload['schema'] !== MODEL_INTELLIGENCE_SCHEMAS.error_envelope
    || !ERROR_REASONS.has(reason)
    || typeof payload['retryable'] !== 'boolean'
    || payload['retryable'] !== RETRYABLE_REASONS.has(reason)
    || Object.keys(details).length > 16
    || Object.entries(details).some(
      ([key, item]) => !ERROR_DETAIL_KEYS.has(key) || !isScalar(item),
    )
  ) {
    throw new Error('error_envelope_invalid');
  }
  return payload as unknown as ErrorEnvelope;
}

export function parseModelIntelligenceContract(
  kind: ModelIntelligenceContractKind,
  value: unknown,
): ModelIntelligenceContract {
  try {
    switch (kind) {
      case 'model_identity':
        return modelIdentity(value);
      case 'capability_descriptor':
        return capabilityDescriptor(value);
      case 'analysis_job':
        return analysisJob(value);
      case 'artifact_ref':
        return artifactRef(value);
      case 'error_envelope':
        return errorEnvelope(value);
      default:
        throw new Error('contract_kind_invalid');
    }
  } catch {
    throw new ModelIntelligenceContractError();
  }
}
