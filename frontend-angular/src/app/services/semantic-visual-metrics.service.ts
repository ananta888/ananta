
export interface ReconstructionReport {
  readonly schema: 'ananta.reconstruction-report.v1';
  readonly report_id: string; readonly session_id: string; readonly receiver_id: string;
  readonly contract_id: string; readonly contract_digest: string;
  readonly lease_id: string; readonly lease_digest: string; readonly lease_expires_at_ms: number;
  readonly epoch: number; readonly sequence: number; readonly observed_at_ms: number;
  readonly source: 'local_measurement';
  readonly stage_ms: Readonly<{ encode: number; transport: number; reassembly: number; render: number; recovery: number }>;
  readonly resources: Readonly<{ cpu_ms: number; gpu_ms: number; working_bytes: number }>;
  readonly bytes: Readonly<{ encoded: number; transported: number }>;
  readonly delay_ms: number;
  readonly quality: Readonly<{ score: number; drift: number; stale_regions: number }>;
  readonly input_digest: string; readonly output_digest: string;
}

export type MetricRecordResult =
  | { readonly status: 'recorded' | 'sampled_out' }
  | { readonly status: 'rejected'; readonly reasonCode: string };

export class SemanticVisualMetricsService {
  private readonly history: ReconstructionReport[] = [];
  private lastExportMs = Number.NEGATIVE_INFINITY;

  constructor(
    private readonly sampleEvery = 4,
    private readonly maxHistory = 128,
    private readonly maxPayloadBytes = 32 * 1024,
    private readonly minExportIntervalMs = 1000,
  ) {}

  record(raw: unknown, nowMs = Date.now()): MetricRecordResult {
    const validated = validateReport(raw, nowMs, this.maxPayloadBytes);
    if (typeof validated === 'string') return Object.freeze({ status: 'rejected', reasonCode: validated });
    if (validated.sequence % this.sampleEvery !== 0) return Object.freeze({ status: 'sampled_out' });
    this.history.push(deepFreeze(structuredClone(validated)) as unknown as ReconstructionReport);
    if (this.history.length > this.maxHistory) this.history.splice(0, this.history.length - this.maxHistory);
    return Object.freeze({ status: 'recorded' });
  }

  exportBatch(nowMs = Date.now(), maxReports = 16): readonly ReconstructionReport[] {
    if (!Number.isSafeInteger(nowMs) || nowMs - this.lastExportMs < this.minExportIntervalMs
        || !Number.isSafeInteger(maxReports) || maxReports < 1 || maxReports > 32) return Object.freeze([]);
    this.lastExportMs = nowMs;
    const batch: ReconstructionReport[] = [];
    let bytes = 2;
    while (this.history.length && batch.length < maxReports) {
      const report = this.history[0];
      const size = new TextEncoder().encode(JSON.stringify(report)).byteLength + (batch.length ? 1 : 0);
      if (bytes + size > this.maxPayloadBytes) break;
      batch.push(this.history.shift()!); bytes += size;
    }
    return Object.freeze(batch);
  }

  resetLease(leaseId: string): void {
    for (let index = this.history.length - 1; index >= 0; index -= 1) {
      if (this.history[index].lease_id === leaseId) this.history.splice(index, 1);
    }
  }

  snapshot(): Readonly<{ reports: number; estimatedBytes: number; timers: number }> {
    return Object.freeze({
      reports: this.history.length,
      estimatedBytes: new TextEncoder().encode(JSON.stringify(this.history)).byteLength,
      timers: 0,
    });
  }
}

const TOP = [
  'schema', 'report_id', 'session_id', 'receiver_id', 'contract_id', 'contract_digest', 'lease_id',
  'lease_digest', 'lease_expires_at_ms', 'epoch', 'sequence', 'observed_at_ms', 'source', 'stage_ms',
  'resources', 'bytes', 'delay_ms', 'quality', 'input_digest', 'output_digest',
];

function validateReport(raw: unknown, nowMs: number, maxBytes: number): ReconstructionReport | string {
  if (!exact(raw, TOP)) return 'metric_shape_invalid_or_content_leak';
  const value = raw as Record<string, any>;
  let encoded: Uint8Array;
  try { encoded = new TextEncoder().encode(JSON.stringify(value)); } catch { return 'metric_json_invalid'; }
  if (encoded.byteLength > maxBytes) return 'metric_payload_exceeded';
  if (value['schema'] !== 'ananta.reconstruction-report.v1' || value['source'] !== 'local_measurement') return 'metric_schema_invalid';
  for (const field of ['report_id', 'session_id', 'receiver_id', 'contract_id', 'lease_id']) {
    if (typeof value[field] !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value[field])) return 'metric_binding_invalid';
  }
  for (const field of ['contract_digest', 'lease_digest', 'input_digest', 'output_digest']) {
    if (typeof value[field] !== 'string' || !/^[0-9a-f]{64}$/.test(value[field])) return 'metric_binding_invalid';
  }
  for (const field of ['lease_expires_at_ms', 'epoch', 'sequence', 'observed_at_ms']) {
    if (!Number.isSafeInteger(value[field]) || value[field] < (field === 'sequence' || field === 'observed_at_ms' ? 0 : 1)) return 'metric_binding_invalid';
  }
  if (value['lease_expires_at_ms'] <= nowMs || value['observed_at_ms'] > nowMs + 5_000 || nowMs - value['observed_at_ms'] > 60_000) {
    return 'metric_stale_lease_or_observation';
  }
  if (!exact(value['stage_ms'], ['encode', 'transport', 'reassembly', 'render', 'recovery'])
      || !boundedNumbers(value['stage_ms'], 0, 60_000)) return 'metric_stage_invalid';
  if (!exact(value['resources'], ['cpu_ms', 'gpu_ms', 'working_bytes'])
      || !boundedNumbers(value['resources'], 0, 134_217_728)
      || value['resources']['cpu_ms'] > 60_000 || value['resources']['gpu_ms'] > 60_000
      || !Number.isSafeInteger(value['resources']['working_bytes'])) return 'metric_resource_invalid';
  if (!exact(value['bytes'], ['encoded', 'transported'])
      || !Number.isSafeInteger(value['bytes']['encoded']) || !Number.isSafeInteger(value['bytes']['transported'])
      || value['bytes']['encoded'] < 0 || value['bytes']['encoded'] > 524_288
      || value['bytes']['transported'] < 0 || value['bytes']['transported'] > 1_048_576) return 'metric_cost_invalid';
  if (!Number.isFinite(value['delay_ms']) || value['delay_ms'] < 0 || value['delay_ms'] > 60_000) return 'metric_delay_invalid';
  if (!exact(value['quality'], ['score', 'drift', 'stale_regions'])
      || !Number.isFinite(value['quality']['score']) || value['quality']['score'] < 0 || value['quality']['score'] > 1
      || !Number.isFinite(value['quality']['drift']) || value['quality']['drift'] < 0 || value['quality']['drift'] > 1
      || !Number.isSafeInteger(value['quality']['stale_regions'])
      || value['quality']['stale_regions'] < 0 || value['quality']['stale_regions'] > 256) return 'metric_quality_invalid';
  return value as ReconstructionReport;
}

function exact(raw: unknown, fields: readonly string[]): boolean {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return false;
  const keys = Object.keys(raw as object);
  return keys.length === fields.length && keys.every(key => fields.includes(key));
}
function boundedNumbers(raw: Record<string, unknown>, minimum: number, maximum: number): boolean {
  return Object.values(raw).every(value => typeof value === 'number' && Number.isFinite(value)
    && value >= minimum && value <= maximum);
}
function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object') {
    Object.values(value as Record<string, unknown>).forEach(item => deepFreeze(item)); Object.freeze(value);
  }
  return value;
}
