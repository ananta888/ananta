const limits = {
  elapsed_ms: 7_400_000, self_cpu_ms: 500_000_000, terminated_children_cpu_ms: 500_000_000,
  self_max_rss_kib: 16_777_216, terminated_child_max_rss_kib: 16_777_216,
} as const;
const reasons = ['assignment_elapsed', 'control_stale', 'hub_unavailable_or_revoked', 'session_failed', 'runtime_failed'] as const;
export type DialogStopReason = typeof reasons[number];
export type DialogMeasurements = Record<keyof typeof limits, number>;
export interface DialogTerminalObservation {
  schema: 'ananta.meet-dialog-terminal-observation.v1'; stop_reason: DialogStopReason;
  measurements: DialogMeasurements | null;
}
export interface MeetDialogDiagnostics {
  schema: 'ananta.meet-dialog-diagnostics.v1'; task_id: string;
  observation_status: 'missing' | 'recorded'; classification: 'unverified_worker_observation';
  observation: DialogTerminalObservation | null; recorded_at_ms: number | null;
  hub_task_status: 'in_progress' | 'completed' | 'failed' | 'cancelled'; hub_control_revision_at_receipt: number | null;
}
function invalid(): never { throw new Error('meet_dialog_diagnostics_contract_invalid'); }
function closed(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join() !== [...keys].sort().join()) invalid();
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
export function validateDialogDiagnostics(input: unknown, task: string): MeetDialogDiagnostics {
  const value = closed(input, ['schema', 'task_id', 'observation_status', 'classification', 'observation',
    'recorded_at_ms', 'hub_task_status', 'hub_control_revision_at_receipt']);
  if (value['schema'] !== 'ananta.meet-dialog-diagnostics.v1' || value['task_id'] !== task
    || !/^[A-Za-z0-9_.:-]{1,160}$/.test(task) || value['classification'] !== 'unverified_worker_observation'
    || !['in_progress', 'completed', 'failed', 'cancelled'].includes(value['hub_task_status'] as string)) invalid();
  if (value['observation_status'] === 'missing') {
    if (value['observation'] !== null || value['recorded_at_ms'] !== null || value['hub_control_revision_at_receipt'] !== null) invalid();
  } else if (value['observation_status'] === 'recorded') {
    if (value['hub_task_status'] === 'in_progress' || !integer(value['recorded_at_ms'], 1, Number.MAX_SAFE_INTEGER)
      || !integer(value['hub_control_revision_at_receipt'], 1, 1023)) invalid();
    const observation = closed(value['observation'], ['schema', 'stop_reason', 'measurements']);
    if (observation['schema'] !== 'ananta.meet-dialog-terminal-observation.v1'
      || !(reasons as readonly unknown[]).includes(observation['stop_reason'])) invalid();
    if (observation['measurements'] !== null) {
      const measurements = closed(observation['measurements'], Object.keys(limits));
      for (const [key, maximum] of Object.entries(limits)) if (!integer(measurements[key], 0, maximum)) invalid();
    }
  } else invalid();
  return input as MeetDialogDiagnostics;
}
