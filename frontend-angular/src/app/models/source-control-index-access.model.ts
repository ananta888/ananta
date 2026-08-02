export interface SourceControlIndexAccessSourceRevision {
  readonly source_revision_id: string;
  readonly revision_digest: string;
  readonly admission_state: string;
  readonly captured_at: string;
}

export interface SourceControlIndexAccessDestination {
  readonly destination_id: string;
  readonly worker_id: string;
  readonly runtime_kind: string;
  readonly provider_location: 'local_container';
  readonly data_residency: 'local';
}

export interface SourceControlIndexAccessEffect {
  readonly provider_location: 'local';
  readonly transformation: 'redacted';
  readonly one_time: true;
}

export interface SourceControlIndexAccessDuration {
  readonly minimum: number;
  readonly maximum: number;
  readonly default: number;
}

export interface SourceControlIndexAccessOption {
  readonly option_id: string;
  readonly preset_id: string;
  readonly label: string;
  readonly effect: SourceControlIndexAccessEffect;
  readonly duration_seconds: SourceControlIndexAccessDuration;
}

export interface SourceControlIndexAccessPreparation {
  readonly connection_id: string;
  readonly source_revision: SourceControlIndexAccessSourceRevision;
  readonly destinations: readonly SourceControlIndexAccessDestination[];
  readonly options: readonly SourceControlIndexAccessOption[];
  readonly readiness: { readonly ready: boolean; readonly reason_codes: readonly string[] };
  readonly etag: string;
}

export interface SourceControlIndexAccessResult {
  readonly access_ready: true;
  readonly connection_id: string;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly option_id: string;
  readonly effect: SourceControlIndexAccessEffect;
  readonly policy: {
    readonly policy_id: string;
    readonly version: number;
    readonly state: string;
    readonly etag: string;
  };
  readonly grant: {
    readonly grant_id: string;
    readonly state: string;
    readonly etag: string;
    readonly expires_at: string;
  };
  readonly next_actions: readonly ['start_index_run'];
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$/;
const SHA256 = /^[0-9a-f]{64}$/;

export function parseSourceControlIndexAccessPreparation(
  value: unknown,
): SourceControlIndexAccessPreparation {
  const data = exactRecord(value, [
    'connection_id', 'source_revision', 'destinations', 'options', 'readiness', 'etag',
  ], 'index_access_preparation');
  const revision = exactRecord(data['source_revision'], [
    'source_revision_id', 'revision_digest', 'admission_state', 'captured_at',
  ], 'index_access_preparation.source_revision');
  const readiness = exactRecord(data['readiness'], ['ready', 'reason_codes'], 'index_access_preparation.readiness');
  if (typeof readiness['ready'] !== 'boolean' || !Array.isArray(readiness['reason_codes'])) {
    fail('index_access_preparation.readiness_invalid');
  }
  return {
    connection_id: identifier(data['connection_id'], 'index_access_preparation.connection_id'),
    source_revision: {
      source_revision_id: identifier(revision['source_revision_id'], 'index_access_preparation.source_revision.source_revision_id'),
      revision_digest: sha256(revision['revision_digest'], 'index_access_preparation.source_revision.revision_digest'),
      admission_state: text(revision['admission_state'], 'index_access_preparation.source_revision.admission_state'),
      captured_at: text(revision['captured_at'], 'index_access_preparation.source_revision.captured_at'),
    },
    destinations: array(data['destinations'], 'index_access_preparation.destinations').map(parseDestination),
    options: array(data['options'], 'index_access_preparation.options').map(parseOption),
    readiness: {
      ready: readiness['ready'],
      reason_codes: readiness['reason_codes'].map((reason, index) =>
        identifier(reason, `index_access_preparation.readiness.reason_codes[${index}]`)),
    },
    etag: sha256(data['etag'], 'index_access_preparation.etag'),
  };
}

export function parseSourceControlIndexAccessResult(
  value: unknown,
): SourceControlIndexAccessResult {
  const data = exactRecord(value, [
    'access_ready', 'connection_id', 'source_revision_id', 'destination_id', 'option_id',
    'effect', 'policy', 'grant', 'next_actions',
  ], 'index_access_result');
  if (data['access_ready'] !== true) fail('index_access_result.access_ready_invalid');
  const policy = exactRecord(data['policy'], ['policy_id', 'version', 'state', 'etag'], 'index_access_result.policy');
  const grant = exactRecord(data['grant'], ['grant_id', 'state', 'etag', 'expires_at'], 'index_access_result.grant');
  const actions = array(data['next_actions'], 'index_access_result.next_actions');
  if (actions.length !== 1 || actions[0] !== 'start_index_run') {
    fail('index_access_result.next_actions_invalid');
  }
  return {
    access_ready: true,
    connection_id: identifier(data['connection_id'], 'index_access_result.connection_id'),
    source_revision_id: identifier(data['source_revision_id'], 'index_access_result.source_revision_id'),
    destination_id: identifier(data['destination_id'], 'index_access_result.destination_id'),
    option_id: identifier(data['option_id'], 'index_access_result.option_id'),
    effect: parseEffect(data['effect'], 'index_access_result.effect'),
    policy: {
      policy_id: identifier(policy['policy_id'], 'index_access_result.policy.policy_id'),
      version: positiveInteger(policy['version'], 'index_access_result.policy.version'),
      state: text(policy['state'], 'index_access_result.policy.state'),
      etag: sha256(policy['etag'], 'index_access_result.policy.etag'),
    },
    grant: {
      grant_id: identifier(grant['grant_id'], 'index_access_result.grant.grant_id'),
      state: text(grant['state'], 'index_access_result.grant.state'),
      etag: sha256(grant['etag'], 'index_access_result.grant.etag'),
      expires_at: text(grant['expires_at'], 'index_access_result.grant.expires_at'),
    },
    next_actions: ['start_index_run'],
  };
}

function parseDestination(value: unknown, index: number): SourceControlIndexAccessDestination {
  const path = `index_access_preparation.destinations[${index}]`;
  const item = exactRecord(value, [
    'destination_id', 'worker_id', 'runtime_kind', 'provider_location', 'data_residency',
  ], path);
  if (item['provider_location'] !== 'local_container') fail(`${path}.provider_location_invalid`);
  if (item['data_residency'] !== 'local') fail(`${path}.data_residency_invalid`);
  return {
    destination_id: identifier(item['destination_id'], `${path}.destination_id`),
    worker_id: identifier(item['worker_id'], `${path}.worker_id`),
    runtime_kind: text(item['runtime_kind'], `${path}.runtime_kind`),
    provider_location: 'local_container',
    data_residency: 'local',
  };
}

function parseOption(value: unknown, index: number): SourceControlIndexAccessOption {
  const path = `index_access_preparation.options[${index}]`;
  const item = exactRecord(value, ['option_id', 'preset_id', 'label', 'effect', 'duration_seconds'], path);
  const duration = exactRecord(item['duration_seconds'], ['minimum', 'maximum', 'default'], `${path}.duration_seconds`);
  const minimum = positiveInteger(duration['minimum'], `${path}.duration_seconds.minimum`);
  const maximum = positiveInteger(duration['maximum'], `${path}.duration_seconds.maximum`);
  const defaultValue = positiveInteger(duration['default'], `${path}.duration_seconds.default`);
  if (maximum < minimum || defaultValue < minimum || defaultValue > maximum) {
    fail(`${path}.duration_seconds_invalid`);
  }
  return {
    option_id: identifier(item['option_id'], `${path}.option_id`),
    preset_id: identifier(item['preset_id'], `${path}.preset_id`),
    label: text(item['label'], `${path}.label`),
    effect: parseEffect(item['effect'], `${path}.effect`),
    duration_seconds: { minimum, maximum, default: defaultValue },
  };
}

function parseEffect(value: unknown, path: string): SourceControlIndexAccessEffect {
  const effect = exactRecord(value, ['provider_location', 'transformation', 'one_time'], path);
  if (
    effect['provider_location'] !== 'local'
    || effect['transformation'] !== 'redacted'
    || effect['one_time'] !== true
  ) {
    fail(`${path}_unsafe`);
  }
  return { provider_location: 'local', transformation: 'redacted', one_time: true };
}

function exactRecord(value: unknown, keys: readonly string[], path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${path}_invalid`);
  const record = value as Record<string, unknown>;
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(`${path}_keys_invalid`);
  }
  return record;
}

function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) fail(`${path}_invalid`);
  return value;
}

function identifier(value: unknown, path: string): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail(`${path}_invalid`);
  return value;
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 512) fail(`${path}_invalid`);
  return value;
}

function sha256(value: unknown, path: string): string {
  if (typeof value !== 'string' || !SHA256.test(value)) fail(`${path}_invalid`);
  return value;
}

function positiveInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) fail(`${path}_invalid`);
  return Number(value);
}

function fail(reason: string): never {
  throw new Error(reason);
}
