import { ReconstructionReport, SemanticVisualMetricsService } from './semantic-visual-metrics.service';

const D = 'a'.repeat(64);
function report(patch: Record<string, unknown> = {}): ReconstructionReport {
  return {
    schema: 'ananta.reconstruction-report.v1', report_id: 'report', session_id: 'session', receiver_id: 'receiver',
    contract_id: 'contract', contract_digest: D, lease_id: 'lease', lease_digest: D, lease_expires_at_ms: 5000,
    epoch: 1, sequence: 4, observed_at_ms: 1000, source: 'local_measurement',
    stage_ms: { encode: 1, transport: 2, reassembly: 1, render: 3, recovery: 0 },
    resources: { cpu_ms: 3, gpu_ms: 0, working_bytes: 100 }, bytes: { encoded: 50, transported: 60 },
    delay_ms: 2000, quality: { score: 0.9, drift: 0.01, stale_regions: 0 }, input_digest: D, output_digest: D,
    ...patch,
  } as ReconstructionReport;
}

describe('SemanticVisualMetricsService', () => {
  it('records separately-unitized content-free metrics and bounds sampling/history/export rate', () => {
    const service = new SemanticVisualMetricsService(4, 2, 32 * 1024, 1000);
    expect(service.record(report({ sequence: 1 }), 1000).status).toBe('sampled_out');
    expect(service.record(report({ report_id: 'r4', sequence: 4 }), 1000).status).toBe('recorded');
    expect(service.record(report({ report_id: 'r8', sequence: 8 }), 1000).status).toBe('recorded');
    expect(service.record(report({ report_id: 'r12', sequence: 12 }), 1000).status).toBe('recorded');
    expect(service.snapshot().reports).toBe(2);
    expect(service.exportBatch(1000)).toHaveLength(2);
    expect(service.exportBatch(1500)).toEqual([]);
    expect(service.snapshot().timers).toBe(0);
  });

  it.each([
    ['metric_stage_invalid', () => report({ stage_ms: { encode: Number.NaN, transport: 0, reassembly: 0, render: 0, recovery: 0 } })],
    ['metric_binding_invalid', () => report({ output_digest: '' })],
    ['metric_stale_lease_or_observation', () => report({ lease_expires_at_ms: 999 })],
    ['metric_cost_invalid', () => report({ bytes: { encoded: -1, transported: 0 } })],
    ['metric_shape_invalid_or_content_leak', () => ({ ...report(), pixels: [1, 2, 3] })],
    ['metric_shape_invalid_or_content_leak', () => ({ ...report(), sensitive_geometry: { x: 0 } })],
  ])('rejects %s', (reason, build) => {
    const service = new SemanticVisualMetricsService(1);
    expect(service.record(build(), 1000)).toEqual({ status: 'rejected', reasonCode: reason });
  });

  it('deletes queued metrics for a reset/revoked lease', () => {
    const service = new SemanticVisualMetricsService(1);
    service.record(report(), 1000);
    service.resetLease('lease');
    expect(service.snapshot().reports).toBe(0);
  });
});
