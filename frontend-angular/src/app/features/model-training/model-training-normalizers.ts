import {
  AdapterSummary,
  DatasetDetail,
  DatasetFormat,
  DatasetRecord,
  DatasetSplit,
  DatasetSummary,
  DatasetValidationIssue,
  DatasetValidationReport,
  EvaluationReport,
  TrainingEventPage,
  TrainingJobAcceptance,
  TrainingJobDetail,
  TrainingJobEvent,
  TrainingMetric,
  TrainingPage,
  UnslothStorageArtifact,
  UnslothStorageKindUsage,
  UnslothStorageReadModel,
} from './model-training.models';

type JsonObject = Record<string, unknown>;

function objectOf(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}

function arrayOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function firstNumber(fallback: number, ...values: unknown[]): number {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return fallback;
}

function firstBoolean(...values: unknown[]): boolean | undefined {
  return values.find(value => typeof value === 'boolean') as boolean | undefined;
}

function formatOf(value: unknown): DatasetFormat {
  const format = firstString(value).toLowerCase();
  return format === 'instruction' || format === 'chat' || format === 'mixed' ? format : 'unknown';
}

function datasetValidationStatus(value: unknown, datasetStatus: unknown): string {
  const status = firstString(value, datasetStatus).toLowerCase();
  if (['passed', 'valid', 'validated'].includes(status)) return 'valid';
  if (['failed', 'invalid', 'quarantined'].includes(status)) return 'invalid';
  if (status === 'in_progress') return 'validating';
  return status || 'uploaded';
}

export function entityFrom(value: unknown, key: string): unknown {
  const root = objectOf(value);
  const nested = root[key];
  return nested && typeof nested === 'object' ? nested : value;
}

export function normalizePage<T>(
  value: unknown,
  alternativeItemKeys: string[],
  normalize: (item: unknown) => T,
): TrainingPage<T> {
  if (Array.isArray(value)) return { items: value.map(normalize), count: value.length, next_cursor: null };
  const root = objectOf(value);
  const candidates = [root.items, ...alternativeItemKeys.map(key => root[key])];
  const items = arrayOf(candidates.find(Array.isArray)).map(normalize);
  const cursorValue = root.next_cursor ?? root.cursor ?? root.next_offset;
  return {
    items,
    count: firstNumber(items.length, root.count, root.total_count, root.returned_count),
    next_cursor: cursorValue === null || cursorValue === undefined || cursorValue === '' ? null : String(cursorValue),
  };
}

export function normalizeDataset(value: unknown): DatasetDetail {
  const source = objectOf(entityFrom(value, 'dataset'));
  const validation = objectOf(source.validation);
  const validationSummary = objectOf(validation.summary);
  const partitions = objectOf(source.partitions);
  const trainPartition = objectOf(partitions.train);
  const validationPartition = objectOf(partitions.validation);
  const splitSource = objectOf(source.split);
  const externalValidation = objectOf(source.external_validation);
  const id = firstString(source.id, source.dataset_id);
  const status = firstString(source.status) || 'uploaded';
  const recordCount = firstNumber(0, source.record_count, source.input_record_count);
  const trainCount = firstNumber(0, source.train_record_count, trainPartition.record_count, splitSource.train_count, splitSource.train_record_count);
  const validationCount = firstNumber(0, source.validation_record_count, validationPartition.record_count, splitSource.validation_count, splitSource.validation_record_count);
  const validationStatus = datasetValidationStatus(source.validation_status ?? validation.status, status);
  const rawReport = source.validation_report
    ?? (validation.reason_codes || validation.train || validation.issues ? validation : null);
  const report = rawReport
    ? normalizeValidationReport(rawReport, id, recordCount, trainCount, validationCount)
    : summaryValidationReport(validationSummary, id, recordCount, trainCount, validationCount, validationStatus);
  const hasSplitMetadata = Object.keys(splitSource).length > 0 || trainCount > 0 || validationCount > 0;

  return {
    id,
    name: firstString(source.name, id) || 'Unbenanntes Dataset',
    purpose: firstString(source.purpose) || undefined,
    license: firstString(source.license) || undefined,
    privacy: firstString(source.privacy) || undefined,
    format: formatOf(source.format ?? source.format_type),
    status,
    sha256: firstString(source.sha256, source.content_sha256, source.dataset_sha256) || undefined,
    size_bytes: firstNumber(0, source.size_bytes, source.dataset_bytes),
    record_count: recordCount,
    accepted_record_count: firstNumber(recordCount, source.accepted_record_count, source.accepted_records),
    rejected_record_count: firstNumber(0, source.rejected_record_count, source.rejected_records),
    duplicate_record_count: firstNumber(0, source.duplicate_record_count, source.duplicate_count),
    train_record_count: trainCount,
    validation_record_count: validationCount,
    validation_status: validationStatus,
    trainable: firstBoolean(source.trainable, validation.trainable) ?? validationStatus === 'valid',
    created_at: numericTimestamp(source.created_at),
    updated_at: numericTimestamp(source.updated_at),
    split: hasSplitMetadata ? {
      algorithm_version: firstString(splitSource.algorithm_version, splitSource.algorithm) || undefined,
      seed: firstNumber(0, splitSource.seed),
      validation_ratio: firstNumber(0, splitSource.validation_ratio, splitSource.ratio),
      train_count: trainCount,
      validation_count: validationCount,
    } : undefined,
    external_validation: Object.keys(externalValidation).length ? {
      dataset_id: firstString(externalValidation.dataset_id),
      semantic_overlap_count: firstNumber(0, externalValidation.semantic_overlap_count),
      algorithm_version: firstString(externalValidation.algorithm_version),
    } : undefined,
    validation_report: report,
  };
}

export function normalizeDatasetSummary(value: unknown): DatasetSummary {
  return normalizeDataset(value);
}

export function normalizeValidationReport(
  value: unknown,
  fallbackDatasetId = '',
  fallbackTotal = 0,
  fallbackTrain = 0,
  fallbackValidation = 0,
): DatasetValidationReport {
  const source = objectOf(entityFrom(value, 'validation_report'));
  const train = objectOf(source.train);
  const validation = objectOf(source.validation);
  const issues = validationIssues(source, train, validation);
  const nestedAccepted = firstNumber(0, train.accepted_records, train.accepted_record_count)
    + firstNumber(0, validation.accepted_records, validation.accepted_record_count);
  const nestedRejected = firstNumber(0, train.rejected_records, train.rejected_record_count)
    + firstNumber(0, validation.rejected_records, validation.rejected_record_count);
  const accepted = firstNumber(
    0,
    source.accepted_records,
    source.accepted_record_count,
    nestedAccepted,
  );
  const rejected = firstNumber(
    0,
    source.rejected_records,
    source.rejected_record_count,
    nestedRejected,
  );
  const secretFindings = firstNumber(
    arrayOf(source.secret_findings).length,
    source.secret_finding_count,
    source.secret_findings,
    arrayOf(train.secret_findings).length + arrayOf(validation.secret_findings).length,
  );
  const valid = firstBoolean(source.valid, source.ok) ?? firstString(source.status).toLowerCase() === 'passed';
  return {
    dataset_id: firstString(source.dataset_id, fallbackDatasetId),
    valid,
    trainable: firstBoolean(source.trainable) ?? valid,
    format: formatOf(source.format ?? source.format_type ?? train.format_type ?? validation.format_type),
    total_records: firstNumber(fallbackTotal, source.total_records, source.record_count, source.total_lines, accepted + rejected),
    accepted_records: accepted,
    rejected_records: rejected,
    duplicate_records: firstNumber(
      firstNumber(0, train.duplicate_records, train.duplicate_count) + firstNumber(0, validation.duplicate_records, validation.duplicate_count),
      source.duplicate_records,
      source.duplicate_count,
    ),
    secret_findings: secretFindings,
    pii_findings: firstNumber(arrayOf(source.pii_findings).length, source.pii_finding_count, source.pii_findings),
    train_records: firstNumber(fallbackTrain, source.train_records, source.train_record_count),
    validation_records: firstNumber(fallbackValidation, source.validation_records, source.validation_record_count),
    issues,
    generated_at: numericTimestamp(source.generated_at ?? source.validated_at),
  };
}

export function normalizeDatasetRecord(value: unknown, split: DatasetSplit): DatasetRecord {
  const wrapper = objectOf(value);
  const record = Object.keys(objectOf(wrapper.record)).length ? objectOf(wrapper.record) : wrapper;
  const messages = arrayOf(record.messages)
    .map(objectOf)
    .filter(message => firstString(message.role) || firstString(message.content))
    .map(message => ({ role: firstString(message.role) || 'unknown', content: firstString(message.content) }));
  const state = firstString(wrapper.state);
  return {
    id: firstString(wrapper.id, record.id) || undefined,
    index: firstNumber(0, wrapper.index, wrapper.record_index, record.index),
    split: (firstString(wrapper.split, wrapper.partition, split) || split) as DatasetSplit,
    format: formatOf(record.format ?? (messages.length ? 'chat' : 'instruction')),
    instruction: firstString(record.instruction) || undefined,
    input: firstString(record.input) || undefined,
    output: firstString(record.output) || undefined,
    messages: messages.length ? messages : undefined,
    token_count: firstNumber(0, record.token_count) || undefined,
    valid: firstBoolean(wrapper.valid, record.valid) ?? (!state || state === 'ready'),
    reason_codes: stringArray(wrapper.reason_codes).length
      ? stringArray(wrapper.reason_codes)
      : state && state !== 'ready' ? [state] : [],
  };
}

export function normalizeTrainingJob(value: unknown): TrainingJobDetail {
  const source = objectOf(entityFrom(value, 'job'));
  const result = objectOf(source.result);
  const rawMetrics = arrayOf(source.metrics).length ? arrayOf(source.metrics) : arrayOf(result.metrics);
  const error = objectOf(source.error);
  const id = firstString(source.id, source.job_id);
  return {
    id,
    task_id: firstString(source.task_id) || undefined,
    status: firstString(source.status) || 'queued',
    phase: firstString(source.phase) || undefined,
    dataset_id: firstString(source.dataset_id) || '-',
    dataset_name: firstString(source.dataset_name) || undefined,
    base_model_id: firstString(source.base_model_id, source.base_model) || '-',
    backend: firstString(source.backend) || '-',
    mode: normalizeMode(source.mode),
    queue_position: nullableNumber(source.queue_position),
    progress_percent: optionalNumber(source.progress_percent),
    current_step: optionalNumber(source.current_step),
    max_steps: optionalNumber(source.max_steps),
    epoch: optionalNumber(source.epoch),
    latest_train_loss: optionalNumber(source.latest_train_loss ?? source.train_loss),
    latest_eval_loss: optionalNumber(source.latest_eval_loss ?? source.eval_loss),
    created_at: numericTimestamp(source.created_at),
    started_at: numericTimestamp(source.started_at),
    finished_at: numericTimestamp(source.finished_at),
    configuration: objectOrUndefined(source.configuration) as unknown as TrainingJobDetail['configuration'],
    metrics: rawMetrics.map(normalizeTrainingMetric).filter(metric => Number.isFinite(metric.step)),
    adapter_id: firstString(source.adapter_id, result.adapter_id) || undefined,
    artifact_refs: artifactRefs(source.artifact_refs ?? result.artifacts),
    cancellable: firstBoolean(source.cancellable),
    cancel_mode: firstString(source.cancel_mode, result.cancel_mode) as TrainingJobDetail['cancel_mode'],
    error: Object.keys(error).length ? {
      code: firstString(error.code, source.error_code) || 'training_failed',
      message: firstString(error.message, source.error_message),
      retriable: firstBoolean(error.retriable, source.retryable),
    } : source.error_code ? {
      code: firstString(source.error_code), message: firstString(source.error_message), retriable: firstBoolean(source.retryable),
    } : null,
  };
}

export function normalizeTrainingJobAcceptance(value: unknown): TrainingJobAcceptance {
  const source = objectOf(entityFrom(value, 'job'));
  return {
    job_id: firstString(source.job_id, source.id),
    task_id: firstString(source.task_id) || undefined,
    status: firstString(source.status) || 'queued',
    poll_url: firstString(source.poll_url) || undefined,
    events_url: firstString(source.events_url) || undefined,
    idempotent_replay: firstBoolean(source.idempotent_replay, source.idempotentReplay),
  };
}

export function normalizeTrainingEvent(value: unknown): TrainingJobEvent {
  const source = objectOf(value);
  const payload = objectOf(source.payload);
  const explicitMetric = objectOf(source.metric ?? payload.metric);
  const metricSource = Object.keys(explicitMetric).length ? explicitMetric : payload;
  const hasMetric = ['step', 'current_step', 'train_loss', 'eval_loss', 'learning_rate', 'epoch']
    .some(key => optionalNumber(metricSource[key]) !== undefined);
  return {
    sequence: firstNumber(0, source.sequence),
    event_type: firstString(source.event_type, source.type) || 'event',
    phase: firstString(source.phase, payload.phase) || undefined,
    progress_percent: optionalNumber(source.progress_percent ?? payload.progress_percent),
    metric: hasMetric ? normalizeTrainingMetric(metricSource) : undefined,
    message: firstString(source.message, payload.message) || undefined,
    reason_code: firstString(source.reason_code, payload.reason_code) || undefined,
    timestamp: numericTimestamp(source.timestamp ?? source.created_at),
  };
}

export function normalizeTrainingEventPage(value: unknown): TrainingEventPage {
  const page = normalizePage(value, ['events'], normalizeTrainingEvent);
  const root = objectOf(value);
  return {
    items: page.items,
    count: page.count,
    next_sequence: firstNumber(
      page.items.at(-1)?.sequence || 0,
      root.next_sequence,
      root.after_sequence,
    ),
  };
}

export function normalizeAdapter(value: unknown): AdapterSummary {
  const source = objectOf(entityFrom(value, 'adapter'));
  return {
    id: firstString(source.id, source.adapter_id),
    name: firstString(source.name, source.adapter_name, source.id, source.adapter_id) || 'Unbenannter Adapter',
    version: firstNumber(1, source.version),
    adapter_version: optionalNumber(source.adapter_version) ?? (firstString(source.adapter_version) || undefined),
    registry_version: optionalNumber(source.registry_version),
    base_model_id: firstString(source.base_model_id, source.base_model) || '-',
    method: firstString(source.method) || undefined,
    status: firstString(source.status) || 'trained',
    score: optionalNumber(source.score ?? source.evaluation_score),
    sha256: firstString(source.sha256, source.content_sha256, source.artifact_sha256) || undefined,
    size_bytes: optionalNumber(source.size_bytes),
    active: firstBoolean(source.active),
    hash_verified: firstBoolean(source.hash_verified),
    artifact_exists: firstBoolean(source.artifact_exists),
    evaluation_id: firstString(source.evaluation_id, source.eval_report_id) || undefined,
    created_at: numericTimestamp(source.created_at),
    updated_at: numericTimestamp(source.updated_at),
  };
}

export function normalizeEvaluation(value: unknown): EvaluationReport {
  const source = objectOf(entityFrom(value, 'evaluation'));
  return {
    id: firstString(source.id, source.evaluation_id),
    adapter_id: firstString(source.adapter_id),
    dataset_id: firstString(source.dataset_id, source.eval_dataset_id),
    status: firstString(source.status) || 'queued',
    passed: firstBoolean(source.passed, source.gate_passed),
    aggregate_score: optionalNumber(source.aggregate_score ?? source.score),
    metrics: arrayOf(source.metrics).map(item => {
      const metric = objectOf(item);
      return {
        name: firstString(metric.name, metric.metric) || 'metric',
        base_value: firstNumber(0, metric.base_value),
        adapter_value: firstNumber(0, metric.adapter_value),
        delta: firstNumber(0, metric.delta),
        higher_is_better: firstBoolean(metric.higher_is_better),
        threshold: optionalNumber(metric.threshold),
        passed: firstBoolean(metric.passed),
      };
    }),
    samples: arrayOf(source.samples).map(item => {
      const sample = objectOf(item);
      return {
        id: firstString(sample.id, sample.sample_id) || undefined,
        record_index: optionalNumber(sample.record_index),
        base_output: firstString(sample.base_output),
        adapter_output: firstString(sample.adapter_output),
        expected_output: firstString(sample.expected_output) || undefined,
        winner: firstString(sample.winner) || undefined,
      };
    }),
    reason_code: firstString(source.reason_code) || undefined,
    created_at: numericTimestamp(source.created_at),
    finished_at: numericTimestamp(source.finished_at),
  };
}

export function normalizeUnslothStorage(value: unknown): UnslothStorageReadModel {
  const envelope = objectOf(value);
  const data = objectOf(envelope.data);
  const root = Object.keys(data).length ? data : objectOf(entityFrom(value, 'storage'));
  const usage = objectOf(root.usage);
  if (firstBoolean(usage.paths_exposed) !== false) {
    throw new Error('unsloth_storage_paths_exposed');
  }
  const quotas = objectOf(usage.quotas);
  const rawKindUsage = objectOf(usage.usage);
  const kindUsage: Record<string, UnslothStorageKindUsage> = {};
  for (const [kind, raw] of Object.entries(rawKindUsage)) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(kind)) continue;
    const item = objectOf(raw);
    kindUsage[kind] = {
      bytes: Math.max(0, firstNumber(0, item.bytes)),
      artifacts: Math.max(0, firstNumber(0, item.artifacts)),
    };
  }
  const items = arrayOf(root.items)
    .map(normalizeUnslothStorageArtifact)
    .filter((item): item is UnslothStorageArtifact => item !== null);
  return {
    usage: {
      schema: storageOpaque(usage.schema, 127) || 'ananta.unsloth-storage-usage.v1',
      catalog_revision: Math.max(0, firstNumber(0, usage.catalog_revision)),
      usage: kindUsage,
      tenant_total_bytes: Math.max(0, firstNumber(0, usage.tenant_total_bytes)),
      quotas: {
        dataset_bytes: Math.max(0, firstNumber(0, quotas.dataset_bytes)),
        model_bytes: Math.max(0, firstNumber(0, quotas.model_bytes)),
        checkpoint_bytes: Math.max(0, firstNumber(0, quotas.checkpoint_bytes)),
        export_bytes: Math.max(0, firstNumber(0, quotas.export_bytes)),
        tenant_total_bytes: Math.max(0, firstNumber(0, quotas.tenant_total_bytes)),
        retention_seconds: Math.max(0, firstNumber(0, quotas.retention_seconds)),
        max_cleanup_items: Math.max(0, firstNumber(0, quotas.max_cleanup_items)),
      },
      paths_exposed: false,
    },
    items,
    count: items.length,
  };
}

function summaryValidationReport(
  summary: JsonObject,
  datasetId: string,
  total: number,
  trainCount: number,
  validationCount: number,
  status: string,
): DatasetValidationReport | null {
  if (!Object.keys(summary).length && status === 'uploaded') return null;
  const issues: DatasetValidationIssue[] = [];
  const errorCount = firstNumber(0, summary.error_count);
  const warningCount = firstNumber(0, summary.warning_count);
  if (errorCount) issues.push({ code: 'validation_errors', severity: 'error', count: errorCount });
  if (warningCount) issues.push({ code: 'validation_warnings', severity: 'warning', count: warningCount });
  return {
    dataset_id: datasetId,
    valid: status === 'valid',
    trainable: status === 'valid',
    total_records: total,
    accepted_records: total,
    rejected_records: 0,
    duplicate_records: 0,
    secret_findings: firstNumber(0, summary.secret_finding_count, summary.secret_findings),
    pii_findings: firstNumber(0, summary.pii_finding_count, summary.pii_findings),
    train_records: trainCount,
    validation_records: validationCount,
    issues,
  };
}

function validationIssues(source: JsonObject, train: JsonObject, validation: JsonObject): DatasetValidationIssue[] {
  const issues: DatasetValidationIssue[] = [];
  for (const item of arrayOf(source.issues)) {
    const issue = objectOf(item);
    issues.push({
      code: firstString(issue.code, issue.reason_code, issue.type) || 'validation_issue',
      severity: firstString(issue.severity) || 'error',
      count: optionalNumber(issue.count),
      record_index: optionalNumber(issue.record_index),
      field: firstString(issue.field) || undefined,
      message: firstString(issue.message) || undefined,
      redacted: firstBoolean(issue.redacted),
    });
  }
  for (const reason of stringArray(source.reason_codes)) issues.push({ code: reason, severity: 'error' });
  for (const [scope, report] of [['train', train], ['validation', validation]] as const) {
    for (const [key, severity] of [['errors', 'error'], ['warnings', 'warning']] as const) {
      for (const item of arrayOf(report[key])) {
        const issue = objectOf(item);
        issues.push({
          code: firstString(issue.code, issue.reason_code, issue.type) || `${scope}_${key}`,
          severity,
          record_index: optionalNumber(issue.record_index ?? issue.line),
          field: firstString(issue.field) || scope,
          message: firstString(issue.message) || undefined,
          redacted: true,
        });
      }
    }
  }
  return Array.from(new Map(issues.map(issue => [`${issue.code}:${issue.record_index ?? 'all'}`, issue])).values()).slice(0, 500);
}

function normalizeTrainingMetric(value: unknown): TrainingMetric {
  const metric = objectOf(value);
  return {
    step: firstNumber(0, metric.step, metric.current_step),
    max_steps: optionalNumber(metric.max_steps),
    epoch: optionalNumber(metric.epoch),
    train_loss: optionalNumber(metric.train_loss),
    eval_loss: optionalNumber(metric.eval_loss),
    learning_rate: optionalNumber(metric.learning_rate),
    gpu_memory_bytes: optionalNumber(metric.gpu_memory_bytes),
    recorded_at: numericTimestamp(metric.recorded_at ?? metric.timestamp),
  };
}

function normalizeUnslothStorageArtifact(value: unknown): UnslothStorageArtifact | null {
  const source = objectOf(value);
  const artifactId = storageOpaque(source.artifact_id);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/.test(artifactId)) return null;
  const storageRef = storageOpaque(source.storage_ref, 255);
  return {
    artifact_id: artifactId,
    storage_ref: /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(storageRef)
      ? storageRef
      : undefined,
    kind: storageOpaque(source.kind, 63) || 'unknown',
    job_id: storageOpaque(source.job_id),
    attempt_id: storageOpaque(source.attempt_id),
    sha256: /^[0-9a-f]{64}$/.test(firstString(source.sha256))
      ? firstString(source.sha256)
      : '',
    size_bytes: Math.max(0, firstNumber(0, source.size_bytes)),
    created_at: numericTimestamp(source.created_at),
    retention_until: numericTimestamp(source.retention_until),
    state: storageOpaque(source.state, 63) || 'active',
    reference_kinds: stringArray(source.reference_kinds)
      .map((item) => storageOpaque(item, 63))
      .filter(Boolean),
    referenced: firstBoolean(source.referenced) ?? false,
    cleanup_task_id: storageOpaque(source.cleanup_task_id) || undefined,
  };
}

function storageOpaque(value: unknown, maxLength = 191): string {
  const candidate = firstString(value);
  if (candidate.length > maxLength) return '';
  return /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(candidate) ? candidate : '';
}

function optionalNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return undefined;
}

function nullableNumber(value: unknown): number | null | undefined {
  return value === null ? null : optionalNumber(value);
}

function numericTimestamp(value: unknown): number | undefined {
  const numeric = optionalNumber(value);
  if (numeric !== undefined) return numeric;
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return undefined;
}

function stringArray(value: unknown): string[] {
  return arrayOf(value).map(firstString).filter(Boolean).slice(0, 500);
}

function objectOrUndefined(value: unknown): JsonObject | undefined {
  const object = objectOf(value);
  return Object.keys(object).length ? object : undefined;
}

function artifactRefs(value: unknown): string[] | undefined {
  const refs = arrayOf(value).map(item => {
    if (typeof item === 'string') return item;
    const artifact = objectOf(item);
    return firstString(artifact.id, artifact.artifact_id, artifact.ref);
  }).filter(Boolean).slice(0, 100);
  return refs.length ? refs : undefined;
}

function normalizeMode(value: unknown): TrainingJobDetail['mode'] {
  const mode = firstString(value);
  return mode === 'live' || mode === 'dry_run' ? mode : undefined;
}
