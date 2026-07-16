type JsonObject = Record<string, unknown>;

const TERMINAL_STATUSES = new Set([
  'cancelled',
  'canceled',
  'completed',
  'failed',
  'interrupted',
  'rejected',
  'succeeded',
  'success',
]);

export interface VpTrainingRuntimeView {
  jobId: string;
  status: string;
  phase: string;
  datasetId?: string;
  trainingProfileId?: string;
  terminal: boolean;
  terminalResult?: unknown;
  modelTrainingUrl: string;
  jobUrl: string;
  datasetUrl?: string;
}

export interface VpDatasetBuildRuntimeView {
  datasetId: string;
  status: string;
  validationStatus?: string;
  trainable?: boolean;
  recordCount: number;
  trainRecordCount: number;
  validationRecordCount: number;
  sourceMode?: string;
  modelTrainingUrl: string;
  datasetUrl: string;
}

function objectOf(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function firstBoolean(...values: unknown[]): boolean | undefined {
  for (const value of values) {
    if (typeof value === 'boolean') return value;
  }
  return undefined;
}

function boundedCount(...values: unknown[]): number {
  for (const value of values) {
    const parsed = typeof value === 'number' ? value : Number(value);
    if (Number.isSafeInteger(parsed) && parsed >= 0) return parsed;
  }
  return 0;
}

function safeControlCenterLink(value: unknown, fallback: string): string {
  const candidate = firstString(value);
  if (!candidate.startsWith('/') || candidate.startsWith('//')) return fallback;
  try {
    const parsed = new URL(candidate, 'https://ananta.invalid');
    if (parsed.origin !== 'https://ananta.invalid' || parsed.pathname !== '/model-training' || parsed.hash) {
      return fallback;
    }
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return fallback;
  }
}

/**
 * Reads the additive LoRA runtime contract from all currently supported VP
 * status envelopes. The function is deliberately independent from Angular so
 * polling, readonly embeds and tests share one defensive interpretation.
 */
export function extractVpTrainingRuntime(value: unknown): VpTrainingRuntimeView | null {
  const root = objectOf(value);
  const result = objectOf(root['result']);
  const executionResult = objectOf(root['execution_result'] ?? root['executionResult']);
  const source = objectOf(
    root['training']
    ?? root['outputs']
    ?? result['outputs']
    ?? executionResult['outputs']
    ?? root['output']
    ?? value,
  );
  const job = objectOf(source['job_result'] ?? source['jobResult'] ?? source['job']);
  const links = objectOf(source['links']);
  const jobId = firstString(source['jobId'], source['job_id'], job['id'], job['job_id']);
  if (!jobId) return null;

  const datasetId = firstString(source['datasetId'], source['dataset_id'], job['dataset_id']);
  const profileId = firstString(
    source['trainingProfileId'],
    source['training_profile_id'],
    source['training_profile'],
    job['training_profile_id'],
    job['gpu_profile'],
  );
  const status = firstString(
    source['trainingStatus'],
    source['training_status'],
    source['status'],
    job['status'],
  ) || 'unknown';
  const phase = firstString(
    source['trainingPhase'],
    source['training_phase'],
    source['phase'],
    job['phase'],
    status,
  );
  const explicitTerminal = firstBoolean(source['terminal'], job['terminal']);
  const terminal = explicitTerminal ?? TERMINAL_STATUSES.has(status.toLowerCase());
  const terminalResult = source['terminalResult']
    ?? source['terminal_result']
    ?? (terminal ? job['result'] : undefined);
  const modelTrainingFallback = '/model-training';
  const jobFallback = `${modelTrainingFallback}?tab=jobs&job_id=${encodeURIComponent(jobId)}`;
  const datasetFallback = datasetId
    ? `${modelTrainingFallback}?tab=datasets&dataset_id=${encodeURIComponent(datasetId)}`
    : undefined;

  return {
    jobId,
    status,
    phase,
    datasetId: datasetId || undefined,
    trainingProfileId: profileId || undefined,
    terminal,
    terminalResult: terminalResult === null ? undefined : terminalResult,
    modelTrainingUrl: safeControlCenterLink(
      source['modelTrainingUrl'] ?? source['model_training_url'] ?? links['model_training'],
      modelTrainingFallback,
    ),
    jobUrl: safeControlCenterLink(source['jobUrl'] ?? source['job_url'] ?? links['job'], jobFallback),
    datasetUrl: datasetFallback
      ? safeControlCenterLink(source['datasetUrl'] ?? source['dataset_url'] ?? links['dataset'], datasetFallback)
      : undefined,
  };
}

/** Reads the canonical output of ml_intern_build_lora_dataset without paths. */
export function extractVpDatasetBuildRuntime(value: unknown): VpDatasetBuildRuntimeView | null {
  const root = objectOf(value);
  const result = objectOf(root['result']);
  const executionResult = objectOf(root['execution_result'] ?? root['executionResult']);
  const source = objectOf(
    root['datasetBuild']
    ?? root['dataset_build']
    ?? root['outputs']
    ?? result['outputs']
    ?? executionResult['outputs']
    ?? root['output']
    ?? value,
  );
  const dataset = objectOf(source['dataset_build_result'] ?? source['datasetBuildResult'] ?? source['dataset']);
  const diagnostics = objectOf(root['diagnostics'] ?? result['diagnostics'] ?? executionResult['diagnostics']);
  const datasetId = firstString(source['datasetId'], source['dataset_id'], dataset['id'], dataset['dataset_id']);
  if (!datasetId) return null;

  const modelTrainingFallback = '/model-training';
  const datasetFallback = `${modelTrainingFallback}?tab=datasets&dataset_id=${encodeURIComponent(datasetId)}`;
  const links = objectOf(source['links']);
  return {
    datasetId,
    status: firstString(source['datasetStatus'], source['dataset_status'], dataset['status']) || 'unknown',
    validationStatus: firstString(dataset['validation_status'], dataset['validationStatus']) || undefined,
    trainable: firstBoolean(dataset['trainable']),
    recordCount: boundedCount(dataset['record_count'], dataset['recordCount'], diagnostics['record_count']),
    trainRecordCount: boundedCount(
      dataset['train_record_count'], dataset['trainRecordCount'], diagnostics['train_record_count'],
    ),
    validationRecordCount: boundedCount(
      dataset['validation_record_count'],
      dataset['validationRecordCount'],
      diagnostics['validation_record_count'],
    ),
    sourceMode: firstString(diagnostics['source_mode'], source['source_mode']) || undefined,
    modelTrainingUrl: safeControlCenterLink(
      source['modelTrainingUrl'] ?? source['model_training_url'] ?? links['model_training'],
      modelTrainingFallback,
    ),
    datasetUrl: safeControlCenterLink(
      source['datasetUrl'] ?? source['dataset_url'] ?? links['dataset'],
      datasetFallback,
    ),
  };
}

export function stringifyVpRuntimeResult(value: unknown): string {
  if (value === undefined || value === null) return 'Kein terminales Ergebnis gemeldet.';
  try {
    const serialized = JSON.stringify(value, null, 2);
    if (typeof serialized !== 'string') return String(value);
    return serialized.length > 4000 ? `${serialized.slice(0, 4000)}\n…` : serialized;
  } catch {
    const fallback = String(value);
    return fallback.length > 4000 ? `${fallback.slice(0, 4000)}…` : fallback;
  }
}
