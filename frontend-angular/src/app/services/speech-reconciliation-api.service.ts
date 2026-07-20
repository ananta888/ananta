import { Injectable } from '@angular/core';
import { Observable, map } from 'rxjs';

import { ApiBaseService } from './api-base.service';

export const SPEECH_RECONCILIATION_CONTRACT_VERSION = 'ananta.speech-reconciliation.v1';
export const MAX_SPEECH_RECONCILIATION_PAGE_SIZE = 100;
export const MAX_SPEECH_RECONCILIATION_SOURCE_DURATION_MS = 8 * 60 * 60 * 1_000;
export const MAX_SPEECH_RECONCILIATION_FACTOR = 100;

export type SpeechReconciliationState =
  | 'queued'
  | 'running'
  | 'paused'
  | 'cancel_requested'
  | 'completed'
  | 'dataset_only_completed'
  | 'failed'
  | 'cancelled'
  | 'expired';

export type SpeechReconciliationStage =
  | 'admission'
  | 'staging'
  | 'slow_asr'
  | 'alignment'
  | 'resolution'
  | 'dataset'
  | 'training_delegation'
  | 'evaluation'
  | 'finalization';

export type SpeechReconciliationAction = 'pause' | 'resume' | 'cancel' | 'reduce';

export interface SpeechResourceVectorView {
  readonly wall_time_ms: number;
  readonly cpu_time_ms: number;
  readonly gpu_time_ms: number;
  readonly memory_byte_ms: number;
  readonly disk_bytes: number;
  readonly checkpoint_bytes: number;
  readonly energy_millijoules: number;
}

export interface SpeechReconciliationBudgetView {
  readonly allocated: SpeechResourceVectorView;
  readonly reserved: SpeechResourceVectorView;
  readonly consumed: SpeechResourceVectorView;
  readonly remaining: SpeechResourceVectorView;
}

export interface SpeechReconciliationConflictCounts {
  readonly resolved: number;
  readonly unresolved: number;
  readonly rejected: number;
  readonly quarantined: number;
}

/** Content-free projection returned by the authoritative Hub. */
export interface SpeechReconciliationJobView {
  readonly job_id: string;
  readonly state: SpeechReconciliationState;
  readonly stage: SpeechReconciliationStage;
  readonly reason_code: string;
  readonly source_duration_ms: number;
  readonly max_compute_factor: number;
  readonly ledger_sequence: number;
  readonly key_epoch: number;
  readonly checkpoint_count: number;
  readonly conflict_counts: SpeechReconciliationConflictCounts;
  readonly budget: SpeechReconciliationBudgetView | null;
  readonly active_attempt_id: string | null;
  readonly version: number;
  readonly created_at_ms: number;
  readonly updated_at_ms: number;
  readonly finished_at_ms: number | null;
}

export interface SpeechReconciliationJobPage {
  readonly jobs: readonly SpeechReconciliationJobView[];
  readonly next_offset: number | null;
}

export interface SpeechReconciliationBudgetPlanView {
  readonly compute_factor: number;
  readonly compute_equivalent_ms: number;
  readonly allocated: SpeechResourceVectorView;
}

export interface SpeechReconciliationJobAcceptance extends SpeechReconciliationJobView {
  readonly budget_plan: SpeechReconciliationBudgetPlanView;
}

export interface SpeechReconciliationCreateRequest {
  readonly consent_id: string;
  readonly consent_version: number;
  readonly revocation_epoch: number;
  readonly input_manifest_digest: string;
  readonly policy_digest: string;
  readonly research_policy_ref: string | null;
  readonly max_compute_factor: number;
  readonly key_epoch: number;
  readonly deadline_at_ms: number;
  readonly resource_limits: SpeechResourceVectorView;
}

export interface SpeechReconciliationMutationRequest {
  readonly expected_version: number;
}

export interface SpeechReconciliationReduceRequest extends SpeechReconciliationMutationRequest {
  readonly max_compute_factor: number;
}

type Envelope<T> = Readonly<{ ok: true; data: T }>;

const JOB_FIELDS = [
  'job_id', 'state', 'stage', 'reason_code', 'source_duration_ms', 'max_compute_factor',
  'ledger_sequence', 'key_epoch', 'checkpoint_count', 'conflict_counts', 'budget',
  'active_attempt_id', 'version', 'created_at_ms', 'updated_at_ms', 'finished_at_ms',
] as const;
const RESOURCE_FIELDS = [
  'wall_time_ms', 'cpu_time_ms', 'gpu_time_ms', 'memory_byte_ms', 'disk_bytes',
  'checkpoint_bytes', 'energy_millijoules',
] as const;
const STATES = new Set<SpeechReconciliationState>([
  'queued', 'running', 'paused', 'cancel_requested', 'completed',
  'dataset_only_completed', 'failed', 'cancelled', 'expired',
]);
const STAGES = new Set<SpeechReconciliationStage>([
  'admission', 'staging', 'slow_asr', 'alignment', 'resolution', 'dataset',
  'training_delegation', 'evaluation', 'finalization',
]);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const MAX_COUNT = 10_000_000;
const MAX_TIMESTAMP_MS = 8_640_000_000_000_000;

export class SpeechReconciliationApiContractError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'SpeechReconciliationApiContractError';
  }
}

@Injectable({ providedIn: 'root' })
export class SpeechReconciliationApiService extends ApiBaseService {
  list(hubUrl: string, offset = 0, limit = 50): Observable<SpeechReconciliationJobPage> {
    boundedInteger(offset, 'offset', 0, 1_000_000);
    boundedInteger(limit, 'limit', 1, MAX_SPEECH_RECONCILIATION_PAGE_SIZE);
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    return this.core.get<unknown>(`${this.endpoint(hubUrl)}?${query.toString()}`, hubUrl, undefined, false).pipe(
      map(parseJobPageEnvelope),
    );
  }

  get(hubUrl: string, jobId: string): Observable<SpeechReconciliationJobView> {
    identifier(jobId, 'job_id');
    return this.core.get<unknown>(`${this.endpoint(hubUrl)}/${encodeURIComponent(jobId)}`, hubUrl, undefined, false).pipe(
      map(parseJobEnvelope),
    );
  }

  create(
    hubUrl: string,
    request: SpeechReconciliationCreateRequest,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobAcceptance> {
    validateSpeechReconciliationCreateRequest(request);
    validateIdempotencyKey(idempotencyKey);
    return this.core.request<unknown>('POST', this.endpoint(hubUrl), hubUrl, {
      body: request,
      headers: { 'Idempotency-Key': idempotencyKey },
    }).pipe(map(parseCreateEnvelope));
  }

  pause(
    hubUrl: string,
    jobId: string,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobView> {
    return this.mutate(hubUrl, jobId, 'pause', { expected_version: expectedVersion }, idempotencyKey);
  }

  resume(
    hubUrl: string,
    jobId: string,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobView> {
    return this.mutate(hubUrl, jobId, 'resume', { expected_version: expectedVersion }, idempotencyKey);
  }

  cancel(
    hubUrl: string,
    jobId: string,
    expectedVersion: number,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobView> {
    return this.mutate(hubUrl, jobId, 'cancel', { expected_version: expectedVersion }, idempotencyKey);
  }

  reduce(
    hubUrl: string,
    jobId: string,
    expectedVersion: number,
    maxComputeFactor: number,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobView> {
    boundedInteger(maxComputeFactor, 'max_compute_factor', 1, MAX_SPEECH_RECONCILIATION_FACTOR);
    return this.mutate(
      hubUrl,
      jobId,
      'reduce',
      { expected_version: expectedVersion, max_compute_factor: maxComputeFactor },
      idempotencyKey,
    );
  }

  private mutate(
    hubUrl: string,
    jobId: string,
    action: SpeechReconciliationAction,
    body: SpeechReconciliationMutationRequest | SpeechReconciliationReduceRequest,
    idempotencyKey: string,
  ): Observable<SpeechReconciliationJobView> {
    identifier(jobId, 'job_id');
    boundedInteger(body.expected_version, 'expected_version', 1, 2_147_483_647);
    validateIdempotencyKey(idempotencyKey);
    return this.core.request<unknown>(
      'POST',
      `${this.endpoint(hubUrl)}/${encodeURIComponent(jobId)}/${action}`,
      hubUrl,
      {
        body,
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${body.expected_version}"`,
        },
      },
    ).pipe(map(parseJobEnvelope));
  }

  private endpoint(hubUrl: string): string {
    const normalized = String(hubUrl || '').trim().replace(/\/+$/, '');
    if (!normalized) throw new SpeechReconciliationApiContractError('speech_reconciliation_hub_url_required');
    return `${normalized}/v1/voice/speech-reconciliation`;
  }
}

export function parseSpeechReconciliationJob(value: unknown): SpeechReconciliationJobView {
  const row = closedRecord(value, JOB_FIELDS, 'job');
  const state = enumValue(row['state'], STATES, 'state');
  const stage = enumValue(row['stage'], STAGES, 'stage');
  return Object.freeze({
    job_id: identifier(row['job_id'], 'job_id'),
    state,
    stage,
    reason_code: identifier(row['reason_code'], 'reason_code'),
    source_duration_ms: boundedInteger(
      row['source_duration_ms'], 'source_duration_ms', 1, MAX_SPEECH_RECONCILIATION_SOURCE_DURATION_MS,
    ),
    max_compute_factor: boundedInteger(
      row['max_compute_factor'], 'max_compute_factor', 1, MAX_SPEECH_RECONCILIATION_FACTOR,
    ),
    ledger_sequence: boundedInteger(row['ledger_sequence'], 'ledger_sequence', 0, Number.MAX_SAFE_INTEGER),
    key_epoch: boundedInteger(row['key_epoch'], 'key_epoch', 1, 2_147_483_647),
    checkpoint_count: boundedInteger(row['checkpoint_count'], 'checkpoint_count', 0, MAX_COUNT),
    conflict_counts: parseConflictCounts(row['conflict_counts']),
    budget: row['budget'] === null ? null : parseBudget(row['budget']),
    active_attempt_id: nullableIdentifier(row['active_attempt_id'], 'active_attempt_id'),
    version: boundedInteger(row['version'], 'version', 1, 2_147_483_647),
    created_at_ms: boundedInteger(row['created_at_ms'], 'created_at_ms', 0, MAX_TIMESTAMP_MS),
    updated_at_ms: boundedInteger(row['updated_at_ms'], 'updated_at_ms', 0, MAX_TIMESTAMP_MS),
    finished_at_ms: nullableInteger(row['finished_at_ms'], 'finished_at_ms', 0, MAX_TIMESTAMP_MS),
  });
}

function parseJobEnvelope(value: unknown): SpeechReconciliationJobView {
  const envelope = parseEnvelope(value);
  const data = closedRecord(envelope.data, ['job'], 'job_response');
  return parseSpeechReconciliationJob(data['job']);
}

function parseCreateEnvelope(value: unknown): SpeechReconciliationJobAcceptance {
  const envelope = parseEnvelope(value);
  const data = closedRecord(envelope.data, ['job'], 'job_response');
  const raw = closedRecord(data['job'], [...JOB_FIELDS, 'budget_plan'], 'created_job');
  const job = parseSpeechReconciliationJob(Object.fromEntries(JOB_FIELDS.map(field => [field, raw[field]])));
  const plan = closedRecord(raw['budget_plan'], ['compute_factor', 'compute_equivalent_ms', 'allocated'], 'budget_plan');
  return Object.freeze({
    ...job,
    budget_plan: Object.freeze({
      compute_factor: boundedInteger(
        plan['compute_factor'], 'budget_plan.compute_factor', 1, MAX_SPEECH_RECONCILIATION_FACTOR,
      ),
      compute_equivalent_ms: boundedInteger(
        plan['compute_equivalent_ms'], 'budget_plan.compute_equivalent_ms', 1, Number.MAX_SAFE_INTEGER,
      ),
      allocated: parseResourceVector(plan['allocated'], 'budget_plan.allocated'),
    }),
  });
}

function parseJobPageEnvelope(value: unknown): SpeechReconciliationJobPage {
  const envelope = parseEnvelope(value);
  const data = closedRecord(envelope.data, ['jobs', 'next_offset'], 'job_page');
  if (!Array.isArray(data['jobs']) || data['jobs'].length > MAX_SPEECH_RECONCILIATION_PAGE_SIZE) {
    fail('speech_reconciliation_page_size_invalid');
  }
  const nextOffset = data['next_offset'] === null
    ? null
    : boundedInteger(data['next_offset'], 'next_offset', 0, 1_000_000);
  return Object.freeze({
    jobs: Object.freeze(data['jobs'].map(parseSpeechReconciliationJob)),
    next_offset: nextOffset,
  });
}

function parseEnvelope(value: unknown): Envelope<unknown> {
  const envelope = closedRecord(value, ['ok', 'data'], 'envelope');
  if (envelope['ok'] !== true) fail('speech_reconciliation_envelope_invalid');
  return envelope as unknown as Envelope<unknown>;
}

function parseConflictCounts(value: unknown): SpeechReconciliationConflictCounts {
  const counts = closedRecord(value, ['resolved', 'unresolved', 'rejected', 'quarantined'], 'conflict_counts');
  return Object.freeze({
    resolved: boundedInteger(counts['resolved'], 'resolved', 0, MAX_COUNT),
    unresolved: boundedInteger(counts['unresolved'], 'unresolved', 0, MAX_COUNT),
    rejected: boundedInteger(counts['rejected'], 'rejected', 0, MAX_COUNT),
    quarantined: boundedInteger(counts['quarantined'], 'quarantined', 0, MAX_COUNT),
  });
}

function parseBudget(value: unknown): SpeechReconciliationBudgetView {
  const budget = closedRecord(value, ['allocated', 'reserved', 'consumed', 'remaining'], 'budget');
  const parsed = Object.freeze({
    allocated: parseResourceVector(budget['allocated'], 'allocated'),
    reserved: parseResourceVector(budget['reserved'], 'reserved'),
    consumed: parseResourceVector(budget['consumed'], 'consumed'),
    remaining: parseResourceVector(budget['remaining'], 'remaining'),
  });
  for (const field of RESOURCE_FIELDS) {
    if (
      BigInt(parsed.allocated[field])
      !== BigInt(parsed.reserved[field]) + BigInt(parsed.consumed[field]) + BigInt(parsed.remaining[field])
    ) {
      fail('speech_reconciliation_budget_arithmetic_invalid');
    }
  }
  return parsed;
}

function parseResourceVector(value: unknown, label: string): SpeechResourceVectorView {
  const vector = closedRecord(value, RESOURCE_FIELDS, label);
  return Object.freeze(Object.fromEntries(
    RESOURCE_FIELDS.map(field => [field, boundedInteger(vector[field], `${label}.${field}`, 0, Number.MAX_SAFE_INTEGER)]),
  )) as unknown as SpeechResourceVectorView;
}

export function validateSpeechReconciliationCreateRequest(
  value: SpeechReconciliationCreateRequest,
  nowMs = Date.now(),
): void {
  closedRecord(value, [
    'consent_id', 'consent_version', 'revocation_epoch', 'input_manifest_digest', 'policy_digest',
    'research_policy_ref', 'max_compute_factor', 'key_epoch', 'deadline_at_ms', 'resource_limits',
  ], 'create_request');
  identifier(value.consent_id, 'consent_id');
  boundedInteger(value.consent_version, 'consent_version', 1, 2_147_483_647);
  boundedInteger(value.revocation_epoch, 'revocation_epoch', 0, 2_147_483_647);
  digest(value.input_manifest_digest, 'input_manifest_digest');
  digest(value.policy_digest, 'policy_digest');
  if (value.research_policy_ref !== null && (
    !/^artifact:\/\/speech-policies\/[A-Za-z0-9_./:-]{1,480}$/.test(value.research_policy_ref)
    || value.research_policy_ref.includes('..')
  )) fail('speech_reconciliation_research_policy_ref_invalid');
  const factor = boundedInteger(
    value.max_compute_factor, 'max_compute_factor', 1, MAX_SPEECH_RECONCILIATION_FACTOR,
  );
  if (factor > 20 && value.research_policy_ref === null) fail('speech_reconciliation_research_policy_required');
  boundedInteger(value.key_epoch, 'key_epoch', 1, 2_147_483_647);
  boundedInteger(
    value.deadline_at_ms,
    'deadline_at_ms',
    nowMs + 60_000,
    Math.min(MAX_TIMESTAMP_MS, nowMs + 30 * 24 * 60 * 60 * 1_000),
  );
  const limits = parseResourceVector(value.resource_limits, 'resource_limits');
  if (RESOURCE_FIELDS.every(field => limits[field] === 0)) fail('speech_reconciliation_resource_limits_empty');
}

function validateIdempotencyKey(value: string): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 256 || !/^[\x21-\x7E]+$/.test(value)) {
    fail('speech_reconciliation_idempotency_key_invalid');
  }
  return value;
}

function closedRecord(
  value: unknown,
  fields: readonly string[],
  label: string,
): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`speech_reconciliation_${label}_invalid`);
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  if (keys.length !== fields.length || fields.some(field => !Object.hasOwn(record, field))) {
    fail(`speech_reconciliation_${label}_shape_invalid`);
  }
  return record;
}

function boundedInteger(value: unknown, label: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    fail(`speech_reconciliation_${label}_invalid`);
  }
  return value as number;
}

function nullableInteger(value: unknown, label: string, minimum: number, maximum: number): number | null {
  return value === null ? null : boundedInteger(value, label, minimum, maximum);
}

function identifier(value: unknown, label: string): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail(`speech_reconciliation_${label}_invalid`);
  return value as string;
}

function nullableIdentifier(value: unknown, label: string): string | null {
  return value === null ? null : identifier(value, label);
}

function digest(value: unknown, label: string): string {
  if (typeof value !== 'string' || !DIGEST.test(value)) fail(`speech_reconciliation_${label}_invalid`);
  return value as string;
}

function enumValue<T extends string>(value: unknown, allowed: ReadonlySet<T>, label: string): T {
  if (typeof value !== 'string' || !allowed.has(value as T)) fail(`speech_reconciliation_${label}_invalid`);
  return value as T;
}

function fail(reasonCode: string): never {
  throw new SpeechReconciliationApiContractError(reasonCode);
}
