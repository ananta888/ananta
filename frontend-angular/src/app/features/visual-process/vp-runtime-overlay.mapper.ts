import type {
  VpRuntimeOverlay,
  VpRuntimeStepOverlay,
} from './visual-process-api.service';
import {
  extractVpDatasetBuildRuntime,
  extractVpTrainingRuntime,
} from './vp-model-training-contract';

const MAX_ID_LENGTH = 160;
export const VP_WORKFLOW_STATUS_SCHEMA = 'ananta.workflow_backend_status.v1';

const OVERALL_STATUS_ALIASES = Object.freeze({
  created: 'pending',
  queued: 'pending',
  pending: 'pending',
  waiting: 'pending',
  running: 'running',
  in_progress: 'running',
  cancel_requested: 'running',
  paused: 'awaiting_approval',
  waiting_for_approval: 'awaiting_approval',
  waiting_for_review: 'awaiting_approval',
  done: 'succeeded',
  success: 'succeeded',
  completed: 'succeeded',
  succeeded: 'succeeded',
  error: 'failed',
  failed: 'failed',
  degraded: 'failed',
  unavailable: 'failed',
  interrupted: 'failed',
  rejected: 'failed',
  rejected_by_policy: 'failed',
  cancelled: 'cancelled',
  canceled: 'cancelled',
  skipped: 'skipped',
  unknown: 'unknown',
} satisfies Readonly<Record<string, VpRuntimeStepOverlay['status']>>);

const STEP_STATUS_ALIASES = Object.freeze({
  ...OVERALL_STATUS_ALIASES,
});

const TERMINAL_STATUSES = new Set<VpRuntimeStepOverlay['status']>([
  'succeeded',
  'failed',
  'cancelled',
  'skipped',
]);

export interface VpRuntimeDecodeScope {
  readonly workflow_id?: string;
  readonly graph_id?: string;
  readonly run_id?: string;
  readonly require_revision?: boolean;
}

export interface VpDecodedRuntime {
  readonly kind: 'runtime';
  readonly overlay: VpRuntimeOverlay;
  readonly normalized_status: VpRuntimeStepOverlay['status'];
  readonly revision: number | null;
  readonly terminal: boolean;
}

export interface VpDecodedNoRun {
  readonly kind: 'no_run';
  readonly workflow_id: string;
}

export type VpDecodedWorkflowStatus = VpDecodedRuntime | VpDecodedNoRun;

export class VpRuntimeOverlayContractError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'VpRuntimeOverlayContractError';
  }
}

/**
 * Strictly decodes the allowlisted runtime projection shared by the full
 * VisualProcess editor and the CaseFlow read-only session. In particular, a
 * workflow ID is never substituted for the Hub-owned top-level run ID.
 */
export function decodeVpWorkflowStatus(
  raw: unknown,
  scope: Readonly<VpRuntimeDecodeScope>,
): VpDecodedWorkflowStatus {
  const value = record(raw, 'vp_runtime_response_invalid');
  if (value['schema'] !== VP_WORKFLOW_STATUS_SCHEMA) {
    fail('vp_runtime_schema_unsupported');
  }
  const workflowId = identity(value['workflow_id'], 'vp_runtime_workflow_id_invalid');
  if (scope.workflow_id !== undefined && workflowId !== scope.workflow_id) {
    fail('vp_runtime_workflow_scope_mismatch');
  }

  const sourceOverallStatus = requiredStatusSource(value['status']);
  const rawOverallStatus = statusText(sourceOverallStatus);
  if (rawOverallStatus === 'not_found') {
    if (presentIdentity(value['run_id']) !== null) fail('vp_runtime_not_found_run_id_invalid');
    return Object.freeze({ kind: 'no_run', workflow_id: workflowId });
  }

  const overallStatus = normalizeStatus(rawOverallStatus, OVERALL_STATUS_ALIASES);
  const runId = identity(value['run_id'], 'vp_runtime_run_id_invalid');
  if (scope.run_id !== undefined && runId !== scope.run_id) {
    fail('vp_runtime_run_scope_mismatch');
  }

  const processId = optionalIdentity(value['process_id'], 'vp_runtime_process_id_invalid');
  const graphId = processId ?? workflowId;
  if (scope.graph_id !== undefined && graphId !== scope.graph_id) {
    fail('vp_runtime_graph_scope_mismatch');
  }

  const revision = optionalRevision(value['revision']);
  if (scope.require_revision && revision === null) fail('vp_runtime_revision_required');
  const updatedAt = nonNegativeFinite(value['updated_at'], 'vp_runtime_updated_at_invalid');
  const rawSteps = value['steps'] === undefined
    ? []
    : array(value['steps'], 'vp_runtime_steps_invalid');
  const mappedSteps: Record<string, VpRuntimeStepOverlay> = {};
  for (const rawStep of rawSteps) {
    const step = decodeStep(rawStep);
    if (Object.hasOwn(mappedSteps, step.step_id)) fail('vp_runtime_duplicate_step_id');
    mappedSteps[step.step_id] = step;
  }
  assertOverallStepConsistency(overallStatus, mappedSteps);

  const currentStepIds = Object.values(mappedSteps)
    .filter(step => step.status === 'running' || step.status === 'awaiting_approval')
    .map(step => step.step_id);
  const processVersion = optionalText(value['process_version'], 'vp_runtime_process_version_invalid');
  const snapshotHash = optionalText(value['snapshot_hash'], 'vp_runtime_snapshot_hash_invalid');
  const startedAt = optionalNonNegativeFinite(value['started_at'], 'vp_runtime_started_at_invalid');
  const finishedAt = optionalNonNegativeFinite(value['finished_at'], 'vp_runtime_finished_at_invalid');
  const error = optionalText(value['error'], 'vp_runtime_error_invalid');
  const gate = optionalRecordCopy(value['gate'], 'vp_runtime_gate_invalid');

  const overlay: VpRuntimeOverlay = {
    run_id: runId,
    workflow_id: workflowId,
    ...(processId === undefined ? {} : { process_id: processId }),
    ...(processVersion === undefined ? {} : { process_version: processVersion }),
    ...(snapshotHash === undefined ? {} : { snapshot_hash: snapshotHash }),
    overall_status: sourceOverallStatus,
    current_step_ids: currentStepIds,
    steps: mappedSteps,
    ...(startedAt === undefined ? {} : { started_at: startedAt }),
    ...(finishedAt === undefined ? {} : { finished_at: finishedAt }),
    updated_at: updatedAt,
    ...(error === undefined ? {} : { error }),
    ...(gate === undefined ? {} : { gate }),
  };

  return Object.freeze({
    kind: 'runtime',
    overlay: freezeOverlay(overlay),
    normalized_status: overallStatus,
    revision,
    terminal: TERMINAL_STATUSES.has(overallStatus),
  });
}

function assertOverallStepConsistency(
  overallStatus: VpRuntimeStepOverlay['status'],
  steps: Readonly<Record<string, VpRuntimeStepOverlay>>,
): void {
  const stepStatuses = Object.values(steps).map(step => step.status);
  if (
    TERMINAL_STATUSES.has(overallStatus)
    && stepStatuses.some(status => status === 'running' || status === 'awaiting_approval')
  ) {
    fail('vp_runtime_terminal_step_state_conflict');
  }
  if (
    overallStatus === 'succeeded'
    && stepStatuses.some(status => status === 'pending' || status === 'unknown')
  ) {
    fail('vp_runtime_terminal_step_state_conflict');
  }
}

function decodeStep(raw: unknown): VpRuntimeStepOverlay {
  const value = record(raw, 'vp_runtime_step_invalid');
  const stepId = identity(value['step_id'], 'vp_runtime_step_id_invalid');
  const status = decodeStepStatus(value);
  const startedAt = optionalNonNegativeFinite(value['started_at'], 'vp_runtime_step_started_at_invalid');
  const finishedAt = optionalNonNegativeFinite(value['finished_at'], 'vp_runtime_step_finished_at_invalid');
  const durationMs = optionalNonNegativeFinite(value['duration_ms'], 'vp_runtime_step_duration_invalid');
  const error = optionalText(value['error'], 'vp_runtime_step_error_invalid');
  const gate = optionalRecordCopy(value['gate'], 'vp_runtime_step_gate_invalid');
  const selectedModelProfileId = optionalText(
    value['selected_model_profile_id'],
    'vp_runtime_step_model_profile_invalid',
  );
  const selectedProviderId = optionalText(
    value['selected_provider_id'],
    'vp_runtime_step_provider_invalid',
  );
  const selectedModel = optionalText(value['selected_model'], 'vp_runtime_step_model_invalid');
  const fallbackAttempts = optionalArrayCopy(value['fallback_attempts'], 'vp_runtime_step_fallbacks_invalid');
  const llmCallProfile = optionalArrayCopy(value['llm_call_profile'], 'vp_runtime_step_llm_profile_invalid');
  const training = extractVpTrainingRuntime(value) ?? undefined;
  const datasetBuild = extractVpDatasetBuildRuntime(value) ?? undefined;

  return Object.freeze({
    step_id: stepId,
    status,
    ...(startedAt === undefined ? {} : { started_at: startedAt }),
    ...(finishedAt === undefined ? {} : { finished_at: finishedAt }),
    ...(durationMs === undefined ? {} : { duration_ms: durationMs }),
    ...(error === undefined ? {} : { error }),
    ...(gate === undefined ? {} : { gate }),
    ...(selectedModelProfileId === undefined ? {} : { selected_model_profile_id: selectedModelProfileId }),
    ...(selectedProviderId === undefined ? {} : { selected_provider_id: selectedProviderId }),
    ...(selectedModel === undefined ? {} : { selected_model: selectedModel }),
    ...(fallbackAttempts === undefined ? {} : { fallback_attempts: fallbackAttempts }),
    ...(llmCallProfile === undefined ? {} : { llm_call_profile: llmCallProfile }),
    ...(training === undefined ? {} : { training }),
    ...(datasetBuild === undefined ? {} : { datasetBuild }),
  });
}

function decodeStepStatus(value: Readonly<Record<string, unknown>>): VpRuntimeStepOverlay['status'] {
  const hasRunState = value['run_state'] !== undefined && value['run_state'] !== null;
  const hasStatus = value['status'] !== undefined && value['status'] !== null;
  if (!hasRunState && !hasStatus) fail('vp_runtime_status_invalid');

  const runState = hasRunState
    ? normalizeStatus(statusText(value['run_state']), STEP_STATUS_ALIASES)
    : null;
  const status = hasStatus
    ? normalizeStatus(statusText(value['status']), STEP_STATUS_ALIASES)
    : null;
  if (runState !== null && status !== null && runState !== status) {
    fail('vp_runtime_step_status_conflict');
  }
  return runState ?? status!;
}

function freezeOverlay(overlay: VpRuntimeOverlay): VpRuntimeOverlay {
  Object.freeze(overlay.current_step_ids);
  Object.freeze(overlay.steps);
  return Object.freeze(overlay);
}

function normalizeStatus(
  raw: string,
  aliases: Readonly<Record<string, VpRuntimeStepOverlay['status']>>,
): VpRuntimeStepOverlay['status'] {
  const result = aliases[raw];
  if (result === undefined) fail('vp_runtime_status_invalid');
  return result;
}

function statusText(raw: unknown): string {
  const result = requiredStatusSource(raw).trim().toLowerCase();
  if (!result) fail('vp_runtime_status_invalid');
  return result;
}

function requiredStatusSource(raw: unknown): string {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 64) {
    fail('vp_runtime_status_invalid');
  }
  return raw;
}

function identity(raw: unknown, reason: string): string {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > MAX_ID_LENGTH) fail(reason);
  if (raw !== raw.trim() || hasControlCharacter(raw)) fail(reason);
  return raw;
}

function presentIdentity(raw: unknown): string | null {
  if (raw === undefined || raw === null || raw === '') return null;
  return identity(raw, 'vp_runtime_run_id_invalid');
}

function optionalIdentity(raw: unknown, reason: string): string | undefined {
  if (raw === undefined || raw === null || raw === '') return undefined;
  return identity(raw, reason);
}

function optionalText(raw: unknown, reason: string): string | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== 'string' || raw.length > 2048) fail(reason);
  return raw;
}

function optionalRevision(raw: unknown): number | null {
  if (raw === undefined || raw === null) return null;
  if (!Number.isSafeInteger(raw) || Number(raw) < 0) fail('vp_runtime_revision_invalid');
  return Number(raw);
}

function optionalNonNegativeFinite(raw: unknown, reason: string): number | undefined {
  if (raw === undefined || raw === null) return undefined;
  return nonNegativeFinite(raw, reason);
}

function nonNegativeFinite(raw: unknown, reason: string): number {
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0) fail(reason);
  return raw;
}

function optionalRecordCopy(
  raw: unknown,
  reason: string,
): Record<string, unknown> | undefined {
  if (raw === undefined || raw === null) return undefined;
  return Object.freeze({ ...record(raw, reason) });
}

function optionalArrayCopy(raw: unknown, reason: string): unknown[] | undefined {
  if (raw === undefined || raw === null) return undefined;
  return Object.freeze([...array(raw, reason)]) as unknown as unknown[];
}

function array(raw: unknown, reason: string): unknown[] {
  if (!Array.isArray(raw) || raw.length > 4096) fail(reason);
  return raw;
}

function record(raw: unknown, reason: string): Record<string, unknown> {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) fail(reason);
  return raw as Record<string, unknown>;
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });
}

function fail(reason: string): never {
  throw new VpRuntimeOverlayContractError(reason);
}
